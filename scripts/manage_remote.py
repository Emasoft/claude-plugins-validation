#!/usr/bin/env python3
"""Remote plugin management for Claude Code plugins.

Manages remote plugins from registered GitHub marketplaces by
delegating to the `claude` CLI:
- Install/update/uninstall remote plugins
- Enable/disable remote plugins
- List available/installed remote plugins

Usage:
    uv run scripts/manage_remote.py install <plugin>@<marketplace> [--scope user|project|local]
    uv run scripts/manage_remote.py update <plugin>@<marketplace>
    uv run scripts/manage_remote.py uninstall <plugin>@<marketplace>
    uv run scripts/manage_remote.py list [--available] [--json]
    uv run scripts/manage_remote.py enable <plugin>@<marketplace>
    uv run scripts/manage_remote.py disable <plugin>@<marketplace>
"""

import sys
from typing import List

from manage_marketplace import _require_claude_cli, _run_claude_plugin
from cpv_management_common import err

__all__ = ["do_remote", "_remote_help"]

# ── Color constants ──────────────────────────────────────
# Match the style used by the rest of the management suite.
_C = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
BOLD = "\033[1m" if _C else ""
NC = "\033[0m" if _C else ""


# ── Remote subcommand dispatch ───────────────────────────


def do_remote(argv: List[str]):
    """Handle `claude-plugin-install remote <subcommand> [args]`."""
    if not argv:
        _remote_help()
        sys.exit(1)

    # Extract --quiet / -q before routing subcommands
    quiet = "--quiet" in argv or "-q" in argv
    argv = [a for a in argv if a not in ("--quiet", "-q")]

    subcmd = argv[0]
    rest = argv[1:]

    if subcmd == "install" or subcmd == "i":
        if not rest:
            err(
                "Usage: claude-plugin-install remote install <plugin[@marketplace]> [--scope <scope>]"
            )
            sys.exit(1)
        cmd = ["install"] + rest
        rc = _run_claude_plugin(cmd, quiet=quiet)
        sys.exit(rc)

    elif subcmd == "update":
        if not rest:
            err(
                "Usage: claude-plugin-install remote update <plugin[@marketplace]> [--scope <scope>]"
            )
            sys.exit(1)
        cmd = ["update"] + rest
        rc = _run_claude_plugin(cmd, quiet=quiet)
        sys.exit(rc)

    elif subcmd == "uninstall" or subcmd == "remove" or subcmd == "rm":
        if not rest:
            err(
                "Usage: claude-plugin-install remote uninstall <plugin[@marketplace]> [--scope <scope>]"
            )
            sys.exit(1)
        cmd = ["uninstall"] + rest
        rc = _run_claude_plugin(cmd, quiet=quiet)
        sys.exit(rc)

    elif subcmd == "list" or subcmd == "ls":
        cmd = ["list"] + rest
        rc = _run_claude_plugin(cmd, quiet=quiet)
        sys.exit(rc)

    elif subcmd == "enable":
        if not rest:
            err(
                "Usage: claude-plugin-install remote enable <plugin[@marketplace]> [--scope <scope>]"
            )
            sys.exit(1)
        cmd = ["enable"] + rest
        rc = _run_claude_plugin(cmd, quiet=quiet)
        sys.exit(rc)

    elif subcmd == "disable":
        if not rest:
            err(
                "Usage: claude-plugin-install remote disable <plugin[@marketplace]> [--scope <scope>]"
            )
            sys.exit(1)
        cmd = ["disable"] + rest
        rc = _run_claude_plugin(cmd, quiet=quiet)
        sys.exit(rc)

    elif subcmd == "validate":
        if not rest:
            err("Usage: claude-plugin-install remote validate <path>")
            sys.exit(1)
        cmd = ["validate"] + rest
        rc = _run_claude_plugin(cmd, quiet=quiet)
        sys.exit(rc)

    elif subcmd in ("help", "--help", "-h"):
        _remote_help()
        sys.exit(0)

    else:
        err(f"Unknown remote subcommand: {subcmd}")
        _remote_help()
        sys.exit(1)


# ── Help ─────────────────────────────────────────────────


def _remote_help():
    print(f"""{BOLD}claude-plugin-install remote{NC} — Manage plugins from GitHub marketplaces

{BOLD}Usage:{NC}
  claude-plugin-install remote <command> <plugin[@marketplace]> [options]

{BOLD}Commands:{NC}
  install <plugin[@marketplace]> [--scope <scope>]
      Install a plugin from registered marketplaces.
      Use plugin@marketplace to target a specific marketplace.

  update <plugin[@marketplace]> [--scope <scope>]
      Update a plugin to the latest version from its marketplace.

  uninstall <plugin[@marketplace]> [--scope <scope>]
      Uninstall a remotely installed plugin.

  list [--json] [--available]
      List installed plugins. Use --available --json to see all available.

  enable <plugin[@marketplace]> [--scope <scope>]
      Enable a disabled plugin.

  disable <plugin[@marketplace]> [--scope <scope>]
      Disable an installed plugin.

  validate <path>
      Validate a plugin or marketplace manifest.

{BOLD}Scopes:{NC}
  user       User-level (default, applies to all projects)
  project    Project-level (only for current project)
  local      Local-level

{BOLD}Examples:{NC}
  # Install a plugin from a registered marketplace
  claude-plugin-install remote install my-plugin@my-marketplace

  # Install with project scope
  claude-plugin-install remote install my-plugin --scope project

  # Update a plugin to latest version
  claude-plugin-install remote update my-plugin@my-marketplace

  # List all installed plugins
  claude-plugin-install remote list

  # List all available plugins (JSON)
  claude-plugin-install remote list --available --json

  # Uninstall a remote plugin
  claude-plugin-install remote uninstall my-plugin@my-marketplace

  # Enable/disable
  claude-plugin-install remote enable my-plugin@my-marketplace
  claude-plugin-install remote disable my-plugin@my-marketplace
""")


# ── Entry point ──────────────────────────────────────────


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        _remote_help()
        sys.exit(0)
    do_remote(sys.argv[1:])


if __name__ == "__main__":
    main()
