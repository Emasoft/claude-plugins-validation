#!/usr/bin/env python3
"""
sync_marketplace_versions.py - Sync plugin versions from submodules to marketplace.json

This script reads version information from each plugin's plugin.json and updates
the corresponding entry in marketplace.json. It's designed to be run after
git submodule update to keep versions in sync.

Usage:
    python sync_marketplace_versions.py [--marketplace PATH] [--dry-run]

Exit codes:
    0 - Success (updated or already in sync)
    1 - Error (missing files, invalid JSON, etc.)
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def find_marketplace_json(start_path: Path) -> Path | None:
    """Find marketplace.json in common locations."""
    candidates = [
        start_path / ".claude-plugin" / "marketplace.json",
        start_path / "marketplace.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_json(path: Path) -> dict[str, Any] | None:
    """Load and parse a JSON file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Error loading {path}: {e}", file=sys.stderr)
        return None


def save_json(path: Path, data: dict[str, Any]) -> bool:
    """Save data to a JSON file with pretty formatting."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        return True
    except Exception as e:
        print(f"Error saving {path}: {e}", file=sys.stderr)
        return False


def get_plugin_version(plugin_dir: Path) -> str | None:
    """Get version from a plugin's plugin.json."""
    plugin_json_path = plugin_dir / ".claude-plugin" / "plugin.json"
    if not plugin_json_path.exists():
        return None

    data = load_json(plugin_json_path)
    if data is None:
        return None

    return data.get("version")


def sync_versions(
    marketplace_path: Path, dry_run: bool = False, verbose: bool = True
) -> tuple[bool, list[str]]:
    """
    Sync plugin versions from submodules to marketplace.json.

    Args:
        marketplace_path: Path to marketplace.json
        dry_run: If True, don't write changes
        verbose: If True, print progress

    Returns:
        Tuple of (success, list of updated plugin names)
    """
    marketplace_dir = marketplace_path.parent
    if marketplace_path.parent.name == ".claude-plugin":
        marketplace_dir = marketplace_path.parent.parent

    # Load marketplace.json
    marketplace_data = load_json(marketplace_path)
    if marketplace_data is None:
        return False, []

    plugins = marketplace_data.get("plugins", [])
    if not plugins:
        if verbose:
            print("No plugins found in marketplace.json")
        return True, []

    updated_plugins: list[str] = []
    changes_made = False

    for plugin in plugins:
        plugin_name = plugin.get("name", "")
        if not plugin_name:
            continue

        # Determine plugin directory
        source = plugin.get("source", f"./{plugin_name}")
        if source.startswith("./"):
            plugin_dir = marketplace_dir / source[2:]
        else:
            plugin_dir = marketplace_dir / plugin_name

        if not plugin_dir.exists():
            if verbose:
                print(f"  [SKIP] {plugin_name}: directory not found")
            continue

        # Get version from plugin.json
        actual_version = get_plugin_version(plugin_dir)
        if actual_version is None:
            if verbose:
                print(f"  [SKIP] {plugin_name}: could not read version")
            continue

        marketplace_version = plugin.get("version", "")

        if actual_version != marketplace_version:
            if verbose:
                print(f"  [UPDATE] {plugin_name}: {marketplace_version} -> {actual_version}")
            plugin["version"] = actual_version
            updated_plugins.append(plugin_name)
            changes_made = True
        else:
            if verbose:
                print(f"  [OK] {plugin_name}: {actual_version}")

    # Save changes
    if changes_made and not dry_run:
        if not save_json(marketplace_path, marketplace_data):
            return False, updated_plugins

    return True, updated_plugins


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Sync plugin versions from submodules to marketplace.json"
    )
    parser.add_argument(
        "--marketplace",
        type=Path,
        default=None,
        help="Path to marketplace.json (auto-detected if not specified)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without making changes",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress output except errors",
    )

    args = parser.parse_args()

    # Find marketplace.json
    if args.marketplace:
        marketplace_path = args.marketplace
    else:
        marketplace_path = find_marketplace_json(Path.cwd())

    if marketplace_path is None:
        print("Error: Could not find marketplace.json", file=sys.stderr)
        print("Use --marketplace to specify the path", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"Syncing versions in {marketplace_path}")
        if args.dry_run:
            print("(dry run - no changes will be made)")

    success, updated = sync_versions(
        marketplace_path, dry_run=args.dry_run, verbose=not args.quiet
    )

    if not success:
        return 1

    if not args.quiet:
        if updated:
            print(f"\nUpdated {len(updated)} plugin(s): {', '.join(updated)}")
        else:
            print("\nAll versions are in sync")

    return 0


if __name__ == "__main__":
    sys.exit(main())
