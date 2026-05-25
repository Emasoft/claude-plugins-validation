#!/usr/bin/env python3
"""Claude Plugins Validation — prompt-cache audit (CA-01 .. CA-07).

Validates a plugin against Anthropic's 6 prompt-caching rules surfaced by
ussumant/cache-audit (https://github.com/ussumant/cache-audit), plus CA-07
(the `context: fork`/`branch` cache cost). Plugins that ship hooks / skills /
agents can silently break the prompt cache for every user that installs them
— multiplying API costs by 5-10x and adding latency on every turn. This
validator catches the documented breakage patterns before publication.

Reference: "Lessons from Building Claude Code: Prompt Caching Is
Everything" by Thariq Shihipar (Anthropic).

Usage::

    uv run python scripts/validate_cache.py path/to/plugin/
    uv run python scripts/validate_cache.py path/to/plugin/ --report /tmp/c.md

Exit codes (standard CPV severity-coded):

    0 - No blocking issues. Since v2.102.0 EVERY cache-discipline finding
        (CA-01 .. CA-06) is reported at WARNING severity — a cache miss costs
        tokens/latency but never makes a plugin invalid, so the cache audit
        alone always exits 0.
    1 - CRITICAL — invocation error ONLY (target path missing / not a
        directory / no .claude-plugin/plugin.json). The CA-NN rules
        themselves never raise CRITICAL.

`validate_plugin` CALLS this scanner (it does not merge its logic): the cache
findings are written to a SEPARATE report and surfaced as a one-line pointer
in the main validation report, so cache warnings never enter the main verdict.
CA-04 covers a `model:` frontmatter on ANY component (agents, commands AND
skills) — `model: inherit` is exempt.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from cpv_management_common import load_jsonc
from cpv_parallel_runner import parallel_scan
from cpv_validation_common import (
    EXIT_CRITICAL,
    EXIT_OK,
    ValidationReport,
    ValidationResult,
    check_remote_execution_guard,
    print_results_by_level,
    save_report_and_print_summary,
)

# =============================================================================
# CA-01 — Dynamic-content patterns that break the static prompt prefix
# =============================================================================

# Dynamic placeholder tokens that change every session/turn. Static
# placeholders ({{CLAUDE_PROJECT_DIR}}, {{CLAUDE_PLUGIN_ROOT}},
# {{CLAUDE_PLUGIN_DATA}}) are explicitly excluded — those resolve to a
# stable path for the lifetime of the session and don't bust the cache.
_DYNAMIC_PLACEHOLDER = re.compile(
    r"\{\{\s*(?:TIMESTAMP|DATE|TIME|NOW|CURRENT_TIME|CURRENT_DATE|"
    r"TODAY|GIT_STATUS|GIT_LOG|GIT_DIFF|RANDOM|UUID|SESSION_ID)\s*\}\}",
    re.IGNORECASE,
)

# Shell command-substitution that produces session-specific output. We
# only match these inside files we treat as part of the static prefix
# (CLAUDE.md, agent system-prompt, skill SKILL.md). Bash backticks and
# $(...) inside hook scripts / fenced ```bash blocks aren't a CA-01
# concern — those are runtime-only and never get cached.
_DYNAMIC_SHELL_CMD = re.compile(
    r"\$\(\s*(?:date|git\s+(?:status|log|diff|show)|"
    r"hostname|whoami|uptime|uname)\b"
)

# CLAUDE_PLUGIN_OPTION_<KEY> placeholders — dynamic per-user, but stable
# within a session. Treated as static for cache purposes.
_STATIC_OPTION_PLACEHOLDER = re.compile(r"\$\{?CLAUDE_PLUGIN_OPTION_[A-Z0-9_]+\}?")


# =============================================================================
# CA-02 — Hook scripts that mutate the cached system-prompt prefix
# =============================================================================

# Files that, when written from a SessionStart / UserPromptSubmit / PreCompact
# hook, invalidate the cached prefix for the entire session.
_PREFIX_FILE_PATTERNS = (
    re.compile(r"\bCLAUDE\.md\b"),
    re.compile(r"\bclaude\.md\b"),
    re.compile(r"\.claude/CLAUDE\.md"),
    re.compile(r"\.claude/settings\.json"),
    re.compile(r"\.claude-plugin/plugin\.json"),
    re.compile(r"\.claude-plugin/marketplace\.json"),
)

# Shell write operators against a target path (>>, >, tee -a, sed -i).
#
# The redirect alternatives use a `(?<![0-9&])` negative lookbehind and a
# `(?!/dev/(?:null|stderr|stdout)\b)(?!&)` negative lookahead so that an FD
# redirect / discard (`2>/dev/null`, `1>&2`, `2>&1`, `> /dev/null`) does NOT
# count as a file write. Without this, a line like
# `foo 2>/dev/null && echo CLAUDE.md is fine` matched the bare `>` op and, with
# the same-line CLAUDE.md mention, produced a false CA-02 WARNING (audit NIT #7).
# A genuine `> CLAUDE.md` / `>> file` / `echo x > out` still matches.
_FILE_WRITE_OPS = re.compile(
    r"(?:"
    r"\btee(?:\s+-a|\s+--append)?\s+\S+"  # tee / tee -a FILE
    r"|(?<![0-9&])>>\s*(?!/dev/(?:null|stderr|stdout)\b)(?!&)\S+"  # >> FILE (not FD dup / discard)
    r"|(?<![0-9&])>\s*(?!/dev/(?:null|stderr|stdout)\b)(?!&)\S+"  # > FILE (not FD redirect / discard)
    r"|\bsed\s+-i\s+\S+\s+\S+"  # sed -i ... FILE
    r"|\bcp\s+\S+\s+\S+"  # cp src dst
    r"|\bmv\s+\S+\s+\S+"  # mv src dst
    r"|\becho\s+\S+\s*(?<![0-9&])>>?(?!&)"  # echo X >> / > (not echo X 2>… FD redirect)
    r")"
)

# Hook events whose output IS part of the cached prefix. Stop / SubagentStop
# / Notification / PostToolUse run AFTER the turn or as side-effects, so
# touching CLAUDE.md from those is a CA-02 PASS (not a violation).
_PREFIX_AFFECTING_EVENTS: frozenset[str] = frozenset(
    {
        "SessionStart",
        "UserPromptSubmit",
        "PreCompact",
        "InstructionsLoaded",
    }
)


# =============================================================================
# CA-03 — Tool-set instability patterns
# =============================================================================

# Hook scripts that mutate the allow/deny / tool list in settings.json
# would cause the tool schema to differ between turns.
_TOOL_LIST_MUTATION = re.compile(r"\b(?:allow|deny|allowedTools|disallowedTools|enabled[Mm]cp[Ss]ervers)\b")


# =============================================================================
# CA-05 — Hook scripts likely to dump unbounded output
# =============================================================================

# Patterns that emit potentially large text to stdout. Each is paired with
# a corresponding "bounded" guard pattern; if we see the unbounded form
# WITHOUT the guard on the same line, we flag.
_UNBOUNDED_PATTERNS: tuple[tuple[re.Pattern[str], str, re.Pattern[str]], ...] = (
    (
        re.compile(r"\bgit\s+status\b(?!\s+(?:--short|--porcelain|-s))"),
        "git status (use --short or --porcelain | head)",
        re.compile(r"\bhead\s+-n?\s*\d+\b|\|\s*head\b"),
    ),
    (
        re.compile(r"\bgit\s+log\b(?!\s+--oneline)(?!.*-n\s*\d+)"),
        "git log (use -n N or --oneline | head)",
        re.compile(r"-n\s*\d+\b|--oneline\b|\|\s*head\b"),
    ),
    (
        re.compile(r"\bgit\s+diff\b(?!.*--stat)(?!.*-U0)"),
        "git diff (use --stat or | head)",
        re.compile(r"--stat\b|-U0\b|\|\s*head\b"),
    ),
    (
        re.compile(r"\bfind\s+\S+(?:\s+-\w+\s+\S+)*"),
        "find (cap with -maxdepth or | head)",
        re.compile(r"-maxdepth\s+\d+\b|\|\s*head\b"),
    ),
    (
        re.compile(r"\bls\s+-[laR]+\b"),
        "ls -laR (cap with | head)",
        re.compile(r"\|\s*head\b"),
    ),
    (
        re.compile(r"\bcat\s+\S+"),
        "cat (cap with | head)",
        re.compile(r"\|\s*head\b|\|\s*tail\b"),
    ),
)


# =============================================================================
# Helpers
# =============================================================================


_INLINE_CODE_SPAN = re.compile(r"`[^`\n]+`")


def _strip_fences_for_dynamic_check(content: str) -> str:
    """Remove all code formatting before CA-01 dynamic-marker check.

    Dynamic markers inside ```fenced blocks``` AND single-backtick `inline
    code` are documentation examples — neither participates in the cached
    prefix as live data (Claude Code does not shell-evaluate `.md` content).
    Stripping both lets CA-01 catch only literal dynamic substitutions in
    plain prose, which is the actual cache-busting failure mode.
    """
    lines = content.split("\n")
    kept: list[str] = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # Strip inline `code spans` from the prose-only line
        kept.append(_INLINE_CODE_SPAN.sub("", line))
    return "\n".join(kept)


def _iter_static_prefix_files(plugin_root: Path) -> Iterable[Path]:
    """Yield files whose content forms the cached system-prompt prefix.

    These are files Claude Code reads at session start / sub-agent dispatch
    to build the static prefix:
    - Plugin's CLAUDE.md (root or .claude/CLAUDE.md)
    - All agent .md files (system prompts)
    - All skill SKILL.md files (skill body becomes context when invoked)
    """
    candidates = [
        plugin_root / "CLAUDE.md",
        plugin_root / ".claude" / "CLAUDE.md",
    ]
    for c in candidates:
        if c.is_file():
            yield c
    agents_dir = plugin_root / "agents"
    if agents_dir.is_dir():
        for p in agents_dir.rglob("*.md"):
            yield p
    skills_dir = plugin_root / "skills"
    if skills_dir.is_dir():
        for p in skills_dir.rglob("SKILL.md"):
            yield p


def _resolve_hook_command(plugin_root: Path, command: str) -> Path | None:
    """Resolve a hooks.json `command` field to an absolute file path.

    Returns None if the command refers to a system binary (`bash`, `python3`)
    rather than a script shipped with the plugin. The CA-02 / CA-05 checks
    can only inspect scripts that live inside the plugin tree.
    """
    if not command:
        return None
    # Strip env-var expansion + leading args; we only need the script path
    # the command actually executes.
    cleaned = command.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root))
    cleaned = cleaned.replace("$CLAUDE_PLUGIN_ROOT", str(plugin_root))
    parts = cleaned.split()
    for p in parts:
        if p.startswith("-") or "=" in p:
            continue
        candidate = Path(p)
        if not candidate.is_absolute():
            candidate = plugin_root / candidate
        if candidate.is_file():
            return candidate
    return None


# =============================================================================
# CA-01 — Static prompt prefix scan
# =============================================================================


def _collect_static_prefix(file_path: Path, plugin_root: Path) -> list[ValidationResult]:
    """Per-file CA-01 collector — returns a list of ValidationResult.

    Extracted so the same scan body runs both inline (legacy serial path)
    and inside a `ProcessPoolExecutor` worker (parallel path). Returning
    `ValidationResult` instances rather than mutating a shared report
    object is what makes the worker pickleable and the parallel branch
    bit-identical to the serial branch — the parent merges the returned
    list into its master report in input order.
    """
    out: list[ValidationResult] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    rel = str(file_path.relative_to(plugin_root)) if file_path.is_relative_to(plugin_root) else str(file_path)

    # Normalize CRLF → LF so fence-stripping (which splits on "\n") sees clean
    # lines and the CA-01 regexes scan the same text whether the prefix file was
    # authored with LF or CRLF endings (audit MINOR #5).
    content = content.replace("\r\n", "\n")

    # Strip option placeholders (CLAUDE_PLUGIN_OPTION_*) before any scan —
    # those resolve once per session install and are stable.
    sanitized = _STATIC_OPTION_PLACEHOLDER.sub("CLAUDE_PLUGIN_OPTION", content)
    fenced_stripped = _strip_fences_for_dynamic_check(sanitized)

    for match in _DYNAMIC_PLACEHOLDER.finditer(fenced_stripped):
        out.append(
            ValidationResult(
                level="WARNING",
                message=f"CA-01: dynamic placeholder {match.group(0)!r} in cached prefix file",
                file=rel,
            )
        )
    for match in _DYNAMIC_SHELL_CMD.finditer(fenced_stripped):
        out.append(
            ValidationResult(
                level="WARNING",
                message=f"CA-01: shell command substitution {match.group(0)!r} in cached prefix file",
                file=rel,
            )
        )
    return out


def scan_static_prefix(file_path: Path, report: ValidationReport, plugin_root: Path) -> int:
    """Flag dynamic placeholders / shell substitutions in static-prefix files.

    Public API preserved for backwards compatibility. Internally delegates
    to ``_collect_static_prefix`` and forwards the returned findings into
    ``report``.
    """
    findings = _collect_static_prefix(file_path, plugin_root)
    for r in findings:
        report.results.append(r)
    return len(findings)


# =============================================================================
# CA-02 — Hook scripts that mutate the cached prefix
# =============================================================================


def _collect_hook_for_prefix_mutation(
    script_path: Path,
    event: str,
    plugin_root: Path,
) -> list[ValidationResult]:
    """Per-hook CA-02 collector — returns the findings list."""
    out: list[ValidationResult] = []
    if event not in _PREFIX_AFFECTING_EVENTS:
        return out
    try:
        content = script_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    rel = str(script_path.relative_to(plugin_root)) if script_path.is_relative_to(plugin_root) else str(script_path)

    for line_num, line in enumerate(content.split("\n"), start=1):
        # Skip pure comments
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            continue
        if not _FILE_WRITE_OPS.search(line):
            continue
        for prefix_pat in _PREFIX_FILE_PATTERNS:
            if prefix_pat.search(line):
                out.append(
                    ValidationResult(
                        level="WARNING",
                        message=f"CA-02: {event} hook writes to cached-prefix file",
                        file=rel,
                        line=line_num,
                    )
                )
                break
    return out


def scan_hook_for_prefix_mutation(
    script_path: Path,
    event: str,
    report: ValidationReport,
    plugin_root: Path,
) -> int:
    """Flag a hook script that writes to a cached-prefix file.

    Public API preserved; delegates to ``_collect_hook_for_prefix_mutation``.
    """
    findings = _collect_hook_for_prefix_mutation(script_path, event, plugin_root)
    for r in findings:
        report.results.append(r)
    return len(findings)


# =============================================================================
# CA-03 — Hook scripts that toggle the tool set
# =============================================================================


def _collect_hook_for_tool_mutation(
    script_path: Path,
    event: str,
    plugin_root: Path,
) -> list[ValidationResult]:
    """Per-hook CA-03 collector — returns the findings list."""
    out: list[ValidationResult] = []
    if event not in _PREFIX_AFFECTING_EVENTS:
        return out
    try:
        content = script_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    rel = str(script_path.relative_to(plugin_root)) if script_path.is_relative_to(plugin_root) else str(script_path)

    for line_num, line in enumerate(content.split("\n"), start=1):
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            continue
        # We only flag if BOTH (a) the line writes to a settings/config file
        # AND (b) it mentions one of the tool-list keys. Either alone is FP.
        if not _FILE_WRITE_OPS.search(line):
            continue
        if "settings.json" not in line and ".claude-plugin" not in line:
            continue
        if not _TOOL_LIST_MUTATION.search(line):
            continue
        out.append(
            ValidationResult(
                level="WARNING",
                message=f"CA-03: {event} hook mutates tool-list field",
                file=rel,
                line=line_num,
            )
        )
    return out


def scan_hook_for_tool_mutation(
    script_path: Path,
    event: str,
    report: ValidationReport,
    plugin_root: Path,
) -> int:
    """Flag hook scripts that flip allow/deny lists or enable MCP servers.

    Public API preserved; delegates to ``_collect_hook_for_tool_mutation``.
    """
    findings = _collect_hook_for_tool_mutation(script_path, event, plugin_root)
    for r in findings:
        report.results.append(r)
    return len(findings)


# =============================================================================
# CA-04 — `model:` frontmatter on ANY component forces an in-line model switch
# =============================================================================


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
_MODEL_FIELD_RE = re.compile(r"^model:\s*(.+)$", re.MULTILINE)


def _collect_component_for_model_override(
    md_file: Path,
    plugin_root: Path,
    component_kind: str,
) -> list[ValidationResult]:
    """Per-component CA-04 collector — returns the findings list."""
    out: list[ValidationResult] = []
    try:
        content = md_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    # Normalize CRLF → LF: `_FRONTMATTER_RE` is `^---\n...\n---`, so a
    # CRLF-authored component ("---\r\nmodel: opus\r\n---") would NOT match and
    # silently escape CA-04. Claude Code accepts CRLF line endings, so such files
    # are real and shippable (audit MINOR #5).
    content = content.replace("\r\n", "\n")
    fm = _FRONTMATTER_RE.match(content)
    if not fm:
        return out
    front = fm.group(1)
    m = _MODEL_FIELD_RE.search(front)
    if not m:
        return out
    model = m.group(1).strip().strip("'").strip('"')
    # `model: inherit` uses the parent/session model — no in-line switch, so
    # the cache is never split. Treat it exactly like omitting the field.
    if model.lower() == "inherit":
        return out
    rel = str(md_file.relative_to(plugin_root)) if md_file.is_relative_to(plugin_root) else str(md_file)
    out.append(
        ValidationResult(
            level="WARNING",
            message=(
                f"CA-04: {component_kind} declares `model: {model}` in frontmatter — forces an in-line "
                f"model switch that fragments the prompt cache (each model keeps a separate cache, so this "
                f"{component_kind} pays a cold-cache miss on every dispatch instead of reusing the session's "
                f"warm prefix). Omit the `model:` field to inherit the session model and keep the cache warm; "
                f"use `model: inherit` if you must name it explicitly."
            ),
            file=rel,
        )
    )
    return out


def scan_component_for_model_override(
    md_file: Path,
    report: ValidationReport,
    plugin_root: Path,
    component_kind: str,
) -> int:
    """Flag a component .md whose frontmatter declares a `model:` override.

    Applies to agents, commands AND skills alike. Pinning a component to a
    specific model forces an in-line model switch when that component runs:
    each model keeps a SEPARATE prompt cache, so the pinned component pays a
    cold-cache miss on every dispatch instead of reusing the session's warm
    prefix. ``model: inherit`` is exempt — it explicitly uses the session
    model, so it triggers no switch and no cache split.

    The regex only matches a top-level ``model:`` key (column 0 of the
    frontmatter). A ``model:`` substring inside an indented block-scalar
    description never starts at column 0, so prose mentions don't false-fire.

    Public API preserved; delegates to ``_collect_component_for_model_override``.
    """
    findings = _collect_component_for_model_override(md_file, plugin_root, component_kind)
    for r in findings:
        report.results.append(r)
    return len(findings)


# =============================================================================
# CA-07 — `context: fork` / `context: branch` re-primes the cache from cold
# =============================================================================


_CONTEXT_FIELD_RE = re.compile(r"^context:\s*(.+)$", re.MULTILINE)


def _collect_component_for_context_fork(
    md_file: Path,
    plugin_root: Path,
    component_kind: str,
) -> list[ValidationResult]:
    """Per-component CA-07 collector — returns the findings list."""
    out: list[ValidationResult] = []
    try:
        content = md_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    # Normalize CRLF → LF so `_FRONTMATTER_RE` (`^---\n...\n---`) matches a
    # CRLF-authored component; otherwise `context: fork` silently escapes CA-07
    # (audit MINOR #5).
    content = content.replace("\r\n", "\n")
    fm = _FRONTMATTER_RE.match(content)
    if not fm:
        return out
    m = _CONTEXT_FIELD_RE.search(fm.group(1))
    if not m:
        return out
    value = m.group(1).strip().strip("'").strip('"').lower()
    if value not in ("fork", "branch"):
        return out
    rel = str(md_file.relative_to(plugin_root)) if md_file.is_relative_to(plugin_root) else str(md_file)
    out.append(
        ValidationResult(
            level="WARNING",
            message=(
                f"CA-07: {component_kind} declares `context: {value}` in frontmatter — forks a fresh "
                f"subagent whose prompt prefix is re-primed from cold (up to ~1M tokens when the harness "
                f"carries many skills/MCP/tools; only CLAUDE.md + rules files survive a fork). Keep the fork "
                f"ONLY if this {component_kind} needs a fresh context (independent audit / error-checking) or "
                f"the room to read many files; otherwise drop the `context:` field to inherit the parent "
                f"context and keep the cache warm."
            ),
            file=rel,
        )
    )
    return out


def scan_component_for_context_fork(
    md_file: Path,
    report: ValidationReport,
    plugin_root: Path,
    component_kind: str,
) -> int:
    """Flag a component whose frontmatter declares `context: fork` (or `branch`).

    Forking spins up a fresh subagent: its system-prompt + tool-schema prefix is
    re-primed from cold — up to ~1M tokens when the harness carries many skills /
    MCP servers / tools (only CLAUDE.md and the rules files survive a fork
    unchanged). Fork ONLY when the work genuinely needs a FRESH context
    (independent audit / error-checking, free of parent baggage) or the ROOM a
    fresh context buys (reading many files). Otherwise inherit the parent context
    and keep the cache warm. WARNING — advisory, never blocks.

    `branch` is a documented synonym for `fork`; both are flagged. Any other
    `context:` value (e.g. the default inherited context) is exempt.

    Public API preserved; delegates to ``_collect_component_for_context_fork``.
    """
    findings = _collect_component_for_context_fork(md_file, plugin_root, component_kind)
    for r in findings:
        report.results.append(r)
    return len(findings)


# =============================================================================
# CA-05 — Hook scripts likely to emit unbounded output
# =============================================================================


def _collect_hook_for_unbounded_output(
    script_path: Path,
    event: str,
    plugin_root: Path,
) -> list[ValidationResult]:
    """Per-hook CA-05 collector — returns the findings list."""
    out: list[ValidationResult] = []
    if event not in _PREFIX_AFFECTING_EVENTS:
        return out
    try:
        content = script_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    rel = str(script_path.relative_to(plugin_root)) if script_path.is_relative_to(plugin_root) else str(script_path)

    for line_num, line in enumerate(content.split("\n"), start=1):
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            continue
        # CA-05 only cares about output DUMPED to stdout (which bloats the cached
        # prefix). When the command's output is redirected to a FILE on the same
        # line (e.g. `cat x > CLAUDE.md`, `find . -name y > out.txt`,
        # `ls -laR > listing`), stdout is captured to disk and cannot inflate the
        # prefix, so the unbounded-output patterns (`cat \S+`, `find …`,
        # `ls -[laR]+`) were firing on plain WRITES and producing noise. Skip such
        # lines (audit NIT #8). `_FILE_WRITE_OPS` is already FD-redirect-aware, so
        # a `2>/dev/null` on a genuine dump line does NOT suppress the finding.
        if _FILE_WRITE_OPS.search(line):
            continue
        for unbounded_pat, label, guard_pat in _UNBOUNDED_PATTERNS:
            if unbounded_pat.search(line) and not guard_pat.search(line):
                out.append(
                    ValidationResult(
                        level="WARNING",
                        message=f"CA-05: {event} hook may emit unbounded output: {label}",
                        file=rel,
                        line=line_num,
                    )
                )
                break  # one finding per line is enough
    return out


def scan_hook_for_unbounded_output(
    script_path: Path,
    event: str,
    report: ValidationReport,
    plugin_root: Path,
) -> int:
    """Flag hook scripts that emit unbounded git/find/cat/ls output.

    Public API preserved; delegates to ``_collect_hook_for_unbounded_output``.
    """
    findings = _collect_hook_for_unbounded_output(script_path, event, plugin_root)
    for r in findings:
        report.results.append(r)
    return len(findings)


# =============================================================================
# CA-06 — Compaction / subagent fork-safety
# =============================================================================


_FORK_AFFECTING_EVENTS: frozenset[str] = frozenset(
    {
        "PreCompact",
        "PostCompact",
        "SubagentStart",
    }
)


def _collect_hook_for_fork_unsafe(
    script_path: Path,
    event: str,
    plugin_root: Path,
) -> list[ValidationResult]:
    """Per-hook CA-06 collector — returns the findings list."""
    out: list[ValidationResult] = []
    if event not in _FORK_AFFECTING_EVENTS:
        return out
    try:
        content = script_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    rel = str(script_path.relative_to(plugin_root)) if script_path.is_relative_to(plugin_root) else str(script_path)

    if any(p.search(content) for p in _PREFIX_FILE_PATTERNS) and _FILE_WRITE_OPS.search(content):
        out.append(
            ValidationResult(
                level="WARNING",
                message=f"CA-06: {event} hook touches cached-prefix files — verify the parent prefix is preserved across the fork",
                file=rel,
            )
        )
    return out


def scan_hook_for_fork_unsafe(
    script_path: Path,
    event: str,
    report: ValidationReport,
    plugin_root: Path,
) -> int:
    """Flag fork-affecting hooks that overwrite the cached prefix.

    Conservative: only emits a WARNING for now since most plugins do not
    ship compaction logic and a definitive answer requires runtime inspection.

    Public API preserved; delegates to ``_collect_hook_for_fork_unsafe``.
    """
    findings = _collect_hook_for_fork_unsafe(script_path, event, plugin_root)
    for r in findings:
        report.results.append(r)
    return len(findings)


# =============================================================================
# Plugin-level orchestration
# =============================================================================


def _iter_hook_entries(hooks_obj: object) -> Iterable[tuple[str, dict]]:
    """Walk a hooks.json structure and yield (event, hook_dict) tuples.

    Schema per Claude Code v2.1.x:
      hooks: { <Event>: [ { hooks: [ { type, command, ... }, ... ], matcher: "..." }, ... ] }
    """
    if not isinstance(hooks_obj, dict):
        return
    hooks_section = hooks_obj.get("hooks", hooks_obj)
    if not isinstance(hooks_section, dict):
        return
    for event, matchers in hooks_section.items():
        if not isinstance(event, str):
            continue
        if not isinstance(matchers, list):
            continue
        for matcher in matchers:
            if not isinstance(matcher, dict):
                continue
            inner = matcher.get("hooks", [])
            if not isinstance(inner, list):
                continue
            for h in inner:
                if isinstance(h, dict):
                    yield event, h


def _collect_hook_files(plugin_root: Path) -> list[tuple[str, Path]]:
    """Resolve every hook script the plugin ships, paired with its event name."""
    out: list[tuple[str, Path]] = []
    sources = [
        plugin_root / "hooks" / "hooks.json",
        plugin_root / "hooks" / "hooks.jsonc",
    ]
    for src in sources:
        if not src.is_file():
            continue
        try:
            data = load_jsonc(src) if src.suffix == ".jsonc" else json.loads(src.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for event, hook in _iter_hook_entries(data):
            command = hook.get("command", "") if isinstance(hook, dict) else ""
            if hook.get("type") not in (None, "command"):
                # http / prompt / agent hooks don't execute a script we can scan
                continue
            script = _resolve_hook_command(plugin_root, command)
            if script is not None:
                out.append((event, script))
    return out


# =============================================================================
# Task #384 — Parallel per-file dispatch via the shared parallel_scan harness
# =============================================================================
#
# The cache validator's per-file work is split across FIVE distinct logics
# (CA-01 static prefix, CA-02/03/05 per-hook, CA-04 model override on
# agents/commands/skills, CA-06 fork-unsafe hook, CA-07 context fork on
# skills/commands). Each per-file scan is independent — it reads ONE file,
# applies its regex/frontmatter check, and returns findings. No scanner
# shares mutable state with any other.
#
# A single uniform work unit (``_CacheWorkUnit``) tags each file with the
# kind of scan it needs (``"static_prefix"`` / ``"hook"`` / ``"component"``)
# plus the small bit of per-unit context (the event name for hooks, the
# component kind + which sub-scans to run for components). The top-level
# pickleable worker (``scan_one_cache_unit``) dispatches on ``kind``,
# delegates to the matching ``_collect_*`` helper, and returns a list of
# ``ValidationResult`` dataclasses. The parent merges those lists back into
# the master report IN INPUT ORDER so the final result is bit-identical to
# the legacy serial walk.
#
# Env-var escape hatch ``CPV_CACHE_PARALLEL=0`` forces the serial path
# (consistent with ``CPV_HOOK_PARALLEL`` and ``CPV_SECURITY_PARALLEL``).
# Any other value (or unset) keeps parallel as the default.
#
# Why one uniform work unit instead of five separate worker functions:
# (1) the per-file work for each kind is small enough that the dispatch
# overhead is in the noise; (2) keeping ONE pool with ONE work queue lets
# the harness load-balance across all units regardless of kind, instead of
# spawning five separate pools; (3) the merge logic becomes a single
# in-order loop over a single list rather than five parallel merges.


@dataclass(frozen=True)
class _CacheWorkUnit:
    """One per-file cache scan plus its discriminator + context.

    Frozen + primitives-only so the unit pickles cleanly across the
    ProcessPoolExecutor worker boundary. Paths are passed as strings (then
    reconstructed inside the worker) to dodge any Path-pickling quirks on
    Windows worker processes — same defensive choice the hook validator's
    ``_HookWorkUnit`` makes.

    Fields:
        kind: ``"static_prefix"`` | ``"hook"`` | ``"component"``. Selects
            which ``_collect_*`` helper the worker dispatches to.
        file_path_str: Absolute path to the file being scanned.
        plugin_root_str: Absolute plugin root (used to compute rel paths
            inside the worker).
        event: For ``kind="hook"``, the hooks.json event name (e.g.
            ``"SessionStart"``). Unused for other kinds.
        component_kind: For ``kind="component"``, the human-readable
            component kind (``"agent"`` / ``"command"`` / ``"skill"``).
        include_context_fork: For ``kind="component"``, whether to run the
            CA-07 ``context: fork`` check in addition to CA-04. Agents have
            no ``context:`` field — an agent IS the forked subagent —
            so this is False for agents and True for commands + skills.
    """

    kind: str
    file_path_str: str
    plugin_root_str: str
    event: str = ""
    component_kind: str = ""
    include_context_fork: bool = False


def scan_one_cache_unit(unit: _CacheWorkUnit) -> list[ValidationResult]:
    """Top-level pickleable worker: run one cache scan and return findings.

    Dispatches on ``unit.kind`` to the matching ``_collect_*`` helper.
    Returns the findings list as ``ValidationResult`` dataclasses — those
    are pickleable across the worker boundary; the parent appends them to
    the master report in input order.

    For ``kind="hook"`` we run ALL FOUR hook checks (CA-02/03/05/06) in
    one worker call because they all share the same input (read the same
    script file) — combining them halves the IPC overhead vs spawning four
    separate units for the same script. The findings are returned in the
    same order the serial loop produced them (prefix-mutation, then
    tool-mutation, then unbounded-output, then fork-unsafe), preserving
    the parity invariant.
    """
    file_path = Path(unit.file_path_str)
    plugin_root = Path(unit.plugin_root_str)

    if unit.kind == "static_prefix":
        return _collect_static_prefix(file_path, plugin_root)

    if unit.kind == "hook":
        out: list[ValidationResult] = []
        out.extend(_collect_hook_for_prefix_mutation(file_path, unit.event, plugin_root))
        out.extend(_collect_hook_for_tool_mutation(file_path, unit.event, plugin_root))
        out.extend(_collect_hook_for_unbounded_output(file_path, unit.event, plugin_root))
        out.extend(_collect_hook_for_fork_unsafe(file_path, unit.event, plugin_root))
        return out

    if unit.kind == "component":
        out = []
        out.extend(_collect_component_for_model_override(file_path, plugin_root, unit.component_kind))
        if unit.include_context_fork:
            out.extend(_collect_component_for_context_fork(file_path, plugin_root, unit.component_kind))
        return out

    # Unknown kind — return an empty list rather than raising so a future
    # accidental enum drift doesn't crash the validator. The aggregator
    # will surface this as missing findings, which the parity tests catch.
    return []


def _cache_parallel_enabled() -> bool:
    """Read the ``CPV_CACHE_PARALLEL`` env-var.

    Returns False when set to ``"0"`` / ``"false"`` / ``"no"`` / ``"off"``
    (case-insensitive) — the serial path is taken. Any other value, or no
    value at all, returns True (default = parallel). Mirrors the parsing
    in ``_hook_parallel_enabled`` for cross-validator consistency.
    """
    val = os.environ.get("CPV_CACHE_PARALLEL")
    if val is None:
        return True
    return val.strip().lower() not in {"0", "false", "no", "off"}


def _build_cache_work_units(plugin_root: Path) -> list[_CacheWorkUnit]:
    """Enumerate every per-file cache scan as a uniform work unit.

    The unit order matches the legacy serial loop EXACTLY (CA-01 static
    prefix → hooks → agents → commands → skills) so the merged result
    list is bit-identical to the pre-task-384 output. Tests pin this
    invariant via parallel-vs-serial parity.
    """
    units: list[_CacheWorkUnit] = []
    plugin_root_str = str(plugin_root)

    # CA-01 — static prefix files (in iteration order)
    for f in _iter_static_prefix_files(plugin_root):
        units.append(
            _CacheWorkUnit(
                kind="static_prefix",
                file_path_str=str(f),
                plugin_root_str=plugin_root_str,
            )
        )

    # CA-02 / CA-03 / CA-05 / CA-06 — per-hook checks (one unit per script,
    # all four checks run together inside the worker)
    for event, script in _collect_hook_files(plugin_root):
        units.append(
            _CacheWorkUnit(
                kind="hook",
                file_path_str=str(script),
                plugin_root_str=plugin_root_str,
                event=event,
            )
        )

    # CA-04 — agents (no CA-07: an agent IS the forked subagent so the
    # `context:` field doesn't apply); CA-04+CA-07 — commands; CA-04+CA-07 —
    # skills. We walk in the same order the legacy loop did
    # (("agents", "agent"), ("commands", "command"), then skills) so the
    # merged finding sequence matches the serial baseline.
    for sub, kind in (("agents", "agent"), ("commands", "command")):
        comp_dir = plugin_root / sub
        if not comp_dir.is_dir():
            continue
        for md_file in comp_dir.rglob("*.md"):
            units.append(
                _CacheWorkUnit(
                    kind="component",
                    file_path_str=str(md_file),
                    plugin_root_str=plugin_root_str,
                    component_kind=kind,
                    include_context_fork=(sub == "commands"),
                )
            )

    skills_dir = plugin_root / "skills"
    if skills_dir.is_dir():
        for skill_md in skills_dir.rglob("SKILL.md"):
            units.append(
                _CacheWorkUnit(
                    kind="component",
                    file_path_str=str(skill_md),
                    plugin_root_str=plugin_root_str,
                    component_kind="skill",
                    include_context_fork=True,
                )
            )

    return units


def scan_plugin_for_cache(plugin_root: Path) -> ValidationReport:
    """Run all CA-01 .. CA-06 checks against a plugin tree.

    Two execution paths:
      * Serial path (default when ``CPV_CACHE_PARALLEL=0`` is set) —
        runs the legacy per-file loop bit-identically to the pre-task-384
        behavior. Each scanner mutates the shared report directly.
      * Parallel path (default) — enumerates every per-file scan as a
        uniform ``_CacheWorkUnit``, dispatches them across a
        ``ProcessPoolExecutor`` via the shared ``parallel_scan`` harness,
        then merges per-file findings back into the master report in
        input order. Bit-identical findings, same exit code, same
        per-file order. Pin the parity invariant via
        ``tests/test_validate_cache_parallelism.py``.

    Errors: per-file worker exceptions never crash the validator. They
    surface as one per-file WARNING via ``ScanResult.error``, consistent
    with the spec contract shared across A2 (security) / A6 (hook) / A8
    (cache).
    """
    report = ValidationReport()
    source_root = str(plugin_root)

    if not plugin_root.exists():
        report.critical(f"Plugin path does not exist: {source_root}", source_root)
        return report
    if not plugin_root.is_dir():
        report.critical(f"Plugin path is not a directory: {source_root}", source_root)
        return report
    if not (plugin_root / ".claude-plugin" / "plugin.json").is_file():
        report.critical(f"No .claude-plugin/plugin.json found at {source_root}", source_root)
        return report

    # Capture the result count BEFORE per-file scans so the post-scan
    # "no violations detected" PASSED line fires iff the parallel/serial
    # branch added zero findings — same trigger condition as the legacy
    # ``total == 0`` check.
    anchor_index = len(report.results)

    if not _cache_parallel_enabled():
        # Serial path — kept BIT-IDENTICAL to the pre-task-384 behavior.
        # Used by the parity regression test (which runs both paths and
        # asserts the ValidationResult sequences match exactly) and by
        # users debugging a parallel-path regression.
        # CA-01 — static prefix files
        for f in _iter_static_prefix_files(plugin_root):
            scan_static_prefix(f, report, plugin_root)

        # CA-02 / CA-03 / CA-05 / CA-06 — per-hook checks
        for event, script in _collect_hook_files(plugin_root):
            scan_hook_for_prefix_mutation(script, event, report, plugin_root)
            scan_hook_for_tool_mutation(script, event, report, plugin_root)
            scan_hook_for_unbounded_output(script, event, report, plugin_root)
            scan_hook_for_fork_unsafe(script, event, report, plugin_root)

        # CA-04 — `model:` frontmatter on ANY component (agents, commands,
        # skills). A pinned model forces an in-line switch that fragments
        # the prompt cache; `model: inherit` is exempt (handled inside the
        # scanner). CA-07 — `context: fork`/`branch` on skills + commands
        # (agents have no `context:` field — an agent IS the forked
        # subagent).
        for sub, kind in (("agents", "agent"), ("commands", "command")):
            comp_dir = plugin_root / sub
            if comp_dir.is_dir():
                for md_file in comp_dir.rglob("*.md"):
                    scan_component_for_model_override(md_file, report, plugin_root, kind)
                    if sub == "commands":
                        scan_component_for_context_fork(md_file, report, plugin_root, kind)
        skills_dir = plugin_root / "skills"
        if skills_dir.is_dir():
            for skill_md in skills_dir.rglob("SKILL.md"):
                scan_component_for_model_override(skill_md, report, plugin_root, "skill")
                scan_component_for_context_fork(skill_md, report, plugin_root, "skill")
    else:
        # Parallel path. Build one uniform work unit per per-file scan in
        # the same order the serial loop visits them — then dispatch via
        # the shared harness. ``parallel_scan`` preserves input order, so
        # the merge loop can iterate by index without sorting.
        units = _build_cache_work_units(plugin_root)
        if units:
            # ``parallel_scan`` signature is ``Sequence[Path]`` for the
            # first arg, but the harness just iterates and pickles the
            # items unchanged — it never instantiates ``Path`` on its
            # own. The frozen dataclass pickles cleanly, and the worker
            # reconstructs Paths from the string fields. Type-checkers
            # complain because the parameter is annotated ``Sequence[Path]``;
            # we silence with a per-call ignore rather than weakening the
            # harness's annotation (other validators DO pass Paths).
            scan_results = parallel_scan(units, scan_one_cache_unit)  # type: ignore[arg-type]
            for idx, sr in enumerate(scan_results):
                unit = units[idx]
                if sr.error is not None:
                    # Worker raised — spec mandates "surface as a per-file
                    # WARNING in the report (don't crash the whole
                    # validator)". Use the rel path so the message stays
                    # consistent with the legacy serial format.
                    file_path = Path(unit.file_path_str)
                    try:
                        rel = str(file_path.relative_to(plugin_root))
                    except ValueError:
                        rel = unit.file_path_str
                    report.warning(
                        f"Cache scan worker raised on this file: {sr.error}",
                        rel,
                    )
                    continue
                # ``findings`` is already a list[ValidationResult] — the
                # worker returns that shape directly. Append in place to
                # preserve input order at append-time.
                report.results.extend(sr.findings)

    # Same trigger as the legacy ``total == 0`` check: any per-file
    # WARNING — whether produced by a CA rule or by the worker-error
    # surfacing — counts as "violations detected", suppressing the PASSED
    # line. The serial path used a running counter; the parallel path
    # measures the result-list delta from the anchor we captured BEFORE
    # the scan.
    if len(report.results) == anchor_index:
        report.passed(
            "No prompt-cache violations detected across the 6 cache-audit rules.",
            source_root,
        )
    return report


# =============================================================================
# CLI + reporting
# =============================================================================


def print_results(report: ValidationReport, verbose: bool = False) -> None:
    """Human-readable summary reusing the shared ValidationReport printer."""
    print_results_by_level(report, verbose=verbose)


def print_json(report: ValidationReport) -> None:
    """Emit the full report as JSON."""
    print(json.dumps(report.to_dict(), indent=2))


def main() -> int:
    """Main entry point for ``cpv-validate-cache``."""
    check_remote_execution_guard()

    from cpv_validation_common import launcher_epilog

    parser = argparse.ArgumentParser(
        description="Validate prompt-cache discipline (CA-01..CA-06) for a Claude Code plugin",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Checks performed (every finding is a WARNING — non-blocking):
  CA-01 Static prompt prefix — no dynamic data in CLAUDE.md / agents / skills.
  CA-02 Hook scripts must not mutate cached-prefix files (CLAUDE.md, settings.json).
  CA-03 Hook scripts must not toggle tool allow/deny lists mid-session.
  CA-04 Components (agents/commands/skills) must not pin `model:` in frontmatter
        (forces an in-line model switch; `model: inherit` is exempt).
  CA-05 Hook scripts should not emit unbounded git/find/ls/cat output.
  CA-06 Compaction & subagent hooks must preserve the cached prefix.
  CA-07 Avoid `context: fork`/`branch` on skills/commands unless a fresh
        context (audit/error-checking) or many-file reads justify the cost.

Exit codes:
  0 - No blocking issues. All CA-01..CA-06 findings are WARNING, so a clean
      OR warning-only audit both exit 0.
  1 - CRITICAL — invocation error only (path missing / not a directory /
      no .claude-plugin/plugin.json). The CA-NN rules never raise CRITICAL.

"""
        + launcher_epilog("cache"),
    )
    parser.add_argument("target", help="Path to a plugin directory")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show PASSED/INFO results")
    parser.add_argument("--json", action="store_true", help="Emit results as JSON")
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Save detailed report to file, print only summary to stdout",
    )
    args = parser.parse_args()

    target = Path(args.target).resolve()
    if not target.exists():
        print(f"Error: {target} does not exist", file=sys.stderr)
        return EXIT_CRITICAL

    report = scan_plugin_for_cache(target)

    if args.json:
        print_json(report)
    elif args.report:
        save_report_and_print_summary(
            report,
            Path(args.report),
            "Cache Validation",
            print_results,
            args.verbose,
            plugin_path=str(target),
        )
    else:
        print_results(report, args.verbose)

    return report.exit_code if report.exit_code is not None else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
