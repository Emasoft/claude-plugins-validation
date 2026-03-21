#!/usr/bin/env python3
"""Marketplace management for Claude Code plugins.

Manages GitHub marketplace registrations by delegating to the `claude` CLI:
- Add/remove/list/update marketplace registrations
- Normalize GitHub URL formats (HTTPS, SSH, git://, owner/repo)
- Require and invoke the `claude plugin marketplace` subcommand

Usage:
    uv run scripts/manage_marketplace.py add <owner/repo>
    uv run scripts/manage_marketplace.py remove <name>
    uv run scripts/manage_marketplace.py list [--json]
    uv run scripts/manage_marketplace.py update [name]
"""

import os
import re
import shutil
import subprocess
import sys
from typing import List

from cpv_management_common import (
    BOLD,
    KNOWN_MARKETPLACES_FILE,
    MARKETPLACES_DIR,
    NC,
    err,
    info,
    load_json_safe,
    ok,
    save_json_safe,
)

__all__ = [
    "_require_claude_cli",
    "_run_claude_plugin",
    "_normalize_github_source",
    "do_marketplace",
    "_marketplace_help",
    "main",
]


def _require_claude_cli() -> str:
    """Return path to `claude` binary, or exit with error if not found."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        err("Claude Code CLI ('claude') not found on PATH.")
        err("Install Claude Code first: https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)
    return claude_bin


def _run_claude_plugin(args: List[str], quiet: bool = False) -> int:
    """Run `claude plugin <args>` and stream stdout/stderr. Returns exit code.
    Removes CLAUDECODE env var so claude doesn't refuse to run inside an agent."""
    claude_bin = _require_claude_cli()
    cmd = [claude_bin, "plugin"] + args
    if not quiet:
        info(f"Running: claude plugin {' '.join(args)}")
    # Remove env vars that prevent claude from running inside another claude instance
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
    }
    try:
        result = subprocess.run(cmd, env=env, timeout=300)
        return result.returncode
    except subprocess.TimeoutExpired:
        err("Command timed out after 5 minutes.")
        return 1
    except OSError as e:
        err(f"Failed to run claude: {e}")
        return 1


def _normalize_github_source(source: str) -> str:
    """Normalize any GitHub URL form to owner/repo for claude plugin marketplace add.
    Accepts: full URLs, git:// URLs, .git suffixes, ssh URLs, and owner/repo."""
    s = source.strip().rstrip("/")
    # Strip query strings and fragment identifiers from full URLs before pattern matching.
    # Preserve #ref and @ref for bare owner/repo format (used as branch/tag ref separators).
    if s.startswith(("http://", "https://", "git://", "ssh://", "git@")):
        s = s.split("?")[0].split("#")[0].rstrip("/")
    # https://github.com/owner/repo[.git][/...]
    m = re.match(r"https?://github\.com/([^/]+/[^/]+?)(?:\.git)?(?:/.*)?$", s)
    if m:
        return m.group(1)
    # git@github.com:owner/repo[.git]
    m = re.match(r"git@github\.com:([^/]+/[^/]+?)(?:\.git)?$", s)
    if m:
        return m.group(1)
    # git://github.com/owner/repo[.git]
    m = re.match(r"git://github\.com/([^/]+/[^/]+?)(?:\.git)?$", s)
    if m:
        return m.group(1)
    # ssh://git@github.com/owner/repo[.git]
    m = re.match(r"ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?$", s)
    if m:
        return m.group(1)
    # Already owner/repo or owner/repo.git
    if "/" in s and not s.startswith(("http", "git@", "git://", "ssh://")):
        return s.removesuffix(".git")
    return s


def do_marketplace(argv: List[str]):
    """Handle `manage_marketplace <subcommand> [args]`."""
    if not argv:
        _marketplace_help()
        sys.exit(1)

    # Extract --quiet / -q before routing subcommands
    quiet = "--quiet" in argv or "-q" in argv
    argv = [a for a in argv if a not in ("--quiet", "-q")]

    subcmd = argv[0]
    rest = argv[1:]

    if subcmd == "add":
        if not rest:
            err(
                "Usage: manage_marketplace add <source> [--scope <scope>] [--sparse <paths>...]"
            )
            sys.exit(1)
        # Normalize the source (first positional arg) — accept any GitHub URL form
        normalized = _normalize_github_source(rest[0])
        if not quiet and normalized != rest[0]:
            info(f"Normalized source: {rest[0]} → {normalized}")
        cmd = ["marketplace", "add", normalized] + rest[1:]
        rc = _run_claude_plugin(cmd, quiet=quiet)
        sys.exit(rc)

    elif subcmd == "remove" or subcmd == "rm":
        if not rest:
            err("Usage: manage_marketplace remove <name>")
            sys.exit(1)
        mp_name = rest[0]
        cmd = ["marketplace", "remove"] + rest
        rc = _run_claude_plugin(cmd, quiet=quiet)
        # Post-cleanup: ensure known_marketplaces.json is cleaned too
        # (Claude CLI should handle this, but belt-and-suspenders)
        mp_dir = MARKETPLACES_DIR / mp_name
        if not mp_dir.exists() and KNOWN_MARKETPLACES_FILE.exists():
            km = load_json_safe(KNOWN_MARKETPLACES_FILE)
            if km.pop(mp_name, None) is not None:
                save_json_safe(KNOWN_MARKETPLACES_FILE, km)
                if not quiet:
                    ok(f"Cleaned '{mp_name}' from known_marketplaces.json")
        sys.exit(rc)

    elif subcmd == "list" or subcmd == "ls":
        cmd = ["marketplace", "list"] + rest
        rc = _run_claude_plugin(cmd, quiet=quiet)
        sys.exit(rc)

    elif subcmd == "update":
        # Optional marketplace name — updates all if omitted
        cmd = ["marketplace", "update"] + rest
        rc = _run_claude_plugin(cmd, quiet=quiet)
        sys.exit(rc)

    elif subcmd in ("help", "--help", "-h"):
        _marketplace_help()
        sys.exit(0)

    else:
        err(f"Unknown marketplace subcommand: {subcmd}")
        _marketplace_help()
        sys.exit(1)


def _marketplace_help():
    print(f"""{BOLD}manage_marketplace{NC} — Manage Claude Code marketplaces

{BOLD}Usage:{NC}
  manage_marketplace <command> [args]

{BOLD}Commands:{NC}
  add <source> [--scope <scope>] [--sparse <paths>...]
      Add a marketplace from a GitHub repo (owner/repo), URL, or local path.

  remove <name>
      Remove a configured marketplace.

  list [--json]
      List all configured marketplaces.

  update [name]
      Update marketplace(s) from their source. Updates all if no name given.

{BOLD}Examples:{NC}
  # Add a GitHub marketplace
  manage_marketplace add anthropics/claude-plugins-official

  # Add with sparse checkout (monorepo — only fetch specific dirs)
  manage_marketplace add owner/repo --sparse .claude-plugin plugins

  # List all marketplaces
  manage_marketplace list

  # Update all marketplaces
  manage_marketplace update

  # Update a specific marketplace
  manage_marketplace update my-marketplace

  # Remove a marketplace
  manage_marketplace remove old-marketplace
""")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        _marketplace_help()
        sys.exit(0)
    do_marketplace(sys.argv[1:])


if __name__ == "__main__":
    main()
