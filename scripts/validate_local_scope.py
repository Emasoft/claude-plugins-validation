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
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cc_scope_rules import (
    ENABLED_PLUGIN_RE,
    GLOBAL_CONFIG_KEYS,
    MANAGED_ONLY_KEYS,
    MANAGED_ONLY_NESTED_KEYS,
    MAX_CLAUDE_MD_BYTES,
    MAX_FILES_PER_FOLDER,
    MAX_HOME_CLAUDE_JSON_BYTES,
    MAX_MARKDOWN_BYTES,
    MAX_SETTINGS_JSON_BYTES,
    PLUGIN_ONLY_KEYS,
    OversizedFileError,
    classify_file_scope,
    classify_folder_scope,
    find_git_root,
    gitignore_covers_path,
    is_git_tracked,
    list_tracked_files_under,
    redact_home_path,
    resolve_plugin_cache_dir,
    resolve_within,
    safe_load_jsonc,
    safe_parse_frontmatter,
    safe_read_text,
    validate_claude_md_imports,
)
from cpv_validation_common import (
    ValidationReport,
    check_remote_execution_guard,
    print_results_by_level,
    save_report_and_print_summary,
)

# TRDD-f4e2d385: Deep-validation imports. These are the same functions that
# cpv-validate-plugin / cpv-validate-skill / cpv-validate-hook etc. invoke, so
# a locally-installed agent/skill/command/rule/hook/mcp/lsp gets the SAME
# diagnostic coverage it would if it were part of a published plugin. The
# behavioral invariant: a finding that fires for `cpv-validate-skill` on a
# plugin's SKILL.md MUST also fire when that same SKILL.md is dropped into
# `.claude/skills/` and validated via `cpv-validate-local-scope`.
from validate_agent import validate_agent as _deep_validate_agent  # noqa: E402
from validate_command import validate_command as _deep_validate_command  # noqa: E402
from validate_hook import validate_hooks as _deep_validate_hooks  # noqa: E402
from validate_mcp import validate_mcp_config as _deep_validate_mcp  # noqa: E402
from validate_rules import validate_rules_directory as _deep_validate_rules  # noqa: E402
from validate_skill_comprehensive import validate_skill as _deep_validate_skill  # noqa: E402

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

# v2.22.0: `.claude/loop.md` is a plain-markdown file that replaces the built-in
# `/loop` maintenance prompt (scheduled-tasks.md). Content above 25,000 bytes
# is silently truncated by Claude Code, so we enforce the same cap with a MAJOR
# finding pointing at the doc rule.
MAX_LOOP_MD_BYTES: int = 25_000


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


def _flag_permissions_default_mode_local(
    data: dict[str, Any], report: ValidationReport, file_label: str
) -> None:
    """Validate ``permissions.defaultMode`` against the 6 permission-modes.md
    values. Same enforcement as project scope — an out-of-enum value is
    silently dropped so the author's intent never takes effect.
    """
    from cpv_validation_common import VALID_PERMISSION_MODES  # lazy to avoid cycle

    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        return
    default_mode = permissions.get("defaultMode")
    if default_mode is None:
        return
    if not isinstance(default_mode, str) or default_mode not in VALID_PERMISSION_MODES:
        report.major(
            (
                f"{file_label} sets 'permissions.defaultMode' to "
                f"{default_mode!r} — must be one of "
                f"{sorted(VALID_PERMISSION_MODES)}."
            ),
            file_label,
        )


def _flag_managed_only_nested_keys_local(
    data: dict[str, Any], report: ValidationReport, file_label: str
) -> None:
    """Nested admin kill-switches (``permissions.disableAutoMode`` etc.) are
    silently ignored outside managed settings. MAJOR so the user moves them to
    the correct scope.
    """
    for path_tuple in sorted(MANAGED_ONLY_NESTED_KEYS):
        cursor: Any = data
        for segment in path_tuple:
            if not isinstance(cursor, dict) or segment not in cursor:
                cursor = None
                break
            cursor = cursor[segment]
        if cursor is not None:
            dotted = ".".join(path_tuple)
            report.major(
                (
                    f"{file_label} sets '{dotted}' — this is a managed-only "
                    "admin kill-switch and has no effect in a regular settings "
                    "file. Deploy via managed-settings.json or server-managed "
                    "settings instead."
                ),
                file_label,
            )


def _flag_plugin_only_keys_local(
    data: dict[str, Any], report: ValidationReport, file_label: str
) -> None:
    """Plugin-only keys (``lspServers``, ``monitors``) never work in a settings
    file — Claude Code reads them only from a plugin package's ``plugin.json``.
    CRITICAL because the author's intent is silently dropped.
    """
    for key in sorted(PLUGIN_ONLY_KEYS):
        if key in data:
            report.critical(
                (
                    f"{file_label} has plugin-only key '{key}' — Claude Code reads "
                    "this ONLY from a plugin's plugin.json (top-level field). The "
                    f"'{key}' block in a settings file is silently dropped. Move "
                    "the declaration into the owning plugin's plugin.json, or "
                    "delete it."
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
) -> dict[str, Any] | None:
    """Apply local-scope rules to ``.claude/settings.local.json`` contents.

    Returns the parsed top-level dict (or ``None`` when parsing failed or the
    root is not an object) so the caller can reuse it for downstream subtree
    validation without re-reading the file. Reading once eliminates the
    TOCTOU window between the schema check here and the subtree walk below.
    """
    file_label = ".claude/settings.local.json"
    data = _load_json_or_report(settings_path, MAX_SETTINGS_JSON_BYTES, report, file_label)
    if data is None:
        return None
    if not isinstance(data, dict):
        report.critical(f"{file_label}: root must be a JSON object", file_label)
        return None

    _flag_managed_only_keys_local(data, report, file_label)
    _flag_managed_only_nested_keys_local(data, report, file_label)
    _flag_global_config_keys_local(data, report, file_label)
    _flag_plugin_only_keys_local(data, report, file_label)
    _flag_permissions_default_mode_local(data, report, file_label)
    _suggest_typically_shared_keys(data, report, file_label)
    _flag_deprecated_keys(data, report, file_label)
    _flag_missing_schema_local(data, report, file_label)

    if not report.has_critical and not report.has_major and not report.has_minor:
        report.passed(f"{file_label} local-scope rules OK", file_label)
    return data


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
    """Validate ``CLAUDE.local.md`` — must be gitignored, structurally valid.

    Besides the gitignored + size-cap checks, the body is scanned for
    ``@path/to/file.md`` import tokens (memory.md L95-107). The same
    classification applies as for project-scope CLAUDE.md:

    - CRITICAL: absolute path that resolves outside the repo root (e.g.
      absolute path that escapes the repo). Local scope relaxes
      home-path checks, NOT
      security leaks — a personal CLAUDE.local.md can still exfiltrate
      host files into Claude's context.
    - MAJOR: ``..`` escape, missing file, depth >5, or cycle.
    """
    rel = md_path.relative_to(repo_root)
    try:
        safe_read_text(md_path, MAX_CLAUDE_MD_BYTES)
    except OversizedFileError:
        report.major(
            f"{rel}: exceeds {MAX_CLAUDE_MD_BYTES} byte size cap — skipping",
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
    # @path import recursion check (memory.md L95-107).
    validate_claude_md_imports(
        md_path, repo_root, report, str(rel), max_bytes=MAX_CLAUDE_MD_BYTES
    )
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
# .claude/loop.md — v2.22.0
#
# Per scheduled-tasks.md, `.claude/loop.md` replaces the built-in `/loop`
# maintenance prompt. Project-level takes precedence over user-level
# (`~/.claude/loop.md`). The file is a plain-markdown maintenance instruction
# bounded at 25,000 bytes (content above the cap is silently truncated by
# Claude Code). Untracked instances belong to local scope.
# =============================================================================


def validate_loop_md_local(
    loop_path: Path, repo_root: Path, project_root: Path, report: ValidationReport
) -> None:
    """Validate an UNTRACKED ``.claude/loop.md`` file (local scope).

    Rules (TRDD-479cde0c §NOW #19, scheduled-tasks.md):

    - If the file is git-tracked, silently skip — that's project-scope's
      territory, handled by ``validate_loop_md_project``.
    - If the file exceeds 25,000 bytes: MAJOR (content above the cap is
      silently truncated by Claude Code per scheduled-tasks.md).
    - If the file is not UTF-8 decodable: CRITICAL (same pattern used by
      other markdown readers in this module).
    - Otherwise: INFO noting that ``loop.md`` replaces the built-in
      ``/loop`` prompt so the author confirms the content is a maintenance
      instruction, not an inadvertent command.
    """
    rel = loop_path.relative_to(project_root)
    # Tracked files are validated by the project-scope validator — skip here.
    if is_git_tracked(loop_path, repo_root):
        return
    try:
        safe_read_text(loop_path, MAX_LOOP_MD_BYTES)
    except OversizedFileError:
        report.major(
            f"{rel}: exceeds {MAX_LOOP_MD_BYTES}-byte cap from scheduled-tasks.md "
            "— content above this size is silently truncated by Claude Code. "
            "Trim the loop prompt or split it into multiple files.",
            str(rel),
        )
        return
    except (OSError, UnicodeDecodeError) as exc:
        report.critical(f"{rel}: read failed ({type(exc).__name__})", str(rel))
        return
    report.info(
        f"{rel}: loop.md present — replaces the built-in /loop prompt. Ensure "
        "content is a maintenance instruction, not an inadvertent command.",
        str(rel),
    )


# =============================================================================
# Orchestrator
# =============================================================================


def _validate_wip_shared_settings(
    settings_path: Path, report: ValidationReport
) -> None:
    """Apply strict project-scope rules to an UNTRACKED settings.json.

    TRDD-f4e2d385 §3.1 invariant: a finding that fires for a tracked
    ``.claude/settings.json`` MUST also fire here — the content is
    on its way to being committed and the developer wants the same
    diagnostic coverage pre-push. That means:

    1. Top-level schema (rejected keys, secrets, absolute paths) via
       ``validate_settings_json_project_scope``.
    2. Deep subtree validation for ``hooks`` — the ONLY element
       definition block Claude Code accepts inside settings files.
       ``lspServers`` is plugin-only (see ``plugin.json``), and inline
       ``mcpServers`` definitions are only meaningful in ``.mcp.json``
       / ``~/.claude.json`` / plugin.json — not in settings. A
       ``lspServers`` block in settings is a CRITICAL
       ``PLUGIN_ONLY_KEY`` finding raised by the top-level schema
       check above.

    Deferred imports avoid a module-level circular dependency between
    the two orchestrators.
    """
    from validate_project_scope import (
        _validate_project_settings_hooks,
        validate_settings_json_project_scope,
    )

    settings_data = validate_settings_json_project_scope(settings_path, report)
    if isinstance(settings_data, dict):
        _validate_project_settings_hooks(settings_data, ".claude/settings.json", report)


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
    settings_local_data: dict[str, Any] | None = None
    if settings_local.exists():
        if not no_git and is_git_tracked(settings_local, repo_root):
            report.major(
                ".claude/settings.local.json is git-tracked — it must be "
                "gitignored per settings.md. Personal config should not be "
                "committed.",
                ".claude/settings.local.json",
            )
        else:
            # TRDD-f4e2d385 §3.2: deep-validate the inline ``hooks`` subtree.
            # Read the file ONCE via ``validate_settings_local_json`` (which
            # returns the parsed dict) so the subtree walk operates on the
            # same snapshot as the top-level schema check — closes the H-3
            # TOCTOU window. ``lspServers`` is plugin-only and is flagged as
            # a CRITICAL plugin-only-key violation by the top-level schema
            # check; we do NOT deep-validate it here as if it were
            # semantically valid in settings.
            settings_local_data = validate_settings_local_json(settings_local, report)
            if isinstance(settings_local_data, dict):
                _validate_settings_hooks_subtree(
                    settings_local_data, ".claude/settings.local.json", report
                )
                _validate_settings_mcp_subtree(
                    settings_local_data, ".claude/settings.local.json", report
                )

    # 2. Untracked settings.json (WIP shared config) — use STRICT project rules.
    # Per user spec for local scope: settings.json is IGNORED (only
    # settings.local.json is the local-scope source of truth). An untracked
    # settings.json still gets a gentle WARNING so users notice the anomaly,
    # but we do NOT deep-validate its hooks/mcp/lsp subtrees from here —
    # that's validate_project_scope's territory when it becomes tracked.
    settings = claude_dir / "settings.json"
    if settings.exists() and classify_file_scope(settings, git_repo) in ("local", "no-git"):
        report.warning(
            ".claude/settings.json exists but is not git-tracked. Validating "
            "with strict project-scope rules since this file is usually shared "
            "with the team once committed.",
            ".claude/settings.json",
        )
        _validate_wip_shared_settings(settings, report)

    # 3. Untracked .mcp.json — WARNING per TRDD 5.6 + deep MCP validation.
    mcp_path = project_root / ".mcp.json"
    if mcp_path.exists() and classify_file_scope(mcp_path, git_repo) in ("local", "no-git"):
        report.warning(
            ".mcp.json exists but is not git-tracked — per mcp.md, .mcp.json "
            "is meant to be committed so the whole team gets the same MCP "
            "servers. Is this intentional?",
            ".mcp.json",
        )
        # TRDD-f4e2d385 §3.3: deep MCP validation of the file's content.
        _validate_mcp_json_file_deep(mcp_path, project_root, report, "local")

    # 4-8. Walk each .claude/<element>/ folder if it's local-scope.
    # TRDD-f4e2d385 §3.1: DEEP validators invoke the full per-element
    # pipeline (validate_agent, validate_skill_comprehensive, validate_command,
    # validate_rules_directory). Output-styles stays on the shallow
    # frontmatter-only path because no dedicated validator exists for them.
    for subfolder, validator in (
        ("agents", validate_local_agents_deep),
        ("skills", validate_local_skills_deep),
        ("commands", validate_local_commands_deep),
        ("rules", validate_local_rules_deep),
        ("output-styles", validate_local_output_styles),
    ):
        folder = claude_dir / subfolder
        if classify_folder_scope(folder, git_repo) in ("local", "no-git"):
            if folder.exists():
                validator(folder, repo_root, project_root, report)

    # 8b. Enumerate and validate locally-enabled plugins.
    # TRDD-f4e2d385 §3.4: for each `plugin@marketplace: true` in
    # settings.local.json.enabledPlugins, resolve the plugin cache directory
    # and run the full plugin validator. Missing plugins emit MAJOR (a
    # no-op enable is almost always a user mistake).
    if isinstance(settings_local_data, dict):
        enabled_plugins = settings_local_data.get("enabledPlugins")
        if isinstance(enabled_plugins, dict):
            validate_locally_enabled_plugins(enabled_plugins, report)

    # 9. CLAUDE.local.md at project root
    claude_local_md = project_root / "CLAUDE.local.md"
    if claude_local_md.exists():
        validate_claude_local_md(claude_local_md, repo_root, report)

    # 9b. .claude/loop.md (v2.22.0, scheduled-tasks.md).
    # Only validated here when untracked — tracked loop.md belongs to
    # validate_project_scope. Size-cap and UTF-8 check only (local rules
    # are deliberately relaxed).
    loop_md = claude_dir / "loop.md"
    if loop_md.exists():
        validate_loop_md_local(loop_md, repo_root, project_root, report)

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
# TRDD-f4e2d385: Deep element validation helpers.
#
# Goal: when a .claude/agents/X.md, .claude/skills/Y/SKILL.md, .claude/commands/
# Z.md, .claude/rules/W.md, or an inline hooks/mcpServers/lspServers block in
# settings.local.json is detected, run the SAME validator used by the plugin
# pipeline. Users get diagnoses with identical wording; the fix-validation
# skill's error-index already maps every finding to its remediation.
# =============================================================================


def _merge_subreport(subreport: ValidationReport, parent: ValidationReport, label_prefix: str) -> None:
    """Copy findings from a sub-validator's report into the main local-scope
    report, prepending `label_prefix` to each message so the user can tell
    which element the finding came from.

    We intentionally copy only real diagnostics (CRITICAL/MAJOR/MINOR/NIT/
    WARNING/INFO/PASSED). The sub-validator's "all good" summary lines are
    preserved verbatim — double-summary is cheap and the caller controls
    how they surface.
    """
    for r in subreport.results:
        parent.add(
            r.level,
            f"{label_prefix} {r.message}",
            r.file,
            r.line,
        )


def _validate_untracked_file_deep(
    path: Path,
    project_root: Path,
    tracked: set[Path],
    validator_fn,
    parent_report: ValidationReport,
    label_kind: str,
) -> None:
    """Run `validator_fn(path)` on a single untracked file and merge findings.

    `tracked` is the precomputed set of tracked paths (resolved). Files inside
    that set are skipped — they are validate_project_scope's concern. `path`
    is resolved and symlink-checked first to block escape attacks.
    """
    real = resolve_within(path, project_root)
    if real is None:
        parent_report.major(
            f"[{label_kind}] {path.relative_to(project_root)}: path resolves "
            "outside the project root (symlink escape) — skipping",
            str(path.relative_to(project_root)),
        )
        return
    if real in tracked:
        return  # tracked — skipped at local scope
    rel = path.relative_to(project_root)
    try:
        subreport = validator_fn(path)
    except Exception as exc:  # pragma: no cover — defensive
        parent_report.critical(
            f"[{label_kind}] {rel}: validator raised {type(exc).__name__}",
            str(rel),
        )
        return
    _merge_subreport(subreport, parent_report, f"[{label_kind} {rel}]")


def validate_local_agents_deep(
    agents_dir: Path, repo_root: Path, project_root: Path, report: ValidationReport
) -> None:
    """Deep-validate every UNTRACKED .md file under `.claude/agents/` with
    the full `validate_agent` pipeline (required fields, tools allowlist,
    model, TaskOutput deprecation, plugin-shipped restrictions, etc.).
    """
    tracked = list_tracked_files_under(agents_dir, repo_root) or set()
    count = 0
    for md in sorted(agents_dir.glob("*.md")):
        count += 1
        if count > MAX_FILES_PER_FOLDER:
            report.warning(
                f".claude/agents: stopped walking at {MAX_FILES_PER_FOLDER} files",
                ".claude/agents",
            )
            return
        _validate_untracked_file_deep(md, project_root, tracked, _deep_validate_agent, report, "agent")


def validate_local_commands_deep(
    commands_dir: Path, repo_root: Path, project_root: Path, report: ValidationReport
) -> None:
    """Deep-validate every UNTRACKED .md file under `.claude/commands/`."""
    tracked = list_tracked_files_under(commands_dir, repo_root) or set()
    count = 0
    for md in sorted(commands_dir.glob("*.md")):
        count += 1
        if count > MAX_FILES_PER_FOLDER:
            report.warning(
                f".claude/commands: stopped walking at {MAX_FILES_PER_FOLDER} files",
                ".claude/commands",
            )
            return
        _validate_untracked_file_deep(md, project_root, tracked, _deep_validate_command, report, "command")


def validate_local_skills_deep(
    skills_dir: Path, repo_root: Path, project_root: Path, report: ValidationReport
) -> None:
    """Deep-validate every UNTRACKED skill directory under `.claude/skills/`.

    A skill directory is `.claude/skills/<skill-name>/SKILL.md` plus optional
    resources. We consider a skill-dir local-scope when its SKILL.md is
    untracked. If SKILL.md IS tracked but some sibling resource isn't, the
    tracked SKILL.md is project-scope's concern — we skip the whole dir.
    """
    count = 0
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        count += 1
        if count > MAX_FILES_PER_FOLDER:
            report.warning(
                f".claude/skills: stopped walking at {MAX_FILES_PER_FOLDER} skills",
                ".claude/skills",
            )
            return
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            skill_md_lower = skill_dir / "skill.md"
            if skill_md_lower.exists():
                skill_md = skill_md_lower
            else:
                report.minor(
                    f".claude/skills/{skill_dir.name}: missing SKILL.md",
                    f".claude/skills/{skill_dir.name}",
                )
                continue
        real = resolve_within(skill_md, project_root)
        if real is None:
            report.major(
                f".claude/skills/{skill_dir.name}: symlink escape — skipping",
                f".claude/skills/{skill_dir.name}",
            )
            continue
        tracked = list_tracked_files_under(skill_dir, repo_root) or set()
        if real in tracked:
            continue  # tracked skill is project-scope's concern
        try:
            subreport = _deep_validate_skill(skill_dir)
        except Exception as exc:  # pragma: no cover — defensive
            report.critical(
                f"[skill .claude/skills/{skill_dir.name}]: validator raised "
                f"{type(exc).__name__}",
                f".claude/skills/{skill_dir.name}",
            )
            continue
        _merge_subreport(subreport, report, f"[skill .claude/skills/{skill_dir.name}]")


def validate_local_rules_deep(
    rules_dir: Path, repo_root: Path, project_root: Path, report: ValidationReport
) -> None:
    """Validate UNTRACKED rule files using `validate_rules_directory`.

    `validate_rules_directory` walks the full folder — we can't pre-filter
    it. Instead we run it on the whole folder and then filter out findings
    that refer to tracked files by path. This is the simplest correct
    approach given the existing validator's API.
    """
    tracked = list_tracked_files_under(rules_dir, repo_root) or set()
    rules_report = ValidationReport()
    try:
        _deep_validate_rules(rules_dir, rules_report, plugin_root=None)
    except Exception as exc:  # pragma: no cover — defensive
        report.critical(
            f"[rules .claude/rules]: validator raised {type(exc).__name__}",
            ".claude/rules",
        )
        return

    # Filter out findings whose `file` path resolves to a tracked rule —
    # those are project-scope's concern.
    #
    # BUG FIX (CPV audit 2026-04-17): `_deep_validate_rules` emits `r.file`
    # as a path RELATIVE TO `rules_dir.parent` (e.g. ".claude/") — see
    # validate_rules.validate_rules_directory where
    # `rel_path = rule_path.relative_to(rules_dir.parent)`. Joining it with
    # `project_root` produced `<project_root>/rules/<file>.md` which never
    # matches entries in `tracked` (which are resolved to
    # `<project_root>/.claude/rules/<file>.md`). The net effect was that
    # tracked rules incorrectly leaked into local-scope findings as
    # duplicates of what `validate_project_scope` already reported. We must
    # resolve against `rules_dir.parent` to reconstruct the same absolute
    # path that `tracked` holds.
    rel_base = rules_dir.parent
    for r in rules_report.results:
        if r.file:
            try:
                rfile = Path(r.file)
                resolved = rfile.resolve() if rfile.is_absolute() else (rel_base / rfile).resolve()
            except (OSError, ValueError):
                resolved = None
            if resolved is not None and resolved in tracked:
                continue
        report.add(r.level, f"[rules] {r.message}", r.file, r.line)


# =============================================================================
# TRDD-f4e2d385: Settings subtree validation.
#
# settings.local.json can inline-declare hooks, mcpServers, lspServers. We
# dump each subtree to a tempfile in the canonical shape the per-element
# validator expects, invoke that validator, and merge findings.
# =============================================================================


def _run_settings_subtree_validator(
    subtree_key: str,
    subtree_value: Any,
    settings_file_label: str,
    validator: Callable[..., ValidationReport],
    report: ValidationReport,
) -> None:
    """Dump ``{subtree_key: subtree_value}`` to a temp JSON file, run the
    per-element ``validator`` on it, and merge the sub-report.

    Uses ``tempfile.TemporaryDirectory`` (mode 0700 on POSIX) so the tempfile
    inside is unreadable by other users even on shared-tmp hosts, and the
    enclosing directory is always cleaned up by the context manager's
    ``shutil.rmtree`` regardless of interpreter-crash path (H-2 hardening).
    Non-dict subtree values short-circuit as MAJOR before the tempfile
    exists.
    """
    if subtree_value is None:
        return
    if not isinstance(subtree_value, dict):
        report.major(
            f"[{subtree_key} in {settings_file_label}] '{subtree_key}' must be an "
            f"object, got {type(subtree_value).__name__}",
            settings_file_label,
        )
        return
    with tempfile.TemporaryDirectory(prefix="cpv-subtree-") as tmpdir:
        tmp_path = Path(tmpdir) / "settings.json"
        tmp_path.write_text(json.dumps({subtree_key: subtree_value}), encoding="utf-8")
        try:
            subreport = validator(tmp_path, plugin_root=None)
        except Exception as exc:  # pragma: no cover — defensive
            report.critical(
                f"[{subtree_key} in {settings_file_label}] validator raised "
                f"{type(exc).__name__}",
                settings_file_label,
            )
            return
    _merge_subreport(subreport, report, f"[{subtree_key} in {settings_file_label}]")


def _validate_settings_hooks_subtree(
    settings: dict[str, Any], settings_file_label: str, report: ValidationReport
) -> None:
    """Extract `hooks` from a settings dict and run the hook validator."""
    _run_settings_subtree_validator(
        "hooks", settings.get("hooks"), settings_file_label, _deep_validate_hooks, report
    )


def _validate_settings_mcp_subtree(
    settings: dict[str, Any], settings_file_label: str, report: ValidationReport
) -> None:
    """Extract `mcpServers` from a settings dict and run the MCP validator."""
    _run_settings_subtree_validator(
        "mcpServers",
        settings.get("mcpServers"),
        settings_file_label,
        _deep_validate_mcp,
        report,
    )


# `lspServers` is plugin-only (per Claude Code plugin-reference: plugin.json
# top-level `lspServers`). Settings files (settings.json / settings.local.json)
# do NOT accept an `lspServers` block — Claude Code silently drops it. We
# deliberately do NOT deep-validate such a block here, because that would
# mask the real problem: the key is in the wrong file. The top-level schema
# check (_flag_plugin_only_keys_*) fires a CRITICAL when it sees it.


# =============================================================================
# TRDD-f4e2d385: .mcp.json (outside .claude/)
# =============================================================================


def _validate_mcp_json_file_deep(
    mcp_path: Path, project_root: Path, report: ValidationReport, scope_label: str
) -> None:
    """Run the full MCP validator on a `.mcp.json` file.

    `scope_label` is `local` or `project` — prefixed into finding messages
    so users can tell which scope ran the check.
    """
    try:
        subreport = _deep_validate_mcp(mcp_path, plugin_root=None)
    except Exception as exc:  # pragma: no cover — defensive
        report.critical(
            f"[.mcp.json] validator raised {type(exc).__name__}",
            ".mcp.json",
        )
        return
    _merge_subreport(subreport, report, "[.mcp.json]")


# =============================================================================
# TRDD-f4e2d385: Locally-enabled plugin enumeration.
#
# settings.local.json.enabledPlugins is a map of "<plugin>@<marketplace>" → bool.
# For each true entry we resolve the plugin's cache directory and, if it
# exists, recursively validate it with the full plugin pipeline. If the
# plugin is enabled but not installed, emit a MAJOR — an enabled-but-missing
# plugin is a silent no-op that the user almost certainly didn't intend.
# =============================================================================


# Plugin-cache resolver + regex are shared with validate_project_scope.py,
# hosted in cc_scope_rules.py so both sides agree on semver normalization,
# symlink-escape containment, and INFO emission on mixed-version fallback.


def _validate_plugin_all_checks(plugin_root: Path, report: ValidationReport) -> None:
    """Run the core validate_plugin sub-validators on a plugin directory.

    validate_plugin.py exposes its sub-validators as individual functions
    rather than a single orchestrator. This helper runs the subset that
    makes sense for an INSTALLED plugin (from the Claude Code cache) —
    we intentionally skip checks that are authoring-workflow-specific
    (gitignore, license, readme, pipeline readiness). These would be
    false-positives for a cached copy that may have been stripped by
    the marketplace publisher.
    """
    # Lazy import to avoid a hard cycle at import time.
    from validate_plugin import (  # noqa: E402
        validate_agents as _vp_agents,
    )
    from validate_plugin import (
        validate_bin_executables as _vp_bin,
    )
    from validate_plugin import (
        validate_commands as _vp_commands,
    )
    from validate_plugin import (
        validate_cross_platform as _vp_cross,
    )
    from validate_plugin import (
        validate_hooks as _vp_hooks,
    )
    from validate_plugin import (
        validate_manifest as _vp_manifest,
    )
    from validate_plugin import (
        validate_mcp as _vp_mcp,
    )
    from validate_plugin import (
        validate_no_local_paths as _vp_no_local,
    )
    from validate_plugin import (
        validate_output_styles as _vp_styles,
    )
    from validate_plugin import (
        validate_rules as _vp_rules,
    )
    from validate_plugin import (
        validate_scripts as _vp_scripts,
    )
    from validate_plugin import (
        validate_skills as _vp_skills,
    )
    from validate_plugin import (
        validate_structure as _vp_structure,
    )

    _vp_manifest(plugin_root, report, False)
    _vp_structure(plugin_root, report, False)
    _vp_commands(plugin_root, report)
    _vp_agents(plugin_root, report)
    _vp_hooks(plugin_root, report)
    _vp_mcp(plugin_root, report)
    _vp_scripts(plugin_root, report)
    _vp_bin(plugin_root, report)
    _vp_skills(plugin_root, report, None)
    _vp_rules(plugin_root, report)
    _vp_styles(plugin_root, report)
    _vp_no_local(plugin_root, report)
    _vp_cross(plugin_root, report)


def validate_locally_enabled_plugins(
    enabled_plugins: object, report: ValidationReport
) -> None:
    """For each `plugin@marketplace: true` in enabledPlugins, locate the
    installed plugin and run the core plugin checks on it.

    Takes ``object`` (not ``dict``) because the source is
    ``settings.local.json`` — untrusted JSON content. A runtime non-dict
    value short-circuits cleanly instead of raising.
    """
    if not isinstance(enabled_plugins, dict):
        return

    for key, value in enabled_plugins.items():
        if value is not True:
            continue  # disabled or non-true value → skipped
        m = ENABLED_PLUGIN_RE.match(str(key))
        if not m:
            report.minor(
                f"[enabledPlugins] '{key}' does not match "
                "'<plugin>@<marketplace>' form — skipping",
                "enabledPlugins",
            )
            continue
        plugin = m.group("plugin")
        marketplace = m.group("marketplace")
        cache_dir = resolve_plugin_cache_dir(
            plugin, marketplace, report=report, scope_label="enabledPlugins"
        )
        if cache_dir is None:
            report.major(
                f"[enabledPlugins {key}] plugin is enabled but NOT installed "
                f"at ~/.claude/plugins/cache/{marketplace}/{plugin}/ — enabling "
                "a non-installed plugin has no effect. Install with "
                f"`/plugin install {plugin}@{marketplace}` or remove from "
                "enabledPlugins.",
                "enabledPlugins",
            )
            continue
        subreport = ValidationReport()
        try:
            _validate_plugin_all_checks(cache_dir, subreport)
        except Exception as exc:  # pragma: no cover — defensive
            report.critical(
                f"[enabledPlugins {key}] plugin validator raised "
                f"{type(exc).__name__}",
                "enabledPlugins",
            )
            continue
        _merge_subreport(subreport, report, f"[enabled plugin {key}]")


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
