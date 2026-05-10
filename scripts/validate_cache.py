#!/usr/bin/env python3
"""Claude Plugins Validation — prompt-cache audit (CA-01 .. CA-06).

Validates a plugin against Anthropic's 6 prompt-caching rules surfaced by
ussumant/cache-audit (https://github.com/ussumant/cache-audit). Plugins
that ship hooks/skills/agents can silently break the prompt cache for
every user that installs them — multiplying API costs by 5-10x and
adding latency on every turn. This validator catches the documented
breakage patterns before publication.

Reference: "Lessons from Building Claude Code: Prompt Caching Is
Everything" by Thariq Shihipar (Anthropic).

Usage::

    uv run python scripts/validate_cache.py path/to/plugin/
    uv run python scripts/validate_cache.py path/to/plugin/ --report /tmp/c.md

Exit codes (standard CPV severity-coded):

    0 - No blocking issues
    1 - CRITICAL  (CA layer never raises CRITICAL — reserved for SECURITY)
    2 - MAJOR     (CA-01 / CA-02 / CA-03 — cache-prefix invalidation)
    3 - MINOR     (CA-04 / CA-05 — cost/latency impact)
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
        report.major(
            f"CA-01: dynamic placeholder {match.group(0)!r} in cached prefix file",
            rel,
        )
        issues += 1
    for match in _DYNAMIC_SHELL_CMD.finditer(fenced_stripped):
        report.major(
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
                report.major(
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
        report.major(
            f"CA-03: {event} hook mutates tool-list field",
            rel,
            line_num,
        )
        issues += 1
    return issues


# =============================================================================
# CA-04 — Skill `model:` field forces an in-line model switch
# =============================================================================


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
_MODEL_FIELD_RE = re.compile(r"^model:\s*(.+)$", re.MULTILINE)


def scan_skill_for_model_override(skill_md: Path, report: ValidationReport, plugin_root: Path) -> int:
    """Flag SKILL.md files whose frontmatter declares an in-line `model:` switch."""
    try:
        content = skill_md.read_text(encoding="utf-8", errors="ignore")
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
    rel = str(skill_md.relative_to(plugin_root)) if skill_md.is_relative_to(plugin_root) else str(skill_md)
    report.minor(
        f"CA-04: skill declares `model: {model}` — forces in-line model switch (use an agent instead)",
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
                report.minor(
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

    # CA-04 — skills with `model:` frontmatter
    skills_dir = plugin_root / "skills"
    if skills_dir.is_dir():
        for skill_md in skills_dir.rglob("SKILL.md"):
            total += scan_skill_for_model_override(skill_md, report, plugin_root)

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
Checks performed:
  CA-01 Static prompt prefix — no dynamic data in CLAUDE.md / agents / skills.
  CA-02 Hook scripts must not mutate cached-prefix files (CLAUDE.md, settings.json).
  CA-03 Hook scripts must not toggle tool allow/deny lists mid-session.
  CA-04 Skills must not declare `model:` (forces in-line model switch).
  CA-05 Hook scripts should not emit unbounded git/find/ls/cat output.
  CA-06 Compaction & subagent hooks must preserve the cached prefix.

Exit codes:
  0 - No blocking issues
  2 - MAJOR issues (CA-01 / CA-02 / CA-03)
  3 - MINOR issues (CA-04 / CA-05)

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
