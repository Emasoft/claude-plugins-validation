#!/usr/bin/env python3
"""Bump version in plugin.json and pyproject.toml.

Usage:
    uv run scripts/bump_version.py --patch   # 1.2.0 -> 1.2.1
    uv run scripts/bump_version.py --minor   # 1.2.0 -> 1.3.0
    uv run scripts/bump_version.py --major   # 1.2.0 -> 2.0.0
    uv run scripts/bump_version.py --set 2.0.0  # explicit version
"""

import argparse
import json
import re
import sys
from pathlib import Path


def bump_version(current: str, part: str) -> str:
    """Bump a semver string by the specified part."""
    parts = current.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        print(f"ERROR: '{current}' is not valid semver (x.y.z)", file=sys.stderr)
        sys.exit(1)
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    if part == "major":
        return f"{major + 1}.0.0"
    elif part == "minor":
        return f"{major}.{minor + 1}.0"
    else:
        return f"{major}.{minor}.{patch + 1}"


def main():
    parser = argparse.ArgumentParser(description="Bump plugin version")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--patch", action="store_true", help="Bump patch version")
    group.add_argument("--minor", action="store_true", help="Bump minor version")
    group.add_argument("--major", action="store_true", help="Bump major version")
    group.add_argument("--set", type=str, help="Set explicit version (x.y.z)")
    args = parser.parse_args()

    # Resolve plugin root from script location
    script_dir = Path(__file__).resolve().parent
    plugin_root = script_dir.parent
    plugin_json = plugin_root / ".claude-plugin" / "plugin.json"
    pyproject = plugin_root / "pyproject.toml"

    if not plugin_json.exists():
        print(f"ERROR: {plugin_json} not found", file=sys.stderr)
        sys.exit(1)

    # Read current version
    manifest = json.loads(plugin_json.read_text(encoding="utf-8"))
    current = manifest.get("version", "0.0.0")

    # Compute new version
    if args.set:
        new_version = args.set
    elif args.major:
        new_version = bump_version(current, "major")
    elif args.minor:
        new_version = bump_version(current, "minor")
    else:
        new_version = bump_version(current, "patch")

    if new_version == current:
        print(f"Version unchanged: {current}")
        return

    # Update plugin.json
    manifest["version"] = new_version
    plugin_json.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"  {plugin_json.relative_to(plugin_root)}: {current} -> {new_version}")

    # Update pyproject.toml if present
    if pyproject.exists():
        content = pyproject.read_text(encoding="utf-8")
        new_content = re.sub(
            r'^(version\s*=\s*["\'])([^"\']+)(["\'])',
            rf"\g<1>{new_version}\3",
            content,
            count=1,
            flags=re.MULTILINE,
        )
        if new_content != content:
            pyproject.write_text(new_content, encoding="utf-8")
            print(f"  {pyproject.relative_to(plugin_root)}: {current} -> {new_version}")

    print(f"\nVersion bumped: {current} -> {new_version}")


if __name__ == "__main__":
    main()
