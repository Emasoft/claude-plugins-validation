#!/usr/bin/env python3
"""
update_marketplace_metadata.py - Update marketplace.json when plugin files change.

Recalculates checksums, updates timestamps, and ensures marketplace metadata
stays in sync with the actual plugin contents.

Exit codes:
    0 - Success (marketplace.json updated or already up-to-date)
    1 - Error (missing files, invalid JSON, etc.)
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def get_plugin_root() -> Path:
    """Get the plugin root directory (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent


def calculate_file_checksum(file_path: Path) -> str:
    """
    Calculate SHA-256 checksum of a file.

    Args:
        file_path: Path to the file

    Returns:
        Hex-encoded SHA-256 checksum
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def calculate_directory_checksum(dir_path: Path, exclude_patterns: Optional[list[str]] = None) -> str:
    """
    Calculate combined checksum of all files in a directory.

    Args:
        dir_path: Path to the directory
        exclude_patterns: Patterns to exclude (e.g., ['__pycache__', '.git'])

    Returns:
        Hex-encoded SHA-256 checksum of all file contents combined
    """
    exclude_patterns = exclude_patterns or ["__pycache__", ".git", ".mypy_cache", ".ruff_cache", "*.pyc"]

    sha256_hash = hashlib.sha256()

    def should_exclude(path: Path) -> bool:
        for pattern in exclude_patterns:
            if pattern.startswith("*"):
                # Suffix pattern
                if path.name.endswith(pattern[1:]):
                    return True
            else:
                # Exact match in path parts
                if pattern in path.parts:
                    return True
        return False

    # Sort files for consistent ordering
    files = sorted(f for f in dir_path.rglob("*") if f.is_file() and not should_exclude(f))

    for file_path in files:
        # Include relative path in hash for structure-awareness
        rel_path = file_path.relative_to(dir_path)
        sha256_hash.update(str(rel_path).encode("utf-8"))
        sha256_hash.update(file_path.read_bytes())

    return sha256_hash.hexdigest()


def get_plugin_version(plugin_root: Path) -> Optional[str]:
    """
    Get version from plugin.json.

    Args:
        plugin_root: Root directory of the plugin

    Returns:
        Version string or None if not found
    """
    plugin_json_path = plugin_root / ".claude-plugin" / "plugin.json"

    if not plugin_json_path.exists():
        return None

    try:
        with open(plugin_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        version: Optional[str] = data.get("version")
        return version
    except Exception:
        return None


def get_plugin_name(plugin_root: Path) -> Optional[str]:
    """
    Get name from plugin.json.

    Args:
        plugin_root: Root directory of the plugin

    Returns:
        Name string or None if not found
    """
    plugin_json_path = plugin_root / ".claude-plugin" / "plugin.json"

    if not plugin_json_path.exists():
        return None

    try:
        with open(plugin_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        name: Optional[str] = data.get("name")
        return name
    except Exception:
        return None


def load_marketplace_json(marketplace_path: Path) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """
    Load existing marketplace.json.

    Args:
        marketplace_path: Path to marketplace.json

    Returns:
        Tuple of (data_dict, error_message)
    """
    if not marketplace_path.exists():
        return None, None  # File doesn't exist yet, not an error

    try:
        with open(marketplace_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data, None
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON in marketplace.json: {e}"
    except Exception as e:
        return None, f"Error reading marketplace.json: {e}"


def create_marketplace_entry(plugin_root: Path) -> dict[str, Any]:
    """
    Create a marketplace entry for the plugin.

    Args:
        plugin_root: Root directory of the plugin

    Returns:
        Dictionary with marketplace entry data
    """
    plugin_name = get_plugin_name(plugin_root) or plugin_root.name
    plugin_version = get_plugin_version(plugin_root) or "0.0.0"

    # Calculate checksum of entire plugin directory
    plugin_checksum = calculate_directory_checksum(plugin_root)

    # Get current timestamp in ISO format
    timestamp = datetime.now(timezone.utc).isoformat()

    # Read plugin.json for additional metadata
    plugin_json_path = plugin_root / ".claude-plugin" / "plugin.json"
    plugin_metadata: dict[str, Any] = {}
    if plugin_json_path.exists():
        try:
            with open(plugin_json_path, "r", encoding="utf-8") as f:
                plugin_metadata = json.load(f)
        except Exception:
            pass

    return {
        "name": plugin_name,
        "version": plugin_version,
        "description": plugin_metadata.get("description", ""),
        "author": plugin_metadata.get("author", {}),
        "homepage": plugin_metadata.get("homepage", ""),
        "repository": plugin_metadata.get("repository", ""),
        "license": plugin_metadata.get("license", ""),
        "keywords": plugin_metadata.get("keywords", []),
        "checksum": plugin_checksum,
        "checksum_algorithm": "sha256",
        "last_modified": timestamp,
        "source": {"type": "local", "path": str(plugin_root.resolve())},
    }


def update_marketplace_json(
    plugin_root: Path, marketplace_path: Optional[Path] = None, force: bool = False, verbose: bool = False
) -> tuple[bool, str, bool]:
    """
    Update marketplace.json with current plugin metadata.

    Args:
        plugin_root: Root directory of the plugin
        marketplace_path: Path to marketplace.json (default: plugin_root/marketplace.json)
        force: Update even if checksum hasn't changed
        verbose: Print detailed output

    Returns:
        Tuple of (success, message, was_updated)
    """
    if marketplace_path is None:
        marketplace_path = plugin_root / "marketplace.json"

    # Load existing marketplace data
    existing_data, error = load_marketplace_json(marketplace_path)
    if error:
        return False, error, False

    # Create new entry
    new_entry = create_marketplace_entry(plugin_root)

    # Check if update is needed
    if existing_data is not None and not force:
        # Find existing entry for this plugin
        plugins = existing_data.get("plugins", [])
        for i, plugin in enumerate(plugins):
            if plugin.get("name") == new_entry["name"]:
                if plugin.get("checksum") == new_entry["checksum"]:
                    return True, "marketplace.json is up-to-date (checksum unchanged)", False
                break

    # Build or update marketplace structure
    if existing_data is None:
        marketplace_data = {
            "name": f"{new_entry['name']}-marketplace",
            "description": f"Local marketplace for {new_entry['name']} plugin",
            "version": "1.0.0",
            "plugins": [new_entry],
            "last_updated": new_entry["last_modified"],
        }
    else:
        marketplace_data = existing_data

        # Update or add plugin entry
        plugins = marketplace_data.get("plugins", [])
        found = False
        for i, plugin in enumerate(plugins):
            if plugin.get("name") == new_entry["name"]:
                plugins[i] = new_entry
                found = True
                if verbose:
                    print(f"Updated existing entry for {new_entry['name']}")
                break

        if not found:
            plugins.append(new_entry)
            if verbose:
                print(f"Added new entry for {new_entry['name']}")

        marketplace_data["plugins"] = plugins
        marketplace_data["last_updated"] = new_entry["last_modified"]

    # Write updated marketplace.json
    try:
        with open(marketplace_path, "w", encoding="utf-8") as f:
            json.dump(marketplace_data, f, indent=2)
            f.write("\n")

        return True, f"Updated marketplace.json with version {new_entry['version']}", True
    except Exception as e:
        return False, f"Error writing marketplace.json: {e}", False


def main() -> int:
    """Main entry point for the marketplace metadata updater."""
    parser = argparse.ArgumentParser(
        description="Update marketplace.json when plugin files change.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This script:
  1. Calculates checksums of plugin contents
  2. Updates last_modified timestamps
  3. Syncs metadata from plugin.json
  4. Creates marketplace.json if it doesn't exist

Can be run manually or integrated into git hooks.

Examples:
  %(prog)s                          # Update in plugin directory
  %(prog)s --plugin-dir /path/to    # Specify plugin location
  %(prog)s --force                  # Force update even if unchanged
        """,
    )

    parser.add_argument(
        "--plugin-dir", type=Path, default=None, help="Plugin root directory (default: parent of scripts/)"
    )

    parser.add_argument(
        "--marketplace", type=Path, default=None, help="Path to marketplace.json (default: plugin-dir/marketplace.json)"
    )

    parser.add_argument("--force", action="store_true", help="Force update even if checksum unchanged")

    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed output")

    parser.add_argument("--json", action="store_true", help="Output results as JSON")

    parser.add_argument("--check-only", action="store_true", help="Check if update is needed without making changes")

    args = parser.parse_args()

    # Determine plugin root
    plugin_root = args.plugin_dir if args.plugin_dir else get_plugin_root()
    plugin_root = plugin_root.resolve()

    if not plugin_root.exists():
        if args.json:
            print(json.dumps({"error": f"Plugin directory not found: {plugin_root}"}))
        else:
            print(f"Error: Plugin directory not found: {plugin_root}", file=sys.stderr)
        return 1

    # Verify plugin.json exists
    plugin_json_path = plugin_root / ".claude-plugin" / "plugin.json"
    if not plugin_json_path.exists():
        if args.json:
            print(json.dumps({"error": f"plugin.json not found at {plugin_json_path}"}))
        else:
            print(f"Error: plugin.json not found at {plugin_json_path}", file=sys.stderr)
        return 1

    marketplace_path = args.marketplace if args.marketplace else plugin_root / "marketplace.json"

    if args.check_only:
        # Just check if update is needed
        existing_data, error = load_marketplace_json(marketplace_path)
        if error:
            if args.json:
                print(json.dumps({"needs_update": True, "reason": error}))
            else:
                print(f"Update needed: {error}")
            return 0

        if existing_data is None:
            if args.json:
                print(json.dumps({"needs_update": True, "reason": "marketplace.json does not exist"}))
            else:
                print("Update needed: marketplace.json does not exist")
            return 0

        new_entry = create_marketplace_entry(plugin_root)
        plugins = existing_data.get("plugins", [])
        for plugin in plugins:
            if plugin.get("name") == new_entry["name"]:
                if plugin.get("checksum") == new_entry["checksum"]:
                    if args.json:
                        print(json.dumps({"needs_update": False, "reason": "checksum unchanged"}))
                    else:
                        print("No update needed: checksum unchanged")
                    return 0

        if args.json:
            print(json.dumps({"needs_update": True, "reason": "checksum changed"}))
        else:
            print("Update needed: checksum changed")
        return 0

    # Perform update
    success, message, was_updated = update_marketplace_json(plugin_root, marketplace_path, args.force, args.verbose)

    if args.json:
        output = {
            "success": success,
            "message": message,
            "updated": was_updated,
            "marketplace_path": str(marketplace_path),
        }
        print(json.dumps(output, indent=2))
    else:
        status = "[OK]" if success else "[ERROR]"
        print(f"{status} {message}")
        if success and was_updated:
            print(f"  Marketplace: {marketplace_path}")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
