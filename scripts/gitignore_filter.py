#!/usr/bin/env python3
"""Gitignore-aware file filtering for plugin validation.

Provides a GitignoreFilter class that loads .gitignore patterns once
and exposes helpers to filter os.walk, rglob, and iterdir results.
All validators should use this to skip gitignored files/directories.

Usage:
    gi = GitignoreFilter(plugin_root)
    for path in gi.walk_files(plugin_root, skip_dirs={"__pycache__"}):
        # path is a Path object, gitignored files are excluded
        ...

    for path in gi.rglob(plugin_root, "*.pyc"):
        # gitignored matches excluded
        ...
"""

from __future__ import annotations

from pathlib import Path

from cpv_validation_common import is_path_gitignored, parse_gitignore


class GitignoreFilter:
    """Gitignore-aware file filter — loads patterns once, reuses for all scans.

    Uses pathlib exclusively for cross-platform compatibility.

    Trust boundary: scanners run against attacker-controlled trees (cloned
    plugins, archive extracts, …). To prevent a malicious plugin from
    smuggling in a symlink that escapes the plugin root and tricks a
    downstream scanner into reading host files, this filter REFUSES to
    follow symlinks by default. Pass `follow_symlinks=True` only when the
    target tree is fully trusted; even then, the filter still rejects any
    symlink whose resolved target leaves `plugin_root`.
    """

    def __init__(self, plugin_root: Path, *, follow_symlinks: bool = False) -> None:
        self.root = plugin_root.resolve()
        self.follow_symlinks = follow_symlinks
        gitignore_path = self.root / ".gitignore"
        self.patterns = parse_gitignore(gitignore_path) if gitignore_path.is_file() else []

    def _is_unsafe_symlink(self, entry: Path) -> bool:
        """Return True when `entry` is a symlink that must be skipped.

        Default mode (follow_symlinks=False): every symlink is unsafe. The
        walker rejects them so `entry.is_dir()` / `entry.is_file()`, which
        follow symlinks, never get a chance to escape the plugin root.

        Opt-in mode (follow_symlinks=True): allow symlinks only when the
        canonical resolved target stays under `self.root`. Broken symlinks,
        symlink loops, and permission errors during resolution are also
        treated as unsafe — fail-closed.
        """
        if not entry.is_symlink():
            return False
        if not self.follow_symlinks:
            return True
        try:
            resolved = entry.resolve(strict=True)
        except (OSError, RuntimeError):
            return True
        try:
            resolved.relative_to(self.root)
        except ValueError:
            return True
        return False

    def is_ignored(self, path: Path) -> bool:
        """Check if a path should be skipped based on .gitignore patterns."""
        if not self.patterns:
            return False
        try:
            # Use PurePosixPath-style forward slashes for gitignore matching
            rel = path.relative_to(self.root).as_posix()
        except ValueError:
            return False
        return is_path_gitignored(rel, self.patterns)

    def is_dir_ignored(self, dirpath: Path) -> bool:
        """Check if a directory should be skipped — appends trailing / for dir-only patterns."""
        if not self.patterns:
            return False
        try:
            rel = dirpath.relative_to(self.root).as_posix()
        except ValueError:
            return False
        # Check both with and without trailing slash (gitignore treats dir/ specially)
        return is_path_gitignored(rel, self.patterns) or is_path_gitignored(rel + "/", self.patterns)

    def _walk_pathlib(
        self,
        directory: Path,
        skip_dirs: set[str],
        skip_hidden: bool,
    ):
        """Recursive directory walk using pathlib only (cross-platform).

        Yields (dirpath: Path, subdirs: list[str], files: list[str]).
        Compatible with os.walk() return signature but uses Path objects.
        """
        subdirs: list[str] = []
        files: list[str] = []

        try:
            entries = sorted(directory.iterdir())
        except PermissionError:
            return

        for entry in entries:
            # Reject symlinks BEFORE is_dir/is_file (both follow links).
            if self._is_unsafe_symlink(entry):
                continue
            if entry.is_dir():
                if skip_hidden and entry.name.startswith("."):
                    continue
                if entry.name in skip_dirs:
                    continue
                if self.is_dir_ignored(entry):
                    continue
                subdirs.append(entry.name)
            elif entry.is_file():
                if not self.is_ignored(entry):
                    files.append(entry.name)

        yield str(directory), subdirs, files

        # Recurse into non-ignored subdirectories
        for subdir_name in subdirs:
            yield from self._walk_pathlib(directory / subdir_name, skip_dirs, skip_hidden)

    def walk(
        self,
        root: Path | None = None,
        skip_dirs: set[str] | None = None,
        skip_hidden: bool = True,
    ):
        """Gitignore-aware directory walk using pathlib (cross-platform).

        Yields (dirpath: str, dirnames: list[str], filenames: list[str]).
        Automatically prunes gitignored directories and files.
        """
        root = root or self.root
        extra_skip = skip_dirs or set()
        yield from self._walk_pathlib(root, extra_skip, skip_hidden)

    def rglob(self, pattern: str, root: Path | None = None):
        """Gitignore-aware rglob — yields Path objects that are not gitignored.

        Skips symlinks per the trust-boundary rule (see `_is_unsafe_symlink`).

        The implementation walks the tree directory-by-directory (not via
        ``Path.rglob`` which would descend into gitignored dirs first and
        only filter individual matches afterwards). Pruning at descent
        time means a 600-MB ``INPUT_DEV/`` listed in ``.gitignore`` is
        never enumerated — fixes issue #19 where ``cpv-remote-validate
        lint`` picked up thousands of files inside gitignored reference
        tarballs.

        Pattern matching uses ``Path.match(pattern)`` — same semantics as
        ``Path.rglob(pattern)`` minus the unconditional descent.
        """
        import fnmatch

        root = root or self.root
        # `Path.match` matches against the **basename** for unanchored
        # patterns like ``*.py``; for patterns containing a path separator
        # it matches the whole tail. Use ``fnmatch`` directly on the
        # basename for the common case so behaviour matches Path.rglob.
        if "/" in pattern or "\\" in pattern:

            def matches(p: Path) -> bool:
                try:
                    return p.match(pattern)
                except (ValueError, OSError):
                    return False
        else:

            def matches(p: Path) -> bool:
                return fnmatch.fnmatch(p.name, pattern)

        # Iterative DFS using the same pruning rules as _walk_pathlib.
        stack: list[Path] = [root]
        while stack:
            current = stack.pop()
            try:
                entries = list(current.iterdir())
            except (PermissionError, NotADirectoryError, FileNotFoundError):
                continue
            for entry in entries:
                if self._is_unsafe_symlink(entry):
                    continue
                if entry.is_dir():
                    # Skip hidden dirs (.git, .venv, etc.) AND gitignored
                    # dirs at descent time. Matches `_walk_pathlib`'s
                    # pruning so behaviour is consistent across both
                    # iterators.
                    if entry.name.startswith(".") and entry.name != ".":
                        continue
                    if self.is_dir_ignored(entry):
                        continue
                    stack.append(entry)
                elif entry.is_file():
                    if self.is_ignored(entry):
                        continue
                    if matches(entry):
                        yield entry

    def iterdir(self, directory: Path | None = None, skip_hidden: bool = False):
        """Gitignore-aware iterdir — yields Path objects that are not gitignored.

        Skips symlinks per the trust-boundary rule (see `_is_unsafe_symlink`).
        """
        directory = directory or self.root
        for item in directory.iterdir():
            if self._is_unsafe_symlink(item):
                continue
            if skip_hidden and item.name.startswith("."):
                continue
            if not self.is_ignored(item):
                yield item
