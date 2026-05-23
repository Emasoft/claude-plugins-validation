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
import re
import sys
from pathlib import Path
from typing import Iterable

from cpv_management_common import load_jsonc
from cpv_validation_common import (
    EXIT_CRITICAL,
    EXIT_OK,
    ValidationReport,
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

# Shell write operators against a target path (>>, >, tee -a, sed -i)
_FILE_WRITE_OPS = re.compile(
    r"(?:"
    r"\btee(?:\s+-a|\s+--append)?\s+\S+"  # tee / tee -a FILE
    r"|>>\s*\S+"  # >> FILE
    r"|>\s*\S+"  # > FILE
    r"|\bsed\s+-i\s+\S+\s+\S+"  # sed -i ... FILE
    r"|\bcp\s+\S+\s+\S+"  # cp src dst
    r"|\bmv\s+\S+\s+\S+"  # mv src dst
    r"|\becho\s+\S+\s*>>?"  # echo X >> / >
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


def scan_static_prefix(file_path: Path, report: ValidationReport, plugin_root: Path) -> int:
    """Flag dynamic placeholders / shell substitutions in static-prefix files."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    rel = str(file_path.relative_to(plugin_root)) if file_path.is_relative_to(plugin_root) else str(file_path)

    # Strip option placeholders (CLAUDE_PLUGIN_OPTION_*) before any scan —
    # those resolve once per session install and are stable.
    sanitized = _STATIC_OPTION_PLACEHOLDER.sub("CLAUDE_PLUGIN_OPTION", content)
    fenced_stripped = _strip_fences_for_dynamic_check(sanitized)

    issues = 0
    for match in _DYNAMIC_PLACEHOLDER.finditer(fenced_stripped):
        report.warning(
            f"CA-01: dynamic placeholder {match.group(0)!r} in cached prefix file",
            rel,
        )
        issues += 1
    for match in _DYNAMIC_SHELL_CMD.finditer(fenced_stripped):
        report.warning(
            f"CA-01: shell command substitution {match.group(0)!r} in cached prefix file",
            rel,
        )
        issues += 1
    return issues


# =============================================================================
# CA-02 — Hook scripts that mutate the cached prefix
# =============================================================================


def scan_hook_for_prefix_mutation(
    script_path: Path,
    event: str,
    report: ValidationReport,
    plugin_root: Path,
) -> int:
    """Flag a hook script that writes to a cached-prefix file."""
    if event not in _PREFIX_AFFECTING_EVENTS:
        return 0
    try:
        content = script_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    rel = str(script_path.relative_to(plugin_root)) if script_path.is_relative_to(plugin_root) else str(script_path)

    issues = 0
    for line_num, line in enumerate(content.split("\n"), start=1):
        # Skip pure comments
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            continue
        if not _FILE_WRITE_OPS.search(line):
            continue
        for prefix_pat in _PREFIX_FILE_PATTERNS:
            if prefix_pat.search(line):
                report.warning(
                    f"CA-02: {event} hook writes to cached-prefix file",
                    rel,
                    line_num,
                )
                issues += 1
                break
    return issues


# =============================================================================
# CA-03 — Hook scripts that toggle the tool set
# =============================================================================


def scan_hook_for_tool_mutation(
    script_path: Path,
    event: str,
    report: ValidationReport,
    plugin_root: Path,
) -> int:
    """Flag hook scripts that flip allow/deny lists or enable MCP servers."""
    if event not in _PREFIX_AFFECTING_EVENTS:
        return 0
    try:
        content = script_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    rel = str(script_path.relative_to(plugin_root)) if script_path.is_relative_to(plugin_root) else str(script_path)

    issues = 0
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
        report.warning(
            f"CA-03: {event} hook mutates tool-list field",
            rel,
            line_num,
        )
        issues += 1
    return issues


# =============================================================================
# CA-04 — `model:` frontmatter on ANY component forces an in-line model switch
# =============================================================================


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
_MODEL_FIELD_RE = re.compile(r"^model:\s*(.+)$", re.MULTILINE)


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
    """
    try:
        content = md_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    fm = _FRONTMATTER_RE.match(content)
    if not fm:
        return 0
    front = fm.group(1)
    m = _MODEL_FIELD_RE.search(front)
    if not m:
        return 0
    model = m.group(1).strip().strip("'").strip('"')
    # `model: inherit` uses the parent/session model — no in-line switch, so
    # the cache is never split. Treat it exactly like omitting the field.
    if model.lower() == "inherit":
        return 0
    rel = str(md_file.relative_to(plugin_root)) if md_file.is_relative_to(plugin_root) else str(md_file)
    report.warning(
        f"CA-04: {component_kind} declares `model: {model}` in frontmatter — forces an in-line "
        f"model switch that fragments the prompt cache (each model keeps a separate cache, so this "
        f"{component_kind} pays a cold-cache miss on every dispatch instead of reusing the session's "
        f"warm prefix). Omit the `model:` field to inherit the session model and keep the cache warm; "
        f"use `model: inherit` if you must name it explicitly.",
        rel,
    )
    return 1


# =============================================================================
# CA-07 — `context: fork` / `context: branch` re-primes the cache from cold
# =============================================================================


_CONTEXT_FIELD_RE = re.compile(r"^context:\s*(.+)$", re.MULTILINE)


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
    """
    try:
        content = md_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    fm = _FRONTMATTER_RE.match(content)
    if not fm:
        return 0
    m = _CONTEXT_FIELD_RE.search(fm.group(1))
    if not m:
        return 0
    value = m.group(1).strip().strip("'").strip('"').lower()
    if value not in ("fork", "branch"):
        return 0
    rel = str(md_file.relative_to(plugin_root)) if md_file.is_relative_to(plugin_root) else str(md_file)
    report.warning(
        f"CA-07: {component_kind} declares `context: {value}` in frontmatter — forks a fresh "
        f"subagent whose prompt prefix is re-primed from cold (up to ~1M tokens when the harness "
        f"carries many skills/MCP/tools; only CLAUDE.md + rules files survive a fork). Keep the fork "
        f"ONLY if this {component_kind} needs a fresh context (independent audit / error-checking) or "
        f"the room to read many files; otherwise drop the `context:` field to inherit the parent "
        f"context and keep the cache warm.",
        rel,
    )
    return 1


# =============================================================================
# CA-05 — Hook scripts likely to emit unbounded output
# =============================================================================


def scan_hook_for_unbounded_output(
    script_path: Path,
    event: str,
    report: ValidationReport,
    plugin_root: Path,
) -> int:
    """Flag hook scripts that emit unbounded git/find/cat/ls output."""
    if event not in _PREFIX_AFFECTING_EVENTS:
        return 0
    try:
        content = script_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    rel = str(script_path.relative_to(plugin_root)) if script_path.is_relative_to(plugin_root) else str(script_path)

    issues = 0
    for line_num, line in enumerate(content.split("\n"), start=1):
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            continue
        for unbounded_pat, label, guard_pat in _UNBOUNDED_PATTERNS:
            if unbounded_pat.search(line) and not guard_pat.search(line):
                report.warning(
                    f"CA-05: {event} hook may emit unbounded output: {label}",
                    rel,
                    line_num,
                )
                issues += 1
                break  # one finding per line is enough
    return issues


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


def scan_hook_for_fork_unsafe(
    script_path: Path,
    event: str,
    report: ValidationReport,
    plugin_root: Path,
) -> int:
    """Flag fork-affecting hooks that overwrite the cached prefix.

    Conservative: only emits a WARNING for now since most plugins do not
    ship compaction logic and a definitive answer requires runtime inspection.
    """
    if event not in _FORK_AFFECTING_EVENTS:
        return 0
    try:
        content = script_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return 0
    rel = str(script_path.relative_to(plugin_root)) if script_path.is_relative_to(plugin_root) else str(script_path)

    if any(p.search(content) for p in _PREFIX_FILE_PATTERNS) and _FILE_WRITE_OPS.search(content):
        report.warning(
            f"CA-06: {event} hook touches cached-prefix files — verify the parent prefix is preserved across the fork",
            rel,
        )
        return 1
    return 0


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


def scan_plugin_for_cache(plugin_root: Path) -> ValidationReport:
    """Run all CA-01 .. CA-06 checks against a plugin tree."""
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

    total = 0
    # CA-01 — static prefix files
    for f in _iter_static_prefix_files(plugin_root):
        total += scan_static_prefix(f, report, plugin_root)

    # CA-02 / CA-03 / CA-05 / CA-06 — per-hook checks
    hook_files = _collect_hook_files(plugin_root)
    for event, script in hook_files:
        total += scan_hook_for_prefix_mutation(script, event, report, plugin_root)
        total += scan_hook_for_tool_mutation(script, event, report, plugin_root)
        total += scan_hook_for_unbounded_output(script, event, report, plugin_root)
        total += scan_hook_for_fork_unsafe(script, event, report, plugin_root)

    # CA-04 — `model:` frontmatter on ANY component (agents, commands, skills).
    # A pinned model forces an in-line switch that fragments the prompt cache;
    # `model: inherit` is exempt (handled inside the scanner).
    # CA-07 — `context: fork`/`branch` on skills + commands (agents have no
    # `context:` field — an agent IS the forked subagent).
    for sub, kind in (("agents", "agent"), ("commands", "command")):
        comp_dir = plugin_root / sub
        if comp_dir.is_dir():
            for md_file in comp_dir.rglob("*.md"):
                total += scan_component_for_model_override(md_file, report, plugin_root, kind)
                if sub == "commands":
                    total += scan_component_for_context_fork(md_file, report, plugin_root, kind)
    skills_dir = plugin_root / "skills"
    if skills_dir.is_dir():
        for skill_md in skills_dir.rglob("SKILL.md"):
            total += scan_component_for_model_override(skill_md, report, plugin_root, "skill")
            total += scan_component_for_context_fork(skill_md, report, plugin_root, "skill")

    if total == 0:
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
