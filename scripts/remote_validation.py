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
    # From the CPV plugin cache:
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" validate_plugin /path/to/target --verbose

    # Via uvx from GitHub:
    uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-remote-validate validate_plugin /path/to/target

    # Shorthand — script name without .py extension:
    python3 remote_validation.py validate_plugin /path/to/target

What it does:
    1. Writes a temporary mypy.ini with safe defaults (ignore-missing-imports ON,
       but no mypy_path or explicit_package_bases that could resolve stale modules)
    2. Sets MYPY_CONFIG_FILE to that temp file — overrides target's pyproject.toml
    3. Strips MYPYPATH and PYTHONPATH — prevents stale module resolution
    4. Puts CPV's scripts/ dir first on sys.path — ensures CPV's own imports win
    5. Delegates to the requested CPV script's main()
    6. Cleans up the temp config on exit
"""

from __future__ import annotations

import atexit
import os
import sys
import tempfile

# ── Environment isolation ───────────────────────────────────────────────

# CPV's scripts directory (where this file lives)
_cpv_scripts_dir = os.path.dirname(os.path.abspath(__file__))

# 1. Ensure CPV's scripts dir is FIRST on sys.path
if _cpv_scripts_dir in sys.path:
    sys.path.remove(_cpv_scripts_dir)
sys.path.insert(0, _cpv_scripts_dir)

# 2. Write a temporary mypy config with safe remote-validation defaults.
#    This catches real errors (missing imports that break at runtime) while
#    preventing the target's pyproject.toml from resolving stale CPV modules
#    via mypy_path or explicit_package_bases.
_MYPY_REMOTE_CONFIG = """\
[mypy]
ignore_missing_imports = True
warn_return_any = False
warn_unused_configs = False
disable_error_code = no-any-return, import-untyped
"""

_tmpfile = tempfile.NamedTemporaryFile(
    mode="w", suffix=".ini", prefix="cpv_mypy_", delete=False
)
_tmpfile.write(_MYPY_REMOTE_CONFIG)
_tmpfile.close()
atexit.register(lambda: os.unlink(_tmpfile.name))

os.environ["MYPY_CONFIG_FILE"] = _tmpfile.name

# 3. Strip MYPYPATH — prevents stale module search paths from leaking in
os.environ.pop("MYPYPATH", None)

# 4. Strip PYTHONPATH — prevents local Python modules from shadowing CPV's
os.environ.pop("PYTHONPATH", None)

# 5. Mark that we're running in remote validation mode (scripts can check this)
os.environ["CPV_REMOTE_VALIDATION"] = "1"


# ── Script dispatch ─────────────────────────────────────────────────────

# Map of short names → module names (without .py)
_SCRIPT_MAP = {
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
}


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: remote_validation.py <script_name> [args...]", file=sys.stderr)
        print(f"\nAvailable scripts: {', '.join(sorted(_SCRIPT_MAP))}", file=sys.stderr)
        return 1

    script_name = sys.argv[1]

    # Strip .py extension if provided
    if script_name.endswith(".py"):
        script_name = script_name[:-3]

    if script_name not in _SCRIPT_MAP:
        print(f"Unknown script: {script_name}", file=sys.stderr)
        print(f"Available: {', '.join(sorted(_SCRIPT_MAP))}", file=sys.stderr)
        return 1

    module_name = _SCRIPT_MAP[script_name]

    # Shift argv so the target script sees its own args correctly
    sys.argv = sys.argv[1:]
    # Replace script name with full path for argparse usage messages
    sys.argv[0] = os.path.join(_cpv_scripts_dir, module_name + ".py")

    # Import and run
    import importlib

    module = importlib.import_module(module_name)
    result: int = module.main()
    return result


if __name__ == "__main__":
    sys.exit(main())
