#!/usr/bin/env python3
"""Validate non-git-tracked (local-scope) Claude Code configuration.

Per TRDD-2be75e88 section 5, this validator walks
``<project_path>/.claude/`` and its companion files and validates the
**personal, non-shared** Claude Code configuration for a single project:
``.claude/settings.local.json``, ``CLAUDE.local.md``, and any folder or
file under ``.claude/`` that is NOT git-tracked.

A "local scope" file is a file that:

- Exists under the project path
- Is either in ``.gitignore`` OR has never been added to git (untracked)

This is the companion to ``validate_project_scope.py``. Elements that the
project-scope validator covers (because they are committed) are skipped
here.

The rules at local scope are **deliberately relaxed** compared to project
scope:

- **Absolute user paths are OK.** Only the local developer reads this
  config, so ``/Users/alice/...`` is portable-enough.
- **Secrets in env are OK** but still discouraged — prefer `.env` files.
- **Managed-only and global-config keys are still rejected** — those keys
  never work in a regular settings file, regardless of scope.
- **Files must actually be gitignored.** If a file named ``settings.local.json``
  is committed to git, that is a MAJOR finding: it leaks personal config
  into shared history.

Additional local-scope checks:

- ``~/.claude.json`` may contain per-project MCP state under
  ``projects[<abs_path>].mcpServers``. Reported as INFO when present.
- ``.gitignore`` missing entries for ``settings.local.json`` and
  ``CLAUDE.local.md`` — INFO level.

Exit codes follow the CPV convention:

- 0: no blocking issues
- 1: CRITICAL
- 2: MAJOR
- 3: MINOR
- 4: NIT (only in --strict mode)

Usage::

    uv run python scripts/validate_local_scope.py <project_path> [options]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cc_scope_rules import (
    GLOBAL_CONFIG_KEYS,
    MANAGED_ONLY_KEYS,
    classify_file_scope,
    classify_folder_scope,
    find_git_root,
    is_git_tracked,
)
from cpv_management_common import load_jsonc
from cpv_validation_common import (
    ValidationReport,
    check_remote_execution_guard,
    print_results_by_level,
    save_report_and_print_summary,
)

# Keys that are considered typically SHARED — if they show up in
# settings.local.json, emit a MINOR suggesting they move to project scope.
_TYPICALLY_SHARED_KEYS: frozenset[str] = frozenset(
    {
        "extraKnownMarketplaces",
        "enableAllProjectMcpServers",
        "enabledMcpjsonServers",
        "disabledMcpjsonServers",
    }
)


# =============================================================================
# settings.local.json — local-scope rules
# =============================================================================


def _flag_managed_only_keys_local(
    data: dict[str, Any], report: ValidationReport, file_label: str
) -> None:
    """Managed-only keys are wrong in local scope too (never read outside managed)."""
    for key in sorted(MANAGED_ONLY_KEYS):
        if key in data:
            report.major(
                (
                    f"{file_label} has managed-only key '{key}' — this is "
                    "silently ignored unless deployed via managed-settings.json "
                    "by an administrator. Remove it."
                ),
                file_label,
            )


def _flag_global_config_keys_local(
    data: dict[str, Any], report: ValidationReport, file_label: str
) -> None:
    """Global-config keys belong in ~/.claude.json, not in any settings.json."""
    for key in sorted(GLOBAL_CONFIG_KEYS):
        if key in data:
            report.major(
                (
                    f"{file_label} has global-config-only key '{key}' — this key "
                    "lives in ~/.claude.json only and triggers a schema error."
                ),
                file_label,
            )


def _suggest_typically_shared_keys(
    data: dict[str, Any], report: ValidationReport, file_label: str
) -> None:
    """Hint that some keys should probably live in shared project settings."""
    for key in sorted(_TYPICALLY_SHARED_KEYS):
        if key in data:
            report.minor(
                (
                    f"{file_label} has '{key}' — this is typically shared with "
                    "the whole team. Consider moving it to .claude/settings.json "
                    "so everyone gets the same behaviour."
                ),
                file_label,
            )


def _flag_deprecated_keys(
    data: dict[str, Any], report: ValidationReport, file_label: str
) -> None:
    """Flag deprecated keys as NIT."""
    if "includeCoAuthoredBy" in data:
        report.nit(
            (
                f"{file_label}: 'includeCoAuthoredBy' is deprecated — use "
                "'attribution.commit' / 'attribution.pr' instead."
            ),
            file_label,
        )


def _flag_missing_schema_local(
    data: dict[str, Any], report: ValidationReport, file_label: str
) -> None:
    """NIT: settings.local.json should declare $schema too."""
    if "$schema" not in data:
        report.nit(
            (
                f"{file_label} is missing $schema — consider adding "
                '"$schema": "https://json.schemastore.org/claude-code-settings.json" '
                "for editor autocomplete."
            ),
            file_label,
        )


def validate_settings_local_json(
    settings_path: Path, report: ValidationReport
) -> None:
    """Apply local-scope rules to ``.claude/settings.local.json`` contents."""
    file_label = ".claude/settings.local.json"
    try:
        data = load_jsonc(settings_path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        report.critical(f"{file_label}: parse error: {exc}", file_label)
        return
    if not isinstance(data, dict):
        report.critical(f"{file_label}: root must be a JSON object", file_label)
        return

    _flag_managed_only_keys_local(data, report, file_label)
    _flag_global_config_keys_local(data, report, file_label)
    _suggest_typically_shared_keys(data, report, file_label)
    _flag_deprecated_keys(data, report, file_label)
    _flag_missing_schema_local(data, report, file_label)

    if not report.has_critical and not report.has_major and not report.has_minor:
        report.passed(f"{file_label} local-scope rules OK", file_label)


# =============================================================================
# Markdown elements under .claude/ — relaxed validation
# =============================================================================


def _validate_markdown_frontmatter_only(
    path: Path, report: ValidationReport, rel_label: str
) -> None:
    """Light-touch validation: YAML frontmatter parseable, name/description present.

    Intentionally does NOT check for absolute home paths — local scope is
    personal config.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.critical(f"{rel_label}: cannot read: {exc}", rel_label)
        return
    if not content.startswith("---"):
        report.minor(f"{rel_label}: missing YAML frontmatter", rel_label)
        return
    lines = content.splitlines()
    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        report.minor(f"{rel_label}: unterminated YAML frontmatter", rel_label)
        return
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        # PyYAML missing — can only check structure
        return
    try:
        fm = yaml.safe_load("\n".join(lines[1:end_idx]))
    except yaml.YAMLError as exc:
        report.minor(f"{rel_label}: YAML parse error: {exc}", rel_label)
        return
    if not isinstance(fm, dict):
        report.minor(f"{rel_label}: frontmatter is not a mapping", rel_label)
        return
    name = fm.get("name")
    if not isinstance(name, str) or not name.strip():
        report.nit(f"{rel_label}: frontmatter 'name' missing or empty", rel_label)


def _walk_local_markdown_folder(
    folder: Path, repo_root: Path, report: ValidationReport, glob: str
) -> None:
    """Validate every .md file matching glob inside a local-scope folder."""
    for md in sorted(folder.rglob(glob)):
        if is_git_tracked(md, repo_root):
            continue
        rel = md.relative_to(repo_root)
        _validate_markdown_frontmatter_only(md, report, str(rel))


def validate_local_agents(
    agents_dir: Path, repo_root: Path, report: ValidationReport
) -> None:
    """Validate untracked agent .md files."""
    _walk_local_markdown_folder(agents_dir, repo_root, report, "*.md")


def validate_local_skills(
    skills_dir: Path, repo_root: Path, report: ValidationReport
) -> None:
    """Validate untracked SKILL.md files."""
    _walk_local_markdown_folder(skills_dir, repo_root, report, "SKILL.md")


def validate_local_commands(
    commands_dir: Path, repo_root: Path, report: ValidationReport
) -> None:
    """Validate untracked command .md files."""
    _walk_local_markdown_folder(commands_dir, repo_root, report, "*.md")


def validate_local_rules(
    rules_dir: Path, repo_root: Path, report: ValidationReport
) -> None:
    """Validate untracked rule .md files (frontmatter optional)."""
    for md in sorted(rules_dir.rglob("*.md")):
        if is_git_tracked(md, repo_root):
            continue
        rel = md.relative_to(repo_root)
        try:
            md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            report.critical(f"{rel}: cannot read: {exc}", str(rel))
            continue
        report.info(f"{rel}: local-scope rule file", str(rel))


# =============================================================================
# CLAUDE.local.md
# =============================================================================


def validate_claude_local_md(
    md_path: Path, repo_root: Path, report: ValidationReport
) -> None:
    """Validate ``CLAUDE.local.md`` — must be gitignored, structurally valid."""
    rel = md_path.relative_to(repo_root)
    try:
        md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.critical(f"{rel}: cannot read: {exc}", str(rel))
        return
    # If the file is tracked, that's a scope violation
    if is_git_tracked(md_path, repo_root):
        report.major(
            f"{rel}: CLAUDE.local.md is git-tracked — it should be gitignored "
            "per memory.md ('private per-project preferences that shouldn't "
            "be checked into version control').",
            str(rel),
        )
        return
    report.passed(f"{rel}: CLAUDE.local.md present and not tracked", str(rel))


# =============================================================================
# ~/.claude.json per-project MCP state
# =============================================================================


def validate_home_claude_json_for_project(
    project_root: Path, report: ValidationReport
) -> None:
    """Look up per-project state in ~/.claude.json.

    Reports any ``projects[<abs_path>].mcpServers`` entries as INFO. This
    is user-managed state and cannot really be "wrong" — we just surface
    what Claude Code itself has stored for this project on this machine.
    """
    home_claude_json = Path.home() / ".claude.json"
    if not home_claude_json.exists():
        report.info(
            "~/.claude.json not found — no per-project local MCP state registered.",
            "~/.claude.json",
        )
        return
    try:
        data = load_jsonc(home_claude_json)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        report.warning(
            f"~/.claude.json: cannot parse ({exc}) — skipping per-project MCP check.",
            "~/.claude.json",
        )
        return
    if not isinstance(data, dict):
        return
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return
    key = str(project_root.resolve())
    entry = projects.get(key)
    if not isinstance(entry, dict):
        report.info(
            f"~/.claude.json has no entry for this project ({key}).",
            "~/.claude.json",
        )
        return
    mcp_servers = entry.get("mcpServers")
    if isinstance(mcp_servers, dict) and mcp_servers:
        names = ", ".join(sorted(mcp_servers.keys()))
        report.info(
            f"~/.claude.json has {len(mcp_servers)} local MCP server(s) for this "
            f"project: {names}",
            "~/.claude.json",
        )
    else:
        report.info(
            "~/.claude.json has an entry for this project but no local MCP servers.",
            "~/.claude.json",
        )


# =============================================================================
# .gitignore hygiene (stricter in local scope — it's the topic of the validator)
# =============================================================================


def _gitignore_covers_settings_local(lines: set[str]) -> bool:
    """Return True when any .gitignore line covers .claude/settings.local.json."""
    return (
        ".claude/" in lines
        or ".claude" in lines
        or ".claude/*" in lines
        or ".claude/**" in lines
        or ".claude/settings.local.json" in lines
        or "settings.local.json" in lines
    )


def _gitignore_covers_claude_local_md(lines: set[str]) -> bool:
    """Return True when any .gitignore line covers CLAUDE.local.md."""
    return (
        "CLAUDE.local.md" in lines
        or "/CLAUDE.local.md" in lines
        or "*.local.md" in lines
    )


def validate_gitignore_for_local_files(
    repo_root: Path, report: ValidationReport
) -> None:
    """Check that common local-scope files are gitignored."""
    gitignore = repo_root / ".gitignore"
    if not gitignore.exists():
        report.info(
            "No .gitignore at repo root — Claude Code will auto-add one on first "
            "'settings.local.json' creation, but consider creating it now.",
            ".gitignore",
        )
        return
    try:
        content = gitignore.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    lines = {ln.strip() for ln in content.splitlines() if ln.strip() and not ln.strip().startswith("#")}
    if not _gitignore_covers_settings_local(lines):
        report.minor(
            ".gitignore does not cover '.claude/settings.local.json' — add "
            "'.claude/settings.local.json' (or '.claude/') to prevent accidental "
            "commits of personal settings.",
            ".gitignore",
        )
    if not _gitignore_covers_claude_local_md(lines):
        report.minor(
            ".gitignore does not cover 'CLAUDE.local.md' — add it to prevent "
            "accidental commits of personal memory notes.",
            ".gitignore",
        )


# =============================================================================
# Orchestrator
# =============================================================================


def validate_local_scope(project_root: Path, report: ValidationReport) -> None:
    """Walk the project tree and validate every non-git-tracked Claude element.

    Behaviour:

    - If the project has no ``.git`` ancestor, every file under
      ``.claude/`` is treated as local scope (there is no "tracked").
    - Folders that are project-scope (all files tracked) are skipped —
      they are covered by validate_project_scope.
    """
    if not project_root.exists() or not project_root.is_dir():
        report.critical(
            f"Project path does not exist or is not a directory: {project_root}",
            str(project_root),
        )
        return

    repo_root = find_git_root(project_root) or project_root
    no_git = not (repo_root / ".git").exists()
    if no_git:
        report.info(
            "Not a git repository — every file under .claude/ is treated as "
            "local scope.",
            str(project_root),
        )

    claude_dir = project_root / ".claude"

    # 1. settings.local.json — always local-scope
    settings_local = claude_dir / "settings.local.json"
    if settings_local.exists():
        if not no_git and is_git_tracked(settings_local, repo_root):
            report.major(
                ".claude/settings.local.json is git-tracked — it must be "
                "gitignored per settings.md. Personal config should not be "
                "committed.",
                ".claude/settings.local.json",
            )
        else:
            validate_settings_local_json(settings_local, report)

    # 2. Untracked settings.json (rare — usually a WIP)
    settings = claude_dir / "settings.json"
    if settings.exists() and classify_file_scope(settings, None if no_git else repo_root) in ("local", "no-git"):
        report.warning(
            ".claude/settings.json exists but is not git-tracked. Usually "
            "settings.json is committed to share with the team.",
            ".claude/settings.json",
        )
        validate_settings_local_json(settings, report)  # re-use local rules

    # 3. .claude/agents/ (walk if folder is local-scope)
    agents_dir = claude_dir / "agents"
    if classify_folder_scope(agents_dir, None if no_git else repo_root) in ("local", "no-git"):
        if agents_dir.exists():
            validate_local_agents(agents_dir, repo_root, report)

    # 4. .claude/skills/
    skills_dir = claude_dir / "skills"
    if classify_folder_scope(skills_dir, None if no_git else repo_root) in ("local", "no-git"):
        if skills_dir.exists():
            validate_local_skills(skills_dir, repo_root, report)

    # 5. .claude/commands/
    commands_dir = claude_dir / "commands"
    if classify_folder_scope(commands_dir, None if no_git else repo_root) in ("local", "no-git"):
        if commands_dir.exists():
            validate_local_commands(commands_dir, repo_root, report)

    # 6. .claude/rules/
    rules_dir = claude_dir / "rules"
    if classify_folder_scope(rules_dir, None if no_git else repo_root) in ("local", "no-git"):
        if rules_dir.exists():
            validate_local_rules(rules_dir, repo_root, report)

    # 7. CLAUDE.local.md at project root
    claude_local_md = project_root / "CLAUDE.local.md"
    if claude_local_md.exists():
        validate_claude_local_md(claude_local_md, repo_root, report)

    # 8. ~/.claude.json per-project MCP state
    validate_home_claude_json_for_project(project_root, report)

    # 9. .gitignore hygiene
    if not no_git:
        validate_gitignore_for_local_files(repo_root, report)

    if not report.results:
        report.info(
            "No local-scope Claude Code configuration found under this path.",
            str(project_root),
        )


# =============================================================================
# CLI entry point
# =============================================================================


def main() -> int:
    """Command-line entry point for ``cpv-validate-local-scope``."""
    check_remote_execution_guard()

    parser = argparse.ArgumentParser(
        description=(
            "Validate non-git-tracked (local-scope) Claude Code configuration "
            "under <project_path>."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", help="Path to the project root directory to validate")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show INFO and PASSED results")
    parser.add_argument("--strict", action="store_true", help="NIT findings also block (exit 4)")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Save full report to a file; print only the compact summary to stdout.",
    )
    args = parser.parse_args()

    project_root = Path(args.path).resolve()
    report = ValidationReport()
    validate_local_scope(project_root, report)

    if args.json:
        payload = {
            "exit_code": report.exit_code,
            "counts": report.count_by_level(),
            "results": [
                {
                    "level": r.level,
                    "message": r.message,
                    "file": r.file,
                    "line": r.line,
                }
                for r in report.results
            ],
        }
        print(json.dumps(payload, indent=2))
    else:
        if args.report:
            save_report_and_print_summary(
                report,
                Path(args.report),
                "Claude Code Local-Scope Validation",
                print_results_by_level,
                args.verbose,
                plugin_path=str(project_root),
            )
        else:
            print_results_by_level(report, args.verbose)

    return report.exit_code_strict() if args.strict else report.exit_code


if __name__ == "__main__":
    sys.exit(main())
