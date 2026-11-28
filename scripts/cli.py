#!/usr/bin/env python3
"""CLI entry points for uvx/pip console_scripts.

These thin wrappers fix sys.path so bare imports (e.g. ``from cpv_validation_common import ...``)
resolve correctly, then delegate to each script's main().

Usage via uvx (no install required):
    uvx --from git+https://github.com/Emasoft/claude-plugins-validation cpv-validate /path/to/plugin
    uvx --from git+https://github.com/Emasoft/claude-plugins-validation cpv-validate-skill /path/to/skill
    uvx --from git+https://github.com/Emasoft/claude-plugins-validation cpv-validate-security /path/to/plugin

Usage after pip/uv install:
    cpv-validate /path/to/plugin --verbose --report report.md
    cpv-validate-skill /path/to/skill --strict
"""

from __future__ import annotations

import os
import sys

# Ensure the scripts directory is on sys.path so bare imports work
# (e.g. ``from cpv_validation_common import ...`` inside each script).
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)


# ── Bytecode isolation ───────────────────────────────────────────────


def _no_bytecode_cache() -> None:
    """Make this process and its child interpreters compile every CPV module from source.

    #256: never read or write bytecode in a process that runs the integrity check. A
    __pycache__/*.pyc whose header matches the verified .py is loaded INSTEAD of it, so a
    planted pyc runs unverified code while verify_self_integrity() passes (and a pyc for the
    verifier itself makes the check pass for a tampered source). It must run before the
    wrapper imports the first CPV module, and the statements stay inline in this file: a
    shared helper module would itself be loaded from a pyc. Pointing the prefix at this file
    (an existing regular file, absolute so a changed working directory cannot matter) makes
    every cache read fail with OSError, so every module is compiled from source; no trusted
    cache directory is introduced. A same-file function rather than module-level code, so
    that tests importing cli leave their own process untouched.
    """
    sys.dont_write_bytecode = True
    sys.pycache_prefix = os.path.abspath(__file__)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PYTHONPYCACHEPREFIX"] = os.path.abspath(__file__)


# ── Validation entry points ──────────────────────────────────────────


def validate_plugin() -> None:
    """Main plugin validator (orchestrates all 17 sub-validators)."""
    _no_bytecode_cache()
    from validate_plugin import main

    sys.exit(main())


def validate_skill() -> None:
    """Comprehensive skill validator (190+ rules)."""
    _no_bytecode_cache()
    from validate_skill_comprehensive import main

    sys.exit(main())


def validate_hooks() -> None:
    """Hooks configuration validator."""
    _no_bytecode_cache()
    from validate_hook import main

    sys.exit(main())


def validate_agents() -> None:
    """Agent .md file validator."""
    _no_bytecode_cache()
    from validate_agent import main

    sys.exit(main())


def validate_command() -> None:
    """Command .md file validator."""
    _no_bytecode_cache()
    from validate_command import main

    sys.exit(main())


def validate_security() -> None:
    """Security vulnerability scanner."""
    _no_bytecode_cache()
    from validate_security import main

    sys.exit(main())


def validate_scoring() -> None:
    """Quality score calculator."""
    _no_bytecode_cache()
    from validate_scoring import main

    sys.exit(main())


def validate_enterprise() -> None:
    """Enterprise compliance validator."""
    _no_bytecode_cache()
    from validate_enterprise import main

    sys.exit(main())


def validate_marketplace() -> None:
    """Marketplace manifest validator."""
    _no_bytecode_cache()
    from validate_marketplace import main

    sys.exit(main())


def validate_encoding() -> None:
    """File encoding validator."""
    _no_bytecode_cache()
    from validate_encoding import main

    sys.exit(main())


def validate_documentation() -> None:
    """Documentation completeness checker."""
    _no_bytecode_cache()
    from validate_documentation import main

    sys.exit(main())


def validate_mcp() -> None:
    """MCP server config validator."""
    _no_bytecode_cache()
    from validate_mcp import main

    sys.exit(main())


def validate_lsp() -> None:
    """LSP server config validator."""
    _no_bytecode_cache()
    from validate_lsp import main

    sys.exit(main())


def validate_rules() -> None:
    """Rules directory validator."""
    _no_bytecode_cache()
    from validate_rules import main

    sys.exit(main())


def validate_xref() -> None:
    """Cross-reference validator."""
    _no_bytecode_cache()
    from validate_xref import main

    sys.exit(main())


def validate_dependencies() -> None:
    """Dependency-graph (cascade) validator (TRDD-747d7bbc)."""
    _no_bytecode_cache()
    from validate_dependencies import main

    sys.exit(main())


def validate_telemetry() -> None:
    """OTEL telemetry supply-chain risk validator (TRDD-e3e74f69)."""
    _no_bytecode_cache()
    from validate_telemetry import main

    sys.exit(main())


def validate_project_scope() -> None:
    """Validate git-tracked (project-scope) Claude Code configuration."""
    _no_bytecode_cache()
    from validate_project_scope import main

    sys.exit(main())


def validate_local_scope() -> None:
    """Validate non-git-tracked (local-scope) Claude Code configuration."""
    _no_bytecode_cache()
    from validate_local_scope import main

    sys.exit(main())


def validate_ide_config() -> None:
    """IDE-config hygiene validator (NIT-level env-var-reference checks).

    Companion to validate_security.scan_ide_config_files — that pass catches
    REAL secret VALUES at CRITICAL severity. This validator adds the
    NIT-level "env-var name looks credential-like" warnings called out under
    TRDD-8ccb9337 §"Additional: warn on .env in IDE configs".
    """
    _no_bytecode_cache()
    from validate_ide_config import main

    sys.exit(main())


# ── Management entry points ──────────────────────────────────────────


def doctor() -> None:
    """Health-check installed plugins, settings, and marketplaces."""
    _no_bytecode_cache()
    from manage_doctor import main

    sys.exit(main())


def standardize() -> None:
    """Audit and fix plugin/marketplace to match CPV standards."""
    _no_bytecode_cache()
    from standardize_plugin import main

    sys.exit(main())


def setup_auth() -> None:
    """Read-only orchestrator for the eight CPV auth surfaces (TRDD-b5e44619).

    Reports SET / NOT SET / PARTIAL / N/A for each of: git identity, gh CLI
    auth, ssh-agent, MARKETPLACE_PAT env var, branch-rules helper presence,
    pre-push hook, commit signing, and external scanners. Use --strict to
    exit non-zero when any required surface (1, 2, 6) is unset.
    """
    _no_bytecode_cache()
    from cpv_setup_auth import main

    sys.exit(main())
