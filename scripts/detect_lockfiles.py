#!/usr/bin/env python3
"""Lockfile detection for plugin projects.

Scans a plugin root for known dependency lockfiles and returns a mapping of
filename -> language. Combined with detect_languages() this lets validators:

- Emit NIT when a lockfile exists without the matching language (orphan lockfile,
  e.g. package-lock.json with no package.json — leftover from a removed workflow)
- Emit WARNING when a lockfile is in .gitignore (CI will use unpinned deps,
  defeating the purpose of a lockfile)
- Suggest adding a lockfile when a language is detected but none is pinned
  (not implemented here — caller's job)

Usage:
    from detect_lockfiles import detect_lockfiles, LOCKFILES
    found = detect_lockfiles(Path("/path/to/plugin"))
    # {'uv.lock': 'python', 'package-lock.json': 'js'}
"""

from __future__ import annotations

from pathlib import Path

# Canonical lockfile registry.
# Maps lockfile filename -> language code used by detect_language.py.
LOCKFILES: dict[str, str] = {
    # Python
    "uv.lock": "python",
    "poetry.lock": "python",
    "Pipfile.lock": "python",
    # JS/TS (all share the same "js" language code — TS transpiles down)
    "package-lock.json": "js",
    "pnpm-lock.yaml": "js",
    "yarn.lock": "js",
    "bun.lockb": "js",
    # Deno
    "deno.lock": "deno",
    # Rust
    "Cargo.lock": "rust",
    # Go
    "go.sum": "go",
    # Ruby
    "Gemfile.lock": "ruby",
    # Elixir
    "mix.lock": "elixir",
}


def detect_lockfiles(plugin_root: Path) -> dict[str, str]:
    """Return a mapping of lockfile filename -> language for every lockfile found.

    Only checks the plugin root (not subdirectories). Lockfiles buried in
    subdirectories belong to nested projects or vendored deps, not the plugin
    itself. The caller should pass the plugin root explicitly.

    Args:
        plugin_root: Path to the plugin root directory.

    Returns:
        Dict of {lockfile_filename: language_code}. Empty if no lockfiles found.
        The keys are filenames (basenames), not absolute paths — callers that
        need the full path should join `plugin_root / filename`.
    """
    if not plugin_root.is_dir():
        return {}

    found: dict[str, str] = {}
    for filename, language in LOCKFILES.items():
        candidate = plugin_root / filename
        if candidate.is_file():
            found[filename] = language
    return found


def find_lockfile_path(plugin_root: Path, lockfile_name: str) -> Path | None:
    """Return the absolute Path to a lockfile at plugin_root, or None if absent.

    Convenience helper for callers that want the full path without re-joining.
    """
    if not lockfile_name:
        return None
    candidate = plugin_root / lockfile_name
    return candidate if candidate.is_file() else None


def main() -> int:
    """CLI entry point — prints lockfiles found in a given plugin path."""
    import argparse

    parser = argparse.ArgumentParser(description="Detect lockfiles in a plugin.")
    parser.add_argument("path", nargs="?", default=".", help="Plugin root path")
    args = parser.parse_args()

    plugin_root = Path(args.path).resolve()
    if not plugin_root.is_dir():
        print(f"Error: {plugin_root} is not a directory")
        return 1

    found = detect_lockfiles(plugin_root)
    if not found:
        print(f"No lockfiles detected in {plugin_root}")
        return 0

    print(f"Lockfiles detected in {plugin_root}:")
    for filename in sorted(found):
        lang = found[filename]
        print(f"  {filename:20s}  ({lang})")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
