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
- ``.claude/rules/*.md``: frontmatter validity (``paths`` field)
- ``CLAUDE.md`` / ``.claude/CLAUDE.md``: absolute home paths in content,
  secret patterns, import targets

Elements are validated only if their containing folder (or the file
itself) is git-tracked under the given project root. Non-git-tracked
elements are the concern of ``validate_local_scope.py``.

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
from pathlib import Path
from typing import Any

from cc_scope_rules import (
    GLOBAL_CONFIG_KEYS,
    MANAGED_ONLY_KEYS,
    PROJECT_REJECTED_KEYS,
    PROJECT_REJECTED_NESTED_KEYS,
    classify_file_scope,
    classify_folder_scope,
    contains_absolute_home_path,
    find_git_root,
    is_git_tracked,
    is_secret_value,
    looks_like_secret_key_name,
)
from cpv_management_common import load_jsonc
from cpv_validation_common import (
    ValidationReport,
    check_remote_execution_guard,
    print_results_by_level,
    save_report_and_print_summary,
)

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


def _flag_secrets_in_env(data: dict[str, Any], report: ValidationReport, file_label: str) -> None:
    """Scan the ``env`` block for literal secrets."""
    env = data.get("env")
    if not isinstance(env, dict):
        return
    for key, value in env.items():
        if not isinstance(key, str):
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
    """Emit a MINOR if ``value`` is a string containing an absolute home path."""
    if isinstance(value, str) and contains_absolute_home_path(value):
        report.minor(
            (
                f"settings.json {label} contains an absolute home path ('{value}') — "
                "this will break for other team members. Use $CLAUDE_PROJECT_DIR "
                "or a relative path instead."
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
) -> None:
    """Apply project-scope rules to ``.claude/settings.json`` contents."""
    file_label = ".claude/settings.json"
    try:
        data = load_jsonc(settings_path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        report.critical(f"settings.json: parse error: {exc}", file_label)
        return
    if not isinstance(data, dict):
        report.critical("settings.json root must be a JSON object", file_label)
        return

    _flag_rejected_top_level_keys(data, report, file_label)
    _flag_rejected_nested_keys(data, report, file_label)
    _flag_managed_only_keys(data, report, file_label)
    _flag_global_config_keys(data, report, file_label)
    _flag_secrets_in_env(data, report, file_label)
    _flag_machine_specific_command_paths(data, report, file_label)
    _flag_hook_command_paths(data, report, file_label)
    _flag_additional_directories(data, report, file_label)
    _flag_claude_md_excludes(data, report, file_label)
    _flag_missing_schema(data, report, file_label)

    if not report.has_critical and not report.has_major and not report.has_minor:
        report.passed("settings.json project-scope rules OK", file_label)


# =============================================================================
# .mcp.json
# =============================================================================


def validate_mcp_json_project_scope(mcp_path: Path, report: ValidationReport) -> None:
    """Apply project-scope rules to a ``.mcp.json`` at the repo root."""
    file_label = ".mcp.json"
    try:
        data = load_jsonc(mcp_path)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        report.critical(f".mcp.json: parse error: {exc}", file_label)
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


def _parse_frontmatter(content: str) -> tuple[dict[str, Any] | None, str]:
    """Split YAML frontmatter from markdown body.

    Returns ``(frontmatter_dict_or_None, body)``. If the file has no
    frontmatter, returns ``(None, content)``.
    """
    if not content.startswith("---"):
        return None, content
    lines = content.splitlines()
    if len(lines) < 2:
        return None, content
    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None, content
    fm_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1 :])
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return None, body
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return None, body
    return (data if isinstance(data, dict) else None), body


def _validate_markdown_file_shared(
    path: Path, report: ValidationReport, rel_label: str, forbid_home_paths: bool
) -> None:
    """Shared frontmatter + home-path scan for project-scope markdown files."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.critical(f"{rel_label}: cannot read: {exc}", rel_label)
        return
    frontmatter, body = _parse_frontmatter(content)
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


def validate_agents_folder(
    agents_dir: Path, repo_root: Path, report: ValidationReport
) -> None:
    """Validate every tracked ``*.md`` file in ``.claude/agents/``."""
    for agent_file in sorted(agents_dir.rglob("*.md")):
        if not is_git_tracked(agent_file, repo_root):
            continue
        rel = agent_file.relative_to(repo_root)
        _validate_markdown_file_shared(agent_file, report, str(rel), forbid_home_paths=True)


def validate_skills_folder(
    skills_dir: Path, repo_root: Path, report: ValidationReport
) -> None:
    """Validate every tracked ``SKILL.md`` in ``.claude/skills/``."""
    for skill_file in sorted(skills_dir.rglob("SKILL.md")):
        if not is_git_tracked(skill_file, repo_root):
            continue
        rel = skill_file.relative_to(repo_root)
        _validate_markdown_file_shared(skill_file, report, str(rel), forbid_home_paths=True)


def validate_commands_folder(
    commands_dir: Path, repo_root: Path, report: ValidationReport
) -> None:
    """Validate every tracked ``*.md`` file in ``.claude/commands/``."""
    for cmd_file in sorted(commands_dir.rglob("*.md")):
        if not is_git_tracked(cmd_file, repo_root):
            continue
        rel = cmd_file.relative_to(repo_root)
        _validate_markdown_file_shared(cmd_file, report, str(rel), forbid_home_paths=True)


def validate_rules_folder(
    rules_dir: Path, repo_root: Path, report: ValidationReport
) -> None:
    """Validate every tracked ``*.md`` file in ``.claude/rules/``."""
    for rule_file in sorted(rules_dir.rglob("*.md")):
        if not is_git_tracked(rule_file, repo_root):
            continue
        rel = rule_file.relative_to(repo_root)
        # Rules are simpler: frontmatter is optional, just scan body for paths.
        try:
            content = rule_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            report.critical(f"{rel}: cannot read: {exc}", str(rel))
            continue
        if contains_absolute_home_path(content):
            report.minor(
                f"{rel}: rule content contains an absolute home path",
                str(rel),
            )


def validate_claude_md_file(
    md_path: Path, repo_root: Path, report: ValidationReport
) -> None:
    """Validate a CLAUDE.md file (project root or .claude/)."""
    rel = md_path.relative_to(repo_root)
    try:
        content = md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.critical(f"{rel}: cannot read: {exc}", str(rel))
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


def validate_gitignore_for_scope_hygiene(repo_root: Path, report: ValidationReport) -> None:
    """Informational: recommend gitignore entries for local-scope files."""
    gitignore = repo_root / ".gitignore"
    if not gitignore.exists():
        return
    try:
        content = gitignore.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    lines = {ln.strip() for ln in content.splitlines() if ln.strip() and not ln.strip().startswith("#")}
    if not _gitignore_covers_settings_local(lines):
        report.info(
            ".gitignore does not cover '.claude/settings.local.json' — "
            "Claude Code auto-adds it on first creation, but pinning is safer.",
            ".gitignore",
        )
    if not _gitignore_covers_claude_local_md(lines):
        report.info(
            ".gitignore does not cover 'CLAUDE.local.md' — pin it to prevent "
            "accidental commits of personal memory notes.",
            ".gitignore",
        )


# =============================================================================
# Orchestrator
# =============================================================================


def validate_project_scope(project_root: Path, report: ValidationReport) -> None:
    """Walk the project tree and validate every git-tracked Claude Code element.

    Behaviour:

    - If the project has no ``.git`` ancestor, emits a WARNING and skips
      project-scope validation (no files can be classified as tracked).
    - If ``.claude/`` does not exist, emits an INFO and validates only
      ``.mcp.json`` / ``CLAUDE.md`` at the project root.
    """
    if not project_root.exists() or not project_root.is_dir():
        report.critical(f"Project path does not exist or is not a directory: {project_root}", str(project_root))
        return

    repo_root = find_git_root(project_root) or project_root
    if not (repo_root / ".git").exists():
        report.warning(
            "Not a git repository — no files can be classified as project-scope. "
            "Initialise a git repo or run validate_local_scope instead.",
            str(project_root),
        )
        return

    # 1. .claude/settings.json
    settings_path = project_root / ".claude" / "settings.json"
    if classify_file_scope(settings_path, repo_root) == "project":
        validate_settings_json_project_scope(settings_path, report)
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

    # 3. .claude/agents/
    agents_dir = project_root / ".claude" / "agents"
    if classify_folder_scope(agents_dir, repo_root) == "project":
        validate_agents_folder(agents_dir, repo_root, report)

    # 4. .claude/skills/
    skills_dir = project_root / ".claude" / "skills"
    if classify_folder_scope(skills_dir, repo_root) == "project":
        validate_skills_folder(skills_dir, repo_root, report)

    # 5. .claude/commands/
    commands_dir = project_root / ".claude" / "commands"
    if classify_folder_scope(commands_dir, repo_root) == "project":
        validate_commands_folder(commands_dir, repo_root, report)

    # 6. .claude/rules/
    rules_dir = project_root / ".claude" / "rules"
    if classify_folder_scope(rules_dir, repo_root) == "project":
        validate_rules_folder(rules_dir, repo_root, report)

    # 7. CLAUDE.md (project root or .claude/)
    for md_candidate in (project_root / "CLAUDE.md", project_root / ".claude" / "CLAUDE.md"):
        if classify_file_scope(md_candidate, repo_root) == "project":
            validate_claude_md_file(md_candidate, repo_root, report)

    # 8. .gitignore hygiene
    validate_gitignore_for_scope_hygiene(repo_root, report)

    if not report.results:
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
