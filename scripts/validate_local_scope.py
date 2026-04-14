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
  ``CLAUDE.local.md`` — MINOR level (this validator cares about them).
- An untracked ``.claude/settings.json`` is validated with the strict
  **project-scope rules** (not local rules), because it is almost always
  a WIP shared config that will be committed soon.
- An untracked ``.mcp.json`` is flagged as WARNING per TRDD 5.6.

This validator is a **single-shot, single-threaded** offline tool. Like
``validate_project_scope``, its ``exists()`` → ``read_text()`` sequences
are a benign TOCTOU window: an attacker who can swap files mid-run
already controls the validation outcome by virtue of owning the project
tree. Do not call these helpers from a background worker or a
long-running service without first adding a locking layer (aegis INFO-1).

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
    MAX_FILES_PER_FOLDER,
    MAX_HOME_CLAUDE_JSON_BYTES,
    MAX_MARKDOWN_BYTES,
    MAX_SETTINGS_JSON_BYTES,
    OversizedFileError,
    classify_file_scope,
    classify_folder_scope,
    find_git_root,
    gitignore_covers_path,
    is_git_tracked,
    list_tracked_files_under,
    redact_home_path,
    resolve_within,
    safe_load_jsonc,
    safe_parse_frontmatter,
    safe_read_text,
)
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
# Shared IO helpers (sanitised error reporting)
# =============================================================================


def _report_parse_error(
    report: ValidationReport, file_label: str, exc: BaseException
) -> None:
    """Record a CRITICAL parse-error finding without leaking file contents.

    Uses ``type(exc).__name__`` only — never ``str(exc)`` — per aegis
    MEDIUM-4.
    """
    report.critical(f"{file_label}: parse error ({type(exc).__name__})", file_label)


def _load_json_or_report(
    path: Path,
    max_bytes: int,
    report: ValidationReport,
    file_label: str,
) -> object | None:
    """Load a JSONC file with size cap + sanitised error reporting."""
    try:
        return safe_load_jsonc(path, max_bytes)
    except OversizedFileError:
        report.major(
            f"{file_label}: exceeds {max_bytes} byte size cap — skipping",
            file_label,
        )
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        _report_parse_error(report, file_label, exc)
        return None


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
    data = _load_json_or_report(settings_path, MAX_SETTINGS_JSON_BYTES, report, file_label)
    if data is None:
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
    """Light-touch validation: YAML frontmatter parseable, name present.

    Intentionally does NOT check for absolute home paths — local scope is
    personal config. Frontmatter parsing uses ``safe_parse_frontmatter``
    which bounds size and alias count.
    """
    try:
        content = safe_read_text(path, MAX_MARKDOWN_BYTES)
    except OversizedFileError:
        report.major(
            f"{rel_label}: exceeds {MAX_MARKDOWN_BYTES} byte size cap — skipping",
            rel_label,
        )
        return
    except (OSError, UnicodeDecodeError) as exc:
        report.critical(f"{rel_label}: read failed ({type(exc).__name__})", rel_label)
        return
    if not content.startswith("---"):
        report.minor(f"{rel_label}: missing YAML frontmatter", rel_label)
        return
    fm, _body = safe_parse_frontmatter(content)
    if fm is None:
        report.minor(
            f"{rel_label}: missing, oversized, or malformed YAML frontmatter",
            rel_label,
        )
        return
    name = fm.get("name")
    if not isinstance(name, str) or not name.strip():
        report.nit(f"{rel_label}: frontmatter 'name' missing or empty", rel_label)


def _walk_local_markdown_folder(
    folder: Path,
    repo_root: Path,
    project_root: Path,
    report: ValidationReport,
    glob: str,
) -> None:
    """Validate every .md file matching glob inside a local-scope folder.

    Uses ``list_tracked_files_under`` once to get the tracked set, then
    filters via set membership instead of running ``is_git_tracked`` per
    file (aegis LOW-3). Symlink escapes are rejected via
    ``resolve_within`` (aegis MEDIUM-1). Walk is capped at
    ``MAX_FILES_PER_FOLDER``.
    """
    tracked = list_tracked_files_under(folder, repo_root)
    if tracked is None:
        tracked = set()
    count = 0
    for md in sorted(folder.rglob(glob)):
        count += 1
        if count > MAX_FILES_PER_FOLDER:
            report.warning(
                f"{folder.relative_to(project_root)}: stopped walking at "
                f"{MAX_FILES_PER_FOLDER} files — truncating validation",
                str(folder.relative_to(project_root)),
            )
            return
        real = resolve_within(md, project_root)
        if real is None:
            report.major(
                f"{md.relative_to(project_root)}: path resolves outside the "
                "project root (symlink escape) — skipping",
                str(md.relative_to(project_root)),
            )
            continue
        if real in tracked:
            continue  # tracked files are project-scope's concern
        rel = md.relative_to(project_root)
        _validate_markdown_frontmatter_only(md, report, str(rel))


def validate_local_agents(
    agents_dir: Path, repo_root: Path, project_root: Path, report: ValidationReport
) -> None:
    """Validate untracked agent .md files."""
    _walk_local_markdown_folder(agents_dir, repo_root, project_root, report, "*.md")


def validate_local_skills(
    skills_dir: Path, repo_root: Path, project_root: Path, report: ValidationReport
) -> None:
    """Validate untracked SKILL.md files."""
    _walk_local_markdown_folder(skills_dir, repo_root, project_root, report, "SKILL.md")


def validate_local_commands(
    commands_dir: Path, repo_root: Path, project_root: Path, report: ValidationReport
) -> None:
    """Validate untracked command .md files."""
    _walk_local_markdown_folder(commands_dir, repo_root, project_root, report, "*.md")


def validate_local_output_styles(
    styles_dir: Path, repo_root: Path, project_root: Path, report: ValidationReport
) -> None:
    """Validate untracked output-styles/*.md files."""
    _walk_local_markdown_folder(styles_dir, repo_root, project_root, report, "*.md")


def validate_local_rules(
    rules_dir: Path, repo_root: Path, project_root: Path, report: ValidationReport
) -> None:
    """Surface untracked rule .md files as INFO (relaxed rules)."""
    tracked = list_tracked_files_under(rules_dir, repo_root) or set()
    count = 0
    for md in sorted(rules_dir.rglob("*.md")):
        count += 1
        if count > MAX_FILES_PER_FOLDER:
            report.warning(
                f".claude/rules: stopped walking at {MAX_FILES_PER_FOLDER} files",
                ".claude/rules",
            )
            return
        real = resolve_within(md, project_root)
        if real is None:
            report.major(
                f"{md.relative_to(project_root)}: symlink escape — skipping",
                str(md.relative_to(project_root)),
            )
            continue
        if real in tracked:
            continue
        rel = md.relative_to(project_root)
        try:
            safe_read_text(md, MAX_MARKDOWN_BYTES)
        except OversizedFileError:
            report.major(
                f"{rel}: exceeds {MAX_MARKDOWN_BYTES} byte size cap — skipping",
                str(rel),
            )
            continue
        except (OSError, UnicodeDecodeError) as exc:
            report.critical(f"{rel}: read failed ({type(exc).__name__})", str(rel))
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
        safe_read_text(md_path, MAX_MARKDOWN_BYTES)
    except OversizedFileError:
        report.major(
            f"{rel}: exceeds {MAX_MARKDOWN_BYTES} byte size cap — skipping",
            str(rel),
        )
        return
    except (OSError, UnicodeDecodeError) as exc:
        report.critical(f"{rel}: read failed ({type(exc).__name__})", str(rel))
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

    The file read is size-capped (aegis MEDIUM-5) and any reported
    project path is run through ``redact_home_path`` before it lands in
    a finding message (aegis INFO-2).
    """
    home_claude_json = Path.home() / ".claude.json"
    if not home_claude_json.exists():
        report.info(
            "~/.claude.json not found — no per-project local MCP state registered.",
            "~/.claude.json",
        )
        return
    try:
        data = safe_load_jsonc(home_claude_json, MAX_HOME_CLAUDE_JSON_BYTES)
    except OversizedFileError:
        report.warning(
            f"~/.claude.json exceeds {MAX_HOME_CLAUDE_JSON_BYTES} byte cap — "
            "skipping per-project MCP check.",
            "~/.claude.json",
        )
        return
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        report.warning(
            f"~/.claude.json: cannot parse ({type(exc).__name__}) — "
            "skipping per-project MCP check.",
            "~/.claude.json",
        )
        return
    if not isinstance(data, dict):
        return
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return
    key = str(project_root.resolve())
    key_display = redact_home_path(key)
    entry = projects.get(key)
    if not isinstance(entry, dict):
        report.info(
            f"~/.claude.json has no entry for this project ({key_display}).",
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
# .gitignore hygiene — delegated to git check-ignore
# =============================================================================


def validate_gitignore_for_local_files(
    repo_root: Path, report: ValidationReport
) -> None:
    """Check that common local-scope files are gitignored.

    Uses ``git check-ignore`` (via ``gitignore_covers_path``) which
    correctly handles every gitignore pattern syntax — including
    ``.claude/``, ``**/*.local.json``, ``/CLAUDE.local.md``, and so on.
    Works on paths that don't exist on disk (check-ignore matches
    patterns, not files).
    """
    gitignore = repo_root / ".gitignore"
    if not gitignore.exists():
        report.info(
            "No .gitignore at repo root — Claude Code will auto-add one on first "
            "'settings.local.json' creation, but consider creating it now.",
            ".gitignore",
        )
        return
    if not gitignore_covers_path(".claude/settings.local.json", repo_root):
        report.minor(
            ".gitignore does not cover '.claude/settings.local.json' — add "
            "'.claude/settings.local.json' (or '.claude/') to prevent accidental "
            "commits of personal settings.",
            ".gitignore",
        )
    if not gitignore_covers_path("CLAUDE.local.md", repo_root):
        report.minor(
            ".gitignore does not cover 'CLAUDE.local.md' — add it to prevent "
            "accidental commits of personal memory notes.",
            ".gitignore",
        )


# =============================================================================
# Orchestrator
# =============================================================================


def _validate_wip_shared_settings(
    settings_path: Path, report: ValidationReport
) -> None:
    """Apply strict project-scope rules to an UNTRACKED settings.json.

    TRDD section 5 + llm-ext correctness-followup finding #1: a developer
    may have authored a new ``.claude/settings.json`` that is not yet
    committed. This file is on its way to being shared with the team, so
    we should enforce the *project-scope* rules right now (secrets,
    absolute paths, rejected keys) rather than the relaxed local-scope
    rules — those would mask issues the developer wants to know about
    before pushing.

    Delegates to ``validate_project_scope.validate_settings_json_project_scope``
    via a deferred import to avoid a module-level circular dependency
    between the two orchestrators.
    """
    from validate_project_scope import validate_settings_json_project_scope

    validate_settings_json_project_scope(settings_path, report)


def validate_local_scope(project_root: Path, report: ValidationReport) -> None:
    """Walk the project tree and validate every non-git-tracked Claude element.

    Behaviour:

    - If the project has no ``.git`` ancestor, every file under
      ``.claude/`` is treated as local scope (there is no "tracked").
    - Folders that are project-scope (all files tracked) are skipped —
      they are covered by validate_project_scope.
    - An untracked ``settings.json`` is validated with strict rules so
      a WIP shared config is still scrubbed for secrets/absolute paths.
    """
    if not project_root.exists() or not project_root.is_dir():
        report.critical(
            f"Project path does not exist or is not a directory: {project_root}",
            str(project_root),
        )
        return

    # Bound the git-root search to project_root so symlinked parents
    # cannot expose unrelated repos (aegis MEDIUM-6).
    repo_root = find_git_root(project_root, boundary=project_root) or project_root
    no_git = not (repo_root / ".git").exists()
    if no_git:
        report.info(
            "Not a git repository — every file under .claude/ is treated as "
            "local scope.",
            str(project_root),
        )

    claude_dir = project_root / ".claude"
    git_repo = None if no_git else repo_root

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

    # 2. Untracked settings.json (WIP shared config) — use STRICT project rules.
    settings = claude_dir / "settings.json"
    if settings.exists() and classify_file_scope(settings, git_repo) in ("local", "no-git"):
        report.warning(
            ".claude/settings.json exists but is not git-tracked. Validating "
            "with strict project-scope rules since this file is usually shared "
            "with the team once committed.",
            ".claude/settings.json",
        )
        _validate_wip_shared_settings(settings, report)

    # 3. Untracked .mcp.json — WARNING per TRDD 5.6
    mcp_path = project_root / ".mcp.json"
    if mcp_path.exists() and classify_file_scope(mcp_path, git_repo) in ("local", "no-git"):
        report.warning(
            ".mcp.json exists but is not git-tracked — per mcp.md, .mcp.json "
            "is meant to be committed so the whole team gets the same MCP "
            "servers. Is this intentional?",
            ".mcp.json",
        )

    # 4-8. Walk each .claude/<element>/ folder if it's local-scope.
    for subfolder, validator in (
        ("agents", validate_local_agents),
        ("skills", validate_local_skills),
        ("commands", validate_local_commands),
        ("rules", validate_local_rules),
        ("output-styles", validate_local_output_styles),
    ):
        folder = claude_dir / subfolder
        if classify_folder_scope(folder, git_repo) in ("local", "no-git"):
            if folder.exists():
                validator(folder, repo_root, project_root, report)

    # 9. CLAUDE.local.md at project root
    claude_local_md = project_root / "CLAUDE.local.md"
    if claude_local_md.exists():
        validate_claude_local_md(claude_local_md, repo_root, report)

    # 10. ~/.claude.json per-project MCP state
    validate_home_claude_json_for_project(project_root, report)

    # 11. .gitignore hygiene
    if not no_git:
        validate_gitignore_for_local_files(repo_root, report)

    # 12. Distinct empty-folder INFO vs "no config found"
    if not report.results:
        if claude_dir.exists() and not any(claude_dir.iterdir()):
            report.info(
                ".claude/ directory exists but is empty — no local configuration "
                "to validate.",
                str(project_root),
            )
        else:
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
