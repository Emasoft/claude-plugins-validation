#!/usr/bin/env python3
"""Bump version in plugin.json and pyproject.toml — thin wrapper.

Delegates to ``publish.py``'s ``bump_semver`` / ``do_bump`` /
``get_current_version`` helpers so there is exactly ONE source of truth
for version-bump logic in this repo. Previously this file shipped its own
copy of the bump code, which could drift from publish.py's implementation
(audit finding MINOR — see docs_dev/recent-changes-audit_20260413_215434.md).

This script DOES NOT push, commit, tag, or run any pipeline gates. For a
full release, use ``scripts/publish.py --patch|--minor|--major`` instead.
This standalone is only useful during development when you want to bump
the version files without triggering the publish flow.

Usage:
    uv run scripts/bump_version.py --patch     # 1.2.0 -> 1.2.1
    uv run scripts/bump_version.py --minor     # 1.2.0 -> 1.3.0
    uv run scripts/bump_version.py --major     # 1.2.0 -> 2.0.0
    uv run scripts/bump_version.py --set 2.0.0 # explicit version
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Make publish.py importable when run via `uv run` or `python scripts/...`
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# publish.py is the single source of truth for bump logic
from publish import bump_semver, do_bump, get_current_version  # noqa: E402

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bump plugin version (delegates to publish.py helpers — single source of truth)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--patch", action="store_true", help="Bump patch version")
    group.add_argument("--minor", action="store_true", help="Bump minor version")
    group.add_argument("--major", action="store_true", help="Bump major version")
    group.add_argument("--set", dest="set_version", type=str, help="Set explicit version (x.y.z)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    plugin_root = _SCRIPT_DIR.parent
    current = get_current_version(plugin_root)
    if current is None:
        print("ERROR: cannot read current version from .claude-plugin/plugin.json", file=sys.stderr)
        return 1

    new_version: str
    if args.set_version is not None:
        if not _SEMVER_RE.match(args.set_version):
            print(f"ERROR: '{args.set_version}' is not valid semver (x.y.z)", file=sys.stderr)
            return 1
        new_version = args.set_version
    else:
        bump_type = "major" if args.major else "minor" if args.minor else "patch"
        bumped = bump_semver(current, bump_type)
        if bumped is None:
            print(f"ERROR: '{current}' is not valid semver (x.y.z)", file=sys.stderr)
            return 1
        new_version = bumped

    if new_version == current:
        print(f"Version unchanged: {current}")
        return 0

    if args.dry_run:
        print(f"Would bump: {current} -> {new_version}")
        return 0

    if not do_bump(plugin_root, new_version, dry_run=False):
        print(f"ERROR: bump failed ({current} -> {new_version})", file=sys.stderr)
        return 1
    print(f"\nVersion bumped: {current} -> {new_version}")
    print("(Use 'scripts/publish.py --patch|--minor|--major' for the full release pipeline)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
