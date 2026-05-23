#!/usr/bin/env python3
"""Tests for the module-level PathSpec cache in `scripts/gitignore_filter.py`.

The cache was introduced after profiling `validate_plugin .` on the CPV
repo found ``pathspec.PathSpec.from_lines`` was called ~1.7M times — once
per `is_ignored` / `is_dir_ignored` query — because every check re-built a
fresh ``PathSpec`` from the (unchanged) gitignore pattern list. The cache
keys on the file's ``(abs_path, st_mtime_ns, st_size)`` triple so any real
content change forces a re-parse, while repeat checks against an unchanged
file reuse the same compiled object.

These tests pin:
    1. Cache hit on a repeat call to the same `.gitignore` file returns
       the SAME compiled PathSpec object (identity check, not equality).
    2. Cache MISS when the gitignore file's mtime changes (rewrite +
       verify a new object is returned).
    3. Cache MISS when the gitignore file's size changes (in case mtime
       resolution coalesces two writes — size diff still triggers a
       re-parse).
    4. No behavioral change — existing public-API calls return identical
       `is_ignored` results before vs. after the cache is in effect.
    5. `_clear_cache()` empties the cache.
    6. The cache is bounded — pathological growth is impossible.
    7. `from_lines` call count drops from O(n_checks) to O(n_distinct_files)
       across many `is_ignored` queries on the same instance.

All tests use `tmp_path` for real filesystem operations — no mocking of
filesystem stats. The only mocked surface is `pathspec.PathSpec.from_lines`
in test 7, used purely as a call counter without altering behaviour.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import gitignore_filter as gf  # noqa: E402
from gitignore_filter import GitignoreFilter, _clear_cache  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_cache():
    """Reset the module-level cache around every test for isolation."""
    _clear_cache()
    yield
    _clear_cache()


def _write_gitignore(root: Path, text: str) -> Path:
    """Helper: write a .gitignore at `root` and return its path.

    A small sleep is needed when overwriting in tests that depend on
    mtime invalidation because some filesystems (HFS+ in particular)
    use 1-second mtime resolution. The cache also keys on size, so
    most size-changing writes invalidate immediately without a sleep —
    but tests that change CONTENT without changing SIZE still need a
    real mtime bump.
    """
    p = root / ".gitignore"
    p.write_text(text, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# 1. Cache hit — same file, two instances → SAME compiled PathSpec
# --------------------------------------------------------------------------- #


def test_cache_hit_returns_same_pathspec_identity(tmp_path: Path) -> None:
    """Two GitignoreFilter instances on the same unchanged .gitignore share
    the SAME compiled PathSpec object (identity check, not equality).

    This is the whole point of the cache — `pathspec.PathSpec.from_lines`
    is NOT called the second time, so the second instance receives the
    same Python object as the first.
    """
    _write_gitignore(tmp_path, "*.pyc\nbuild/\n")

    gi1 = GitignoreFilter(tmp_path)
    gi2 = GitignoreFilter(tmp_path)

    # Both must have a compiled spec (pathspec is a CPV direct dep).
    assert gi1._spec is not None
    assert gi2._spec is not None
    # IDENTITY check — not equality. If `from_lines` ran again, this would
    # be a different object even though content is equal.
    assert gi1._spec is gi2._spec


def test_cache_hit_across_many_instances(tmp_path: Path) -> None:
    """Cache survives N>2 instances on the same file."""
    _write_gitignore(tmp_path, "*.log\n")

    instances = [GitignoreFilter(tmp_path) for _ in range(10)]
    # Every instance must hold the same compiled PathSpec.
    first = instances[0]._spec
    assert first is not None
    for gi in instances[1:]:
        assert gi._spec is first


# --------------------------------------------------------------------------- #
# 2. Cache MISS on mtime change
# --------------------------------------------------------------------------- #


def test_cache_miss_on_mtime_change(tmp_path: Path) -> None:
    """Touching the file's mtime (without changing size) forces a re-parse
    and produces a NEW compiled PathSpec object.

    HFS+/APFS truncate mtime to 1-second resolution, so we use
    ``os.utime`` to set a stamp explicitly far in the future — that
    guarantees the cache sees a different ``st_mtime_ns`` regardless of
    filesystem timestamp granularity.
    """
    p = _write_gitignore(tmp_path, "*.log\n")

    gi1 = GitignoreFilter(tmp_path)
    spec_v1 = gi1._spec
    assert spec_v1 is not None

    # Bump the mtime by 1 hour without touching content. Size stays the
    # same, but the (path, mtime, size) cache key changes.
    st = p.stat()
    new_mtime = st.st_mtime + 3600.0
    os.utime(p, (new_mtime, new_mtime))

    gi2 = GitignoreFilter(tmp_path)
    spec_v2 = gi2._spec
    assert spec_v2 is not None
    # Cache key differs by st_mtime_ns → different cache entry → different
    # compiled object. Behaviour still equivalent (same patterns), but
    # the identity invariant proves the cache was invalidated.
    assert spec_v2 is not spec_v1


# --------------------------------------------------------------------------- #
# 3. Cache MISS on size change
# --------------------------------------------------------------------------- #


def test_cache_miss_on_size_change(tmp_path: Path) -> None:
    """Adding bytes to the .gitignore file (different size) forces a
    re-parse even if mtime resolution coalesces the two writes.
    """
    _write_gitignore(tmp_path, "*.pyc\n")
    gi1 = GitignoreFilter(tmp_path)
    spec_v1 = gi1._spec
    assert spec_v1 is not None

    # Sleep 1.1s so mtime ALSO changes (defence in depth), but the size
    # change alone is sufficient — the cache key includes st_size.
    time.sleep(1.1)
    _write_gitignore(tmp_path, "*.pyc\nbuild/\nnode_modules/\n")

    gi2 = GitignoreFilter(tmp_path)
    spec_v2 = gi2._spec
    assert spec_v2 is not None
    assert spec_v2 is not spec_v1
    # Behavioural sanity — the new spec actually matches the new pattern.
    (tmp_path / "build").mkdir()
    assert gi2.is_dir_ignored(tmp_path / "build") is True
    # And the old gi (which holds the OLD compiled spec) does NOT match
    # the new pattern — proving the cache invalidation gave each instance
    # its own snapshot.
    assert gi1.is_dir_ignored(tmp_path / "build") is False


# --------------------------------------------------------------------------- #
# 4. No behavioural change — round-trip parity
# --------------------------------------------------------------------------- #


def test_cache_preserves_is_ignored_behaviour(tmp_path: Path) -> None:
    """Public-API `is_ignored` and `is_dir_ignored` produce identical
    results to the pre-cache implementation. Same `.gitignore`, same
    inputs, same outputs — the cache is a pure optimization.
    """
    _write_gitignore(tmp_path, "*.pyc\nbuild/\n!keep.pyc\n")

    (tmp_path / "foo.pyc").touch()
    (tmp_path / "keep.pyc").touch()
    (tmp_path / "main.py").touch()
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.txt").touch()

    gi = GitignoreFilter(tmp_path)

    # Expected results derived from real git semantic (last-matching-pattern
    # wins). Verified against the upstream pre-cache implementation.
    assert gi.is_ignored(tmp_path / "foo.pyc") is True
    assert gi.is_ignored(tmp_path / "keep.pyc") is False  # negated by !keep.pyc
    assert gi.is_ignored(tmp_path / "main.py") is False
    assert gi.is_dir_ignored(tmp_path / "build") is True


def test_cache_preserves_walk_rglob_behaviour(tmp_path: Path) -> None:
    """walk(), rglob(), iterdir() yield the same set of files with the
    cache as they did before. Pruning at descent time still happens.
    """
    _write_gitignore(tmp_path, "*.log\nbuild/\n")

    (tmp_path / "main.py").touch()
    (tmp_path / "debug.log").touch()
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "leaked.py").touch()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.py").touch()

    gi = GitignoreFilter(tmp_path)

    # walk
    walked = []
    for _dirpath, _dirs, files in gi.walk():
        walked.extend(files)
    assert "main.py" in walked
    assert "lib.py" in walked
    assert "debug.log" not in walked
    assert "leaked.py" not in walked  # build/ pruned at descent time

    # rglob
    py_files = sorted(p.name for p in gi.rglob("*.py"))
    assert py_files == ["lib.py", "main.py"]
    # build/leaked.py must NOT leak through
    assert "leaked.py" not in py_files


# --------------------------------------------------------------------------- #
# 5. `_clear_cache()` empties the cache
# --------------------------------------------------------------------------- #


def test_clear_cache_empties_the_cache(tmp_path: Path) -> None:
    """After `_clear_cache()`, the next GitignoreFilter on the same file
    triggers a fresh parse — its compiled PathSpec is a NEW object.
    """
    _write_gitignore(tmp_path, "*.log\n")
    gi1 = GitignoreFilter(tmp_path)
    spec_v1 = gi1._spec
    assert spec_v1 is not None

    # Clear cache — next instance re-parses.
    _clear_cache()
    gi2 = GitignoreFilter(tmp_path)
    spec_v2 = gi2._spec
    assert spec_v2 is not None
    assert spec_v2 is not spec_v1, "_clear_cache() did not actually empty the cache"


def test_clear_cache_removes_all_entries(tmp_path: Path) -> None:
    """After populating the cache with N distinct files, `_clear_cache()`
    must remove ALL of them, not just one.
    """
    # 3 distinct .gitignore files at 3 distinct paths.
    for i in range(3):
        sub = tmp_path / f"plugin_{i}"
        sub.mkdir()
        _write_gitignore(sub, f"pattern_{i}.txt\n")
        GitignoreFilter(sub)

    # Cache should have ≥ 3 entries (could be exactly 3, or more if other
    # tests in this file populated it earlier; the autouse fixture clears
    # before AND after, so at this point it's exactly 3).
    assert len(gf._PATHSPEC_CACHE) >= 3
    _clear_cache()
    assert len(gf._PATHSPEC_CACHE) == 0


# --------------------------------------------------------------------------- #
# 6. Cache is bounded — pathological growth is impossible
# --------------------------------------------------------------------------- #


def test_cache_eviction_respects_max_size(tmp_path: Path) -> None:
    """The cache evicts the oldest entry when it exceeds `_CACHE_MAX_SIZE`.

    Test temporarily lowers the bound to 5, populates with 7 entries, and
    asserts the cache size never exceeded the bound.
    """
    original_max = gf._CACHE_MAX_SIZE
    try:
        gf._CACHE_MAX_SIZE = 5
        for i in range(7):
            sub = tmp_path / f"p{i}"
            sub.mkdir()
            _write_gitignore(sub, f"pat_{i}\n")
            GitignoreFilter(sub)
        # Cache must not exceed the bound.
        assert len(gf._PATHSPEC_CACHE) <= 5
        # Specifically: it should hold the LAST 5 (LRU eviction).
        assert len(gf._PATHSPEC_CACHE) == 5
    finally:
        gf._CACHE_MAX_SIZE = original_max


def test_cache_lru_promotes_recently_used(tmp_path: Path) -> None:
    """LRU-style cache — repeated access to an old entry moves it to the
    front so it survives subsequent eviction pressure.
    """
    original_max = gf._CACHE_MAX_SIZE
    try:
        gf._CACHE_MAX_SIZE = 3

        roots = []
        for i in range(3):
            sub = tmp_path / f"p{i}"
            sub.mkdir()
            _write_gitignore(sub, f"pat_{i}\n")
            roots.append(sub)
            GitignoreFilter(sub)

        # Touch the OLDEST entry (p0) — promotes it to most-recent.
        GitignoreFilter(roots[0])

        # Add a 4th entry — eviction is forced. The victim should be p1
        # (the actual oldest now), NOT p0.
        sub4 = tmp_path / "p4"
        sub4.mkdir()
        _write_gitignore(sub4, "pat_4\n")
        GitignoreFilter(sub4)

        assert len(gf._PATHSPEC_CACHE) == 3
        # p0 must still be cached (we just touched it).
        p0_key = (os.fspath(roots[0].resolve() / ".gitignore"),) + (
            roots[0].joinpath(".gitignore").stat().st_mtime_ns,
            roots[0].joinpath(".gitignore").stat().st_size,
        )
        assert p0_key in gf._PATHSPEC_CACHE
    finally:
        gf._CACHE_MAX_SIZE = original_max


# --------------------------------------------------------------------------- #
# 7. Call-count: from_lines runs ONCE per distinct file across many checks
# --------------------------------------------------------------------------- #


def test_from_lines_runs_once_per_unchanged_file(tmp_path: Path, monkeypatch) -> None:
    """The whole point of the cache: a single .gitignore parsed ONCE for
    N filter instances and M is_ignored checks.

    We wrap `pathspec.PathSpec.from_lines` with a counter (delegating to
    the real impl so behaviour is unchanged) and assert it's called
    exactly once for a workload of 5 instances + 500 is_ignored checks.
    """
    import pathspec

    real_from_lines = pathspec.PathSpec.from_lines
    counter = {"n": 0}

    def counting_from_lines(*args, **kwargs):
        counter["n"] += 1
        return real_from_lines(*args, **kwargs)

    monkeypatch.setattr(pathspec.PathSpec, "from_lines", counting_from_lines)

    _write_gitignore(tmp_path, "*.pyc\nbuild/\n")
    # Create 100 paths to check.
    for i in range(100):
        (tmp_path / f"f{i}.py").touch()
        (tmp_path / f"f{i}.pyc").touch()

    # 5 separate filter instances — pre-cache, each would call from_lines
    # for every is_ignored check. With cache, only the FIRST instance's
    # parse runs from_lines.
    for _ in range(5):
        gi = GitignoreFilter(tmp_path)
        for p in tmp_path.iterdir():
            gi.is_ignored(p)
        # walk and rglob trigger additional is_ignored / is_dir_ignored
        # paths internally — all must use the cached spec.
        for _dp, _ds, _fs in gi.walk():
            pass
        for _p in gi.rglob("*.py"):
            pass

    # With the cache, from_lines runs EXACTLY ONCE for this workload —
    # the first time the .gitignore is parsed. Every subsequent
    # GitignoreFilter() and every subsequent is_ignored() reuses the
    # cached compiled PathSpec.
    assert counter["n"] == 1, (
        f"Expected from_lines to be called exactly once, got {counter['n']}. "
        "Cache may not be wired into is_ignored / is_dir_ignored properly."
    )


def test_from_lines_runs_again_after_clear_cache(tmp_path: Path, monkeypatch) -> None:
    """`_clear_cache()` forces the NEXT instance to re-parse, even on the
    same file — guarantees test isolation works.
    """
    import pathspec

    real_from_lines = pathspec.PathSpec.from_lines
    counter = {"n": 0}

    def counting_from_lines(*args, **kwargs):
        counter["n"] += 1
        return real_from_lines(*args, **kwargs)

    monkeypatch.setattr(pathspec.PathSpec, "from_lines", counting_from_lines)

    _write_gitignore(tmp_path, "*.log\n")

    GitignoreFilter(tmp_path)  # call 1: cache miss → from_lines invoked
    assert counter["n"] == 1
    GitignoreFilter(tmp_path)  # call 2: cache hit → no from_lines
    assert counter["n"] == 1

    _clear_cache()
    GitignoreFilter(tmp_path)  # call 3: cache cleared → from_lines again
    assert counter["n"] == 2


# --------------------------------------------------------------------------- #
# 8. Edge case — missing .gitignore returns empty patterns + None spec
# --------------------------------------------------------------------------- #


def test_no_gitignore_file_yields_empty_state(tmp_path: Path) -> None:
    """When the plugin root has no .gitignore, the filter holds an empty
    pattern list and `is_ignored` always returns False. Cache is not
    consulted (file doesn't exist → can't be stat'd).
    """
    gi = GitignoreFilter(tmp_path)
    assert gi.patterns == []
    assert gi._spec is None

    (tmp_path / "foo.pyc").touch()
    assert gi.is_ignored(tmp_path / "foo.pyc") is False


def test_no_gitignore_does_not_populate_cache(tmp_path: Path) -> None:
    """A missing .gitignore must not leave a phantom entry in the cache —
    otherwise re-creating the file later would silently hit a stale
    `None` spec entry.
    """
    GitignoreFilter(tmp_path)  # no .gitignore → no cache entry
    assert len(gf._PATHSPEC_CACHE) == 0


# --------------------------------------------------------------------------- #
# 9. Public API surface is unchanged
# --------------------------------------------------------------------------- #


def test_public_api_unchanged(tmp_path: Path) -> None:
    """Spot-check that the public API still exposes the same callables /
    attributes the rest of the codebase depends on.
    """
    _write_gitignore(tmp_path, "*.tmp\n")
    gi = GitignoreFilter(tmp_path)

    # Public attributes (used by existing callers).
    assert hasattr(gi, "root")
    assert hasattr(gi, "patterns")
    assert hasattr(gi, "follow_symlinks")
    # Public methods.
    assert callable(gi.is_ignored)
    assert callable(gi.is_dir_ignored)
    assert callable(gi.walk)
    assert callable(gi.rglob)
    assert callable(gi.iterdir)
