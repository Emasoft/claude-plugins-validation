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


# ── Validation entry points ──────────────────────────────────────────


def validate_plugin() -> None:
    """Main plugin validator (orchestrates all 17 sub-validators)."""
    from validate_plugin import main

    sys.exit(main())


def validate_skill() -> None:
    """Comprehensive skill validator (190+ rules)."""
    from validate_skill_comprehensive import main

    sys.exit(main())


def validate_hooks() -> None:
    """Hooks configuration validator."""
    from validate_hook import main

    sys.exit(main())


def validate_agents() -> None:
    """Agent .md file validator."""
    from validate_agent import main

    sys.exit(main())


def validate_command() -> None:
    """Command .md file validator."""
    from validate_command import main

    sys.exit(main())


def validate_security() -> None:
    """Security vulnerability scanner."""
    from validate_security import main

    sys.exit(main())


def validate_scoring() -> None:
    """Quality score calculator."""
    from validate_scoring import main

    sys.exit(main())


def validate_enterprise() -> None:
    """Enterprise compliance validator."""
    from validate_enterprise import main

    sys.exit(main())


def validate_marketplace() -> None:
    """Marketplace manifest validator."""
    from validate_marketplace import main

    sys.exit(main())


def validate_encoding() -> None:
    """File encoding validator."""
    from validate_encoding import main

    sys.exit(main())


def validate_documentation() -> None:
    """Documentation completeness checker."""
    from validate_documentation import main

    sys.exit(main())


def validate_mcp() -> None:
    """MCP server config validator."""
    from validate_mcp import main

    sys.exit(main())


def validate_lsp() -> None:
    """LSP server config validator."""
    from validate_lsp import main

    sys.exit(main())


def validate_rules() -> None:
    """Rules directory validator."""
    from validate_rules import main

    sys.exit(main())


def validate_xref() -> None:
    """Cross-reference validator."""
    from validate_xref import main

    sys.exit(main())


# ── Management entry points ──────────────────────────────────────────


def doctor() -> None:
    """Health-check installed plugins, settings, and marketplaces."""
    from manage_doctor import main

    sys.exit(main())


def standardize() -> None:
    """Audit and fix plugin/marketplace to match CPV standards."""
    from standardize_plugin import main

    sys.exit(main())
