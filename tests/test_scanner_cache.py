"""Phase D regression tests: content-hash scanner result cache.

These tests pin the contract of ``cpv_scanner_cache.ScannerCache``:

  - ``get`` returns ``None`` on miss.
  - ``put`` then ``get`` round-trips the result dict byte-identically.
  - File-content drift, args-hash drift, scanner-version drift each
    invalidate the entry independently (no hidden coupling between
    the three).
  - ``invalidate_older_than`` removes stale entries by mtime cutoff.
  - Atomic writes survive a "crash mid-write" simulation: a partially-
    written tmp file does NOT cause subsequent ``get`` calls to crash
    or return stale content.
  - Concurrent writes from N threads to N distinct keys all land
    cleanly (none lost, none corrupted).

All tests use a per-test ``tmp_path`` cache dir so the user's real
``~/.cache/cpv/scanner-results`` is never touched.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

# tests/conftest.py adds scripts/ to sys.path; defensive duplicate so
# the file works when collected in isolation.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_scanner_cache import (  # noqa: E402
    CacheKey,
    ScannerCache,
    sha256_of_args,
    sha256_of_file,
    tree_merkle,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_key(
    *,
    target: str = "/abs/path/to/file.py",
    content: str = "a" * 64,
    scanner: str = "ruff",
    version: str = "ruff 0.6.0",
    args_hash: str = "x" * 64,
) -> CacheKey:
    """Build a CacheKey with sensible defaults for tests that don't
    care about every field. Each test that varies a field passes the
    new value explicitly so the diff against the default is visible.
    """
    return CacheKey(
        target_id=target,
        content_sha256=content,
        scanner_name=scanner,
        scanner_version=version,
        args_hash=args_hash,
    )


# ---------------------------------------------------------------------------
# 1. miss
# ---------------------------------------------------------------------------


def test_get_returns_none_on_miss(tmp_path: Path) -> None:
    """Empty cache + any key → ``get`` returns None."""
    cache = ScannerCache(cache_dir=tmp_path / "scanner-cache")
    key = _make_key()
    assert cache.get(key) is None


# ---------------------------------------------------------------------------
# 2. round-trip
# ---------------------------------------------------------------------------


def test_put_then_get_roundtrip(tmp_path: Path) -> None:
    """Put a result, get returns it byte-identically (modulo dict ordering).

    Uses a moderately complex result with nested structures so we
    catch any silent flattening done by the JSON serialiser.
    """
    cache = ScannerCache(cache_dir=tmp_path / "scanner-cache")
    key = _make_key()
    result = {
        "findings": [
            {"rule": "E501", "line": 42, "msg": "line too long"},
            {"rule": "F401", "line": 10, "msg": "unused import"},
        ],
        "passed": False,
        "ts": 1700000000.0,
    }
    cache.put(key, result)

    got = cache.get(key)
    assert got == result, "round-trip lost information"


# ---------------------------------------------------------------------------
# 3. content drift
# ---------------------------------------------------------------------------


def test_get_returns_none_on_file_change(tmp_path: Path) -> None:
    """Bumping ``content_sha256`` (file edit) → cache miss for new key,
    cache hit for original key still works.
    """
    cache = ScannerCache(cache_dir=tmp_path / "scanner-cache")
    key_orig = _make_key(content="a" * 64)
    key_drifted = _make_key(content="b" * 64)
    cache.put(key_orig, {"findings": []})

    # Original still hits — content didn't change.
    assert cache.get(key_orig) == {"findings": []}
    # Drifted content → miss (no entry was written under that key).
    assert cache.get(key_drifted) is None


# ---------------------------------------------------------------------------
# 4. args drift
# ---------------------------------------------------------------------------


def test_get_returns_none_on_args_change(tmp_path: Path) -> None:
    """Different ``args_hash`` (e.g. flag flipped) → miss."""
    cache = ScannerCache(cache_dir=tmp_path / "scanner-cache")
    key_a = _make_key(args_hash="a" * 64)
    key_b = _make_key(args_hash="b" * 64)
    cache.put(key_a, {"findings": ["x"]})

    assert cache.get(key_a) == {"findings": ["x"]}
    assert cache.get(key_b) is None


# ---------------------------------------------------------------------------
# 5. scanner-version drift
# ---------------------------------------------------------------------------


def test_get_returns_none_on_scanner_version_change(tmp_path: Path) -> None:
    """Bumping the scanner version (e.g. ruff 0.6 → 0.7) → miss for the
    new version. Prior version's entry is untouched (and still hits if
    we ever rolled back).
    """
    cache = ScannerCache(cache_dir=tmp_path / "scanner-cache")
    key_v060 = _make_key(version="ruff 0.6.0")
    key_v070 = _make_key(version="ruff 0.7.0")
    cache.put(key_v060, {"findings": []})

    assert cache.get(key_v060) == {"findings": []}
    assert cache.get(key_v070) is None


# ---------------------------------------------------------------------------
# 6. invalidate_older_than
# ---------------------------------------------------------------------------


def test_invalidate_older_than_removes_stale(tmp_path: Path) -> None:
    """Backdate a cache file's mtime past the cutoff and verify
    ``invalidate_older_than(30)`` removes it. Fresh entries survive.
    """
    cache_dir = tmp_path / "scanner-cache"
    cache = ScannerCache(cache_dir=cache_dir, ttl_days=999)  # disable TTL gate inside get()
    fresh_key = _make_key(target="/fresh.py", content="f" * 64)
    stale_key = _make_key(target="/stale.py", content="s" * 64)

    cache.put(fresh_key, {"findings": []})
    cache.put(stale_key, {"findings": []})

    # Backdate stale entry's mtime to 60 days ago.
    stale_path = cache_dir / stale_key.to_cache_filename()
    sixty_days_ago = time.time() - (60 * 86_400)
    import os as _os

    _os.utime(stale_path, (sixty_days_ago, sixty_days_ago))

    removed = cache.invalidate_older_than(days=30)
    assert removed == 1, f"expected 1 stale entry removed, got {removed}"
    assert cache.get(fresh_key) == {"findings": []}, "fresh entry got swept"
    # Stale entry's file is gone — get() returns None (not a stored value).
    assert not stale_path.exists()


# ---------------------------------------------------------------------------
# 7. atomic-write — partial tmp file is not consumed by readers
# ---------------------------------------------------------------------------


def test_atomic_write_no_partial_on_crash(tmp_path: Path) -> None:
    """Simulate a crash by leaving a half-written tmp file in the cache
    directory and confirming ``get`` does NOT return that partial
    content.

    This pins the invariant that final cache filenames are reached only
    via ``os.replace`` (atomic), never via "write directly to the
    canonical name and pray".
    """
    cache_dir = tmp_path / "scanner-cache"
    cache = ScannerCache(cache_dir=cache_dir)
    key = _make_key()

    # Drop a half-baked tmp file alongside future cache entries.
    # Atomic-write semantics guarantee this file's name never matches
    # what get() looks for; even if it did, the JSON would not parse.
    cache_dir.mkdir(parents=True, exist_ok=True)
    partial = cache_dir / ".cpv-tmp-fake.json"
    partial.write_text('{"key": {"target_id":', encoding="utf-8")  # truncated JSON

    # get() should treat the absent canonical filename as a miss —
    # it MUST NOT scan the tmp file and return its half-parsed body.
    assert cache.get(key) is None

    # Now also verify a corrupted canonical-name file is treated as a
    # miss instead of crashing the caller. Compute the filename, write
    # garbage to it, then call get() — it should return None.
    canonical = cache_dir / key.to_cache_filename()
    canonical.write_text("not-json-at-all", encoding="utf-8")
    assert cache.get(key) is None


# ---------------------------------------------------------------------------
# 8. concurrent writes
# ---------------------------------------------------------------------------


def test_concurrent_writes_safe(tmp_path: Path) -> None:
    """8 threads writing to 8 distinct keys → all 8 entries readable
    after the threads join. None should be missing or corrupted.

    This guards Phase B + Phase D concurrency: linters run inside a
    ThreadPoolExecutor, each may call ``cache.put()`` from its own
    worker thread. Atomic ``os.replace`` keeps the cache consistent
    even under that contention.
    """
    cache = ScannerCache(cache_dir=tmp_path / "scanner-cache")

    def worker(idx: int) -> None:
        # Each thread uses a distinct content hash, so each lands on
        # a distinct cache filename. Writers do NOT contend on the
        # same canonical name — but they DO contend on the same
        # tempfile.mkstemp directory, which is the contention path
        # this test pins.
        key = _make_key(
            target=f"/abs/path/to/file_{idx}.py",
            content=f"{idx:064x}",
        )
        cache.put(key, {"findings": [f"finding_from_thread_{idx}"]})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All 8 entries should now read back cleanly.
    for i in range(8):
        key = _make_key(
            target=f"/abs/path/to/file_{i}.py",
            content=f"{i:064x}",
        )
        got = cache.get(key)
        assert got == {"findings": [f"finding_from_thread_{i}"]}, (
            f"entry {i} missing or corrupted after concurrent writes: got {got!r}"
        )


# ---------------------------------------------------------------------------
# Bonus structural tests
# ---------------------------------------------------------------------------


def test_to_cache_filename_is_deterministic_and_safe() -> None:
    """Same key → same filename across calls; weird scanner names
    can't escape the cache directory (no path traversal).
    """
    k1 = _make_key()
    k2 = _make_key()
    assert k1.to_cache_filename() == k2.to_cache_filename()

    # Filename must not contain path separators or NUL.
    name = k1.to_cache_filename()
    assert "/" not in name
    assert "\\" not in name
    assert "\x00" not in name

    # A scanner name that tries to escape via "../" is sanitised.
    evil = CacheKey(
        target_id="t",
        content_sha256="c",
        scanner_name="../../../etc/passwd",
        scanner_version="v",
        args_hash="a",
    )
    evil_name = evil.to_cache_filename()
    assert "../" not in evil_name and "/" not in evil_name


def test_sha256_of_file_streams_correctly(tmp_path: Path) -> None:
    """Sanity-check: sha256_of_file produces the same digest as a
    one-shot hash of the same content.
    """
    import hashlib as _h

    body = b"hello world\n" * 1000
    p = tmp_path / "f.txt"
    p.write_bytes(body)
    assert sha256_of_file(p) == _h.sha256(body).hexdigest()


def test_sha256_of_args_is_order_sensitive() -> None:
    """The CLI argv order matters (semgrep ``--config p/security
    --config p/secrets`` is NOT the same as ``--config p/secrets
    --config p/security``), so sha256_of_args MUST NOT sort.
    """
    a = sha256_of_args(["--x", "1", "--y", "2"])
    b = sha256_of_args(["--y", "2", "--x", "1"])
    assert a != b


def test_tree_merkle_is_order_independent(tmp_path: Path) -> None:
    """Same files, different traversal orders → same merkle.

    Tree-level scanners feed file lists in indeterminate orders
    (filesystem walk, glob, etc.), so the merkle MUST be sorted
    internally so two callers with the same content always get the
    same key.
    """
    f1 = tmp_path / "a.py"
    f1.write_text("alpha", encoding="utf-8")
    f2 = tmp_path / "b.py"
    f2.write_text("bravo", encoding="utf-8")
    f3 = tmp_path / "c.py"
    f3.write_text("charlie", encoding="utf-8")

    m1 = tree_merkle([f1, f2, f3], base=tmp_path)
    m2 = tree_merkle([f3, f1, f2], base=tmp_path)
    m3 = tree_merkle([f2, f3, f1], base=tmp_path)
    assert m1 == m2 == m3

    # A content drift in any single file changes the merkle.
    f2.write_text("BRAVO", encoding="utf-8")
    m4 = tree_merkle([f1, f2, f3], base=tmp_path)
    assert m4 != m1


def test_corrupted_cache_entry_is_treated_as_miss(tmp_path: Path) -> None:
    """If the JSON body's ``key`` field doesn't match the requested
    key (digest collision in the worst case), get() returns None
    instead of yielding the wrong result.
    """
    cache = ScannerCache(cache_dir=tmp_path / "scanner-cache")
    key = _make_key()
    # Manually drop a file at the canonical name with a body whose
    # stored "key" is a different CacheKey.
    canonical = cache.cache_dir / key.to_cache_filename()
    other_key_dict = {
        "target_id": "different",
        "content_sha256": "different",
        "scanner_name": "different",
        "scanner_version": "different",
        "args_hash": "different",
    }
    canonical.write_text(
        json.dumps({"key": other_key_dict, "result": {"findings": ["wrong"]}, "ts": 0}),
        encoding="utf-8",
    )
    assert cache.get(key) is None


def test_clear_drops_every_entry(tmp_path: Path) -> None:
    """``clear()`` is a test/debug helper — wipes every cache entry."""
    cache = ScannerCache(cache_dir=tmp_path / "scanner-cache")
    for i in range(5):
        cache.put(_make_key(content=f"{i:064x}"), {"findings": [i]})
    assert cache.clear() == 5
    # Subsequent get on any of those keys is a miss.
    assert cache.get(_make_key(content="0" * 64)) is None
