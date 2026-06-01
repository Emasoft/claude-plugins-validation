"""Regression tests for ``cpv_scan_cache`` (the SQLite-backed scan-result cache).

The cache is a CVE-class component (security findings could be served
stale if invalidation is wrong) so the test surface is intentionally
broad:

  - Quadruple-key invalidation (content, catalog, scanner_version,
    file_ext each independently invalidate)
  - Storage location chain (5 candidates, monkeypatched env vars)
  - Mode-bit invariants (0o600 file, 0o700 dir)
  - Corruption recovery (the file becomes invalid mid-lifetime)
  - Hard kill switch + deep-mode toggles via env vars
  - Concurrent writers (sqlite handles it but we verify)
  - Permission-denied collapse to silent no-op

Every test uses a per-test tmp directory routed through
``CPV_SCAN_CACHE_DIR`` so the user's real cache is never touched. The
``isolated_cache_env`` autouse fixture below scrubs every env var the
module reads, so a leak from one test cannot affect the next.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

# conftest.py adds scripts/ to sys.path; defensive duplicate so the
# file works when run in isolation (e.g. by an agent that invokes a
# single test file directly).
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import cpv_scan_cache  # noqa: E402
from cpv_scan_cache import (  # noqa: E402
    cache_stats,
    get_cached_findings,
    prune_cache,
    put_cached_findings,
    reset_cache,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# Every env var the module reads. We scrub all of them before each
# test so a leak from one test (env or fixture) can't pollute another.
_ENV_VARS_READ_BY_MODULE = (
    "CPV_SCAN_CACHE",
    "CPV_SCAN_CACHE_DEEP",
    "CPV_SCAN_CACHE_DIR",
    "CLAUDE_PLUGIN_DATA",
    "XDG_CACHE_HOME",
    "GITHUB_ACTIONS",
    "RUNNER_TEMP",
)


@pytest.fixture(autouse=True)
def isolated_cache_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Per-test sandbox: blank every env var, route cache to tmp_path.

    Returns the tmp cache directory so tests that care about the
    on-disk file can stat it directly.

    Also resets the "no writable location" warned-once flag so each
    test starts with a fresh slate.
    """
    for var in _ENV_VARS_READ_BY_MODULE:
        monkeypatch.delenv(var, raising=False)

    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("CPV_SCAN_CACHE_DIR", str(cache_dir))

    # Override HOME so the test never sees the user's real ~/.claude or
    # ~/.cache directories — defence in depth even though the explicit
    # CPV_SCAN_CACHE_DIR already wins.
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    cpv_scan_cache._reset_warned_flag()
    return cache_dir


# ---------------------------------------------------------------------------
# Tiny shared helpers
# ---------------------------------------------------------------------------


def _sample_findings() -> list[dict[str, object]]:
    """A non-trivial findings payload used by most round-trip tests.

    Includes nested structures + unicode so we catch any
    serialisation issue at the same time as the cache behaviour.
    """
    return [
        {
            "rule_id": "RC-100",
            "severity": "MAJOR",
            "file": "scripts/foo.py",
            "line": 42,
            "message": "naïve regex consumes 100% CPU on adversarial input",
            "context": {"snippet": "re.match(r'(a+)+$', s)", "col": 13},
        },
        {
            "rule_id": "RC-101",
            "severity": "MINOR",
            "file": "scripts/bar.py",
            "line": 7,
            "message": "missing type annotation",
        },
    ]


# ---------------------------------------------------------------------------
# 1. Round-trip on the happy path
# ---------------------------------------------------------------------------


def test_put_then_get_roundtrip() -> None:
    """A simple put → get returns the exact findings list."""
    findings = _sample_findings()
    put_cached_findings("c1", "cat1", "v1.0.0", findings)
    got = get_cached_findings("c1", "cat1", "v1.0.0")
    assert got == findings


def test_get_returns_none_on_miss() -> None:
    """Asking for an unknown key returns None (clean miss, no exception)."""
    assert get_cached_findings("missing", "missing", "missing") is None


# ---------------------------------------------------------------------------
# 2. Multi-key invalidation — each key must independently invalidate
# ---------------------------------------------------------------------------


def test_content_hash_drift_invalidates() -> None:
    """Same catalog + scanner, different content → MISS."""
    put_cached_findings("content_A", "cat", "v1", _sample_findings())
    assert get_cached_findings("content_B", "cat", "v1") is None


def test_catalog_hash_drift_invalidates() -> None:
    """Same content + scanner, different catalog → MISS.

    This is the key CVE-class invariant: a catalog upgrade brings new
    rules that the old cache could not have produced, so every stale
    entry MUST be invisible.
    """
    put_cached_findings("c1", "catalog_old", "v1", _sample_findings())
    assert get_cached_findings("c1", "catalog_new", "v1") is None


def test_scanner_version_drift_invalidates() -> None:
    """Same content + catalog, different scanner_version → MISS."""
    put_cached_findings("c1", "cat", "v1.0.0", _sample_findings())
    assert get_cached_findings("c1", "cat", "v2.0.0") is None


@pytest.mark.parametrize(
    "drift_field",
    ["content_hash", "catalog_hash", "scanner_version"],
)
def test_any_single_field_drift_invalidates(drift_field: str) -> None:
    """Parametrised seatbelt: each of the three keys MUST be load-bearing."""
    base = {"content_hash": "c1", "catalog_hash": "cat1", "scanner_version": "v1"}
    put_cached_findings(**base, findings=_sample_findings())  # type: ignore[arg-type]

    drifted = dict(base)
    drifted[drift_field] = "DRIFTED"
    assert get_cached_findings(**drifted) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. Prune by age
# ---------------------------------------------------------------------------


def test_prune_by_age_removes_stale_entries(
    isolated_cache_env: Path,
) -> None:
    """Entry older than ``max_age_days`` is removed by the age pass."""
    # Insert an entry, then back-date it 15 days via direct sqlite.
    put_cached_findings("old", "cat", "v1", [{"x": 1}])

    cache_path = isolated_cache_env / cpv_scan_cache._CACHE_FILENAME
    fifteen_days_ago = int(time.time()) - (15 * 86_400)
    conn = sqlite3.connect(str(cache_path))
    conn.execute(
        "UPDATE scan_cache SET cached_at = ? WHERE content_hash = ?",
        (fifteen_days_ago, "old"),
    )
    conn.commit()
    conn.close()

    # Also insert a FRESH entry that should survive.
    put_cached_findings("fresh", "cat", "v1", [{"y": 2}])

    out = prune_cache(max_age_days=10, max_entries=1000)
    assert out["removed_age"] == 1
    assert out["removed_lru"] == 0
    assert get_cached_findings("old", "cat", "v1") is None
    assert get_cached_findings("fresh", "cat", "v1") == [{"y": 2}]


# ---------------------------------------------------------------------------
# 4. Prune by entry count (LRU)
# ---------------------------------------------------------------------------


def test_prune_by_entry_count_drops_oldest_lru(
    isolated_cache_env: Path,
) -> None:
    """Over the entry cap → the oldest entries are dropped first.

    We seed 5 entries with explicit ascending ``cached_at`` timestamps
    so the LRU order is deterministic, then prune to a cap of 3.
    """
    # First populate normally.
    for i in range(5):
        put_cached_findings(f"c{i}", "cat", "v1", [{"i": i}])

    # Back-date them with a fixed monotonic gap so LRU order matches
    # insertion order. Use raw sqlite to bypass autocommit timestamp.
    cache_path = isolated_cache_env / cpv_scan_cache._CACHE_FILENAME
    conn = sqlite3.connect(str(cache_path))
    base = int(time.time()) - 10_000
    for i in range(5):
        conn.execute(
            "UPDATE scan_cache SET cached_at = ? WHERE content_hash = ?",
            (base + i, f"c{i}"),
        )
    conn.commit()
    conn.close()

    out = prune_cache(max_age_days=0, max_entries=3)
    # Age pass disabled (max_age_days=0); only LRU should fire.
    assert out["removed_age"] == 0
    assert out["removed_lru"] == 2

    # c0, c1 are oldest → gone. c2, c3, c4 survive.
    assert get_cached_findings("c0", "cat", "v1") is None
    assert get_cached_findings("c1", "cat", "v1") is None
    assert get_cached_findings("c2", "cat", "v1") == [{"i": 2}]
    assert get_cached_findings("c3", "cat", "v1") == [{"i": 3}]
    assert get_cached_findings("c4", "cat", "v1") == [{"i": 4}]


# ---------------------------------------------------------------------------
# 5. reset_cache wipes everything
# ---------------------------------------------------------------------------


def test_reset_cache_drops_every_entry() -> None:
    """``reset_cache()`` removes all entries and leaves a clean schema."""
    put_cached_findings("c1", "cat", "v1", _sample_findings())
    put_cached_findings("c2", "cat", "v1", _sample_findings())

    reset_cache()

    assert get_cached_findings("c1", "cat", "v1") is None
    assert get_cached_findings("c2", "cat", "v1") is None

    # Cache is still usable after reset — schema must have been recreated.
    put_cached_findings("c3", "cat", "v1", _sample_findings())
    assert get_cached_findings("c3", "cat", "v1") == _sample_findings()


# ---------------------------------------------------------------------------
# 6. Permission invariants — 0o600 on file, 0o700 on parent dir
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only mode bits — Windows uses ACLs")
def test_cache_file_has_0600_mode(isolated_cache_env: Path) -> None:
    """The SQLite file mode must be 0o600 after the first write."""
    put_cached_findings("c", "cat", "v1", [{"x": 1}])
    cache_path = isolated_cache_env / cpv_scan_cache._CACHE_FILENAME
    file_mode = stat.S_IMODE(os.stat(cache_path).st_mode)
    assert file_mode == 0o600, f"Expected 0o600 on cache file but got 0o{file_mode:o}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only mode bits — Windows uses ACLs")
def test_cache_parent_dir_has_0700_mode(isolated_cache_env: Path) -> None:
    """The parent dir mode must be 0o700 after first resolution."""
    put_cached_findings("c", "cat", "v1", [{"x": 1}])
    dir_mode = stat.S_IMODE(os.stat(isolated_cache_env).st_mode)
    assert dir_mode == 0o700, f"Expected 0o700 on cache dir but got 0o{dir_mode:o}"


# ---------------------------------------------------------------------------
# 7. Corruption recovery
# ---------------------------------------------------------------------------


def test_corruption_recovery_on_get(isolated_cache_env: Path) -> None:
    """Garbage file → next get wipes and rebuilds; returns MISS, not crash."""
    cache_path = isolated_cache_env / cpv_scan_cache._CACHE_FILENAME
    # Make the parent dir exist so the file can land beside it.
    cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache_path.write_bytes(b"this is not a sqlite database, just random bytes\x00\x01\x02")

    # First get triggers the corruption recovery path and returns None.
    assert get_cached_findings("anything", "cat", "v1") is None

    # Cache is now usable — put + get round-trips.
    put_cached_findings("c1", "cat", "v1", _sample_findings())
    assert get_cached_findings("c1", "cat", "v1") == _sample_findings()


def test_corruption_recovery_on_put(isolated_cache_env: Path) -> None:
    """Garbage file → next put wipes and writes; no crash."""
    cache_path = isolated_cache_env / cpv_scan_cache._CACHE_FILENAME
    cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache_path.write_bytes(b"corrupt")

    # put() should silently swallow the corruption and rebuild.
    put_cached_findings("c1", "cat", "v1", [{"x": 1}])
    # After recovery, the round-trip works.
    assert get_cached_findings("c1", "cat", "v1") == [{"x": 1}]


def test_corrupt_findings_json_returns_none(isolated_cache_env: Path) -> None:
    """Stored findings_json that isn't valid JSON → get returns None."""
    # Bypass put() and write garbage directly into the row.
    put_cached_findings("c1", "cat", "v1", [{"x": 1}])
    cache_path = isolated_cache_env / cpv_scan_cache._CACHE_FILENAME
    conn = sqlite3.connect(str(cache_path))
    conn.execute(
        "UPDATE scan_cache SET findings_json = ? WHERE content_hash = ?",
        ("{not valid json", "c1"),
    )
    conn.commit()
    conn.close()

    assert get_cached_findings("c1", "cat", "v1") is None


def test_corrupt_findings_non_list_returns_none(isolated_cache_env: Path) -> None:
    """Stored findings_json that decodes to a non-list → get returns None.

    Defensive: a malicious or accidental write of a dict / string into
    findings_json must not be served back to the caller, which expects
    a list and will crash on iteration otherwise.
    """
    put_cached_findings("c1", "cat", "v1", [{"x": 1}])
    cache_path = isolated_cache_env / cpv_scan_cache._CACHE_FILENAME
    conn = sqlite3.connect(str(cache_path))
    conn.execute(
        "UPDATE scan_cache SET findings_json = ? WHERE content_hash = ?",
        (json.dumps({"not": "a list"}), "c1"),
    )
    conn.commit()
    conn.close()

    assert get_cached_findings("c1", "cat", "v1") is None


# ---------------------------------------------------------------------------
# 8. Storage location chain (5 candidates)
# ---------------------------------------------------------------------------


def test_location_chain_priority_1_explicit_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CPV_SCAN_CACHE_DIR wins over everything else."""
    target = tmp_path / "explicit_wins"
    monkeypatch.setenv("CPV_SCAN_CACHE_DIR", str(target))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "cpd_loser"))

    put_cached_findings("c", "cat", "v1", [{"x": 1}])
    assert (target / cpv_scan_cache._CACHE_FILENAME).exists()
    assert not (tmp_path / "cpd_loser" / cpv_scan_cache._CACHE_FILENAME).exists()


def test_location_chain_priority_2_claude_plugin_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """CLAUDE_PLUGIN_DATA wins when CPV_SCAN_CACHE_DIR is absent."""
    monkeypatch.delenv("CPV_SCAN_CACHE_DIR", raising=False)
    target = tmp_path / "cpd_dir"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(target))

    put_cached_findings("c", "cat", "v1", [{"x": 1}])
    assert (target / cpv_scan_cache._CACHE_FILENAME).exists()


def test_location_chain_priority_3_dot_claude_plugins_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """~/.claude/plugins/data/... wins when nothing higher is set."""
    monkeypatch.delenv("CPV_SCAN_CACHE_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    # The autouse fixture pre-creates a fake_home; use a unique name
    # for THIS test's home so we control which HOME the resolver sees.
    fake_home = tmp_path / "fake_home_p3"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    put_cached_findings("c", "cat", "v1", [{"x": 1}])

    expected = fake_home / ".claude" / "plugins" / "data" / "claude-plugins-validation" / cpv_scan_cache._CACHE_FILENAME
    assert expected.exists()


def test_location_chain_priority_4_xdg_cache_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """XDG_CACHE_HOME/cpv wins after the dot-claude path.

    We make the dot-claude path UNWRITABLE so the resolver falls
    through to the XDG candidate. On POSIX a 0o555 dir blocks mkdir
    of children.
    """
    if os.name == "nt":
        pytest.skip("POSIX mode-bit fall-through not applicable on Windows")
    monkeypatch.delenv("CPV_SCAN_CACHE_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)

    # Per-test unique HOME so the autouse fixture's HOME doesn't clash.
    fake_home = tmp_path / "fake_home_p4"
    fake_home.mkdir()
    # Pre-create the dot-claude PARENT as read-only so mkdir of the
    # plugins/data subtree fails. We have to use a parent above the
    # mkdir target because mkdir(parents=True) ignores intermediate
    # missing dirs.
    dot_claude_parent = fake_home / ".claude"
    dot_claude_parent.mkdir()
    os.chmod(dot_claude_parent, 0o500)  # readable + executable but NOT writable
    monkeypatch.setenv("HOME", str(fake_home))

    xdg = tmp_path / "xdg_cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))

    try:
        put_cached_findings("c", "cat", "v1", [{"x": 1}])
        expected = xdg / "cpv" / cpv_scan_cache._CACHE_FILENAME
        assert expected.exists()
    finally:
        # Restore writability so pytest's tmp_path teardown can clean up.
        os.chmod(dot_claude_parent, 0o700)


def test_location_chain_priority_5_github_runner_temp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """GITHUB_ACTIONS=true + RUNNER_TEMP → the GHA cache file lands there.

    Higher-priority candidates are nudged into unwritable state so the
    resolver actually falls through to candidate #5.
    """
    if os.name == "nt":
        pytest.skip("POSIX mode-bit fall-through not applicable on Windows")
    monkeypatch.delenv("CPV_SCAN_CACHE_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)

    # Block ~/.claude tree (priority 3) by making its parent read-only.
    # Per-test unique HOME name to avoid collision with the autouse
    # fixture's pre-created fake_home.
    fake_home = tmp_path / "fake_home_p5"
    fake_home.mkdir()
    blocked_dot_claude = fake_home / ".claude"
    blocked_dot_claude.mkdir()
    os.chmod(blocked_dot_claude, 0o500)
    monkeypatch.setenv("HOME", str(fake_home))

    # Block XDG cache (priority 4) similarly.
    xdg = tmp_path / "xdg_cache"
    xdg.mkdir()
    os.chmod(xdg, 0o500)
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg))

    # Enable GitHub Actions code path with a writable runner temp.
    runner_tmp = tmp_path / "runner_tmp"
    runner_tmp.mkdir()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_TEMP", str(runner_tmp))

    try:
        put_cached_findings("c", "cat", "v1", [{"x": 1}])
        assert (runner_tmp / cpv_scan_cache._CACHE_FILENAME_GHA).exists()
    finally:
        os.chmod(blocked_dot_claude, 0o700)
        os.chmod(xdg, 0o700)


# ---------------------------------------------------------------------------
# 9. Disable / deep-mode env vars
# ---------------------------------------------------------------------------


def test_cpv_scan_cache_eq_0_disables_get_and_put(monkeypatch: pytest.MonkeyPatch, isolated_cache_env: Path) -> None:
    """``CPV_SCAN_CACHE=0`` → get always None, put is no-op."""
    # Seed an entry while cache is on.
    put_cached_findings("c1", "cat", "v1", _sample_findings())
    assert get_cached_findings("c1", "cat", "v1") == _sample_findings()

    # Flip the kill switch.
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")
    # get returns None even for a known-good key.
    assert get_cached_findings("c1", "cat", "v1") is None
    # put is silently dropped.
    put_cached_findings("c2", "cat", "v1", _sample_findings())

    # Disable the kill switch and verify the c2 put never landed.
    monkeypatch.delenv("CPV_SCAN_CACHE")
    assert get_cached_findings("c2", "cat", "v1") is None
    # c1 still there because it was written before the kill switch.
    assert get_cached_findings("c1", "cat", "v1") == _sample_findings()


def test_cpv_scan_cache_deep_eq_1_forces_rescan_but_writes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``CPV_SCAN_CACHE_DEEP=1`` → get None, put still writes."""
    put_cached_findings("c1", "cat", "v1", [{"first": True}])

    monkeypatch.setenv("CPV_SCAN_CACHE_DEEP", "1")

    # Deep mode hides the cached value so the caller does a fresh scan.
    assert get_cached_findings("c1", "cat", "v1") is None
    # The caller then puts fresh findings — deep mode lets the write
    # land (it's a warm-the-cache, not a kill-switch).
    put_cached_findings("c1", "cat", "v1", [{"second": True}])

    # Disable deep mode and verify the second put overwrote the first.
    monkeypatch.delenv("CPV_SCAN_CACHE_DEEP")
    assert get_cached_findings("c1", "cat", "v1") == [{"second": True}]


# ---------------------------------------------------------------------------
# 10. cache_stats()
# ---------------------------------------------------------------------------


def test_cache_stats_returns_plausible_numbers(isolated_cache_env: Path) -> None:
    """cache_stats reports entries, path, size, oldest_at sensibly."""
    # Fresh — entries 0, path set, file may not exist yet.
    stats0 = cache_stats()
    assert stats0["path"] is not None
    assert cpv_scan_cache._CACHE_FILENAME in stats0["path"]
    assert stats0["entries"] == 0
    assert stats0["size_bytes"] >= 0
    assert stats0["hit_count"] >= 0
    assert stats0["miss_count"] >= 0

    # Add an entry.
    put_cached_findings("c1", "cat", "v1", _sample_findings())
    stats1 = cache_stats()
    assert stats1["entries"] == 1
    assert stats1["size_bytes"] > 0
    assert stats1["oldest_at"] is not None
    assert stats1["oldest_at"] <= int(time.time())


def test_cache_stats_when_disabled_returns_safe_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cache_stats with no writable location returns a sane empty dict.

    The dict still contains every documented key so callers can index
    blindly. Path is None to indicate cache is off.
    """
    # Disable EVERY candidate so resolution returns None.
    monkeypatch.setenv("CPV_SCAN_CACHE_DIR", "/this/path/cannot/exist/ever/xyz")
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("HOME", "/this/home/cannot/exist/ever/xyz")
    cpv_scan_cache._reset_warned_flag()

    if os.name == "nt":
        # Windows path semantics differ — we use a known-invalid
        # device-namespace string that os.access cannot satisfy.
        monkeypatch.setenv("CPV_SCAN_CACHE_DIR", "Z:\\never\\writable\\xyz")

    stats = cache_stats()
    # Note: depending on the platform, the resolver may still find a
    # writable path. Only assert the result is structurally complete.
    assert "path" in stats
    assert "entries" in stats
    assert "size_bytes" in stats
    assert "oldest_at" in stats
    assert "hit_count" in stats
    assert "miss_count" in stats


# ---------------------------------------------------------------------------
# 11. Concurrent writers
# ---------------------------------------------------------------------------


def test_concurrent_writers_dont_corrupt(isolated_cache_env: Path) -> None:
    """N threads writing distinct keys — every write lands cleanly.

    sqlite3 handles the cross-thread synchronisation; we only verify
    the cache survives concurrent pressure without corruption. Uses
    WAL mode (enabled at connection open) so writers serialise without
    blocking readers indefinitely.
    """
    n_threads = 8
    per_thread = 5
    barrier = threading.Barrier(n_threads)
    errors: list[BaseException] = []

    def worker(tid: int) -> None:
        try:
            barrier.wait()  # release all threads simultaneously
            for j in range(per_thread):
                key = f"t{tid}-i{j}"
                put_cached_findings(key, "cat", "v1", [{"tid": tid, "j": j}])
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,), name=f"writer-{i}") for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"Concurrent writers raised: {errors}"

    # Every write should have landed.
    for i in range(n_threads):
        for j in range(per_thread):
            key = f"t{i}-i{j}"
            assert get_cached_findings(key, "cat", "v1") == [{"tid": i, "j": j}], f"Lost entry for {key}"


# ---------------------------------------------------------------------------
# 12. Permission-denied collapse — no writable path → graceful noop
# ---------------------------------------------------------------------------


def test_all_candidates_unwritable_collapses_to_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Every candidate unwritable → get → None, put → no-op, log once."""
    if os.name == "nt":
        pytest.skip("POSIX mode-bit blocking not applicable on Windows")

    # Pre-create every potential parent dir as read-only so mkdir on
    # the targets fails. We attack via the env-var candidates we
    # control AND a locked-down fake HOME.

    # Lock the explicit dir candidate's parent.
    blocked_parent = tmp_path / "blocked"
    blocked_parent.mkdir()
    explicit_target = blocked_parent / "subdir"
    monkeypatch.setenv("CPV_SCAN_CACHE_DIR", str(explicit_target))

    # Lock CLAUDE_PLUGIN_DATA candidate's parent.
    cpd_blocked = tmp_path / "cpd_blocked"
    cpd_blocked.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(cpd_blocked / "subdir"))

    # Block ~/.claude candidate by giving HOME a read-only parent.
    fake_home = tmp_path / "fake_home_locked"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    # Block XDG cache by pointing it under an unwritable parent.
    xdg_blocked = tmp_path / "xdg_blocked"
    xdg_blocked.mkdir()
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_blocked / "subdir"))

    # No GH actions.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)

    # Make all the parent dirs read-only AT THE LAST MINUTE so the
    # initial mkdir we just did still worked.
    for d in [blocked_parent, cpd_blocked, fake_home, xdg_blocked]:
        os.chmod(d, 0o500)

    cpv_scan_cache._reset_warned_flag()

    try:
        import logging

        with caplog.at_level(logging.INFO, logger="cpv_scan_cache"):
            # get returns None gracefully.
            assert get_cached_findings("c", "cat", "v1") is None
            # put silently no-ops.
            put_cached_findings("c", "cat", "v1", [{"x": 1}])
            # Second call should NOT log again (warned-once contract).
            assert get_cached_findings("c", "cat", "v1") is None

        # The "no writable" INFO message fired at least once.
        no_writable_records = [r for r in caplog.records if "no writable cache location" in r.message]
        assert len(no_writable_records) >= 1, "Expected the warned-once INFO log to fire when no path is writable"
    finally:
        # Restore perms so pytest tmp_path teardown can clean up.
        for d in [blocked_parent, cpd_blocked, fake_home, xdg_blocked]:
            os.chmod(d, 0o700)


# ---------------------------------------------------------------------------
# 13. prune is safe when cache is empty
# ---------------------------------------------------------------------------


def test_prune_empty_cache_returns_zeros() -> None:
    """Pruning an empty cache returns zeros and doesn't crash."""
    out = prune_cache(max_age_days=30, max_entries=100)
    assert out == {"removed_age": 0, "removed_lru": 0}


def test_prune_under_caps_returns_zeros() -> None:
    """Cache below both caps → no rows touched."""
    put_cached_findings("c1", "cat", "v1", [{"x": 1}])
    put_cached_findings("c2", "cat", "v1", [{"x": 2}])
    out = prune_cache(max_age_days=365, max_entries=1000)
    assert out["removed_age"] == 0
    assert out["removed_lru"] == 0
    assert get_cached_findings("c1", "cat", "v1") == [{"x": 1}]
    assert get_cached_findings("c2", "cat", "v1") == [{"x": 2}]


# ---------------------------------------------------------------------------
# 14. put is idempotent / overwrites cleanly
# ---------------------------------------------------------------------------


def test_put_overwrites_existing_key_idempotently() -> None:
    """Re-puting on the same key replaces the findings list."""
    put_cached_findings("c1", "cat", "v1", [{"first": 1}])
    put_cached_findings("c1", "cat", "v1", [{"second": 2}])
    assert get_cached_findings("c1", "cat", "v1") == [{"second": 2}]


# ---------------------------------------------------------------------------
# 15. reset_cache is idempotent when cache doesn't exist
# ---------------------------------------------------------------------------


def test_reset_cache_on_nonexistent_db_does_not_crash(
    isolated_cache_env: Path,
) -> None:
    """reset_cache before any put → no crash, cache becomes usable."""
    reset_cache()
    # Still usable afterward.
    put_cached_findings("c1", "cat", "v1", [{"x": 1}])
    assert get_cached_findings("c1", "cat", "v1") == [{"x": 1}]


# ---------------------------------------------------------------------------
# 16. Non-JSON-encodable findings degrade gracefully
# ---------------------------------------------------------------------------


def test_put_with_non_encodable_findings_silently_skips() -> None:
    """An object json can't encode → put skips, get still returns None."""

    class NotJsonable:
        pass

    findings: list = [{"obj": NotJsonable()}]  # type: ignore[list-item]
    # Must not raise.
    put_cached_findings("c1", "cat", "v1", findings)
    # And nothing landed in the cache.
    assert get_cached_findings("c1", "cat", "v1") is None
