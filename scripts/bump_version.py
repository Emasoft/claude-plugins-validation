#!/usr/bin/env python3
"""
bump_version.py - CLI tool to bump semantic version across all plugin files.

Supports bumping major, minor, or patch versions following semver format.
Updates version in: plugin.json, pyproject.toml, and any __version__ variables.

Exit codes:
    0 - Success
    1 - Error (invalid version, file not found, etc.)
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional


def get_plugin_root() -> Path:
    """Get the plugin root directory (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent


def parse_semver(version: str) -> Optional[tuple[int, int, int]]:
    """
    Parse a semantic version string into (major, minor, patch) tuple.

    Args:
        version: Version string in format "X.Y.Z"

    Returns:
        Tuple of (major, minor, patch) integers, or None if invalid format
    """
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def format_semver(major: int, minor: int, patch: int) -> str:
    """Format version tuple as semver string."""
    return f"{major}.{minor}.{patch}"


def bump_version(current: str, bump_type: str) -> Optional[str]:
    """
    Bump the version according to the specified type.

    Args:
        current: Current version string
        bump_type: One of 'major', 'minor', or 'patch'

    Returns:
        New version string, or None if current version is invalid
    """
    parts = parse_semver(current)
    if parts is None:
        return None

    major, minor, patch = parts

    if bump_type == "major":
        return format_semver(major + 1, 0, 0)
    elif bump_type == "minor":
        return format_semver(major, minor + 1, 0)
    elif bump_type == "patch":
        return format_semver(major, minor, patch + 1)
    else:
        return None


def update_plugin_json(plugin_root: Path, new_version: str) -> tuple[bool, str]:
    """
    Update version in plugin.json.

    Args:
        plugin_root: Root directory of the plugin
        new_version: New version string to set

    Returns:
        Tuple of (success, message)
    """
    plugin_json_path = plugin_root / ".claude-plugin" / "plugin.json"

    if not plugin_json_path.exists():
        return False, f"plugin.json not found at {plugin_json_path}"

    try:
        with open(plugin_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        old_version = data.get("version", "unknown")
        data["version"] = new_version

        with open(plugin_json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")

        return True, f"plugin.json: {old_version} -> {new_version}"
    except json.JSONDecodeError as e:
        return False, f"plugin.json has invalid JSON: {e}"
    except Exception as e:
        return False, f"Error updating plugin.json: {e}"


def update_pyproject_toml(plugin_root: Path, new_version: str) -> tuple[bool, str]:
    """
    Update version in pyproject.toml.

    Args:
        plugin_root: Root directory of the plugin
        new_version: New version string to set

    Returns:
        Tuple of (success, message)
    """
    pyproject_path = plugin_root / "pyproject.toml"

    if not pyproject_path.exists():
        return True, "pyproject.toml not found (skipped)"

    try:
        content = pyproject_path.read_text(encoding="utf-8")

        # Match version = "X.Y.Z" pattern
        pattern = r'^(version\s*=\s*["\'])(\d+\.\d+\.\d+)(["\'])$'

        old_version = None

        def replace_version(match: re.Match[str]) -> str:
            nonlocal old_version
            old_version = match.group(2)
            return f"{match.group(1)}{new_version}{match.group(3)}"

        new_content, count = re.subn(pattern, replace_version, content, flags=re.MULTILINE)

        if count == 0:
            return True, "pyproject.toml has no version field (skipped)"

        pyproject_path.write_text(new_content, encoding="utf-8")
        return True, f"pyproject.toml: {old_version} -> {new_version}"
    except Exception as e:
        return False, f"Error updating pyproject.toml: {e}"


def update_python_version_variables(plugin_root: Path, new_version: str) -> list[tuple[bool, str]]:
    """
    Update __version__ variables in Python files.

    Args:
        plugin_root: Root directory of the plugin
        new_version: New version string to set

    Returns:
        List of (success, message) tuples for each file updated
    """
    results: list[tuple[bool, str]] = []

    # Directories to exclude from scanning
    exclude_dirs = {"__pycache__", ".venv", "venv", "env", ".env", "node_modules", ".git", ".mypy_cache", ".ruff_cache"}

    # Search for Python files with __version__ variable
    for py_file in plugin_root.rglob("*.py"):
        # Skip excluded directories and hidden directories
        parts_set = set(py_file.relative_to(plugin_root).parts)
        if parts_set & exclude_dirs or any(p.startswith(".") for p in py_file.relative_to(plugin_root).parts):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")

            # Match __version__ = "X.Y.Z" or __version__ = 'X.Y.Z'
            pattern = r'^(__version__\s*=\s*["\'])(\d+\.\d+\.\d+)(["\'])$'

            old_version = None

            def replace_version(match: re.Match[str]) -> str:
                nonlocal old_version
                old_version = match.group(2)
                return f"{match.group(1)}{new_version}{match.group(3)}"

            new_content, count = re.subn(pattern, replace_version, content, flags=re.MULTILINE)

            if count > 0:
                py_file.write_text(new_content, encoding="utf-8")
                rel_path = py_file.relative_to(plugin_root)
                results.append((True, f"{rel_path}: {old_version} -> {new_version}"))
        except Exception as e:
            rel_path = py_file.relative_to(plugin_root)
            results.append((False, f"Error updating {rel_path}: {e}"))

    return results


def get_current_version(plugin_root: Path) -> Optional[str]:
    """
    Get the current version from plugin.json.

    Args:
        plugin_root: Root directory of the plugin

    Returns:
        Current version string, or None if not found
    """
    plugin_json_path = plugin_root / ".claude-plugin" / "plugin.json"

    if not plugin_json_path.exists():
        return None

    try:
        with open(plugin_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        version = data.get("version")
        # Explicit type check: return str if string, None otherwise
        if isinstance(version, str):
            return version
        return None
    except Exception:
        return None


def main() -> int:
    """Main entry point for the version bump CLI tool."""
    parser = argparse.ArgumentParser(
        description="Bump semantic version across all plugin files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --patch     # 1.0.0 -> 1.0.1
  %(prog)s --minor     # 1.0.0 -> 1.1.0
  %(prog)s --major     # 1.0.0 -> 2.0.0
  %(prog)s --set 2.5.0 # Set explicit version
        """,
    )

    bump_group = parser.add_mutually_exclusive_group(required=True)
    bump_group.add_argument("--major", action="store_true", help="Bump major version (X.0.0)")
    bump_group.add_argument("--minor", action="store_true", help="Bump minor version (x.Y.0)")
    bump_group.add_argument("--patch", action="store_true", help="Bump patch version (x.y.Z)")
    bump_group.add_argument("--set", metavar="VERSION", help="Set explicit version (format: X.Y.Z)")

    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed without making changes")

    parser.add_argument(
        "--plugin-dir", type=Path, default=None, help="Plugin root directory (default: parent of scripts/)"
    )

    args = parser.parse_args()

    # Determine plugin root
    plugin_root = args.plugin_dir if args.plugin_dir else get_plugin_root()
    plugin_root = plugin_root.resolve()

    if not plugin_root.exists():
        print(f"Error: Plugin directory not found: {plugin_root}", file=sys.stderr)
        return 1

    # Get current version
    current_version = get_current_version(plugin_root)
    if current_version is None:
        print("Error: Could not read current version from plugin.json", file=sys.stderr)
        return 1

    # Determine new version
    if args.set:
        if parse_semver(args.set) is None:
            print(f"Error: Invalid version format '{args.set}'. Expected X.Y.Z", file=sys.stderr)
            return 1
        new_version = args.set
    else:
        bump_type = "major" if args.major else "minor" if args.minor else "patch"
        new_version = bump_version(current_version, bump_type)
        if new_version is None:
            print(f"Error: Current version '{current_version}' is not valid semver", file=sys.stderr)
            return 1

    print(f"Bumping version: {current_version} -> {new_version}")
    if args.dry_run:
        print("(dry-run mode - no files will be changed)")
    print()

    # Collect all updates
    all_results: list[tuple[bool, str]] = []

    if not args.dry_run:
        # Update plugin.json
        success, msg = update_plugin_json(plugin_root, new_version)
        all_results.append((success, msg))

        # Update pyproject.toml
        success, msg = update_pyproject_toml(plugin_root, new_version)
        all_results.append((success, msg))

        # Update __version__ variables
        py_results = update_python_version_variables(plugin_root, new_version)
        all_results.extend(py_results)
    else:
        # Dry run - just list files that would be updated
        all_results.append((True, "[DRY-RUN] plugin.json would be updated"))
        if (plugin_root / "pyproject.toml").exists():
            all_results.append((True, "[DRY-RUN] pyproject.toml would be updated"))

        # Directories to exclude from scanning
        exclude_dirs = {
            "__pycache__",
            ".venv",
            "venv",
            "env",
            ".env",
            "node_modules",
            ".git",
            ".mypy_cache",
            ".ruff_cache",
        }

        for py_file in plugin_root.rglob("*.py"):
            parts_set = set(py_file.relative_to(plugin_root).parts)
            if parts_set & exclude_dirs or any(p.startswith(".") for p in py_file.relative_to(plugin_root).parts):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
                if re.search(r"^__version__\s*=", content, re.MULTILINE):
                    rel_path = py_file.relative_to(plugin_root)
                    all_results.append((True, f"[DRY-RUN] {rel_path} would be updated"))
            except Exception:
                pass

    # Print summary
    print("Summary:")
    print("-" * 50)

    errors = 0
    for success, msg in all_results:
        status = "[OK]" if success else "[ERROR]"
        print(f"  {status} {msg}")
        if not success:
            errors += 1

    print("-" * 50)

    if errors > 0:
        print(f"\nCompleted with {errors} error(s)")
        return 1
    else:
        file_count = len([r for r in all_results if "skipped" not in r[1].lower()])
        print(f"\nSuccessfully updated {file_count} file(s)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
