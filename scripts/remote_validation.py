#!/usr/bin/env python3
"""Remote validation launcher — run any CPV script from GitHub without local interference.

This is the canonical entry point for running CPV validation remotely (from the
plugin cache, via uvx, or any other context where CPV scripts live outside the
target being validated).

It isolates the environment so that the target plugin's local files (pyproject.toml,
.mypy.ini, setup.cfg, stale copies of cpv_validation_common.py, etc.) cannot
interfere with CPV's own scripts — while still catching real errors like missing
imports that would break hooks and scripts at runtime.

Usage:
    # Short aliases:
    cpv-remote-validate plugin /path/to/target
    cpv-remote-validate skill /path/to/skill --strict
    cpv-remote-validate agent /path/to/plugin
    cpv-remote-validate hook /path/to/hooks.json
    cpv-remote-validate security /path/to/plugin
    cpv-remote-validate lint /path/to/plugin

    # Save report to a file:
    cpv-remote-validate plugin /path/to/target -o report.md

    # Full script names also work:
    cpv-remote-validate validate_plugin /path/to/target --verbose

    # From the CPV plugin cache:
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" plugin /path/to/target

    # Via uvx from GitHub:
    uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml \\
        cpv-remote-validate plugin /path/to/target
"""

from __future__ import annotations

import argparse
import atexit
import os
import sys
import tempfile
from pathlib import Path

# ── Environment isolation (runs at import time) ──────────────────────────

# CPV's scripts directory (where this file lives)
_cpv_scripts_dir = os.path.dirname(os.path.abspath(__file__))

# 1. Ensure CPV's scripts dir is FIRST on sys.path.
# Purge ALL occurrences (list.remove only removes the first one, which would
# leave stale duplicates later in sys.path and could shadow CPV modules if
# another importer prepends a different path later).
while _cpv_scripts_dir in sys.path:
    sys.path.remove(_cpv_scripts_dir)
sys.path.insert(0, _cpv_scripts_dir)

# 2. Write a temporary mypy config with safe remote-validation defaults.
_MYPY_REMOTE_CONFIG = """\
[mypy]
ignore_missing_imports = True
warn_return_any = False
warn_unused_configs = False
disable_error_code = no-any-return, import-untyped
"""

_tmpfile = tempfile.NamedTemporaryFile(mode="w", suffix=".ini", prefix="cpv_mypy_", delete=False)
_tmpfile.write(_MYPY_REMOTE_CONFIG)
_tmpfile.close()
# Use missing_ok=True: if the OS/tmp cleaner already removed the file (e.g.
# long-running session, sandbox cleanup), os.unlink would raise and Python's
# atexit machinery prints an ugly traceback to stderr on interpreter exit.
atexit.register(lambda: Path(_tmpfile.name).unlink(missing_ok=True))

os.environ["MYPY_CONFIG_FILE"] = _tmpfile.name

# 3. Strip MYPYPATH/PYTHONPATH — prevents stale module resolution
os.environ.pop("MYPYPATH", None)
os.environ.pop("PYTHONPATH", None)

# 4. Mark remote validation mode (scripts check this to skip --config-file)
os.environ["CPV_REMOTE_VALIDATION"] = "1"


# ── Script mapping ───────────────────────────────────────────────────────

# Short aliases → full script module names
_ALIASES: dict[str, str] = {
    # Short names (user-friendly)
    "plugin": "validate_plugin",
    "skill": "validate_skill_comprehensive",
    "hook": "validate_hook",
    "hooks": "validate_hook",
    "agent": "validate_agent",
    "agents": "validate_agent",
    "command": "validate_command",
    "security": "validate_security",
    "scoring": "validate_scoring",
    "marketplace": "validate_marketplace",
    "enterprise": "validate_enterprise",
    "mcp": "validate_mcp",
    "lsp": "validate_lsp",
    "docs": "validate_documentation",
    "documentation": "validate_documentation",
    "encoding": "validate_encoding",
    "rules": "validate_rules",
    "xref": "validate_xref",
    "lint": "lint_files",
    "doctor": "manage_doctor",
    "registry": "manage_registry",
    "github": "manage_github_validate",
    "standardize": "standardize_plugin",
    # Scope validators (validate a project's .claude/ config, separating
    # git-tracked "project" elements from non-git-tracked "local" elements)
    "local-scope": "validate_local_scope",
    "project-scope": "validate_project_scope",
    "cpv-validate-local-scope": "validate_local_scope",
    "cpv-validate-project-scope": "validate_project_scope",
    # Full script names (also accepted)
    "validate_plugin": "validate_plugin",
    "validate_skill": "validate_skill_comprehensive",
    "validate_skill_comprehensive": "validate_skill_comprehensive",
    "validate_hook": "validate_hook",
    "validate_hooks": "validate_hook",
    "validate_agent": "validate_agent",
    "validate_agents": "validate_agent",
    "validate_command": "validate_command",
    "validate_security": "validate_security",
    "validate_scoring": "validate_scoring",
    "validate_marketplace": "validate_marketplace",
    "validate_enterprise": "validate_enterprise",
    "validate_mcp": "validate_mcp",
    "validate_lsp": "validate_lsp",
    "validate_documentation": "validate_documentation",
    "validate_encoding": "validate_encoding",
    "validate_rules": "validate_rules",
    "validate_xref": "validate_xref",
    "validate_marketplace_pipeline": "validate_marketplace_pipeline",
    "lint_files": "lint_files",
    "manage_doctor": "manage_doctor",
    "manage_registry": "manage_registry",
    "manage_github_validate": "manage_github_validate",
    "standardize_plugin": "standardize_plugin",
    "standardize_marketplace": "standardize_marketplace",
    "bump_version": "bump_version",
    "validate_local_scope": "validate_local_scope",
    "validate_project_scope": "validate_project_scope",
}

# For --help display: short alias → description
_COMMANDS: dict[str, str] = {
    "plugin": "Full plugin validation (all 17 checks + linting)",
    "skill": "Skill validation (190+ rules)",
    "hook": "Hook configuration validation (27 events, 4 types)",
    "agent": "Agent definition validation",
    "command": "Command definition validation",
    "security": "Security vulnerability scan",
    "scoring": "Quality score calculation",
    "marketplace": "Marketplace manifest validation",
    "enterprise": "Enterprise compliance check",
    "mcp": "MCP server config validation",
    "lsp": "LSP server config validation",
    "docs": "Documentation completeness check",
    "encoding": "File encoding validation (UTF-8, BOM, line endings)",
    "rules": "Rules directory validation",
    "xref": "Cross-reference validation",
    "lint": "Lint all scripts (Python, Shell, JS, PowerShell, Go, Rust)",
    "doctor": "Health-check installed plugins and settings",
    "standardize": "Audit and fix plugin repo to match standards",
    "local-scope": "Local scope validation (non-git-tracked .claude/ elements)",
    "project-scope": "Project scope validation (git-tracked .claude/ elements)",
}


def main() -> int:
    # Build help text with command table
    commands_help = "\n".join(f"  {name:<16s} {desc}" for name, desc in _COMMANDS.items())

    parser = argparse.ArgumentParser(
        prog="cpv-remote-validate",
        description="CPV Remote Validation Launcher — validate Claude Code plugins with full environment isolation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available commands:\n{commands_help}\n\n"
        "Examples:\n"
        "  cpv-remote-validate plugin /path/to/plugin\n"
        "  cpv-remote-validate skill /path/to/skill --strict\n"
        "  cpv-remote-validate plugin /path/to/plugin -o report.md --verbose\n"
        "  cpv-remote-validate lint /path/to/plugin\n",
    )
    parser.add_argument(
        "script",
        help="Validation command (e.g., plugin, skill, hook, security, lint)",
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="Path to the plugin, skill, or file to validate",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="Save the validation report to FILE (passed as --report to the script)",
    )

    # Parse only the known args — the rest are passed through to the script
    args, extra = parser.parse_known_args()

    script_name = args.script
    if script_name.endswith(".py"):
        script_name = script_name[:-3]

    if script_name not in _ALIASES:
        parser.error(f"Unknown command: '{script_name}'\nAvailable: {', '.join(sorted(_COMMANDS))}")

    module_name = _ALIASES[script_name]

    # Build the argv for the target script
    script_argv = [os.path.join(_cpv_scripts_dir, module_name + ".py")]
    if args.target:
        script_argv.append(args.target)
    if args.output:
        script_argv.extend(["--report", args.output])
    script_argv.extend(extra)

    sys.argv = script_argv

    # Import and run
    import importlib

    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        # e.g. stale alias pointing at a renamed script. Give a readable
        # error instead of a raw traceback, which users otherwise blame on
        # their own environment.
        print(f"cpv-remote-validate: failed to import '{module_name}': {e}", file=sys.stderr)
        return 1

    main_fn = getattr(module, "main", None)
    if not callable(main_fn):
        print(
            f"cpv-remote-validate: target script '{module_name}.py' has no callable main()",
            file=sys.stderr,
        )
        return 1

    result = main_fn()
    # Scripts that fall off the end of main() return None; passing None to
    # sys.exit silently reports exit code 0 even on a missed-return bug.
    # Coerce None → 0 explicitly and non-int returns → 1 so the exit code
    # always matches the underlying intent.
    if result is None:
        return 0
    if isinstance(result, int):
        return result
    return 1


if __name__ == "__main__":
    sys.exit(main())
