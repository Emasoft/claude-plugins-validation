#!/usr/bin/env python3
"""Validate git-tracked (project-scope) Claude Code configuration.

Per TRDD-2be75e88, this validator walks ``<project_path>/.claude/`` and
``<project_path>/.mcp.json`` and validates the shared team configuration
that lives in version control. "Project scope" is determined by
git-tracking status — a folder or file is in project scope if and only if
``git ls-files`` shows it as tracked.

Rules enforced (summary — see TRDD section 4 for details):

- ``.claude/settings.json``:
    * CRITICAL: keys rejected in project scope (``autoMemoryDirectory``,
      ``autoMode``, ``useAutoModeDuringPlan``,
      ``permissions.skipDangerousModePermissionPrompt``)
    * MAJOR: managed-only keys (``allowedMcpServers``, ``deniedMcpServers``,
      ``strictKnownMarketplaces``, …) and global-config-only keys
      (``editorMode``, ``autoConnectIde``, …)
    * MINOR: secrets in ``env``, absolute user paths in
      ``statusLine.command``, ``fileSuggestion.command``, ``apiKeyHelper``,
      ``awsAuthRefresh``, ``awsCredentialExport``, ``otelHeadersHelper``,
      ``hooks.*.command``, ``additionalDirectories``,
      ``sandbox.filesystem.*``, ``claudeMdExcludes``
- ``.mcp.json``:
    * CRITICAL: JSON parse failure
    * MAJOR: top-level not object / missing ``mcpServers``
    * MINOR: secrets in ``env`` values, absolute home paths in ``command``
- ``.claude/agents/*.md``: frontmatter YAML validity, absolute paths in
  ``system-prompt``/``initialPrompt``
- ``.claude/skills/<name>/SKILL.md``: frontmatter validity
- ``.claude/commands/*.md``: frontmatter validity
- ``.claude/rules/*.md``: body scans for absolute home paths
- ``.claude/output-styles/*.md``: frontmatter validity
- ``.claude/hooks/*.sh`` / ``*.py``: body scans for absolute home paths
- ``CLAUDE.md`` / ``.claude/CLAUDE.md``: absolute home paths in content,
  secret patterns, import targets

Elements are validated only if their containing folder (or the file
itself) is git-tracked under the given project root. Non-git-tracked
elements are the concern of ``validate_local_scope.py``.

Security invariants preserved by this module (see aegis audit):

- Every file read is size-capped via ``safe_read_text`` / ``safe_load_jsonc``.
- Every markdown frontmatter parse is bounded via
  ``safe_parse_frontmatter`` (YAML bomb mitigation).
- Every ``rglob`` hit is re-resolved via ``resolve_within`` to reject
  symlinks that escape the project root.
- Every per-folder file walk is capped at ``MAX_FILES_PER_FOLDER``.
- All absolute home paths in finding messages are run through
  ``redact_home_path`` before being written to the report.
- All parse-error messages use ``type(exc).__name__`` only, never
  ``str(exc)`` (avoids leaking file contents).

This validator is a **single-shot, single-threaded** offline tool. The
``exists()`` → ``read_text()`` sequence in each helper is a benign TOCTOU
window: an attacker who can swap files mid-run already controls the
validation outcome by virtue of owning the project tree. The reported
findings reflect the file state at read time, which may not match later
state — that is intentional and acceptable for an ad-hoc validator
(aegis INFO-1). Do not call these helpers from a background worker or a
long-running service without first adding a locking layer.

Exit codes follow the CPV convention:

- 0: no blocking issues
- 1: CRITICAL
- 2: MAJOR
- 3: MINOR
- 4: NIT (only in --strict mode)

Usage::

    uv run python scripts/validate_project_scope.py <project_path> [options]
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
    MAX_GITIGNORE_BYTES,
    MAX_MARKDOWN_BYTES,
    MAX_MCP_JSON_BYTES,
    MAX_SETTINGS_JSON_BYTES,
    PLUGIN_ONLY_KEYS,
    PROJECT_REJECTED_KEYS,
    PROJECT_REJECTED_NESTED_KEYS,
    SECRET_ENV_VAR_NAMES,
    OversizedFileError,
    classify_file_scope,
    classify_folder_scope,
    contains_absolute_home_path,
    find_git_root,
    gitignore_covers_path,
    is_secret_value,
    list_tracked_files_under,
    looks_like_secret_key_name,
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

# v2.22.0: `.claude/loop.md` is a plain-markdown file that replaces the built-in
# `/loop` maintenance prompt (scheduled-tasks.md). Content above 25,000 bytes
# is silently truncated by Claude Code — we enforce the same cap with a MAJOR
# finding pointing at the doc rule. Tracked instances are validated here;
# untracked ones belong to ``validate_local_scope``.
MAX_LOOP_MD_BYTES: int = 25_000


# =============================================================================
# Shared IO helpers (sanitised error reporting)
# =============================================================================


def _report_parse_error(
    report: ValidationReport, file_label: str, exc: BaseException
) -> None:
    """Record a CRITICAL parse-error finding without leaking file contents.

    Uses ``type(exc).__name__`` only — never ``str(exc)`` — because
    ``json.JSONDecodeError`` and ``UnicodeDecodeError`` messages embed
    line/column excerpts of the failing input, which may contain real
    secrets (aegis MEDIUM-4).
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


def _read_text_or_report(
    path: Path,
    max_bytes: int,
    report: ValidationReport,
    file_label: str,
) -> str | None:
    """Read a text file with size cap + sanitised error reporting."""
    try:
        return safe_read_text(path, max_bytes)
    except OversizedFileError:
        report.major(
            f"{file_label}: exceeds {max_bytes} byte size cap — skipping",
            file_label,
        )
        return None
    except (OSError, UnicodeDecodeError) as exc:
        report.critical(f"{file_label}: read failed ({type(exc).__name__})", file_label)
        return None


def _safe_path_message(value: Any) -> str:
    """Render a value for inclusion in a finding message with redaction."""
    if not isinstance(value, str):
        return repr(value)
    return redact_home_path(value)


# =============================================================================
# settings.json — scope-specific rules
# =============================================================================


def _flag_rejected_top_level_keys(data: dict[str, Any], report: ValidationReport, file_label: str) -> None:
    """Flag top-level keys Claude Code silently drops from project settings."""
    for key in sorted(PROJECT_REJECTED_KEYS):
        if key in data:
            report.critical(
                (
                    f"settings.json has '{key}' — Claude Code silently ignores this "
                    "key when set in project settings.json. Move it to "
                    ".claude/settings.local.json or ~/.claude/settings.json."
                ),
                file_label,
            )


def _flag_rejected_nested_keys(data: dict[str, Any], report: ValidationReport, file_label: str) -> None:
    """Flag nested keys Claude Code silently drops from project settings."""
    for path_tuple in sorted(PROJECT_REJECTED_NESTED_KEYS):
        cursor: Any = data
        for segment in path_tuple:
            if not isinstance(cursor, dict) or segment not in cursor:
                cursor = None
                break
            cursor = cursor[segment]
        if cursor is not None:
            dotted = ".".join(path_tuple)
            report.critical(
                (
                    f"settings.json sets '{dotted}' — Claude Code silently ignores "
                    "this in project settings to prevent untrusted repositories "
                    "from auto-bypassing the prompt. Move it to local or user scope."
                ),
                file_label,
            )


def _flag_permissions_default_mode(
    data: dict[str, Any], report: ValidationReport, file_label: str
) -> None:
    """Validate ``permissions.defaultMode`` against the 6 permission-modes.md
    values. An out-of-enum value is silently dropped by Claude Code, so the
    author's intended default never takes effect — MAJOR, not CRITICAL
    (Claude Code falls back to ``default`` behavior instead of erroring).
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
                f"{sorted(VALID_PERMISSION_MODES)}. Any other value is "
                "silently dropped by Claude Code; the intended default never "
                "takes effect."
            ),
            file_label,
        )


def _flag_managed_only_nested_keys(
    data: dict[str, Any], report: ValidationReport, file_label: str
) -> None:
    """Flag nested paths that only work in a managed settings file.

    ``permissions.disableAutoMode`` and ``permissions.disableBypassPermissionsMode``
    are admin kill-switches — placing them in project settings has no effect
    (Claude Code ignores them outside managed-settings). MAJOR severity so the
    user moves them to the correct scope.
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
                    f"settings.json sets '{dotted}' — this is a managed-only "
                    "admin kill-switch and has no effect in project settings. "
                    "Move it into managed-settings.json (macOS: "
                    "/Library/Application Support/ClaudeCode/managed-settings.json) "
                    "or deploy via server-managed settings. Remove from project."
                ),
                file_label,
            )


def _flag_managed_only_keys(data: dict[str, Any], report: ValidationReport, file_label: str) -> None:
    """Flag keys that only work in a managed settings file."""
    for key in sorted(MANAGED_ONLY_KEYS):
        if key in data:
            report.major(
                (
                    f"settings.json has managed-only key '{key}' — Claude Code "
                    "only reads this from managed-settings.json deployed by an "
                    "administrator. Remove it from project settings."
                ),
                file_label,
            )


def _flag_global_config_keys(data: dict[str, Any], report: ValidationReport, file_label: str) -> None:
    """Flag keys that only belong in ~/.claude.json."""
    for key in sorted(GLOBAL_CONFIG_KEYS):
        if key in data:
            report.major(
                (
                    f"settings.json has global-config-only key '{key}' — this key "
                    "lives in ~/.claude.json and triggers a schema error in a "
                    "settings.json file. Remove it."
                ),
                file_label,
            )


def _flag_plugin_only_keys(data: dict[str, Any], report: ValidationReport, file_label: str) -> None:
    """Flag keys that only work inside a plugin package's ``plugin.json``.

    ``lspServers`` and ``monitors`` are Claude Code plugin-manifest fields.
    When placed in a settings file they are silently dropped — the author's
    declaration of language-server spawn rules / background monitors never
    takes effect. This is a CRITICAL because the user's intent is
    load-bearing and the block is being completely ignored.
    """
    for key in sorted(PLUGIN_ONLY_KEYS):
        if key in data:
            report.critical(
                (
                    f"settings.json has plugin-only key '{key}' — Claude Code "
                    "reads this ONLY from a plugin package's plugin.json (top-level "
                    f"field). The '{key}' block in a settings file is silently "
                    "dropped. Move the declaration into the owning plugin's "
                    "plugin.json, or delete it."
                ),
                file_label,
            )


def _flag_secrets_in_env(data: dict[str, Any], report: ValidationReport, file_label: str) -> None:
    """Scan the ``env`` block for literal secrets.

    Three-tier check:
    1. **CRITICAL** — key is a known-secret env var (``SECRET_ENV_VAR_NAMES``:
       ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN, AWS_BEARER_TOKEN_BEDROCK,
       etc.) AND the value is a plain string literal (not ``${VAR}``
       expansion). These names are secrets by definition per env-vars.md;
       pattern-matching doesn't apply.
    2. **MINOR** — value matches a ``SECRET_VALUE_PATTERNS`` regex
       (Anthropic sk-ant-*, GitHub ghp_/gho_*, etc.) regardless of key name.
    3. **MINOR** — key looks secret-ish (``api_key``, ``token``, ``password``)
       AND value is a plain string not using ``${VAR}`` expansion.
    """
    env = data.get("env")
    if not isinstance(env, dict):
        return
    for key, value in env.items():
        if not isinstance(key, str):
            continue
        # Tier 1: known-secret env var with literal value → CRITICAL.
        if (
            key in SECRET_ENV_VAR_NAMES
            and isinstance(value, str)
            and value
            and not value.startswith("${")
            and not value.startswith("$")
        ):
            report.critical(
                (
                    f"settings.json env.{key} is a known-secret env var with a "
                    "hard-coded literal value. This commits a credential into a "
                    "shared settings file. Use ${" + key + "} expansion instead "
                    "and keep the actual value in .env (gitignored) or a secret "
                    "manager."
                ),
                file_label,
            )
            continue
        if is_secret_value(value):
            report.minor(
                (
                    f"settings.json env.{key} contains what looks like a literal "
                    "credential. Reference it via ${VAR} expansion instead and "
                    "store the actual value in .env or ~/.claude/settings.json."
                ),
                file_label,
            )
        elif looks_like_secret_key_name(key) and isinstance(value, str) and value and not value.startswith("${"):
            report.minor(
                (
                    f"settings.json env.{key} has a secret-like name but is not "
                    "using ${VAR} expansion — double-check nothing sensitive is "
                    "being committed."
                ),
                file_label,
            )


def _flag_absolute_home_paths_in_scalar(
    label: str, value: Any, report: ValidationReport, file_label: str
) -> None:
    """Emit a MINOR if ``value`` is a string containing an absolute home path.

    The value is redacted (username replaced with ``<REDACTED>``) before
    being echoed into the finding message (aegis MEDIUM-3).
    """
    if isinstance(value, str) and contains_absolute_home_path(value):
        report.minor(
            (
                f"settings.json {label} contains an absolute home path "
                f"('{_safe_path_message(value)}') — this will break for other "
                "team members. Use $CLAUDE_PROJECT_DIR or a relative path instead."
            ),
            file_label,
        )


def _flag_machine_specific_command_paths(data: dict[str, Any], report: ValidationReport, file_label: str) -> None:
    """Check every field that may legitimately hold a command path."""
    for key in ("apiKeyHelper", "awsAuthRefresh", "awsCredentialExport", "otelHeadersHelper"):
        _flag_absolute_home_paths_in_scalar(key, data.get(key), report, file_label)
    for parent_key in ("statusLine", "fileSuggestion"):
        parent = data.get(parent_key)
        if isinstance(parent, dict):
            _flag_absolute_home_paths_in_scalar(
                f"{parent_key}.command", parent.get("command"), report, file_label
            )


def _flag_hook_command_paths(data: dict[str, Any], report: ValidationReport, file_label: str) -> None:
    """Check hook command strings for absolute home paths."""
    hooks_block = data.get("hooks")
    if not isinstance(hooks_block, dict):
        return
    for event_name, entries in hooks_block.items():
        if not isinstance(entries, list):
            continue
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            nested = entry.get("hooks")
            if isinstance(nested, list):
                for jdx, nested_entry in enumerate(nested):
                    if isinstance(nested_entry, dict):
                        _flag_absolute_home_paths_in_scalar(
                            f"hooks.{event_name}[{idx}].hooks[{jdx}].command",
                            nested_entry.get("command"),
                            report,
                            file_label,
                        )
            _flag_absolute_home_paths_in_scalar(
                f"hooks.{event_name}[{idx}].command", entry.get("command"), report, file_label
            )


def _flag_additional_directories(data: dict[str, Any], report: ValidationReport, file_label: str) -> None:
    """Check permissions.additionalDirectories and sandbox.filesystem.* for home paths."""
    perms = data.get("permissions")
    if isinstance(perms, dict):
        add_dirs = perms.get("additionalDirectories")
        if isinstance(add_dirs, list):
            for idx, entry in enumerate(add_dirs):
                _flag_absolute_home_paths_in_scalar(
                    f"permissions.additionalDirectories[{idx}]", entry, report, file_label
                )
    sandbox = data.get("sandbox")
    if isinstance(sandbox, dict):
        fs = sandbox.get("filesystem")
        if isinstance(fs, dict):
            for sub_key in ("allowWrite", "allowRead", "allowWritePaths", "allowReadPaths"):
                value = fs.get(sub_key)
                if isinstance(value, list):
                    for idx, entry in enumerate(value):
                        _flag_absolute_home_paths_in_scalar(
                            f"sandbox.filesystem.{sub_key}[{idx}]", entry, report, file_label
                        )


def _flag_claude_md_excludes(data: dict[str, Any], report: ValidationReport, file_label: str) -> None:
    """Check claudeMdExcludes for machine-specific absolute paths."""
    excludes = data.get("claudeMdExcludes")
    if isinstance(excludes, list):
        for idx, entry in enumerate(excludes):
            _flag_absolute_home_paths_in_scalar(
                f"claudeMdExcludes[{idx}]", entry, report, file_label
            )


def _flag_missing_schema(data: dict[str, Any], report: ValidationReport, file_label: str) -> None:
    """NIT: settings.json should declare ``$schema`` for editor autocomplete."""
    if "$schema" not in data:
        report.nit(
            (
                "settings.json is missing $schema — consider adding "
                '"$schema": "https://json.schemastore.org/claude-code-settings.json" '
                "for editor autocomplete."
            ),
            file_label,
        )


def validate_settings_json_project_scope(
    settings_path: Path, report: ValidationReport
) -> dict[str, Any] | None:
    """Apply project-scope rules to ``.claude/settings.json`` contents.

    Returns the parsed JSON dict on success so the orchestrator can reuse
    it for subtree deep validation (hooks/mcpServers/lspServers/
    enabledPlugins) without parsing the same file twice. Returns None
    when the file fails to parse or the root is not a JSON object.
    """
    file_label = ".claude/settings.json"
    data = _load_json_or_report(settings_path, MAX_SETTINGS_JSON_BYTES, report, file_label)
    if data is None:
        return None
    if not isinstance(data, dict):
        report.critical("settings.json root must be a JSON object", file_label)
        return None

    _flag_rejected_top_level_keys(data, report, file_label)
    _flag_rejected_nested_keys(data, report, file_label)
    _flag_managed_only_keys(data, report, file_label)
    _flag_managed_only_nested_keys(data, report, file_label)
    _flag_global_config_keys(data, report, file_label)
    _flag_plugin_only_keys(data, report, file_label)
    _flag_permissions_default_mode(data, report, file_label)
    _flag_secrets_in_env(data, report, file_label)
    _flag_machine_specific_command_paths(data, report, file_label)
    _flag_hook_command_paths(data, report, file_label)
    _flag_additional_directories(data, report, file_label)
    _flag_claude_md_excludes(data, report, file_label)
    _flag_missing_schema(data, report, file_label)

    if not report.has_critical and not report.has_major and not report.has_minor:
        report.passed("settings.json project-scope rules OK", file_label)
    return data


# =============================================================================
# .mcp.json
# =============================================================================


def validate_mcp_json_project_scope(mcp_path: Path, report: ValidationReport) -> None:
    """Apply project-scope rules to a ``.mcp.json`` at the repo root."""
    file_label = ".mcp.json"
    data = _load_json_or_report(mcp_path, MAX_MCP_JSON_BYTES, report, file_label)
    if data is None:
        return
    if not isinstance(data, dict):
        report.major(".mcp.json root must be a JSON object", file_label)
        return
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        report.major(".mcp.json must have an 'mcpServers' object", file_label)
        return

    for name, server in servers.items():
        if not isinstance(server, dict):
            report.major(f".mcp.json mcpServers.{name} must be an object", file_label)
            continue
        env = server.get("env")
        if isinstance(env, dict):
            for env_key, env_value in env.items():
                if is_secret_value(env_value):
                    report.minor(
                        (
                            f".mcp.json mcpServers.{name}.env.{env_key} contains a "
                            "literal credential. Use ${VAR} expansion instead."
                        ),
                        file_label,
                    )
        _flag_absolute_home_paths_in_scalar(
            f"mcpServers.{name}.command", server.get("command"), report, file_label
        )
        args = server.get("args")
        if isinstance(args, list):
            for idx, arg in enumerate(args):
                _flag_absolute_home_paths_in_scalar(
                    f"mcpServers.{name}.args[{idx}]", arg, report, file_label
                )
        url = server.get("url")
        if isinstance(url, str) and looks_like_secret_key_name(url):
            report.minor(
                f".mcp.json mcpServers.{name}.url looks like it embeds a credential",
                file_label,
            )

    if not report.has_critical and not report.has_major and not report.has_minor:
        report.passed(".mcp.json project-scope rules OK", file_label)


# =============================================================================
# Markdown elements — lightweight frontmatter + content scans
# =============================================================================


def _validate_markdown_file_shared(
    path: Path, report: ValidationReport, rel_label: str, *, forbid_home_paths: bool
) -> None:
    """Shared frontmatter + home-path scan for project-scope markdown files."""
    content = _read_text_or_report(path, MAX_MARKDOWN_BYTES, report, rel_label)
    if content is None:
        return
    frontmatter, body = safe_parse_frontmatter(content)
    if frontmatter is None:
        report.minor(f"{rel_label}: missing or invalid YAML frontmatter", rel_label)
        return
    name = frontmatter.get("name")
    if not isinstance(name, str) or not name.strip():
        report.minor(f"{rel_label}: frontmatter 'name' is missing or empty", rel_label)
    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        report.minor(f"{rel_label}: frontmatter 'description' is missing or empty", rel_label)

    if forbid_home_paths:
        for field in ("system-prompt", "initialPrompt"):
            value = frontmatter.get(field)
            if isinstance(value, str) and contains_absolute_home_path(value):
                report.minor(
                    f"{rel_label}: frontmatter '{field}' contains an absolute home path",
                    rel_label,
                )
        if contains_absolute_home_path(body):
            report.minor(
                f"{rel_label}: body contains an absolute home path — will break for teammates",
                rel_label,
            )


def _walk_tracked_markdown(
    folder: Path,
    repo_root: Path,
    project_root: Path,
    report: ValidationReport,
    glob_pattern: str,
    *,
    forbid_home_paths: bool,
) -> None:
    """Walk ``folder`` for ``glob_pattern`` matches, apply markdown checks.

    Only tracked files are validated (the folder was already classified as
    project scope by the caller). Symlink escapes are rejected via
    ``resolve_within`` (aegis MEDIUM-1/2). The walk is capped at
    ``MAX_FILES_PER_FOLDER`` to prevent DoS via millions-of-files projects
    (aegis LOW-3).

    A single ``git ls-files`` call is used to get the tracked set for the
    folder, avoiding one ``git ls-files --error-unmatch`` call per file.
    """
    tracked = list_tracked_files_under(folder, repo_root)
    if tracked is None:
        return
    count = 0
    for md in sorted(folder.rglob(glob_pattern)):
        count += 1
        if count > MAX_FILES_PER_FOLDER:
            report.warning(
                f"{folder.relative_to(repo_root)}: stopped walking at "
                f"{MAX_FILES_PER_FOLDER} files — truncating validation",
                str(folder.relative_to(repo_root)),
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
        if real not in tracked:
            continue
        rel = md.relative_to(project_root)
        _validate_markdown_file_shared(
            md, report, str(rel), forbid_home_paths=forbid_home_paths
        )


def validate_agents_folder(
    agents_dir: Path,
    repo_root: Path,
    project_root: Path,
    report: ValidationReport,
) -> None:
    """Validate every tracked ``*.md`` file in ``.claude/agents/``."""
    _walk_tracked_markdown(
        agents_dir, repo_root, project_root, report, "*.md", forbid_home_paths=True
    )


def validate_skills_folder(
    skills_dir: Path,
    repo_root: Path,
    project_root: Path,
    report: ValidationReport,
) -> None:
    """Validate every tracked ``SKILL.md`` in ``.claude/skills/``."""
    _walk_tracked_markdown(
        skills_dir, repo_root, project_root, report, "SKILL.md", forbid_home_paths=True
    )


def validate_commands_folder(
    commands_dir: Path,
    repo_root: Path,
    project_root: Path,
    report: ValidationReport,
) -> None:
    """Validate every tracked ``*.md`` file in ``.claude/commands/``."""
    _walk_tracked_markdown(
        commands_dir, repo_root, project_root, report, "*.md", forbid_home_paths=True
    )


def validate_output_styles_folder(
    styles_dir: Path,
    repo_root: Path,
    project_root: Path,
    report: ValidationReport,
) -> None:
    """Validate every tracked ``*.md`` file in ``.claude/output-styles/``.

    Output-styles are frontmatter + body text per the Claude Code docs.
    The structure is simpler than agents/skills but the same markdown
    hygiene rules apply — no absolute home paths in body, no secrets.
    """
    _walk_tracked_markdown(
        styles_dir, repo_root, project_root, report, "*.md", forbid_home_paths=True
    )


def validate_rules_folder(
    rules_dir: Path,
    repo_root: Path,
    project_root: Path,
    report: ValidationReport,
) -> None:
    """Validate every tracked ``*.md`` file in ``.claude/rules/``.

    Rules are loaded unconditionally when they have no ``paths:`` field or
    when a tracked file matches the glob. Body is scanned for absolute
    home paths only — no frontmatter requirement (rules without
    frontmatter still load).
    """
    tracked = list_tracked_files_under(rules_dir, repo_root)
    if tracked is None:
        return
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
        if real not in tracked:
            continue
        rel = md.relative_to(project_root)
        content = _read_text_or_report(md, MAX_MARKDOWN_BYTES, report, str(rel))
        if content is None:
            continue
        if contains_absolute_home_path(content):
            report.minor(
                f"{rel}: rule content contains an absolute home path",
                str(rel),
            )


def validate_hooks_folder(
    hooks_dir: Path,
    repo_root: Path,
    project_root: Path,
    report: ValidationReport,
) -> None:
    """Validate every tracked ``*.sh`` / ``*.py`` in ``.claude/hooks/``.

    Per hooks.md, hook scripts referenced from ``settings.json`` should
    use ``$CLAUDE_PROJECT_DIR`` / ``${CLAUDE_PLUGIN_ROOT}`` for portable
    absolute paths. Hardcoded home paths break for other team members.
    """
    tracked = list_tracked_files_under(hooks_dir, repo_root)
    if tracked is None:
        return
    count = 0
    for script in sorted(list(hooks_dir.rglob("*.sh")) + list(hooks_dir.rglob("*.py"))):
        count += 1
        if count > MAX_FILES_PER_FOLDER:
            report.warning(
                f".claude/hooks: stopped walking at {MAX_FILES_PER_FOLDER} files",
                ".claude/hooks",
            )
            return
        real = resolve_within(script, project_root)
        if real is None:
            report.major(
                f"{script.relative_to(project_root)}: symlink escape — skipping",
                str(script.relative_to(project_root)),
            )
            continue
        if real not in tracked:
            continue
        rel = script.relative_to(project_root)
        content = _read_text_or_report(script, MAX_MARKDOWN_BYTES, report, str(rel))
        if content is None:
            continue
        if contains_absolute_home_path(content):
            report.minor(
                f"{rel}: hook script contains an absolute home path — "
                "use $CLAUDE_PROJECT_DIR instead",
                str(rel),
            )


def validate_claude_md_file(
    md_path: Path, repo_root: Path, report: ValidationReport
) -> None:
    """Validate a CLAUDE.md file (project root or .claude/).

    ``repo_root`` is used to build relative labels. A ``resolve_within``
    check is applied defensively: although ``classify_file_scope`` in
    the caller restricts us to git-tracked files, git DOES track
    symlinks as blobs pointing at arbitrary targets — so a committed
    symlink could still resolve outside the repo. Per the module's
    "Every rglob/file hit is re-resolved via resolve_within" invariant,
    we enforce the check here too and skip escapes.

    The body is additionally scanned for ``@path/to/file.md`` import
    tokens per memory.md L95-107. Each import is resolved relative to
    the containing file, walked recursively (max depth 5), and
    classified: absolute paths outside the repo are CRITICAL security
    leaks, ``..`` escapes / missing files / over-deep chains / cycles
    are MAJOR, and a summary INFO is emitted at the end.
    """
    rel = md_path.relative_to(repo_root)
    real = resolve_within(md_path, repo_root)
    if real is None:
        report.major(
            f"{rel}: path resolves outside the repo root (symlink escape) — skipping",
            str(rel),
        )
        return
    content = _read_text_or_report(md_path, MAX_CLAUDE_MD_BYTES, report, str(rel))
    if content is None:
        return
    if contains_absolute_home_path(content):
        report.minor(
            f"{rel}: contains an absolute home path — will break for teammates",
            str(rel),
        )
    # Secret detection: look for each line's values
    for lineno, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        for token in stripped.split():
            if is_secret_value(token):
                report.major(
                    f"{rel}:{lineno}: line contains what looks like a literal credential",
                    str(rel),
                    line=lineno,
                )
                break
    # @path import recursion check (memory.md L95-107).
    validate_claude_md_imports(
        md_path, repo_root, report, str(rel), max_bytes=MAX_CLAUDE_MD_BYTES
    )


def validate_gitignore_for_scope_hygiene(
    repo_root: Path, report: ValidationReport
) -> None:
    """Informational: recommend gitignore entries for local-scope files.

    Uses ``git check-ignore`` (via ``gitignore_covers_path``) rather than
    a string-match on ``.gitignore`` lines, so gitignore globs like
    ``*.local.*`` / ``.claude/`` / ``**/settings.local.json`` are all
    recognised correctly.
    """
    gitignore = repo_root / ".gitignore"
    if not gitignore.exists():
        return
    # Size-cap the .gitignore read to prevent DoS via a hostile 2GB file.
    content = _read_text_or_report(gitignore, MAX_GITIGNORE_BYTES, report, ".gitignore")
    if content is None:
        return
    if not gitignore_covers_path(".claude/settings.local.json", repo_root):
        report.info(
            ".gitignore does not cover '.claude/settings.local.json' — "
            "Claude Code auto-adds it on first creation, but pinning is safer.",
            ".gitignore",
        )
    if not gitignore_covers_path("CLAUDE.local.md", repo_root):
        report.info(
            ".gitignore does not cover 'CLAUDE.local.md' — pin it to prevent "
            "accidental commits of personal memory notes.",
            ".gitignore",
        )


# =============================================================================
# .claude/loop.md — v2.22.0 (scheduled-tasks.md)
#
# When `.claude/loop.md` is git-tracked, it is a shared team artefact —
# validate it under project scope. The only hard rules are the size cap
# (25 KB; content above is silently truncated by Claude Code) and UTF-8
# decodability. The file replaces the built-in `/loop` maintenance prompt,
# so we emit an INFO so the author confirms the content is intentional.
# Untracked instances are validate_local_scope's territory.
# =============================================================================


def validate_loop_md_project(
    loop_path: Path, repo_root: Path, report: ValidationReport
) -> None:
    """Validate a TRACKED ``.claude/loop.md`` file (project scope).

    Rules (TRDD-479cde0c §NOW #19, scheduled-tasks.md):

    - If the file exceeds 25,000 bytes: MAJOR (content above the cap is
      silently truncated by Claude Code per scheduled-tasks.md).
    - If the file is not UTF-8 decodable: CRITICAL (same pattern used by
      other markdown readers in this module).
    - Otherwise: INFO noting that ``loop.md`` replaces the built-in
      ``/loop`` prompt so the author confirms the content is a maintenance
      instruction, not an inadvertent command.

    Symlink escapes are rejected via ``resolve_within`` to honour the
    module's security invariant.
    """
    rel = loop_path.relative_to(repo_root)
    real = resolve_within(loop_path, repo_root)
    if real is None:
        report.major(
            f"{rel}: path resolves outside the repo root (symlink escape) — skipping",
            str(rel),
        )
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


# =============================================================================
# TRDD-f4e2d385: Deep validation helpers (project-scope variant).
#
# Mirror of the helpers in validate_local_scope.py, filtered to TRACKED files
# (not untracked). Settings subtree validators pull from settings.json (not
# .local). Plugin enumeration reads enabledPlugins from settings.json.
# =============================================================================


def _merge_subreport_project(subreport: ValidationReport, parent: ValidationReport, label_prefix: str) -> None:
    """Copy findings from a sub-validator into the main report with a prefix."""
    for r in subreport.results:
        parent.add(
            r.level,
            f"{label_prefix} {r.message}",
            r.file,
            r.line,
        )


def _deep_validate_tracked_file(
    path: Path,
    project_root: Path,
    tracked: set[Path],
    validator_fn,
    parent_report: ValidationReport,
    label_kind: str,
) -> None:
    """Run `validator_fn(path)` on a TRACKED file and merge findings."""
    real = resolve_within(path, project_root)
    if real is None:
        parent_report.major(
            f"[{label_kind}] {path.relative_to(project_root)}: symlink escape — skipping",
            str(path.relative_to(project_root)),
        )
        return
    if real not in tracked:
        return  # untracked — skipped at project scope
    rel = path.relative_to(project_root)
    try:
        subreport = validator_fn(path)
    except Exception as exc:  # pragma: no cover — defensive
        # Module invariant (see module docstring §"Security invariants"):
        # exception messages are reported via type(exc).__name__ only —
        # never str(exc). JSON/YAML decode error messages embed excerpts
        # of the failing input, which may contain real secrets.
        parent_report.critical(
            f"[{label_kind}] {rel}: validator raised {type(exc).__name__}",
            str(rel),
        )
        return
    _merge_subreport_project(subreport, parent_report, f"[{label_kind} {rel}]")


def validate_project_agents_deep(
    agents_dir: Path, repo_root: Path, project_root: Path, report: ValidationReport
) -> None:
    """Run `validate_agent` on every TRACKED agent .md file."""
    from validate_agent import validate_agent as _deep_validate_agent  # noqa: E402

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
        _deep_validate_tracked_file(md, project_root, tracked, _deep_validate_agent, report, "agent")


def validate_project_commands_deep(
    commands_dir: Path, repo_root: Path, project_root: Path, report: ValidationReport
) -> None:
    """Run `validate_command` on every TRACKED command .md file."""
    from validate_command import validate_command as _deep_validate_command  # noqa: E402

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
        _deep_validate_tracked_file(md, project_root, tracked, _deep_validate_command, report, "command")


def validate_project_skills_deep(
    skills_dir: Path, repo_root: Path, project_root: Path, report: ValidationReport
) -> None:
    """Run `validate_skill_comprehensive` on every skill dir whose SKILL.md is TRACKED."""
    from validate_skill_comprehensive import validate_skill as _deep_validate_skill  # noqa: E402

    # Hoist the tracked-files lookup out of the loop: one ``git ls-files``
    # subprocess covers the whole skills_dir tree. Previously this spawned
    # one subprocess per skill_dir (O(N) overhead on large plugins).
    tracked = list_tracked_files_under(skills_dir, repo_root) or set()

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
                continue  # no SKILL.md, skip (project scope — not our concern)
        real = resolve_within(skill_md, project_root)
        if real is None:
            continue
        if real not in tracked:
            continue  # untracked skill — validate_local_scope's concern
        try:
            subreport = _deep_validate_skill(skill_dir)
        except Exception as exc:  # pragma: no cover — defensive
            # Module invariant: never leak str(exc) — validator exception
            # messages may embed excerpts of user files (see module docstring).
            report.critical(
                f"[skill .claude/skills/{skill_dir.name}]: validator raised "
                f"{type(exc).__name__}",
                f".claude/skills/{skill_dir.name}",
            )
            continue
        _merge_subreport_project(subreport, report, f"[skill .claude/skills/{skill_dir.name}]")


def _run_project_subtree_validator(
    subtree_key: str,
    subtree_value: Any,
    settings_file_label: str,
    validator: Callable[..., ValidationReport],
    report: ValidationReport,
) -> None:
    """Dump ``{subtree_key: subtree_value}`` to a temp JSON file and run the
    per-element ``validator`` on it (H-2: ``TemporaryDirectory`` ensures
    cleanup even on crash; mode-0700 tempdir contains the file from other
    local users). Non-dict subtree values short-circuit as MAJOR before the
    tempfile exists. Sub-report findings are merged with the caller's label.
    """
    if subtree_value is None:
        return
    if not isinstance(subtree_value, dict):
        report.major(
            f"[{subtree_key} in {settings_file_label}] '{subtree_key}' must be an object",
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
    _merge_subreport_project(subreport, report, f"[{subtree_key} in {settings_file_label}]")


def _validate_project_settings_hooks(
    settings: dict[str, Any], settings_file_label: str, report: ValidationReport
) -> None:
    """Deep-validate `hooks` subtree in settings.json."""
    from validate_hook import validate_hooks as _deep_validate_hooks  # noqa: E402

    _run_project_subtree_validator(
        "hooks", settings.get("hooks"), settings_file_label, _deep_validate_hooks, report
    )


def _validate_project_settings_mcp(
    settings: dict[str, Any], settings_file_label: str, report: ValidationReport
) -> None:
    """Deep-validate `mcpServers` subtree in settings.json."""
    from validate_mcp import validate_mcp_config as _deep_validate_mcp  # noqa: E402

    _run_project_subtree_validator(
        "mcpServers",
        settings.get("mcpServers"),
        settings_file_label,
        _deep_validate_mcp,
        report,
    )


# `lspServers` is plugin-only (per Claude Code plugin-reference: plugin.json
# top-level `lspServers`). Settings files never accept it; a CRITICAL is
# raised by the top-level schema check for misplaced plugin-only keys
# instead of deep-validating an inline block.


# Plugin-cache resolver + regex are shared with validate_local_scope.py in
# cc_scope_rules.py — see ``ENABLED_PLUGIN_RE`` and ``resolve_plugin_cache_dir``.


def validate_project_enabled_plugins(
    enabled_plugins: object, report: ValidationReport
) -> None:
    """For each `plugin@marketplace: true` in settings.json.enabledPlugins,
    validate the installed plugin with the core plugin pipeline.
    """
    if not isinstance(enabled_plugins, dict):
        return
    from validate_plugin import (  # noqa: E402
        validate_agents as _vp_agents,
    )
    from validate_plugin import (
        validate_commands as _vp_commands,
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
    for key, value in enabled_plugins.items():
        if value is not True:
            continue
        m = ENABLED_PLUGIN_RE.match(str(key))
        if not m:
            report.minor(
                f"[enabledPlugins] '{key}' does not match '<plugin>@<marketplace>' form",
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
                f"[enabledPlugins {key}] plugin is enabled in settings.json but "
                f"NOT installed at ~/.claude/plugins/cache/{marketplace}/{plugin}/",
                "enabledPlugins",
            )
            continue
        subreport = ValidationReport()
        try:
            _vp_manifest(cache_dir, subreport, False)
            _vp_structure(cache_dir, subreport, False)
            _vp_commands(cache_dir, subreport)
            _vp_agents(cache_dir, subreport)
            _vp_hooks(cache_dir, subreport)
            _vp_mcp(cache_dir, subreport)
            _vp_scripts(cache_dir, subreport)
            _vp_skills(cache_dir, subreport, None)
            _vp_rules(cache_dir, subreport)
        except Exception as exc:  # pragma: no cover
            # Module invariant: exception strings can leak plugin file
            # contents — use the type name only (see module docstring).
            report.critical(
                f"[enabledPlugins {key}] validator raised {type(exc).__name__}",
                "enabledPlugins",
            )
            continue
        _merge_subreport_project(subreport, report, f"[enabled plugin {key}]")


def validate_project_scope(project_root: Path, report: ValidationReport) -> None:
    """Walk the project tree and validate every git-tracked Claude Code element.

    Behaviour:

    - If the project has no ``.git`` ancestor, emits a WARNING and skips
      project-scope validation (no files can be classified as tracked).
    - If ``.claude/`` does not exist, emits an INFO and validates only
      ``.mcp.json`` / ``CLAUDE.md`` at the project root.
    - If ``.claude/`` exists but is empty, emits a distinct INFO so the
      caller can distinguish "no config" from "empty folder".
    """
    if not project_root.exists() or not project_root.is_dir():
        report.critical(
            f"Project path does not exist or is not a directory: {project_root}",
            str(project_root),
        )
        return

    # Bound find_git_root to the project path so symlinked parents cannot
    # escape the user's nominal working set (aegis MEDIUM-6).
    repo_root = find_git_root(project_root, boundary=project_root) or project_root
    if not (repo_root / ".git").exists():
        report.warning(
            "Not a git repository — no files can be classified as project-scope. "
            "Initialise a git repo or run validate_local_scope instead.",
            str(project_root),
        )
        return

    claude_dir = project_root / ".claude"

    # 1. .claude/settings.json — the project-scope settings.
    # Per user spec: settings.local.json is IGNORED (that's local-scope's job).
    # Deep-validate inline hooks/mcpServers/lspServers subtrees from settings.json.
    settings_path = claude_dir / "settings.json"
    settings_data: dict[str, Any] | None = None
    if classify_file_scope(settings_path, repo_root) == "project":
        # Parse once: validate_settings_json_project_scope returns the
        # parsed dict so we can reuse it for subtree deep validation
        # (hooks/mcpServers/enabledPlugins) and avoid parsing the same JSON
        # file twice. NOTE: ``lspServers`` is plugin-only (plugin.json
        # top-level field) and is NOT deep-validated here — Claude Code
        # silently drops it from settings, so the correct diagnostic is a
        # CRITICAL from the top-level schema check (plugin-only-key
        # rejection), not a deep walk that would imply the block is
        # semantically valid.
        settings_data = validate_settings_json_project_scope(settings_path, report)
        if settings_data is not None:
            _validate_project_settings_hooks(settings_data, ".claude/settings.json", report)
            _validate_project_settings_mcp(settings_data, ".claude/settings.json", report)
    elif settings_path.exists():
        report.info(
            ".claude/settings.json exists but is not git-tracked — validated by "
            "cpv-validate-local-scope instead.",
            ".claude/settings.json",
        )

    # 2. .mcp.json at project root
    mcp_path = project_root / ".mcp.json"
    if classify_file_scope(mcp_path, repo_root) == "project":
        validate_mcp_json_project_scope(mcp_path, report)
    elif mcp_path.exists():
        report.warning(
            ".mcp.json exists but is not git-tracked — per Claude Code docs, "
            ".mcp.json is meant to be committed and shared with the team.",
            ".mcp.json",
        )

    # 3. .claude/agents/ — deep validation on TRACKED files.
    agents_dir = claude_dir / "agents"
    if classify_folder_scope(agents_dir, repo_root) == "project":
        validate_agents_folder(agents_dir, repo_root, project_root, report)
        # TRDD-f4e2d385: add deep validator findings.
        validate_project_agents_deep(agents_dir, repo_root, project_root, report)

    # 4. .claude/skills/ — deep validation via validate_skill_comprehensive.
    skills_dir = claude_dir / "skills"
    if classify_folder_scope(skills_dir, repo_root) == "project":
        validate_skills_folder(skills_dir, repo_root, project_root, report)
        validate_project_skills_deep(skills_dir, repo_root, project_root, report)

    # 5. .claude/commands/ — deep validation via validate_command.
    commands_dir = claude_dir / "commands"
    if classify_folder_scope(commands_dir, repo_root) == "project":
        validate_commands_folder(commands_dir, repo_root, project_root, report)
        validate_project_commands_deep(commands_dir, repo_root, project_root, report)

    # 6. .claude/rules/
    rules_dir = claude_dir / "rules"
    if classify_folder_scope(rules_dir, repo_root) == "project":
        validate_rules_folder(rules_dir, repo_root, project_root, report)

    # 7. .claude/output-styles/ (TRDD 4.7)
    styles_dir = claude_dir / "output-styles"
    if classify_folder_scope(styles_dir, repo_root) == "project":
        validate_output_styles_folder(styles_dir, repo_root, project_root, report)

    # 8. .claude/hooks/ (TRDD 4.9)
    hooks_dir = claude_dir / "hooks"
    if classify_folder_scope(hooks_dir, repo_root) == "project":
        validate_hooks_folder(hooks_dir, repo_root, project_root, report)

    # 9. CLAUDE.md (project root or .claude/)
    for md_candidate in (project_root / "CLAUDE.md", claude_dir / "CLAUDE.md"):
        if classify_file_scope(md_candidate, repo_root) == "project":
            validate_claude_md_file(md_candidate, repo_root, report)

    # 9b. .claude/loop.md (v2.22.0, scheduled-tasks.md).
    # Only validated here when tracked — untracked loop.md belongs to
    # validate_local_scope. Enforces the 25 KB truncation cap and checks
    # UTF-8 decodability.
    loop_md = claude_dir / "loop.md"
    if classify_file_scope(loop_md, repo_root) == "project":
        validate_loop_md_project(loop_md, repo_root, report)

    # 10. .gitignore hygiene
    validate_gitignore_for_scope_hygiene(repo_root, report)

    # 10b. Enumerate and validate project-enabled plugins from settings.json.
    # TRDD-f4e2d385 §3.4: `enabledPlugins` in the SHARED settings.json
    # means "the whole team agrees to enable this plugin". Validate each
    # with the core plugin pipeline.
    if isinstance(settings_data, dict):
        enabled_plugins = settings_data.get("enabledPlugins")
        if isinstance(enabled_plugins, dict):
            validate_project_enabled_plugins(enabled_plugins, report)

    # 11. Distinct empty-folder INFO vs "no config found"
    if not report.results:
        if claude_dir.exists() and not any(claude_dir.iterdir()):
            report.info(
                ".claude/ directory exists but is empty — no configuration to validate.",
                str(project_root),
            )
        else:
            report.info(
                "No Claude Code project-scope configuration found under this path.",
                str(project_root),
            )


# =============================================================================
# CLI entry point
# =============================================================================


def main() -> int:
    """Command-line entry point for ``cpv-validate-project-scope``."""
    check_remote_execution_guard()

    parser = argparse.ArgumentParser(
        description=(
            "Validate git-tracked (project-scope) Claude Code configuration "
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
    validate_project_scope(project_root, report)

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
                "Claude Code Project-Scope Validation",
                print_results_by_level,
                args.verbose,
                plugin_path=str(project_root),
            )
        else:
            print_results_by_level(report, args.verbose)

    return report.exit_code_strict() if args.strict else report.exit_code


if __name__ == "__main__":
    sys.exit(main())
