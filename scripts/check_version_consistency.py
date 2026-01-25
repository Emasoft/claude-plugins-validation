#!/usr/bin/env python3
"""
check_version_consistency.py - Check that version is consistent across all plugin files.

Scans plugin.json, pyproject.toml, CHANGELOG.md, and Python files with __version__
to ensure all version numbers match.

Exit codes:
    0 - All versions are consistent
    1 - Version mismatch detected or error reading files
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class VersionLocation:
    """Represents a version found in a specific file."""

    file_path: Path
    version: str
    source: str  # Description of where in the file (e.g., "version field", "__version__")

    def relative_path(self, root: Path) -> str:
        """Get path relative to root for display."""
        try:
            return str(self.file_path.relative_to(root))
        except ValueError:
            return str(self.file_path)


def get_plugin_root() -> Path:
    """Get the plugin root directory (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent


def extract_version_from_plugin_json(plugin_root: Path) -> Optional[VersionLocation]:
    """
    Extract version from plugin.json.

    Args:
        plugin_root: Root directory of the plugin

    Returns:
        VersionLocation if found, None otherwise
    """
    plugin_json_path = plugin_root / ".claude-plugin" / "plugin.json"

    if not plugin_json_path.exists():
        return None

    try:
        with open(plugin_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        version = data.get("version")
        if version:
            return VersionLocation(file_path=plugin_json_path, version=str(version), source="version field")
    except Exception:
        pass

    return None


def extract_version_from_pyproject(plugin_root: Path) -> Optional[VersionLocation]:
    """
    Extract version from pyproject.toml.

    Args:
        plugin_root: Root directory of the plugin

    Returns:
        VersionLocation if found, None otherwise
    """
    pyproject_path = plugin_root / "pyproject.toml"

    if not pyproject_path.exists():
        return None

    try:
        content = pyproject_path.read_text(encoding="utf-8")

        # Match version = "X.Y.Z" or version = 'X.Y.Z'
        match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
        if match:
            return VersionLocation(file_path=pyproject_path, version=match.group(1), source="version field")
    except Exception:
        pass

    return None


def extract_version_from_changelog(plugin_root: Path) -> Optional[VersionLocation]:
    """
    Extract latest version from CHANGELOG.md.

    Looks for patterns like:
    - ## [1.0.0]
    - ## 1.0.0
    - # Version 1.0.0

    Args:
        plugin_root: Root directory of the plugin

    Returns:
        VersionLocation if found, None otherwise
    """
    changelog_path = plugin_root / "CHANGELOG.md"

    if not changelog_path.exists():
        return None

    try:
        content = changelog_path.read_text(encoding="utf-8")

        # Look for version patterns in headers
        patterns = [
            r"^##\s*\[(\d+\.\d+\.\d+)\]",  # ## [1.0.0]
            r"^##\s*(\d+\.\d+\.\d+)",  # ## 1.0.0
            r"^#\s*[Vv]ersion\s*(\d+\.\d+\.\d+)",  # # Version 1.0.0
        ]

        for pattern in patterns:
            match = re.search(pattern, content, re.MULTILINE)
            if match:
                return VersionLocation(file_path=changelog_path, version=match.group(1), source="latest release header")
    except Exception:
        pass

    return None


def extract_versions_from_python_files(plugin_root: Path) -> list[VersionLocation]:
    """
    Extract __version__ from Python files.

    Args:
        plugin_root: Root directory of the plugin

    Returns:
        List of VersionLocation for each file with __version__
    """
    results: list[VersionLocation] = []

    # Directories to exclude from scanning
    exclude_dirs = {"__pycache__", ".venv", "venv", "env", ".env", "node_modules", ".git", ".mypy_cache", ".ruff_cache"}

    for py_file in plugin_root.rglob("*.py"):
        # Skip excluded directories and hidden directories
        parts_set = set(py_file.relative_to(plugin_root).parts)
        if parts_set & exclude_dirs or any(p.startswith(".") for p in py_file.relative_to(plugin_root).parts):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")

            # Match __version__ = "X.Y.Z" or __version__ = 'X.Y.Z'
            match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            if match:
                results.append(
                    VersionLocation(file_path=py_file, version=match.group(1), source="__version__ variable")
                )
        except Exception:
            pass

    return results


def check_version_consistency(plugin_root: Path, verbose: bool = False) -> tuple[bool, list[VersionLocation]]:
    """
    Check that all versions in the plugin are consistent.

    Args:
        plugin_root: Root directory of the plugin
        verbose: Whether to print detailed output

    Returns:
        Tuple of (is_consistent, list_of_version_locations)
    """
    all_versions: list[VersionLocation] = []

    # Collect all version locations
    plugin_json_version = extract_version_from_plugin_json(plugin_root)
    if plugin_json_version:
        all_versions.append(plugin_json_version)

    pyproject_version = extract_version_from_pyproject(plugin_root)
    if pyproject_version:
        all_versions.append(pyproject_version)

    changelog_version = extract_version_from_changelog(plugin_root)
    if changelog_version:
        all_versions.append(changelog_version)

    python_versions = extract_versions_from_python_files(plugin_root)
    all_versions.extend(python_versions)

    if not all_versions:
        if verbose:
            print("Warning: No version information found in any files")
        return True, []

    # Check consistency
    unique_versions = set(v.version for v in all_versions)
    is_consistent = len(unique_versions) <= 1

    return is_consistent, all_versions


def main() -> int:
    """Main entry point for the version consistency checker."""
    parser = argparse.ArgumentParser(
        description="Check that version is consistent across all plugin files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Scans the following files for version information:
  - .claude-plugin/plugin.json (version field)
  - pyproject.toml (version field)
  - CHANGELOG.md (latest release header)
  - *.py files (__version__ variable)

Exit codes:
  0 - All versions are consistent
  1 - Version mismatch or error
        """,
    )

    parser.add_argument(
        "--plugin-dir", type=Path, default=None, help="Plugin root directory (default: parent of scripts/)"
    )

    parser.add_argument("-v", "--verbose", action="store_true", help="Show detailed output")

    parser.add_argument("--json", action="store_true", help="Output results as JSON")

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

    # Check consistency
    is_consistent, versions = check_version_consistency(plugin_root, args.verbose)

    # Output results
    if args.json:
        output = {
            "consistent": is_consistent,
            "versions": [
                {"file": v.relative_path(plugin_root), "version": v.version, "source": v.source} for v in versions
            ],
            "unique_versions": list(set(v.version for v in versions)),
        }
        print(json.dumps(output, indent=2))
    else:
        if not versions:
            print("Warning: No version information found in any files")
            return 0

        print(f"Checking version consistency in: {plugin_root}")
        print()

        # Group by version
        version_groups: dict[str, list[VersionLocation]] = {}
        for v in versions:
            if v.version not in version_groups:
                version_groups[v.version] = []
            version_groups[v.version].append(v)

        print("Files with version information:")
        print("-" * 60)

        for version, locations in sorted(version_groups.items()):
            for loc in locations:
                rel_path = loc.relative_path(plugin_root)
                print(f"  {rel_path}: {version} ({loc.source})")

        print("-" * 60)
        print()

        if is_consistent:
            canonical_version = versions[0].version
            print(f"[OK] All {len(versions)} files have consistent version: {canonical_version}")
        else:
            unique = set(v.version for v in versions)
            print("[ERROR] Version mismatch detected!")
            print(f"  Found {len(unique)} different versions: {', '.join(sorted(unique))}")
            print()
            print("To fix, run:")
            print("  python scripts/bump_version.py --set <version>")

    return 0 if is_consistent else 1


if __name__ == "__main__":
    sys.exit(main())
