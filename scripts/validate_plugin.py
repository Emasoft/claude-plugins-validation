#!/usr/bin/env python3
"""
Claude Code Plugin Validator

Comprehensive validation suite for Claude Code plugins.
Validates structure, manifest, hooks, skills, scripts, and MCP servers.

Usage:
    uv run python scripts/validate_plugin.py /path/to/plugin
    uv run python scripts/validate_plugin.py --verbose
    uv run python scripts/validate_plugin.py --json
    uv run python scripts/validate_plugin.py --marketplace-only
    uv run python scripts/validate_plugin.py --skip-platform-checks windows

Flags:
    --marketplace-only: Skip plugin.json requirement for marketplace-only
                        distribution (strict=false). When using strict=false,
                        plugin.json should NOT exist (causes CLI issues).

    --skip-platform-checks: Skip platform-specific checks.
                        Valid platforms: windows, macos, linux
                        Use without args to skip all platform checks.
                        Example: --skip-platform-checks windows
                        Example: --skip-platform-checks (skips all)

Exit codes:
    0 - All checks passed (or only INFO/PASSED/WARNING/NIT)
    1 - CRITICAL issues found
    2 - MAJOR issues found
    3 - MINOR issues found
    4 - NIT issues found (--strict mode only)
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import yaml
from cpv_validation_common import (
    COLORS,
    ValidationReport,
    check_remote_execution_guard,
    resolve_tool_command,
    save_report_and_print_summary,
    validate_component_name,
    validate_md_file_paths,
    validate_md_urls,
    validate_no_absolute_paths,
    validate_plugin_shipped_restrictions,
    validate_toc_embedding,
)
from detect_language import detect_languages
from detect_lockfiles import detect_lockfiles
from gitignore_filter import GitignoreFilter
from validate_hook import (
    lint_bash_script,
    lint_js_script,
)
from validate_hook import (
    validate_hooks as validate_hook_file,
)
from validate_mcp import validate_plugin_mcp
from validate_rules import validate_rules_directory

# Import comprehensive skill validator (190+ rules from AgentSkills OpenSpec, Nixtla, Meta-Skills)
from validate_skill_comprehensive import validate_skill as validate_skill_comprehensive

IS_WINDOWS = platform.system() == "Windows"

# Module-level gitignore filter — initialized in main(), used by scan functions
_gi: GitignoreFilter | None = None


def validate_manifest(
    plugin_root: Path, report: ValidationReport, marketplace_only: bool = False
) -> dict[str, Any] | None:
    """Validate plugin.json manifest.

    Args:
        plugin_root: Path to the plugin directory
        report: ValidationReport to add results to
        marketplace_only: If True, skip plugin.json requirement

    Returns:
        The manifest dict if valid, None otherwise
    """
    manifest_path = plugin_root / ".claude-plugin" / "plugin.json"

    if not manifest_path.exists():
        if marketplace_only:
            msg = "plugin.json correctly absent (marketplace-only, strict=false)"
            report.passed(msg, ".claude-plugin/plugin.json")
            return None
        report.critical("plugin.json not found", ".claude-plugin/plugin.json")
        return None

    if marketplace_only:
        report.major(
            "plugin.json EXISTS but should NOT for marketplace-only (strict=false). Remove .claude-plugin/plugin.json to fix CLI uninstall issues.",
            ".claude-plugin/plugin.json",
        )
        return None

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        report.critical(f"Invalid JSON in plugin.json: {e}", ".claude-plugin/plugin.json")
        return None

    report.passed("plugin.json is valid JSON", ".claude-plugin/plugin.json")

    # Required field: name (per Anthropic docs, ONLY 'name' is required)
    if "name" not in manifest:
        report.critical(
            "Missing required field 'name' in plugin.json",
            ".claude-plugin/plugin.json",
        )
    else:
        report.passed("Required field 'name' present", ".claude-plugin/plugin.json")

    # Recommended fields
    recommended_fields = ["version", "description"]
    for fld in recommended_fields:
        if fld not in manifest:
            report.minor(
                f"Missing recommended field '{fld}' in plugin.json",
                ".claude-plugin/plugin.json",
            )
        else:
            report.passed(
                f"Recommended field '{fld}' present",
                ".claude-plugin/plugin.json",
            )

    # Name validation — uses shared validate_component_name for uniform rules
    if "name" in manifest:
        name = manifest["name"]
        if isinstance(name, str):
            validate_component_name(name, "plugin", report)

    # Version validation — guard against non-string values (e.g. "version": 123)
    if "version" in manifest:
        version = manifest["version"]
        if not isinstance(version, str):
            report.major(
                f"Version must be a string, got {type(version).__name__}: {version}",
                ".claude-plugin/plugin.json",
            )
        elif not re.match(r"^\d+\.\d+\.\d+", version):
            report.major(
                f"Version must be semver format: {version}",
                ".claude-plugin/plugin.json",
            )

    # Check for unknown fields — warn but don't block, as custom fields
    # may be consumed by plugin scripts or external tooling
    known_fields = {
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "commands",
        "agents",
        "skills",
        "hooks",
        "mcpServers",
        "outputStyles",
        "lspServers",
        "userConfig",  # User-configurable values prompted at enable time (v2.1.80)
        "channels",  # Channel declarations for message injection (v2.1.85)
    }
    for key in manifest.keys():
        if key not in known_fields:
            report.warning(
                f"Unknown manifest field '{key}' — not part of the Claude Code plugin spec. If used by plugin scripts, consider documenting it.",
                ".claude-plugin/plugin.json",
            )

    # Validate repository field type — Claude Code requires a string URL, not an object
    if "repository" in manifest:
        repo_val = manifest["repository"]
        if not isinstance(repo_val, str):
            report.major(
                f'Field \'repository\' must be a string URL (e.g. "https://github.com/user/repo"), not {type(repo_val).__name__}. Claude Code rejects object format like {{"type":"git","url":"..."}}.',
                ".claude-plugin/plugin.json",
            )

    # Validate author field structure
    if "author" in manifest:
        author = manifest["author"]
        if isinstance(author, str):
            report.passed("Author is a string (acceptable)", ".claude-plugin/plugin.json")
        elif isinstance(author, dict):
            if "name" not in author:
                report.major(
                    "'author' object missing required 'name' field",
                    ".claude-plugin/plugin.json",
                )
            elif not isinstance(author["name"], str):
                report.major(
                    "'author.name' must be a string",
                    ".claude-plugin/plugin.json",
                )
            else:
                report.passed("Author object has valid 'name' field", ".claude-plugin/plugin.json")
        else:
            report.major(
                f"'author' must be a string or object, got {type(author).__name__}",
                ".claude-plugin/plugin.json",
            )

    # Validate keywords field
    if "keywords" in manifest:
        kw = manifest["keywords"]
        if not isinstance(kw, list):
            report.major("'keywords' must be an array", ".claude-plugin/plugin.json")
        elif not all(isinstance(k, str) for k in kw):
            report.major("'keywords' must contain only strings", ".claude-plugin/plugin.json")
        else:
            report.passed(f"Keywords: {len(kw)} keyword(s)", ".claude-plugin/plugin.json")

    # Validate homepage and license field types
    for string_field in ("homepage", "license"):
        if string_field in manifest:
            val = manifest[string_field]
            if not isinstance(val, str):
                report.major(
                    f"'{string_field}' must be a string, got {type(val).__name__}",
                    ".claude-plugin/plugin.json",
                )

    # Validate component path fields start with ./
    path_fields = [
        "commands",
        "agents",
        "skills",
        "hooks",
        "mcpServers",
        "outputStyles",
        "lspServers",
    ]
    for key in path_fields:
        if key in manifest:
            value = manifest[key]
            if isinstance(value, str) and not value.startswith("./"):
                report.major(
                    f"Field '{key}' path must start with './': {value}",
                    ".claude-plugin/plugin.json",
                )
            elif isinstance(value, list):
                for i, path in enumerate(value):
                    if not isinstance(path, str):
                        report.major(
                            f"Field '{key}[{i}]' must be a string path, got {type(path).__name__}",
                            ".claude-plugin/plugin.json",
                        )
                    elif not path.startswith("./"):
                        report.major(
                            f"Field '{key}[{i}]' path must start with './': {path}",
                            ".claude-plugin/plugin.json",
                        )
            elif isinstance(value, dict):
                # Inline configuration object - valid for hooks, mcpServers, lspServers
                if key in ("hooks", "mcpServers", "lspServers"):
                    report.passed(
                        f"Field '{key}' uses inline configuration object",
                        ".claude-plugin/plugin.json",
                    )
                else:
                    report.major(
                        f"Field '{key}' must be a string path or array, not an object",
                        ".claude-plugin/plugin.json",
                    )

    # Validate userConfig schema (v2.1.80): keys must be identifiers, each entry needs title + description.
    # Claude Code's runtime validator enforces 'title' as REQUIRED — issue #9 documented a v1.7.0
    # release of token-reporter that passed CPV but failed at install with:
    #   userConfig.<key>.title: Invalid input: expected string, received undefined
    # Mirror the runtime schema strictly to catch this at validation time.
    USERCONFIG_VALID_TYPES = {"string", "number", "integer", "boolean", "array", "object"}
    USERCONFIG_TYPE_TO_PYTHON: dict[str, tuple[type, ...]] = {
        "string": (str,),
        "number": (int, float),
        "integer": (int,),
        "boolean": (bool,),
        "array": (list,),
        "object": (dict,),
    }
    if "userConfig" in manifest:
        uc = manifest["userConfig"]
        if not isinstance(uc, dict):
            report.major(f"'userConfig' must be an object, got {type(uc).__name__}", ".claude-plugin/plugin.json")
        else:
            for key, entry in uc.items():
                if not isinstance(key, str) or not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", key):
                    report.major(f"'userConfig' key '{key}' must be a valid identifier", ".claude-plugin/plugin.json")
                if not isinstance(entry, dict):
                    report.major(
                        f"'userConfig.{key}' must be an object with 'title' and 'description'",
                        ".claude-plugin/plugin.json",
                    )
                    continue
                # title (REQUIRED — runtime rejects the manifest at install time without it)
                if "title" not in entry:
                    report.major(
                        f"'userConfig.{key}' missing required 'title' field — Claude Code runtime "
                        f"rejects this at install time",
                        ".claude-plugin/plugin.json",
                    )
                elif not isinstance(entry["title"], str):
                    report.major(
                        f"'userConfig.{key}.title' must be a string, got {type(entry['title']).__name__}",
                        ".claude-plugin/plugin.json",
                    )
                # description (recommended)
                if "description" not in entry:
                    report.minor(f"'userConfig.{key}' missing 'description' field", ".claude-plugin/plugin.json")
                elif not isinstance(entry["description"], str):
                    report.major(f"'userConfig.{key}.description' must be a string", ".claude-plugin/plugin.json")
                # type (optional, but if present must be valid)
                declared_type: str | None = None
                if "type" in entry:
                    if not isinstance(entry["type"], str):
                        report.major(
                            f"'userConfig.{key}.type' must be a string, got {type(entry['type']).__name__}",
                            ".claude-plugin/plugin.json",
                        )
                    elif entry["type"] not in USERCONFIG_VALID_TYPES:
                        report.major(
                            f"'userConfig.{key}.type' must be one of "
                            f"{sorted(USERCONFIG_VALID_TYPES)}, got {entry['type']!r}",
                            ".claude-plugin/plugin.json",
                        )
                    else:
                        declared_type = entry["type"]
                # default (optional, but if both type and default present, types must match)
                if "default" in entry and declared_type is not None:
                    expected_py_types = USERCONFIG_TYPE_TO_PYTHON.get(declared_type, ())
                    default_value = entry["default"]
                    # bool is a subclass of int — exclude when checking number/integer
                    is_match = isinstance(default_value, expected_py_types)
                    if declared_type in ("number", "integer") and isinstance(default_value, bool):
                        is_match = False
                    if not is_match:
                        report.major(
                            f"'userConfig.{key}.default' type ({type(default_value).__name__}) "
                            f"does not match declared type ({declared_type})",
                            ".claude-plugin/plugin.json",
                        )
                # sensitive (optional, must be bool)
                if "sensitive" in entry and not isinstance(entry["sensitive"], bool):
                    report.major(f"'userConfig.{key}.sensitive' must be a boolean", ".claude-plugin/plugin.json")
            report.passed(f"'userConfig' schema valid: {len(uc)} config(s)", ".claude-plugin/plugin.json")

    # Validate channels schema (v2.1.85): server is required, must match mcpServers key
    if "channels" in manifest:
        ch = manifest["channels"]
        if not isinstance(ch, list):
            report.major(f"'channels' must be an array, got {type(ch).__name__}", ".claude-plugin/plugin.json")
        else:
            mcp_keys = set()
            if "mcpServers" in manifest and isinstance(manifest["mcpServers"], dict):
                mcp_keys = set(manifest["mcpServers"].keys())
            for i, entry in enumerate(ch):
                if not isinstance(entry, dict):
                    report.major(f"'channels[{i}]' must be an object", ".claude-plugin/plugin.json")
                    continue
                if "server" not in entry:
                    report.major(f"'channels[{i}]' missing required 'server' field", ".claude-plugin/plugin.json")
                elif not isinstance(entry["server"], str):
                    report.major(f"'channels[{i}].server' must be a string", ".claude-plugin/plugin.json")
                elif mcp_keys and entry["server"] not in mcp_keys:
                    report.major(
                        f"'channels[{i}].server' = '{entry['server']}' does not match any mcpServers key",
                        ".claude-plugin/plugin.json",
                    )
            if ch:
                report.passed(f"'channels' schema valid: {len(ch)} channel(s)", ".claude-plugin/plugin.json")

    # Validate lspServers required fields (command + extensionToLanguage)
    if "lspServers" in manifest and isinstance(manifest["lspServers"], dict):
        for name, config in manifest["lspServers"].items():
            if isinstance(config, dict):
                if "command" not in config:
                    report.major(f"LSP server '{name}' missing required 'command' field", ".claude-plugin/plugin.json")
                if "extensionToLanguage" not in config:
                    report.major(
                        f"LSP server '{name}' missing required 'extensionToLanguage' field",
                        ".claude-plugin/plugin.json",
                    )
                elif isinstance(config["extensionToLanguage"], dict):
                    for ext in config["extensionToLanguage"]:
                        if not ext.startswith("."):
                            report.minor(
                                f"LSP server '{name}' extensionToLanguage key '{ext}' should start with '.'",
                                ".claude-plugin/plugin.json",
                            )

    # Claude Code auto-discovers standard directories (commands/, agents/, skills/,
    # hooks/) without needing them declared in plugin.json. Declaring the default path
    # causes Claude Code to reject the manifest as malformed — the plugin won't load.
    auto_discovered_defaults = {
        "commands": "./commands/",
        "agents": "./agents/",
        "skills": "./skills/",
        "hooks": "./hooks/",
        "outputStyles": "./output-styles/",  # Also auto-discovered per official docs
    }
    for key, default_path in auto_discovered_defaults.items():
        if key in manifest:
            value = manifest[key]
            # String pointing to the default directory breaks plugin loading
            if isinstance(value, str):
                normalized = value.replace("\\", "/").rstrip("/") + "/"
                if normalized == default_path:
                    report.critical(
                        f"Field '{key}' points to '{default_path}' which is auto-discovered by Claude Code. This causes a malformed manifest error and the plugin will not load. Remove it from plugin.json — only non-standard paths need explicit declaration.",
                        ".claude-plugin/plugin.json",
                    )
            # Array of files inside the default directory also breaks plugin loading
            elif isinstance(value, list) and all(isinstance(p, str) and p.startswith(default_path) for p in value):
                report.critical(
                    f"Field '{key}' lists files inside '{default_path}' which is auto-discovered by Claude Code. This causes a malformed manifest error and the plugin will not load. Remove it from plugin.json.",
                    ".claude-plugin/plugin.json",
                )

    # Check for duplicate hooks loading — Claude Code auto-discovers hooks/hooks.json,
    # so explicitly pointing to it in plugin.json causes it to load twice (runtime error)
    hooks_value = manifest.get("hooks")
    if isinstance(hooks_value, str):
        normalized_hooks = hooks_value.replace("\\", "/")
        if normalized_hooks in ("./hooks/hooks.json", "hooks/hooks.json"):
            report.warning(
                "Field 'hooks' points to './hooks/hooks.json' which Claude Code already auto-loads by convention. "
                "This causes a 'Duplicate hooks file detected' runtime error. Remove the 'hooks' field from plugin.json "
                "or use a non-default path.",
                ".claude-plugin/plugin.json",
            )

    return cast(dict[str, Any], manifest)


def validate_structure(plugin_root: Path, report: ValidationReport, marketplace_only: bool = False) -> None:
    """Validate plugin directory structure.

    Args:
        plugin_root: Path to the plugin directory
        report: ValidationReport to add results to
        marketplace_only: If True, .claude-plugin directory is optional
    """
    claude_plugin_dir = plugin_root / ".claude-plugin"
    if not claude_plugin_dir.is_dir():
        if marketplace_only:
            msg = ".claude-plugin absent (marketplace-only, uses marketplace.json)"
            report.passed(msg)
        else:
            report.critical(".claude-plugin directory not found")
            return
    else:
        report.passed(".claude-plugin directory exists")

    # Components must be at root, NOT in .claude-plugin
    for component in ["commands", "agents", "skills", "hooks", "scripts", "schemas", "bin"]:
        wrong_path = plugin_root / ".claude-plugin" / component
        if wrong_path.exists():
            report.critical(f"{component}/ must be at plugin root, not in .claude-plugin/")

    # Common directories
    common_dirs = {
        "commands": "INFO",
        "agents": "INFO",
        "skills": "INFO",
        "hooks": "INFO",
        "scripts": "INFO",
        "docs": "INFO",
        "output-styles": "INFO",
    }

    for d, level in common_dirs.items():
        if (plugin_root / d).is_dir():
            report.passed(f"{d}/ directory exists")
        else:
            if level == "INFO":
                report.info(f"Optional directory {d}/ not found")
            else:
                report.minor(f"Directory {d}/ not found")

    # Check for non-standard directories — warn but don't block, since users
    # may add folders like libs/, modules/, resources/ needed by scripts
    known_dirs = {
        ".claude-plugin",
        ".git",
        ".jj",  # Jujutsu VCS metadata (v2.1.86)
        ".sl",  # Sapling VCS metadata (v2.1.86)
        ".github",
        "commands",
        "agents",
        "skills",
        "hooks",
        "scripts",
        "docs",
        "rules",
        "schemas",
        "bin",
        "templates",
        "tests",
        # Common non-standard but legitimate dirs
        "lib",
        "libs",
        "modules",
        "resources",
        "assets",
        "data",
        "config",
        "configs",
        "examples",
        "samples",
        "references",
        # Developer tooling dirs
        "git-hooks",
        "shared",
        "fixtures",
        "vendor",
        "src",
        "dist",
        "build",
        "out",
        "target",
        "output-styles",
        "design",  # TRDD design docs (design/tasks/)
    }
    # Also skip hidden dirs and _dev dirs
    for item in plugin_root.iterdir():
        if not item.is_dir():
            continue
        dirname = item.name
        if dirname.startswith(".") or dirname.endswith("_dev"):
            continue
        if dirname.lower() not in known_dirs:
            report.warning(
                f"Non-standard directory '{dirname}/' — not part of the plugin spec. If needed by plugin scripts, consider documenting its purpose in README."
            )

    # Validate plugin-shipped settings.json if present
    settings_path = plugin_root / "settings.json"
    if settings_path.exists():
        try:
            settings_data = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(settings_data, dict):
                report.major("settings.json: root must be a JSON object", "settings.json")
            else:
                # "agent" is the primary plugin-level setting; "extraKnownMarketplaces"
                # is the v2.1.80 inline-marketplace declaration validated separately below.
                recognized_keys = {"agent", "extraKnownMarketplaces"}
                has_unrecognized = False
                for key in settings_data:
                    if key not in recognized_keys:
                        has_unrecognized = True
                        report.minor(
                            f"settings.json: unrecognized key '{key}' — supported plugin settings: {', '.join(sorted(recognized_keys))}",
                            "settings.json",
                        )
                # Validate 'agent' value references a real agent file
                if "agent" in settings_data:
                    agent_val = settings_data["agent"]
                    if isinstance(agent_val, str) and agent_val:
                        agents_dir = plugin_root / "agents"
                        agent_file = agents_dir / f"{agent_val}.md"
                        if agents_dir.is_dir() and not agent_file.is_file():
                            report.minor(
                                f"settings.json 'agent' value '{agent_val}' does not match any agent file in agents/",
                                "settings.json",
                            )
                    elif not isinstance(agent_val, str):
                        report.major(
                            f"settings.json 'agent' must be a string, got {type(agent_val).__name__}", "settings.json"
                        )
                # v2.1.80+: validate extraKnownMarketplaces block by delegating to the
                # dedicated settings-marketplace validator. Results are merged into this
                # plugin report so all findings land in a single report.
                if "extraKnownMarketplaces" in settings_data:
                    from validate_settings_marketplace import validate_settings_marketplace_file

                    sm_report = validate_settings_marketplace_file(settings_path)
                    report.merge(sm_report)
                if not has_unrecognized:
                    report.passed("settings.json is valid", "settings.json")
                else:
                    report.passed("settings.json is parseable JSON", "settings.json")
        except json.JSONDecodeError as e:
            report.major(f"settings.json: JSON parse error: {e}", "settings.json")

    # Check that plugin has at least some actual content beyond just a manifest
    content_indicators = ["commands", "skills", "agents", "hooks", "scripts", "output-styles"]
    file_indicators = [".mcp.json", ".lsp.json"]
    has_content = any((plugin_root / d).is_dir() for d in content_indicators) or any(
        (plugin_root / f).exists() for f in file_indicators
    )
    if not has_content:
        report.major(
            "Plugin has a manifest but no content — expected at least one of: commands/, skills/, agents/, hooks/, scripts/, .mcp.json, or .lsp.json",
            ".claude-plugin/plugin.json",
        )

    # Check pyproject.toml for Python plugins
    has_py_scripts = (plugin_root / "scripts").is_dir() and any((plugin_root / "scripts").glob("*.py"))
    if has_py_scripts:
        if (plugin_root / "pyproject.toml").exists():
            report.passed("pyproject.toml exists")
        else:
            report.minor("pyproject.toml not found — recommended for Python plugins")
        if (plugin_root / ".python-version").exists():
            report.passed(".python-version exists")
        else:
            report.warning(".python-version not found — recommended for reproducible builds")


def validate_commands(plugin_root: Path, report: ValidationReport) -> None:
    """Validate command definitions."""
    commands_dir = plugin_root / "commands"

    if not commands_dir.is_dir():
        report.info("No commands/ directory found")
        return

    # Find all command files
    cmd_files = list(commands_dir.glob("*.md"))
    if not cmd_files:
        report.info("No command files (*.md) found in commands/")
        return

    report.info(f"Found {len(cmd_files)} command file(s)")

    for cmd_path in cmd_files:
        validate_command_file(cmd_path, report)


def validate_command_file(cmd_path: Path, report: ValidationReport) -> None:
    """Validate a single command file."""
    rel_path = f"commands/{cmd_path.name}"
    content = cmd_path.read_text(encoding="utf-8")

    # Check frontmatter
    if not content.startswith("---"):
        report.critical("No frontmatter in command file", rel_path)
        return

    try:
        parts = content.split("---", 2)
        if len(parts) < 3:
            report.critical("Malformed frontmatter (missing closing ---)", rel_path)
            return

        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        report.critical(f"Invalid YAML frontmatter: {e}", rel_path)
        return

    if not frontmatter:
        report.critical("Empty frontmatter", rel_path)
        return

    report.passed("Valid YAML frontmatter", rel_path)

    # Required fields
    if "name" not in frontmatter:
        report.critical("Missing 'name' in frontmatter", rel_path)
    else:
        expected_name = cmd_path.stem
        if frontmatter["name"] != expected_name:
            report.major(
                f"Command name '{frontmatter['name']}' doesn't match filename '{expected_name}'",
                rel_path,
            )

    if "description" not in frontmatter:
        report.major("Missing 'description' in frontmatter", rel_path)


def validate_agents(plugin_root: Path, report: ValidationReport) -> None:
    """Validate agent definitions."""
    agents_dir = plugin_root / "agents"

    if not agents_dir.is_dir():
        report.info("No agents/ directory found")
        return

    # Find all agent files
    agent_files = list(agents_dir.glob("*.md"))
    if not agent_files:
        report.info("No agent files (*.md) found in agents/")
        return

    report.info(f"Found {len(agent_files)} agent file(s)")

    for agent_path in agent_files:
        validate_agent_file(agent_path, report)


def validate_agent_file(agent_path: Path, report: ValidationReport) -> None:
    """Validate a single agent file."""
    rel_path = f"agents/{agent_path.name}"
    content = agent_path.read_text(encoding="utf-8")

    # Check frontmatter
    if not content.startswith("---"):
        report.critical("No frontmatter in agent file", rel_path)
        return

    try:
        parts = content.split("---", 2)
        if len(parts) < 3:
            report.critical("Malformed frontmatter (missing closing ---)", rel_path)
            return

        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        report.critical(f"Invalid YAML frontmatter: {e}", rel_path)
        return

    if not frontmatter:
        report.critical("Empty frontmatter", rel_path)
        return

    report.passed("Valid YAML frontmatter", rel_path)

    # Required fields for agents
    if "name" not in frontmatter:
        report.critical("Missing 'name' in frontmatter", rel_path)

    if "description" not in frontmatter:
        report.major("Missing 'description' in frontmatter", rel_path)

    # Plugin agents do NOT support hooks, mcpServers, or permissionMode (per official spec).
    # These are security restrictions — only project agents (.claude/agents/) can use them.
    # Uses the shared helper from cpv_validation_common so validate_agent.py and this
    # orchestrator call path emit identical messages.
    validate_plugin_shipped_restrictions(frontmatter, rel_path, report, is_plugin_shipped=True)

    # Validate TOC embedding — agent files must embed TOCs from referenced .md files
    validate_toc_embedding(content, agent_path, agent_path.parent, report)


def validate_hooks(plugin_root: Path, report: ValidationReport) -> None:
    """Validate hook configuration using comprehensive hook validator."""
    hooks_dir = plugin_root / "hooks"

    if not hooks_dir.is_dir():
        report.info("No hooks/ directory found")
        return

    hooks_json = hooks_dir / "hooks.json"
    if not hooks_json.exists():
        report.info("No hooks.json found")
        return

    # Use comprehensive hook validator
    hook_report = validate_hook_file(hooks_json, plugin_root)

    # Transfer all results to main report
    for result in hook_report.results:
        file_path = result.file
        if file_path:
            if file_path.startswith(str(plugin_root)):
                file_path = file_path[len(str(plugin_root)) + 1 :]
            if not file_path.startswith("hooks/"):
                file_path = f"hooks/{file_path}"
        else:
            file_path = "hooks/hooks.json"

        report.add(result.level, result.message, file_path, result.line)


def validate_mcp(plugin_root: Path, report: ValidationReport) -> None:
    """Validate MCP server configurations."""
    # Use comprehensive MCP validator
    mcp_report = validate_plugin_mcp(plugin_root)

    # Transfer all results to main report
    for result in mcp_report.results:
        report.add(result.level, result.message, result.file, result.line)


def _has_shebang(path: Path) -> bool:
    """Check if a file starts with a shebang (#!) line."""
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"#!"
    except Exception:
        return False


def validate_scripts(plugin_root: Path, report: ValidationReport) -> None:
    """Validate all scripts in scripts/ — Python, Shell, JS/TS, PowerShell, Go, Rust."""
    scripts_dir = plugin_root / "scripts"

    if not scripts_dir.is_dir():
        report.info("No scripts/ directory found")
        return

    # When running via remote_validation.py, don't use target's config files
    # for linters — the remote launcher provides its own safe config via env vars
    is_remote = os.environ.get("CPV_REMOTE_VALIDATION") == "1"

    # --- Python scripts (.py) ---
    py_files = list(scripts_dir.glob("*.py"))
    if py_files:
        ruff_cmd = resolve_tool_command("ruff")
        if ruff_cmd:
            ruff_args = ruff_cmd + ["check", "--select", "E,F,W", "--ignore", "E501", "--output-format=concise"]
            if not is_remote:
                pyproject = plugin_root / "pyproject.toml"
                if pyproject.exists():
                    ruff_args.extend(["--config", str(pyproject)])
            ruff_args.extend([str(f) for f in py_files])
            try:
                result = subprocess.run(ruff_args, capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                report.warning("Ruff timed out after 60s — skipping lint check")
                result = None
            if result is not None and result.returncode == 0:
                report.passed(f"Ruff check passed for {len(py_files)} Python files")
            elif result is not None:
                errors_by_file: dict[str, int] = {}
                for ruff_line in result.stdout.strip().split("\n"):
                    if ruff_line and ":" in ruff_line:
                        file_part = ruff_line.split(":")[0].strip()
                        if file_part:
                            errors_by_file[file_part] = errors_by_file.get(file_part, 0) + 1
                for file_path_str, count in sorted(errors_by_file.items()):
                    rel = file_path_str
                    try:
                        rel = str(Path(file_path_str).relative_to(plugin_root))
                    except ValueError:
                        pass
                    report.major(f"Ruff: {count} error(s) in {rel}", rel)
                if not errors_by_file and result.stdout.strip():
                    report.major("Ruff: error(s) across script files")
        else:
            report.minor("ruff not available locally or via uvx, skipping Python lint check")

        mypy_cmd = resolve_tool_command("mypy")
        if mypy_cmd:
            mypy_args = mypy_cmd + ["--ignore-missing-imports"]
            if not is_remote:
                pyproject = plugin_root / "pyproject.toml"
                if pyproject.exists():
                    mypy_args.extend(["--config-file", str(pyproject)])
            mypy_args.extend([str(f) for f in py_files])
            try:
                result = subprocess.run(mypy_args, capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                report.warning("Mypy timed out after 60s — skipping type check")
                result = None
            if result is not None and result.returncode == 0:
                report.passed(f"Mypy check passed for {len(py_files)} Python files")
            elif result is not None:
                for line in result.stdout.strip().split("\n"):
                    if not line or line.startswith("Success") or line.startswith("Found"):
                        continue
                    report.minor(f"Mypy: {line}")
        else:
            report.minor("mypy not available locally or via uvx, skipping type check")

    # --- Shell scripts (.sh, .bash) ---
    sh_files = list(scripts_dir.glob("*.sh")) + list(scripts_dir.glob("*.bash"))
    for sh_file in sh_files:
        if not os.access(sh_file, os.X_OK):
            report.major(f"Shell script not executable: {sh_file.name}", f"scripts/{sh_file.name}")
        else:
            report.passed(f"Shell script executable: {sh_file.name}", f"scripts/{sh_file.name}")
        # Delegate to validate_hook.py's lint function (shellcheck with JSON parsing)
        lint_bash_script(sh_file, report)

    # --- JavaScript/TypeScript scripts (.js, .ts, .mjs, .cjs) ---
    js_files = [f for f in scripts_dir.iterdir() if f.is_file() and f.suffix.lower() in {".js", ".ts", ".mjs", ".cjs"}]
    for js_file in js_files:
        lint_js_script(js_file, report)

    # --- PowerShell scripts (.ps1, .psm1) ---
    ps_files = [f for f in scripts_dir.iterdir() if f.is_file() and f.suffix.lower() in {".ps1", ".psm1"}]
    if ps_files:
        pssa_cmd = resolve_tool_command("PSScriptAnalyzer")
        if pssa_cmd:
            for ps_file in ps_files:
                try:
                    result = subprocess.run(
                        pssa_cmd + ["-Path", str(ps_file), "-Severity", "Error,Warning"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                except subprocess.TimeoutExpired:
                    report.warning(f"PSScriptAnalyzer timed out on {ps_file.name}")
                    continue
                if result.returncode == 0 and not result.stdout.strip():
                    report.passed(f"PSScriptAnalyzer passed: {ps_file.name}")
                elif result.stdout.strip():
                    for line in result.stdout.strip().split("\n")[:5]:
                        report.minor(f"PSScriptAnalyzer: {line.strip()}", f"scripts/{ps_file.name}")
        else:
            report.info("PSScriptAnalyzer not available, skipping PowerShell lint")

    # --- Go scripts (.go) ---
    go_files = list(scripts_dir.glob("*.go"))
    if go_files:
        go_bin = shutil.which("go")
        if go_bin:
            for go_file in go_files:
                try:
                    result = subprocess.run(
                        [go_bin, "vet", str(go_file)],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                except subprocess.TimeoutExpired:
                    report.warning(f"go vet timed out on {go_file.name}")
                    continue
                if result.returncode == 0:
                    report.passed(f"go vet passed: {go_file.name}")
                else:
                    for line in (result.stderr or result.stdout).strip().split("\n")[:5]:
                        if line.strip():
                            report.minor(f"go vet: {line.strip()}", f"scripts/{go_file.name}")
        else:
            report.info("go not available, skipping Go lint")

    # --- Rust scripts (check for Cargo.toml in scripts/) ---
    if (scripts_dir / "Cargo.toml").exists():
        cargo_bin = shutil.which("cargo")
        if cargo_bin:
            try:
                result = subprocess.run(
                    [cargo_bin, "check", "--manifest-path", str(scripts_dir / "Cargo.toml")],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                report.warning("cargo check timed out")
                result = None
            if result is not None and result.returncode == 0:
                report.passed("cargo check passed for Rust scripts")
            elif result is not None:
                for line in result.stderr.strip().split("\n")[:5]:
                    if "error" in line.lower():
                        report.minor(f"cargo: {line.strip()}", "scripts/Cargo.toml")
        else:
            report.info("cargo not available, skipping Rust lint")

    # Check Python scripts with shebang are executable (Unix only)
    if not IS_WINDOWS:
        if scripts_dir.is_dir():
            for py_file in scripts_dir.glob("*.py"):
                if _has_shebang(py_file) and not os.access(py_file, os.X_OK):
                    report.warning(
                        f"scripts/{py_file.name} has shebang but is not executable — run: chmod +x scripts/{py_file.name}",
                        f"scripts/{py_file.name}",
                    )

    # Check shebangs on script files — scripts without shebangs may not run cross-platform
    shebang_extensions = {".py", ".sh", ".bash", ".rb", ".pl", ".php"}
    # __init__.py and _-prefixed files are module markers/internal modules — never need shebangs
    all_scripts = [
        f
        for f in scripts_dir.iterdir()
        if f.is_file()
        and f.suffix.lower() in shebang_extensions
        and f.name != "__init__.py"
        and not f.stem.startswith("_")
    ]
    scripts_missing_shebang = []
    for script in all_scripts:
        try:
            with open(script, errors="replace") as f:
                first_line = f.readline().rstrip("\n")
            if not first_line.startswith("#!"):
                scripts_missing_shebang.append(script.name)
        except (OSError, UnicodeDecodeError):
            pass
    if scripts_missing_shebang:
        report.minor(
            f"Scripts missing shebang (e.g. #!/usr/bin/env python3): {', '.join(sorted(scripts_missing_shebang))}. Without a shebang, scripts may not run correctly across platforms.",
            "scripts/",
        )


# =============================================================================
# Cross-Platform Compatibility Validation
# =============================================================================

# Script extensions and their platform availability
# Each entry: extension -> (language_name, available_platforms, notes)
SCRIPT_PLATFORM_MAP: dict[str, tuple[str, set[str], str]] = {
    ".sh": ("Bash/Shell", {"macos", "linux"}, "Not natively available on Windows"),
    ".bash": ("Bash", {"macos", "linux"}, "Not natively available on Windows"),
    ".zsh": ("Zsh", {"macos"}, "Not standard on Linux or Windows"),
    ".fish": ("Fish shell", set(), "Requires separate installation on all platforms"),
    ".ps1": ("PowerShell", {"windows"}, "Requires pwsh installation on macOS/Linux"),
    ".bat": ("Windows Batch", {"windows"}, "Not available on macOS or Linux"),
    ".cmd": ("Windows Batch", {"windows"}, "Not available on macOS or Linux"),
    ".nix": ("Nix", {"linux"}, "Not standard on macOS or Windows"),
}

# Cross-platform script languages (available everywhere with standard install)
CROSSPLATFORM_EXTENSIONS = {
    ".py",  # Python — widely available
    ".js",  # Node.js — widely available
    ".ts",  # TypeScript (via tsx/ts-node) — widely available
    ".mjs",  # ES module JavaScript
    ".cjs",  # CommonJS JavaScript
    ".rb",  # Ruby — often pre-installed on macOS
}

# Compiled binary extensions by platform
BINARY_PLATFORM_SUFFIXES: dict[str, str] = {
    # macOS
    "-darwin-arm64": "macOS ARM64 (Apple Silicon)",
    "-darwin-amd64": "macOS x86_64 (Intel)",
    "-darwin-x86_64": "macOS x86_64 (Intel)",
    "-darwin-universal": "macOS Universal",
    "-macos-arm64": "macOS ARM64 (Apple Silicon)",
    "-macos-amd64": "macOS x86_64 (Intel)",
    "-macos-x86_64": "macOS x86_64 (Intel)",
    # Linux
    "-linux-arm64": "Linux ARM64",
    "-linux-amd64": "Linux x86_64",
    "-linux-x86_64": "Linux x86_64",
    # Windows
    "-windows-arm64.exe": "Windows ARM64",
    "-windows-amd64.exe": "Windows x86_64",
    "-windows-x86_64.exe": "Windows x86_64",
}

# Minimum recommended platform set for compiled binaries
RECOMMENDED_PLATFORMS = {
    "macOS ARM64 (Apple Silicon)",
    "macOS x86_64 (Intel)",
    "Linux x86_64",
}

# Shebang interpreters that mark a file as an interpreted script rather than a compiled binary.
# Matches `#!/usr/bin/env python3`, `#!/bin/bash`, `#!/usr/bin/python3.12`, etc.
# `\b(name)[\d.]*` allows versioned interpreters like python3 / python3.12 / node18.
_SCRIPT_SHEBANG_RE = re.compile(
    r"^#!.*\b(python|bash|sh|node|deno|ruby|perl|pwsh|fish|zsh|tclsh)[\d.]*\b"
)


def _file_has_script_shebang(path: Path) -> bool:
    """Return True if the file starts with a shebang pointing at a known interpreter.

    Used to distinguish portable extensionless scripts (e.g. ``bin/my-tool``
    starting with ``#!/usr/bin/env python3``) from genuine compiled binaries
    that happen to lack an extension.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(256)
    except (OSError, PermissionError):
        return False
    if not head.startswith(b"#!"):
        return False
    try:
        first_line = head.split(b"\n", 1)[0].decode("utf-8", errors="replace")
    except Exception:
        return False
    return bool(_SCRIPT_SHEBANG_RE.match(first_line))


def _is_python_venv(dirpath: Path) -> bool:
    """Detect Python virtual environments by structural markers, not name.

    A directory is a venv if it contains pyvenv.cfg (created by python -m venv
    and virtualenv). This catches venvs regardless of name (.venv, .windows_venv,
    .virtualenv, my_env, etc.).
    """
    # pyvenv.cfg is the canonical marker — always created by venv/virtualenv
    if (dirpath / "pyvenv.cfg").is_file():
        return True
    # Fallback: bin/activate (Unix) or Scripts/activate.bat (Windows)
    if (dirpath / "bin" / "activate").is_file():
        return True
    if (dirpath / "Scripts" / "activate.bat").is_file():
        return True
    return False


def validate_bin_executables(plugin_root: Path, report: ValidationReport) -> None:
    """Validate bin/ directory — executables added to Bash tool's PATH (v2.1.91).

    Files in bin/ are invokable as bare commands from the Bash tool while the
    plugin is enabled. Files that look like executables (no extension, or script
    extensions) must be executable. Data files, libraries, and configs are skipped.
    """
    bin_dir = plugin_root / "bin"
    if not bin_dir.is_dir():
        return

    bin_files = [f for f in bin_dir.iterdir() if f.is_file()]
    if not bin_files:
        report.info("bin/ directory exists but is empty")
        return

    # Extensions that indicate data/library files — skip executable check
    data_extensions = {
        ".dll",
        ".so",
        ".dylib",
        ".a",
        ".lib",
        ".o",
        ".obj",  # Libraries
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",  # Config
        ".txt",
        ".md",
        ".csv",
        ".log",  # Data/docs
        ".pem",
        ".crt",
        ".key",  # Certificates
        ".wasm",  # WebAssembly modules
    }
    # Extensions that indicate scripts — should be executable
    script_extensions = {".sh", ".bash", ".py", ".rb", ".pl", ".js", ".ts", ".ps1"}

    executable_count = 0
    for bin_file in bin_files:
        ext = bin_file.suffix.lower()
        if ext in data_extensions:
            continue  # Skip data/library files
        # Files with no extension or script extensions should be executable
        if ext == "" or ext in script_extensions:
            if not os.access(bin_file, os.X_OK):
                report.minor(
                    f"bin/{bin_file.name} is not executable — if this is a command, run: chmod +x bin/{bin_file.name}",
                    f"bin/{bin_file.name}",
                )
            else:
                executable_count += 1
                report.passed(f"bin/{bin_file.name} is executable", f"bin/{bin_file.name}")

    if executable_count > 0:
        report.passed(f"bin/ directory: {executable_count} executable(s) found")


def validate_cross_platform(plugin_root: Path, report: ValidationReport) -> None:
    """Validate cross-platform compatibility of plugin scripts and binaries.

    Checks:
    1. Scripts using platform-specific languages get warnings
    2. Compiled source code without binaries or build script = MAJOR error
    3. Compiled binaries should cover all major platforms
    """
    # Collect all files across the entire plugin tree
    platform_specific_scripts: dict[str, list[str]] = {}  # ext -> [relative paths]
    compiled_source_files: dict[str, list[str]] = {}  # lang -> [relative paths]
    all_files: list[str] = []

    # Compiled language source extensions and their build system markers
    compiled_languages: dict[str, tuple[str, list[str]]] = {
        ".rs": ("Rust", ["Cargo.toml", "Cargo.lock"]),
        ".go": ("Go", ["go.mod", "go.sum"]),
        ".c": ("C", ["Makefile", "CMakeLists.txt", "meson.build"]),
        ".cpp": ("C++", ["Makefile", "CMakeLists.txt", "meson.build"]),
        ".cc": ("C++", ["Makefile", "CMakeLists.txt", "meson.build"]),
        ".cxx": ("C++", ["Makefile", "CMakeLists.txt", "meson.build"]),
        ".swift": ("Swift", ["Package.swift"]),
        ".zig": ("Zig", ["build.zig"]),
    }

    # Directories to always skip (build artifacts, caches, developer tooling)
    skip_dirs = {
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "target",
        ".eggs",
        "git-hooks",  # git hooks are developer tooling, not end-user components
        "tests",  # test fixtures may contain platform-specific scripts
        "fixtures",
    }

    # Use gitignore-aware walk to skip ignored files and directories
    for dirpath, dirnames, filenames in _gi.walk(plugin_root, skip_dirs=skip_dirs) if _gi else os.walk(plugin_root):
        if not _gi:
            # Fallback filtering when gitignore filter not initialized
            dirnames[:] = [
                d
                for d in dirnames
                if not d.startswith(".") and d not in skip_dirs and not _is_python_venv(Path(dirpath) / d)
            ]
        rel_dir = Path(dirpath).relative_to(plugin_root)

        for filename in filenames:
            ext = Path(filename).suffix.lower()
            rel_path = str(rel_dir / filename) if str(rel_dir) != "." else filename
            all_files.append(rel_path)

            if ext in SCRIPT_PLATFORM_MAP:
                platform_specific_scripts.setdefault(ext, []).append(rel_path)

            if ext in compiled_languages:
                lang_name = compiled_languages[ext][0]
                compiled_source_files.setdefault(lang_name, []).append(rel_path)

    # --- 1. Report platform-specific interpreted scripts ---
    # When a script has a portable fallback in the same directory (same stem with a
    # cross-platform extension, e.g. install.sh + install.py + install.ps1), demote
    # the warning to INFO since the user has covered the gap. This avoids the surprise
    # of getting a warning for portable POSIX shell scripts that ship alongside Python
    # or PowerShell wrappers.
    def _has_portable_fallback(rel_path: str, all_paths: list[str]) -> bool:
        p = Path(rel_path)
        stem = p.stem
        parent = str(p.parent)
        fallback_extensions = {".py", ".js", ".ts", ".rb", ".ps1"}
        for other in all_paths:
            op = Path(other)
            if op == p:
                continue
            if str(op.parent) == parent and op.stem == stem and op.suffix.lower() in fallback_extensions:
                return True
        return False

    if platform_specific_scripts:
        for ext, paths in platform_specific_scripts.items():
            lang_name, platforms, note = SCRIPT_PLATFORM_MAP[ext]
            covered_paths = [p for p in paths if _has_portable_fallback(p, all_files)]
            uncovered_paths = [p for p in paths if p not in covered_paths]
            if covered_paths:
                report.info(
                    f"Found {len(covered_paths)} {lang_name} script(s) ({ext}) with portable fallback "
                    f"(.py/.ps1/etc.) in the same directory — cross-platform coverage already in place."
                )
            if uncovered_paths:
                if platforms:
                    platforms_str = ", ".join(sorted(platforms))
                    report.warning(
                        f"Found {len(uncovered_paths)} {lang_name} script(s) ({ext}) — only natively available on {platforms_str}. {note}. Consider providing cross-platform alternatives or documenting requirements.",
                    )
                else:
                    report.warning(
                        f"Found {len(uncovered_paths)} {lang_name} script(s) ({ext}) — {note}. Consider providing cross-platform alternatives.",
                    )
    else:
        has_scripts = any(
            any(f.endswith(ext) for ext in CROSSPLATFORM_EXTENSIONS)
            for _, _, files in (_gi.walk(plugin_root, skip_dirs=skip_dirs) if _gi else os.walk(plugin_root))
            for f in files
        )
        if has_scripts:
            report.passed("All scripts use cross-platform languages")

    # --- 2. Check compiled source code has binaries or build script ---
    if compiled_source_files:
        # Search for bin/ directories recursively, skip gitignored paths
        bin_dirs = list(_gi.rglob("bin") if _gi else plugin_root.rglob("bin"))
        has_bin = any(d.is_dir() and any(d.iterdir()) for d in bin_dirs)

        for lang_name, source_paths in compiled_source_files.items():
            # Find expected build system files for this language
            expected_build_files: set[str] = set()
            for ext, (ln, build_markers) in compiled_languages.items():
                if ln == lang_name:
                    expected_build_files.update(build_markers)

            # Check if build system files exist at plugin root
            has_build_system = any((plugin_root / bf).exists() for bf in expected_build_files)

            # Check for a generic build/install script
            has_build_script = any(
                (plugin_root / s).exists()
                for s in [
                    "build.sh",
                    "install.sh",
                    "setup.sh",
                    "compile.sh",
                    "build.py",
                    "install.py",
                    "setup.py",
                    "Makefile",
                    "justfile",
                    "Taskfile.yml",
                ]
            )

            if has_bin:
                report.info(f"Found {len(source_paths)} {lang_name} source file(s) with compiled binaries in bin/")
            elif has_build_system or has_build_script:
                report.warning(
                    f"Found {len(source_paths)} {lang_name} source file(s) with build system but no pre-compiled binaries in bin/. Users will need to compile before use."
                )
            else:
                report.major(
                    f"Found {len(source_paths)} {lang_name} source file(s) but no compiled binaries in bin/ and no build script (build.sh, install.sh, Makefile, etc.). Provide pre-compiled binaries or a build/install script."
                )

    # --- 3. Check compiled binaries platform coverage ---
    # Search for bin/ directories recursively, skip gitignored paths
    all_bin_dirs = []
    for d in _gi.rglob("bin") if _gi else plugin_root.rglob("bin"):
        if not d.is_dir():
            continue
        # Also skip venvs detected structurally
        rel_parts = d.relative_to(plugin_root).parts[:-1]
        if any(_is_python_venv(plugin_root / Path(*rel_parts[: i + 1])) for i in range(len(rel_parts))):
            continue
        all_bin_dirs.append(d)
    if not all_bin_dirs:
        return

    binary_files: list[str] = []
    detected_platforms: set[str] = set()
    base_names: set[str] = set()

    for bin_dir in all_bin_dirs:
        for item in bin_dir.rglob("*"):
            if not item.is_file():
                continue
            name = item.name
            rel_path = str(item.relative_to(plugin_root))

            for suffix, platform_name in BINARY_PLATFORM_SUFFIXES.items():
                if suffix in name.lower():
                    binary_files.append(rel_path)
                    detected_platforms.add(platform_name)
                    base = name[: name.lower().index(suffix.split("-")[0] + "-")]
                    if base.endswith("-"):
                        base = base[:-1]
                    base_names.add(base)
                    break
            else:
                if not item.suffix and os.access(item, os.X_OK):
                    # Skip portable interpreted scripts (Python/Bash/Node/etc.) — they have a
                    # shebang and run on every platform without compilation. Treating them as
                    # compiled binaries produces false-positive "missing platform suffix" warnings.
                    if _file_has_script_shebang(item):
                        continue
                    binary_files.append(rel_path)
                    base_names.add(name)
                elif item.suffix == ".exe":
                    binary_files.append(rel_path)
                    detected_platforms.add("Windows")
                    base_names.add(item.stem)
                elif item.suffix in {".dylib", ".so"}:
                    binary_files.append(rel_path)
                    if item.suffix == ".dylib":
                        detected_platforms.add("macOS")
                    else:
                        detected_platforms.add("Linux")

    if not binary_files:
        return

    report.info(f"Found {len(binary_files)} compiled binary file(s) for {len(base_names)} tool(s)")

    if detected_platforms:
        missing = RECOMMENDED_PLATFORMS - detected_platforms
        if missing:
            missing_str = ", ".join(sorted(missing))
            report.warning(
                f"Compiled binaries missing for: {missing_str}. Detected platforms: {', '.join(sorted(detected_platforms))}. Consider providing binaries for all major platforms."
            )
        else:
            report.passed(f"Compiled binaries cover recommended platforms: {', '.join(sorted(detected_platforms))}")
    else:
        report.warning(
            f"Found {len(binary_files)} binary file(s) without platform identifiers in filename. Use naming convention like 'tool-darwin-arm64', 'tool-linux-amd64', 'tool-windows-amd64.exe' for multi-platform support."
        )


def validate_skills(plugin_root: Path, report: ValidationReport, skip_platform_checks: list[str] | None = None) -> None:
    """Validate all skills in the plugin's skills/ directory.

    Args:
        plugin_root: Path to plugin root directory
        report: ValidationReport to add results to
        skip_platform_checks: List of platforms to skip checks for (e.g., ['windows'])
    """
    skills_dir = plugin_root / "skills"

    if not skills_dir.is_dir():
        report.info("No skills/ directory found")
        return

    # Find all skill directories
    skill_dirs = [d for d in skills_dir.iterdir() if d.is_dir()]

    if not skill_dirs:
        report.info("No skill directories found in skills/")
        return

    report.info(f"Found {len(skill_dirs)} skill(s) to validate")

    # Validate each skill using comprehensive validator (190+ rules)
    for skill_dir in sorted(skill_dirs):
        skill_name = skill_dir.name
        # Use comprehensive validator with all checks enabled
        skill_report = validate_skill_comprehensive(
            skill_dir,
            strict_mode=True,  # Enable Nixtla strict mode
            strict_openspec=False,  # Don't require OpenSpec 6-field whitelist for plugins
            validate_pillars_flag=skill_name.startswith(("lang-", "convert-")),  # Auto-enable for lang-*/convert-*
            skip_platform_checks=skip_platform_checks,
        )

        # Transfer results to main report with skill path prefix
        for result in skill_report.results:
            file_path = f"skills/{skill_name}/{result.file}" if result.file else f"skills/{skill_name}"
            report.add(result.level, result.message, file_path, result.line)


def validate_output_styles(plugin_root: Path, report: ValidationReport) -> None:
    """Validate output-styles/ directory — markdown files with YAML frontmatter.

    Output style files have frontmatter fields:
    - name (string, optional — defaults to filename)
    - description (string, optional — shown in /config picker)
    - keep-coding-instructions (boolean, optional — default false)
    """
    styles_dir = plugin_root / "output-styles"
    if not styles_dir.is_dir():
        return

    import yaml as yaml_mod

    md_files = list(styles_dir.glob("*.md"))
    if not md_files:
        report.info("output-styles/ directory exists but has no .md files")
        return

    valid_fields = {"name", "description", "keep-coding-instructions"}

    for md_file in md_files:
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception as e:
            report.major(f"Cannot read output style: {e}", f"output-styles/{md_file.name}")
            continue

        # Parse frontmatter
        if not content.startswith("---"):
            report.minor(
                f"Output style '{md_file.name}' has no YAML frontmatter",
                f"output-styles/{md_file.name}",
            )
            continue

        parts = content.split("---", 2)
        if len(parts) < 3:
            report.minor(
                f"Output style '{md_file.name}' has malformed frontmatter (missing closing ---)",
                f"output-styles/{md_file.name}",
            )
            continue

        try:
            fm = yaml_mod.safe_load(parts[1])
        except yaml_mod.YAMLError as e:
            report.major(
                f"Output style '{md_file.name}' has invalid YAML: {e}",
                f"output-styles/{md_file.name}",
            )
            continue

        if not isinstance(fm, dict):
            fm = {}

        # Validate fields
        for key in fm:
            if key not in valid_fields:
                report.warning(
                    f"Output style '{md_file.name}' has unknown field '{key}'",
                    f"output-styles/{md_file.name}",
                )

        if "keep-coding-instructions" in fm:
            val = fm["keep-coding-instructions"]
            if not isinstance(val, bool):
                report.major(
                    f"Output style '{md_file.name}': 'keep-coding-instructions' must be boolean, got {type(val).__name__}",
                    f"output-styles/{md_file.name}",
                )

        # Check body content exists
        body = parts[2].strip() if len(parts) > 2 else ""
        if not body:
            report.minor(
                f"Output style '{md_file.name}' has no body content (instructions)",
                f"output-styles/{md_file.name}",
            )
        else:
            report.passed(f"Output style '{md_file.name}' is valid", f"output-styles/{md_file.name}")

    report.passed(f"output-styles/: {len(md_files)} style(s) found")


def validate_readme(plugin_root: Path, report: ValidationReport) -> None:
    """Validate README.md exists and has recommended markers."""
    readme = plugin_root / "README.md"
    if readme.exists():
        report.passed("README.md found")
    else:
        report.minor("README.md not found")

    # Badge markers for automated badge updates
    if readme.exists():
        readme_content = readme.read_text(encoding="utf-8", errors="replace")
        if "<!--BADGES-START-->" in readme_content and "<!--BADGES-END-->" in readme_content:
            report.passed("README.md has badge markers for automated updates", "README.md")
        else:
            report.warning("README.md missing badge markers (<!--BADGES-START--> / <!--BADGES-END-->)", "README.md")


def validate_license(plugin_root: Path, report: ValidationReport) -> None:
    """Validate LICENSE file exists."""
    for license_name in ["LICENSE", "LICENSE.md", "LICENSE.txt"]:
        if (plugin_root / license_name).exists():
            report.passed(f"{license_name} found")
            return

    report.minor("No LICENSE file found")


def validate_rules(plugin_root: Path, report: ValidationReport) -> None:
    """Validate rule files in the plugin's rules/ directory.

    Rules are plain markdown files loaded alongside CLAUDE.md into model context.
    Checks: UTF-8 encoding, optional frontmatter (paths field), token budget.
    """
    rules_dir = plugin_root / "rules"

    if not rules_dir.is_dir():
        report.info("No rules/ directory found")
        return

    # Use the dedicated rules validator
    rules_report = validate_rules_directory(rules_dir, plugin_root=plugin_root)

    # Transfer results to main report
    for result in rules_report.results:
        report.add(result.level, result.message, result.file, result.line)


def validate_no_local_paths(plugin_root: Path, report: ValidationReport) -> None:
    """Validate that plugin files don't contain hardcoded local or absolute paths.

    Uses the stricter absolute path validation from cpv_validation_common.py.

    In plugins, ALL paths should be:
    - Relative to plugin root (e.g., ./scripts/foo.py)
    - Using ${CLAUDE_PLUGIN_ROOT} for runtime resolution
    - Using ${HOME} or ~ for user home directory

    Checks for:
    - Current user's home path (CRITICAL) - auto-detected from system
    - Any absolute home directory paths (MAJOR)

    Excludes:
    - Cache directories (.mypy_cache, .ruff_cache, __pycache__)
    - Development folders (docs_dev/, scripts_dev/, etc.)
    - .git/ directory
    - Allowed system paths (/tmp/, /dev/, /proc/, /sys/)
    - Generic example usernames in documentation
    - Test directories (tests/) — contain intentional test fixture paths
    """
    # Use the strict absolute path validator which checks for:
    # - Current user's username (auto-detected) - CRITICAL
    # - ANY absolute paths that don't use env vars - MAJOR
    # We pass our local report since both have compatible interfaces
    validate_no_absolute_paths(plugin_root, report, skip_dirs={"tests"})  # type: ignore[arg-type]


# =============================================================================
# .gitignore Validation
# =============================================================================

# Patterns that a well-formed plugin .gitignore should include
# Each tuple: (pattern_to_search_for, description, severity)
# We check if the gitignore content covers these categories
EXPECTED_GITIGNORE_CATEGORIES: list[tuple[list[str], str, str]] = [
    # Cache/build artifacts
    (["__pycache__", "*.pyc"], "Python cache files (__pycache__ or *.pyc)", "warning"),
    (["node_modules"], "Node modules (node_modules/)", "warning"),
    ([".mypy_cache", ".ruff_cache", ".pytest_cache"], "Linter/type checker caches", "warning"),
    (["dist", "build", "*.egg-info"], "Build artifacts (dist/, build/, *.egg-info)", "warning"),
    # Temp/editor files
    ([".DS_Store", "Thumbs.db"], "OS metadata files (.DS_Store, Thumbs.db)", "warning"),
    (["*.swp", "*.swo", "*~", ".idea", ".vscode"], "Editor temp files", "warning"),
    # Environment/secrets
    ([".env", "*.env"], "Environment files (.env)", "major"),
    # Virtual environments
    ([".venv", "venv"], "Virtual environment directories", "major"),
    # Claude Code runtime directories
    ([".claude"], "Claude Code cache directory (.claude/)", "minor"),
    (["llm_externalizer_output"], "LLM Externalizer output directory", "warning"),
    ([".tldr"], "TLDR cache directory (.tldr/)", "warning"),
]


def _check_stale_user_settings_local(report: ValidationReport) -> None:
    """Warn if ~/.claude/settings.local.json exists — it should not be at user level.

    settings.local.json only makes sense inside a project directory
    (<project>/.claude/settings.local.json). At ~/.claude/ it indicates a
    leftover from buggy tooling or running Claude Code from ~/ (invalid).
    """
    stale = Path.home() / ".claude" / "settings.local.json"
    if stale.exists():
        report.warning(
            "~/.claude/settings.local.json exists but should NOT be at user level. "
            "This file only makes sense inside project dirs (<project>/.claude/settings.local.json). "
            "Run /cpv-doctor --fix to delete it, or remove it manually.",
            "~/.claude/settings.local.json",
        )


def validate_gitignore(plugin_root: Path, report: ValidationReport) -> None:
    """Validate that the plugin has a .gitignore with essential patterns.

    Checks that cache files, build artifacts, temp files, secrets,
    and virtual environments are properly ignored.
    """
    gitignore_path = plugin_root / ".gitignore"

    if not gitignore_path.exists():
        report.major(
            "No .gitignore file found — cache files, build artifacts, and secrets may be accidentally included in the plugin"
        )
        return

    try:
        content = gitignore_path.read_text(encoding="utf-8")
    except Exception as e:
        report.minor(f"Could not read .gitignore: {e}")
        return

    # Strip comments and empty lines for pattern matching
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
    missing_categories: list[tuple[str, str]] = []

    for patterns, description, severity in EXPECTED_GITIGNORE_CATEGORIES:
        # Check if ANY of the patterns in this category appear in the gitignore
        found = any(any(p.lower() in line.lower() for line in lines) for p in patterns)
        if not found:
            missing_categories.append((description, severity))

    if not missing_categories:
        report.passed(".gitignore covers all expected categories")
    else:
        for description, severity in missing_categories:
            getattr(report, severity)(f".gitignore missing coverage for: {description}")

    # Check for common anti-patterns in .gitignore
    # Ignoring the entire plugin source is almost certainly wrong
    if "*.py" in lines or "*.js" in lines or "*.ts" in lines:
        report.major(
            ".gitignore ignores all source files (*.py, *.js, or *.ts) — this will exclude plugin code from distribution"
        )

    # Scan for actual venv directories by structure (any name, not just .venv/venv)
    for item in plugin_root.iterdir():
        if item.is_dir() and _is_python_venv(item):
            dirname = item.name
            # Check if this specific directory is covered by .gitignore
            covered = any(dirname.lower() in line.lower() for line in lines)
            if not covered:
                report.major(
                    f"Virtual environment '{dirname}/' detected (contains pyvenv.cfg) but not covered by .gitignore. Add '{dirname}/' to .gitignore."
                )

    # Check for bundled dependency directories that should be installed at runtime
    # in ${CLAUDE_PLUGIN_DATA} instead of shipped inside the plugin root.
    # ${CLAUDE_PLUGIN_ROOT} is wiped on every plugin update; ${CLAUDE_PLUGIN_DATA} persists.
    # Skip this check in development mode (.git present = source repo, not installed plugin).
    is_dev_mode = (plugin_root / ".git").exists()
    if not is_dev_mode:
        bundled_dep_dirs = {"node_modules", ".venv", "venv", "vendor", "__pypackages__"}
        for item in plugin_root.iterdir():
            if item.is_dir() and item.name.lower() in bundled_dep_dirs:
                report.warning(
                    f"Bundled dependency directory '{item.name}/' found inside plugin root. "
                    "This directory will be lost on every plugin update because ${{CLAUDE_PLUGIN_ROOT}} is replaced. "
                    "Use a SessionStart hook to install dependencies into ${{CLAUDE_PLUGIN_DATA}} instead — "
                    "see https://code.claude.com/docs/en/plugins-reference#persistent-data-directory",
                )

    # Check that non-plugin artifacts that may exist are ignored
    # Look for actual artifacts in the tree that should be gitignored
    artifact_patterns = {
        "*.pyc": "Compiled Python files",
        ".DS_Store": "macOS metadata",
        "Thumbs.db": "Windows metadata",
    }
    for pattern_glob, desc in artifact_patterns.items():
        # Use gitignore-aware rglob — only find artifacts NOT covered by .gitignore
        if _gi:
            matches = [p for p in _gi.rglob(pattern_glob)]
        else:
            matches = list(plugin_root.rglob(pattern_glob))
        if matches:
            sample = matches[0].relative_to(plugin_root)
            report.warning(f"Found {len(matches)} {desc} file(s) (e.g. {sample}) that are not gitignored")


# Regex to find inline Python blocks inside YAML: `python3 -c "..."`  or `python -c "..."`
# Captures the Python code string passed to -c.
_YAML_INLINE_PYTHON_RE = re.compile(
    r'python3?\s+-c\s+"([^"]*(?:"[^"]*"[^"]*)*)"',
    re.DOTALL,
)

# Dangerous pattern: dict["key"] or dict['key'] inside an f-string.
# In YAML inline Python the shell strips the inner quotes, causing NameError.
# Matches: {expr["key"]}, {expr['key']}, {expr.method()["key"]} etc.
_FSTRING_DICT_BRACKET_RE = re.compile(
    r"""\{[^}]*\[["'][^"']+["']\][^}]*\}""",
)


def validate_workflow_inline_python(plugin_root: Path, report: ValidationReport) -> None:
    """Scan GitHub Actions workflow files for dangerous inline Python patterns.

    When a YAML workflow uses ``python3 -c "..."`` (double-quoted shell string),
    dict bracket access like source["repo"] inside f-strings will fail at
    runtime because the shell strips the inner double quotes before Python
    sees the code.  Python then interprets the bare word as an undefined
    variable name, causing NameError.

    This validator catches that pattern and reports it as MAJOR.
    """
    workflows_dir = plugin_root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return

    yaml_files = list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))
    if not yaml_files:
        return

    found_any = False
    for yaml_path in yaml_files:
        try:
            content = yaml_path.read_text(encoding="utf-8")
        except Exception:
            continue

        rel_path = str(yaml_path.relative_to(plugin_root))

        # Find all inline Python blocks
        for match in _YAML_INLINE_PYTHON_RE.finditer(content):
            python_code = match.group(1)
            block_start_offset = match.start()

            # Search for f-strings with dict bracket access
            for bad_match in _FSTRING_DICT_BRACKET_RE.finditer(python_code):
                abs_offset = block_start_offset + bad_match.start()
                line_num = content[:abs_offset].count("\n") + 1
                snippet = bad_match.group(0)
                found_any = True
                report.major(
                    f"Inline Python uses dict bracket access in f-string: {snippet} -- shell quoting will strip inner quotes causing NameError. Extract value into a local variable first.",
                    rel_path,
                    line_num,
                )

    if not found_any and yaml_files:
        report.passed(f"No inline Python quoting issues in {len(yaml_files)} workflow file(s)")


def print_results(report: ValidationReport, verbose: bool = False) -> None:
    """Print validation results in human-readable format."""
    colors = COLORS

    counts = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0, "NIT": 0, "WARNING": 0, "INFO": 0, "PASSED": 0}
    for r in report.results:
        counts[r.level] += 1

    print("\n" + "=" * 60)
    print("Plugin Validation Report")
    print("=" * 60)

    print("\nSummary:")
    print(f"  {colors['CRITICAL']}CRITICAL: {counts['CRITICAL']}{colors['RESET']}")
    print(f"  {colors['MAJOR']}MAJOR:    {counts['MAJOR']}{colors['RESET']}")
    print(f"  {colors['MINOR']}MINOR:    {counts['MINOR']}{colors['RESET']}")
    print(f"  {colors['NIT']}NIT:      {counts['NIT']}{colors['RESET']}")
    print(f"  {colors['WARNING']}WARNING:  {counts['WARNING']}{colors['RESET']}")
    if verbose:
        print(f"  {colors['INFO']}INFO:     {counts['INFO']}{colors['RESET']}")
        print(f"  {colors['PASSED']}PASSED:   {counts['PASSED']}{colors['RESET']}")

    print("\nDetails:")
    for r in report.results:
        if r.level == "PASSED" and not verbose:
            continue
        if r.level == "INFO" and not verbose:
            continue

        color = colors[r.level]
        reset = colors["RESET"]
        file_info = f" ({r.file})" if r.file else ""
        line_info = f":{r.line}" if r.line else ""
        print(f"  {color}[{r.level}]{reset} {r.message}{file_info}{line_info}")

    print("\n" + "-" * 60)
    if report.exit_code == 0:
        print(f"{colors['PASSED']}✓ All checks passed{colors['RESET']}")
    elif report.exit_code == 1:
        print(f"{colors['CRITICAL']}✗ CRITICAL issues found - plugin will not work{colors['RESET']}")
    elif report.exit_code == 2:
        print(f"{colors['MAJOR']}✗ MAJOR issues found - significant problems{colors['RESET']}")
    else:
        print(f"{colors['MINOR']}! MINOR issues found - may affect UX{colors['RESET']}")

    # Machine-readable summary for CI/CD parsing
    print(
        f"SUMMARY: CRITICAL={counts['CRITICAL']} MAJOR={counts['MAJOR']} MINOR={counts['MINOR']} NIT={counts['NIT']} WARNING={counts['WARNING']}"
    )

    print()

    # If there are any fixable issues, point the user at the fixer agent/skill.
    from cpv_validation_common import _print_fixer_recommendation

    _print_fixer_recommendation(report, None)


def print_json(report: ValidationReport) -> None:
    """Print validation results as JSON."""
    output = {
        "exit_code": report.exit_code,
        "counts": {
            "critical": sum(1 for r in report.results if r.level == "CRITICAL"),
            "major": sum(1 for r in report.results if r.level == "MAJOR"),
            "minor": sum(1 for r in report.results if r.level == "MINOR"),
            "nit": sum(1 for r in report.results if r.level == "NIT"),
            "warning": sum(1 for r in report.results if r.level == "WARNING"),
            "info": sum(1 for r in report.results if r.level == "INFO"),
            "passed": sum(1 for r in report.results if r.level == "PASSED"),
        },
        "results": [{"level": r.level, "message": r.message, "file": r.file, "line": r.line} for r in report.results],
    }
    print(json.dumps(output, indent=2))


def validate_md_content_references(plugin_root: Path, report: ValidationReport) -> None:
    """Validate file path references and URLs inside all .md files in the plugin.

    Scans commands/, agents/, skills/, README.md for:
    - Broken file path references (markdown links and backtick paths)
    - Dead URLs (HTTP HEAD check with sanitization)
    """
    # Collect all .md files to check (excluding tests/, _dev/ dirs, and CHANGELOG)
    md_files: list[Path] = []

    # README.md at root
    readme = plugin_root / "README.md"
    if readme.exists():
        md_files.append(readme)

    # Commands
    commands_dir = plugin_root / "commands"
    if commands_dir.is_dir():
        md_files.extend(commands_dir.glob("*.md"))

    # Agents
    agents_dir = plugin_root / "agents"
    if agents_dir.is_dir():
        md_files.extend(agents_dir.glob("*.md"))

    # Skills (SKILL.md + references/*.md)
    skills_dir = plugin_root / "skills"
    if skills_dir.is_dir():
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir():
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    md_files.append(skill_md)
                refs_dir = skill_dir / "references"
                if refs_dir.is_dir():
                    md_files.extend(refs_dir.glob("*.md"))

    if not md_files:
        return

    report.info(f"Checking content references in {len(md_files)} .md file(s)")

    # Patterns to skip in path validation (common false positives)
    skip_patterns = {
        "node_modules/",
        "__pycache__/",
        ".git/",
        "<placeholder",
        "${",
        "$(",
    }

    # Shared URL cache across all files (avoid re-checking same URL)
    url_cache: dict[str, bool] = {}

    # Reference files (skills/*/references/*.md) are documentation about the USER's
    # plugin, not about this plugin. Backtick paths in those files describe the target
    # plugin structure, so they should not be validated as references to files in THIS
    # plugin. We pass a flag to downgrade plugin-internal backtick path errors to
    # WARNING in reference files.
    for md_file in sorted(md_files):
        # Reference files and command files describe the USER's plugin structure,
        # not this plugin. Backtick paths there are documentation examples.
        is_reference_doc = "/references/" in str(md_file) or "/commands/" in str(md_file)
        # Validate file path references
        validate_md_file_paths(
            md_file, plugin_root, report, skip_patterns=skip_patterns, is_reference_doc=is_reference_doc
        )
        # Validate URLs
        validate_md_urls(md_file, plugin_root, report, url_cache=url_cache)


def validate_pipeline_readiness(plugin_root: Path, report: ValidationReport) -> None:
    """Check that the plugin has CI/CD pipeline infrastructure."""
    # Pre-push hook
    hook_paths = [
        plugin_root / ".githooks" / "pre-push",
        plugin_root / "git-hooks" / "pre-push",
    ]
    if any(p.exists() for p in hook_paths):
        report.passed("Pre-push hook found")
    else:
        report.minor(
            "No pre-push hook found (.githooks/pre-push or git-hooks/pre-push) — recommended for quality gates"
        )

    # Publish script
    if (plugin_root / "scripts" / "publish.py").exists():
        report.passed("scripts/publish.py found")
    else:
        report.warning("No scripts/publish.py found — recommended for release automation")

    # Changelog config
    if (plugin_root / "cliff.toml").exists():
        report.passed("cliff.toml found (git-cliff changelog)")
    else:
        report.warning("No cliff.toml found — recommended for automated changelog generation")

    # GitHub workflows
    workflows_dir = plugin_root / ".github" / "workflows"
    if workflows_dir.is_dir() and list(workflows_dir.glob("*.yml")):
        report.passed("GitHub workflows found")
    else:
        report.minor("No .github/workflows/*.yml found — recommended for CI/CD automation")

    # Marketplace notification workflow
    if workflows_dir.is_dir():
        notify_names = ["notify-marketplace.yml", "notify.yml", "marketplace-notify.yml"]
        if any((workflows_dir / n).exists() for n in notify_names):
            report.passed("Marketplace notification workflow found")
        else:
            report.warning("No notify-marketplace.yml workflow — plugin updates won't auto-notify marketplaces")


def validate_workflow_best_practices(plugin_root: Path, report: ValidationReport) -> None:
    """Check GitHub workflow files for common anti-patterns."""
    workflows_dir = plugin_root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return
    for wf in workflows_dir.glob("*.yml"):
        try:
            content = wf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(wf.relative_to(plugin_root))
        # Check for uv pip install --system (should use uvx)
        if "uv pip install --system" in content:
            report.nit(f"{rel}: uses 'uv pip install --system' — prefer 'uvx' for reproducible installs", rel)
        # Check for unpinned actions/checkout
        if "actions/checkout@" not in content and "actions/checkout" in content:
            report.nit(f"{rel}: uses 'actions/checkout' without version pin — pin to '@v4' or similar", rel)


# =============================================================================
# Submodule + Language + Lockfile Detection (TRDD-79638eb6)
# =============================================================================


def is_plugin_in_submodule(plugin_root: Path) -> Path | None:
    """Detect if plugin_root is registered as a git submodule of a parent repo.

    Walks up the parent chain from plugin_root looking for any ancestor
    directory that contains a .gitmodules file AND references this plugin's
    relative path as a submodule target.

    Why this matters: when a plugin lives inside a parent repo as a submodule,
    the parent repo's CI will not run the plugin's own workflows automatically
    — the plugin must be released/validated independently. Users are often
    surprised by this.

    Args:
        plugin_root: Absolute path to the plugin directory.

    Returns:
        Path to the parent repo root if the plugin is a submodule, else None.
        The returned path is the directory containing .gitmodules that lists
        this plugin.
    """
    try:
        plugin_abs = plugin_root.resolve()
    except OSError:
        return None

    # Walk up the parent chain. Stop at filesystem root.
    current = plugin_abs.parent
    while True:
        gitmodules = current / ".gitmodules"
        if gitmodules.is_file():
            try:
                content = gitmodules.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            # Collect every "path = <relative>" entry in the .gitmodules file.
            # A submodule section looks like:
            #     [submodule "some-name"]
            #         path = some/rel/path
            #         url = https://...
            submodule_paths: list[str] = []
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("path") and "=" in stripped:
                    _, _, val = stripped.partition("=")
                    submodule_paths.append(val.strip())

            # Compute plugin_abs relative to the candidate parent dir, in POSIX form.
            try:
                rel = plugin_abs.relative_to(current)
            except ValueError:
                rel = None

            if rel is not None:
                rel_posix = rel.as_posix()
                if rel_posix in submodule_paths:
                    return current

        # Stop at filesystem root — current.parent == current means we've bottomed out.
        if current.parent == current:
            return None
        current = current.parent


def validate_submodule_containment(plugin_root: Path, report: ValidationReport) -> None:
    """Emit INFO when the plugin lives inside a parent repo as a git submodule.

    Parent repos do not run their submodules' CI — users need to know they
    must trigger the plugin's own release pipeline independently.
    """
    parent = is_plugin_in_submodule(plugin_root)
    if parent is None:
        return
    try:
        parent_display = str(parent)
    except Exception:
        parent_display = "<parent>"
    report.info(
        f"Plugin is a submodule of {parent_display}. Parent repo CI will not run this plugin's pipeline automatically."
    )


def validate_project_languages(plugin_root: Path, report: ValidationReport) -> dict[str, Path]:
    """Detect and report which languages the plugin uses.

    Emits a single INFO line listing all detected languages. The caller can
    use the returned dict to pick which linters/toolchains to invoke.

    Returns:
        Mapping of language -> marker file path (may be empty).
    """
    langs = detect_languages(plugin_root)
    if not langs:
        report.info("No language markers detected (pyproject.toml, package.json, Cargo.toml, etc.)")
        return langs
    names = sorted(langs.keys())
    # Build a concise one-line summary for the report
    summary = ", ".join(f"{name} ({langs[name].name})" for name in names)
    report.info(f"Detected project languages: {summary}")
    return langs


def validate_lockfiles(plugin_root: Path, report: ValidationReport, detected_languages: dict[str, Path]) -> None:
    """Scan for known lockfiles and flag orphan lockfiles or gitignored lockfiles.

    Emits:
        - NIT: lockfile present but its language was not detected (orphan).
               Usually means a config file was removed but the lockfile was left
               behind, or the plugin inherited a lockfile from a parent repo.
        - WARNING: lockfile present but listed in .gitignore. This defeats the
                   purpose of a lockfile — CI will reinstall with unpinned deps
                   and drift from whatever the developer tested.

    Args:
        plugin_root: Plugin directory.
        report: Where to record findings.
        detected_languages: Output from detect_languages() — used to determine
            whether each lockfile has a matching detected language.
    """
    lockfiles = detect_lockfiles(plugin_root)
    if not lockfiles:
        return

    # Parse the .gitignore so we can detect lockfiles that are being filtered
    # out before they reach CI. Use the project-local filter to pick up nested
    # rules as well (it handles walking up to find parent .gitignores).
    gitignore_path = plugin_root / ".gitignore"
    ignored_patterns: list[str] = []
    if gitignore_path.is_file():
        try:
            gi_content = gitignore_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            gi_content = ""
        for line in gi_content.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                ignored_patterns.append(s)

    for lockfile_name, language in sorted(lockfiles.items()):
        rel = lockfile_name
        # Orphan check: lockfile present but no manifest for its language.
        # "js" and "ts" share the same lockfiles — a TypeScript project with
        # only .ts files would still register "ts" (not "js") but its
        # package-lock.json is not orphaned. Treat "js" lockfiles as matched
        # when either "js" or "ts" is detected.
        matched = False
        if language in detected_languages:
            matched = True
        elif language == "js" and "ts" in detected_languages:
            matched = True
        if not matched:
            report.nit(
                f"Lockfile {lockfile_name} present but no {language} project detected — "
                "orphan lockfile (leftover from a removed toolchain?)",
                rel,
            )
            # Still check gitignore status below — both can fire for the same lockfile.

        # Gitignore check: an ignored lockfile will not ship to CI.
        # Use a conservative substring / exact match — we only compare the
        # filename against each active .gitignore entry. Patterns like
        # "*.lock" or "lockfiles/" also match.
        if _lockfile_is_gitignored(lockfile_name, ignored_patterns):
            report.warning(
                f"Lockfile {lockfile_name} is listed in .gitignore — CI will install "
                "with unpinned deps and drift from tested versions",
                rel,
            )


def _lockfile_is_gitignored(lockfile_name: str, patterns: list[str]) -> bool:
    """Cheap check: does any active .gitignore pattern match this lockfile name?

    Uses exact match, substring match, and trivial wildcard expansion. This
    intentionally mirrors the common .gitignore patterns a user would write
    for a lockfile (`uv.lock`, `*.lock`, `/Cargo.lock`) rather than
    implementing the full gitignore grammar.
    """
    import fnmatch

    lower_name = lockfile_name.lower()
    for pat in patterns:
        # Strip leading slash — anchored pattern, still a basename match
        candidate = pat.lstrip("/")
        if not candidate:
            continue
        # Direct exact match
        if candidate == lockfile_name:
            return True
        # Case-insensitive direct match
        if candidate.lower() == lower_name:
            return True
        # fnmatch glob support (covers *.lock, *lock*, etc.)
        if fnmatch.fnmatch(lockfile_name, candidate):
            return True
        if fnmatch.fnmatch(lower_name, candidate.lower()):
            return True
    return False


def main() -> int:
    """Main entry point."""
    check_remote_execution_guard()

    parser = argparse.ArgumentParser(
        description="Validate a Claude Code plugin against all validation rules.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="This is the main entry point. It orchestrates all 17 sub-validators.\nExample: uv run python scripts/validate_plugin.py . --strict --verbose",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show all results including passed checks",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--marketplace-only",
        action="store_true",
        help="Skip plugin.json requirement (for strict=false marketplace distribution)",
    )
    parser.add_argument(
        "--skip-platform-checks",
        nargs="*",
        metavar="PLATFORM",
        help="Skip platform-specific checks (e.g., --skip-platform-checks windows). Valid platforms: windows, macos, linux. Use without args to skip all.",
    )
    parser.add_argument("--strict", action="store_true", help="Strict mode — NIT issues also block validation")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI color codes in output")
    parser.add_argument(
        "--report", type=str, default=None, help="Save detailed report to file, print only summary to stdout"
    )
    parser.add_argument("path", nargs="?", help="Plugin root path (default: parent of scripts/)")
    args = parser.parse_args()

    # Disable ANSI colors when --no-color is passed or stdout is not a TTY
    if args.no_color or not (hasattr(sys.stdout, "isatty") and sys.stdout.isatty()):
        import cpv_validation_common

        for key in list(cpv_validation_common.COLORS.keys()):
            cpv_validation_common.COLORS[key] = ""

    # Determine plugin root — always resolve to absolute path so relative_to() works
    if args.path:
        plugin_root = Path(args.path).resolve()
    else:
        plugin_root = Path(__file__).resolve().parent.parent

    if not plugin_root.is_dir():
        print(f"Error: {plugin_root} is not a directory", file=sys.stderr)
        return 1

    # Auto-resolve plugin cache directories that contain version subdirectories
    # e.g. ~/.claude/plugins/cache/marketplace/plugin-name/{1.0.0, 1.1.7}
    if not (plugin_root / ".claude-plugin").is_dir():
        version_dirs = sorted(
            [d for d in plugin_root.iterdir() if d.is_dir() and re.match(r"\d+\.\d+", d.name)],
            key=lambda d: d.name,
            reverse=True,
        )
        if version_dirs and (version_dirs[0] / ".claude-plugin").is_dir():
            plugin_root = version_dirs[0]
            print(f"Auto-resolved to latest version: {plugin_root.name}", file=sys.stderr)
        elif not args.marketplace_only:
            # No .claude-plugin/ found and no version subdirs — not a plugin directory
            print(
                f"Error: No Claude Code plugin found at {plugin_root}\nExpected a .claude-plugin/ directory. Make sure you are running this from the plugin root directory.",
                file=sys.stderr,
            )
            return 1

    # Initialize gitignore filter — all scan functions use this to skip ignored files
    global _gi  # noqa: PLW0603
    _gi = GitignoreFilter(plugin_root)

    # Run validation
    report = ValidationReport()
    marketplace_only = args.marketplace_only
    skip_platform_checks = args.skip_platform_checks

    validate_manifest(plugin_root, report, marketplace_only)
    validate_structure(plugin_root, report, marketplace_only)
    validate_commands(plugin_root, report)
    validate_agents(plugin_root, report)
    validate_hooks(plugin_root, report)
    validate_mcp(plugin_root, report)
    validate_scripts(plugin_root, report)
    validate_bin_executables(plugin_root, report)
    validate_skills(plugin_root, report, skip_platform_checks)
    validate_rules(plugin_root, report)
    validate_output_styles(plugin_root, report)
    validate_readme(plugin_root, report)
    validate_license(plugin_root, report)
    validate_no_local_paths(plugin_root, report)
    validate_gitignore(plugin_root, report)
    validate_cross_platform(plugin_root, report)
    # Check for stale ~/.claude/settings.local.json — should not exist at user level
    _check_stale_user_settings_local(report)
    validate_md_content_references(plugin_root, report)
    validate_workflow_inline_python(plugin_root, report)
    validate_pipeline_readiness(plugin_root, report)
    validate_workflow_best_practices(plugin_root, report)
    # Submodule + language + lockfile detection (TRDD-79638eb6)
    validate_submodule_containment(plugin_root, report)
    detected_languages = validate_project_languages(plugin_root, report)
    validate_lockfiles(plugin_root, report, detected_languages)

    # Output
    if args.json:
        print_json(report)
    else:
        if args.report:
            save_report_and_print_summary(
                report, Path(args.report), "Plugin Validation", print_results, args.verbose, plugin_path=args.path
            )
        else:
            print_results(report, args.verbose)

    if args.strict:
        return report.exit_code_strict()
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
