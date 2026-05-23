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

Performance note (v2.101+):
    Profiling `validate_plugin .` on the CPV repo showed
    ``pathspec.PathSpec.from_lines`` being called ~1.7M times — once per
    ``is_ignored`` / ``is_dir_ignored`` check, even when the underlying
    ``.gitignore`` file is identical between calls. The parsed
    ``PathSpec`` object is now cached at module level, keyed by the
    ``.gitignore`` file's ``(abs_path, st_mtime_ns, st_size)`` triple so
    cache invalidation tracks the file's actual content changes. The
    public API is unchanged; the only observable difference is wall-time.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path
from threading import Lock

from cpv_validation_common import is_path_gitignored, parse_gitignore

# --------------------------------------------------------------------------- #
# Cached PathSpec store
# --------------------------------------------------------------------------- #
#
# Key: (abs_path: str, st_mtime_ns: int, st_size: int)
#   - `abs_path` is the resolved string form of the .gitignore file path.
#   - `st_mtime_ns` and `st_size` form the cache-invalidation signature:
#     when either changes, the cached PathSpec is no longer trusted and a
#     fresh parse is forced. Tracks the file's actual content state without
#     re-reading the bytes.
#
# Value: tuple[list[str], pathspec.PathSpec | None]
#   - The first element is the parsed pattern list (for the fallback path
#     when pathspec is unavailable).
#   - The second element is the compiled ``pathspec.PathSpec`` (or ``None``
#     when ``pathspec`` is not importable; the fallback path uses the
#     pattern list).
#
# An `OrderedDict` is used so we can evict the oldest entry when the cache
# fills up. The default bound (4096) is well above the number of distinct
# `.gitignore` files seen in a single CPV run, but defensive against
# pathological cases (e.g. someone scanning a monorepo with thousands of
# nested .gitignore files).
#
# A `Lock` protects the cache for thread safety — CPV doesn't currently
# spawn validator threads, but a parallel B-series swarm is in flight and
# the cache must be safe across worker threads regardless.
_CACHE_MAX_SIZE = 4096
_PATHSPEC_CACHE: OrderedDict[tuple[str, int, int], tuple[list[str], object]] = OrderedDict()
_PATHSPEC_CACHE_LOCK = Lock()


def _clear_cache() -> None:
    """Empty the module-level PathSpec cache. Test-only helper.

    Production code MUST NOT call this — invalidation happens automatically
    when the underlying ``.gitignore`` file's mtime or size changes. Tests
    need it for isolation (each test should observe a cold cache).
    """
    with _PATHSPEC_CACHE_LOCK:
        _PATHSPEC_CACHE.clear()


def _load_pathspec(gitignore_path: Path) -> tuple[list[str], object | None]:
    """Return (patterns_list, compiled_spec) for ``gitignore_path``.

    The result is cached keyed by ``(abs_path, st_mtime_ns, st_size)`` so a
    repeat call against an unchanged file returns the same compiled
    PathSpec object — no re-parse, no ``pathspec.PathSpec.from_lines``
    invocation. When the file's mtime or size changes, the cache misses
    and the file is re-read + re-compiled.

    Returns ``([], None)`` when the file does not exist or cannot be read.
    Returns ``(patterns, None)`` when ``pathspec`` cannot be imported (the
    fallback path uses the pattern list against the pure-Python matcher).
    """
    try:
        st = gitignore_path.stat()
    except (OSError, FileNotFoundError):
        return [], None

    key = (os.fspath(gitignore_path.resolve()), st.st_mtime_ns, st.st_size)

    with _PATHSPEC_CACHE_LOCK:
        hit = _PATHSPEC_CACHE.get(key)
        if hit is not None:
            # Mark as recently used (LRU semantic).
            _PATHSPEC_CACHE.move_to_end(key)
            return hit

    # Cache miss — parse + compile outside the lock so unrelated lookups
    # don't block on a slow .gitignore parse.
    patterns = parse_gitignore(gitignore_path)
    spec: object | None = None
    if patterns:
        try:
            import pathspec  # noqa: PLC0415

            spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
        except ImportError:
            spec = None

    with _PATHSPEC_CACHE_LOCK:
        # Re-check in case another thread populated the same key while we
        # were parsing — harmless either way (same key → same content per
        # stat invariant), but keep the LRU happy.
        existing = _PATHSPEC_CACHE.get(key)
        if existing is not None:
            _PATHSPEC_CACHE.move_to_end(key)
            return existing
        _PATHSPEC_CACHE[key] = (patterns, spec)
        # Evict oldest if we're over the bound. Unbounded growth in a
        # long-running orchestrator (many plugins, many .gitignore files)
        # would be a memory leak.
        while len(_PATHSPEC_CACHE) > _CACHE_MAX_SIZE:
            _PATHSPEC_CACHE.popitem(last=False)

    return patterns, spec


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
        # `_load_pathspec` returns ([], None) for a missing .gitignore; that
        # matches the historical behaviour of `parse_gitignore` (empty
        # list) and skips the pathspec compile entirely. Otherwise it
        # returns the cached pattern list AND the cached compiled
        # PathSpec so per-path `is_ignored` checks don't have to rebuild
        # the matcher every call.
        self.patterns, self._spec = _load_pathspec(gitignore_path)

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

    def _match(self, rel: str) -> bool:
        """Run the cached PathSpec against a rel-path string.

        Uses the cached ``pathspec.PathSpec`` when available — this is the
        whole point of the cache (avoiding the ~1.7M
        ``pathspec.PathSpec.from_lines`` calls that the un-cached path
        produced on a single `validate_plugin .` run). When ``pathspec`` is
        unavailable the call falls through to ``is_path_gitignored``,
        which uses the pure-Python fallback matcher.
        """
        if self._spec is not None:
            # `match_file` returns bool — keep the public contract identical
            # to `is_path_gitignored` (which returns bool).
            return bool(self._spec.match_file(rel))
        # Fallback path: pathspec unavailable, defer to common helper which
        # implements its own pathspec-or-fallback chain.
        return is_path_gitignored(rel, self.patterns)

    def is_ignored(self, path: Path) -> bool:
        """Check if a path should be skipped based on .gitignore patterns.

        Filesystem-aware: if `path` is a directory on disk, query the
        gitignore matcher with a trailing slash so dir-only patterns
        (``build/``, ``node_modules/``) match correctly. This mirrors
        git's own behaviour and removes a sharp edge where callers had
        to remember to append ``/`` for directories themselves.
        """
        if not self.patterns:
            return False
        try:
            # Use PurePosixPath-style forward slashes for gitignore matching
            rel = path.relative_to(self.root).as_posix()
        except ValueError:
            return False
        try:
            if path.is_dir():
                rel = rel.rstrip("/") + "/"
        except OSError:
            pass
        return self._match(rel)

    def is_dir_ignored(self, dirpath: Path) -> bool:
        """Check if a directory should be skipped — appends trailing / for dir-only patterns."""
        if not self.patterns:
            return False
        try:
            rel = dirpath.relative_to(self.root).as_posix()
        except ValueError:
            return False
        # Check both with and without trailing slash (gitignore treats dir/ specially)
        return self._match(rel) or self._match(rel + "/")

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
