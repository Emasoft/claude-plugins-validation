"""Phase E regression tests: background prefetch of gh-auth + marketplace.json.

These tests pin the contract:

  - The prefetch threads start at the same moment as the parallel
    preflight block (~Phase C). Two stdlib futures are populated:
    ``gh_auth`` and ``marketplace_json``.
  - When the prefetch resolved cleanly, the consuming gate (Gate 5 for
    marketplace.json, Gate 12 for gh-auth) reuses the cached result and
    does NOT make the synchronous network call. This is asserted via
    mock call counts.
  - When the prefetch failed transiently (the wrapped helper returned
    None or raised an unexpected exception), the consuming gate falls
    back to the synchronous call. Behaviour is identical to the
    pre-Phase-E pipeline.
  - When the prefetch raised SystemExit (permanent gh-auth failure), it
    is re-raised on the main thread when Gate 12 consumes it — the
    fail-fast invariant must survive the prefetch indirection.
  - Layout="none" runs neither prefetch (no marketplace, no fast-path
    win to chase). The shared dataclass is still constructed but both
    futures stay None, and the consuming gates take their existing
    sequential paths.
  - The prefetch ThreadPoolExecutor is shut down on every exit path so
    early-failure runs (Gate 6 returns non-zero) do not leak worker
    threads and stall process exit.

These tests use stdlib monkeypatching only — no extra deps — so they
run cleanly under pytest-xdist (Phase A).
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Defensive: tests/conftest.py adds scripts/ to sys.path; this duplicate
# guard makes the file work when collected in isolation.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import publish  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers — synthesise prefetch futures without spinning up real threads.
# ---------------------------------------------------------------------------


def _resolved_future(value=None, exception: BaseException | None = None):
    """Create a concurrent.futures.Future already in the resolved state.

    Cleaner than spinning up a one-shot ThreadPoolExecutor just to set
    a known result on a future — these tests don't care about timing,
    they care about the ``used the cached result vs called sync helper``
    branch.
    """
    from concurrent.futures import Future

    fut: Future = Future()
    if exception is not None:
        fut.set_exception(exception)
    else:
        fut.set_result(value)
    return fut


def _layout_a_details() -> dict:
    """Return a Layout-A details dict that's accepted by _start_prefetch."""
    return {
        "notify_workflow": Path("/fake/notify.yml"),
        "mkt_owner": "Emasoft",
        "mkt_repo": "test-marketplace",
    }


# ---------------------------------------------------------------------------
# 1. Prefetch success path — cached results consumed, sync calls skipped.
# ---------------------------------------------------------------------------


def test_prefetch_success_path_marketplace_skips_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When the marketplace.json prefetch resolved cleanly, _check_layout_a
    must reuse the cached dict and skip the synchronous fetch entirely.

    Asserts via mock call count: _fetch_remote_marketplace_json gets
    invoked ZERO times during the gate when the prefetch is good.
    """
    plugin_root = tmp_path
    details = _layout_a_details()

    cached_mkt = {
        "plugins": [
            {
                "name": "fake-plugin",
                "source": {"source": "github", "repo": "Emasoft/fake-plugin"},
            }
        ]
    }

    prefetch = publish._PrefetchResults(
        marketplace_json=_resolved_future(cached_mkt),
        marketplace_target=("Emasoft", "test-marketplace"),
    )

    sync_calls: list[tuple] = []

    def fake_fetch_sync(mkt_owner, mkt_repo, *, gh_bin=None):
        sync_calls.append((mkt_owner, mkt_repo))
        return cached_mkt  # never reached, but realistic shape

    monkeypatch.setattr(publish, "_fetch_remote_marketplace_json", fake_fetch_sync)
    # Stub out the side calls that aren't relevant to this test.
    monkeypatch.setattr(publish, "_gh_secret_exists", lambda *_a, **_kw: True)
    monkeypatch.setattr(publish, "_remote_has_receiver_workflow", lambda *_a, **_kw: True)
    monkeypatch.setattr(publish, "_read_plugin_name", lambda _root: "fake-plugin")
    monkeypatch.setattr(publish, "_current_repo_slug", lambda _root: "Emasoft/fake-plugin")
    # Pretend gh CLI is installed so the layout-A check doesn't bail early.
    monkeypatch.setattr(publish.shutil, "which", lambda _name: "/usr/bin/gh")
    # Pretend the notify workflow exists on disk.
    monkeypatch.setattr(Path, "is_file", lambda _self: True)

    rc = publish._check_layout_a(plugin_root, details, prefetch=prefetch)

    assert rc == 0, f"Expected layout-A check to pass, got rc={rc}"
    assert sync_calls == [], (
        f"Expected 0 synchronous _fetch_remote_marketplace_json calls when "
        f"prefetch is good, got {len(sync_calls)}: {sync_calls}"
    )


def test_prefetch_success_path_gh_auth_skips_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When the gh-auth prefetch resolved cleanly (no exception), Gate 12
    must reuse the cached result and skip the synchronous _ensure_gh_auth
    call.

    Asserts via call counter: _ensure_gh_auth gets invoked ZERO times in
    Gate 12 when the prefetch is clean.
    """
    plugin_root = tmp_path

    prefetch = publish._PrefetchResults(
        gh_auth=_resolved_future(None),  # success → result is None, no exception
        gh_auth_target=("Emasoft", "fake-plugin"),
    )

    sync_calls: list[tuple] = []

    def fake_ensure_sync(owner, repo):
        sync_calls.append((owner, repo))

    monkeypatch.setattr(publish, "_ensure_gh_auth", fake_ensure_sync)
    monkeypatch.setattr(publish, "_resolve_owner_repo", lambda _root: ("Emasoft", "fake-plugin"))
    # Stub everything else stage_commit_tag_push touches so we don't have
    # to set up a real git repo. We only care about whether the gh-auth
    # branch fired.
    monkeypatch.setattr(publish, "_head_commit_message", lambda _root: "chore(release): v1.2.3")
    monkeypatch.setattr(publish, "_git_porcelain_clean", lambda _root: True)
    monkeypatch.setattr(publish, "_local_tag_exists", lambda _root, _tag: True)
    monkeypatch.setattr(publish, "_remote_tag_exists", lambda _root, _tag: True)

    # Force the early-return paths so we never hit the actual git push.
    def fake_run(_cmd, *_args, **_kw):
        # Return a success-coded CompletedProcess shape. Accept any
        # positional or keyword args (publish.run takes both forms).
        cp = MagicMock()
        cp.returncode = 0
        cp.stdout = ""
        cp.stderr = ""
        return cp

    monkeypatch.setattr(publish, "run", fake_run)
    monkeypatch.setattr(publish, "_ensure_submodules_pushed", lambda _root: None)

    def fake_git_with_retry(*_args, **_kw):
        cp = MagicMock()
        cp.returncode = 0
        return cp

    monkeypatch.setattr(publish, "git_with_retry", fake_git_with_retry)

    rc = publish.stage_commit_tag_push(plugin_root, "v1.2.3", prefetch=prefetch)

    assert rc == 0, f"Expected Gate 12 to succeed, got rc={rc}"
    assert sync_calls == [], (
        f"Expected 0 synchronous _ensure_gh_auth calls when prefetch is clean, got {len(sync_calls)}: {sync_calls}"
    )


# ---------------------------------------------------------------------------
# 2. Transient failure → fall back to sync call.
# ---------------------------------------------------------------------------


def test_prefetch_transient_failure_falls_back_to_sync_marketplace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When the marketplace.json prefetch returned None (transient network
    failure: 503 from github.com edge, parse failure on a partial body,
    etc.), the gate MUST fall back to its synchronous fetch — not skip
    the check entirely.

    Asserts via mock call count: _fetch_remote_marketplace_json IS called
    once during the gate.
    """
    plugin_root = tmp_path
    details = _layout_a_details()

    # Simulate a transient network failure: the prefetch returned None
    # (the helper's documented "transient failure" signal — see
    # publish._fetch_remote_marketplace_json for the contract).
    prefetch = publish._PrefetchResults(
        marketplace_json=_resolved_future(None),
        marketplace_target=("Emasoft", "test-marketplace"),
    )

    sync_calls: list[tuple] = []

    def fake_fetch_sync(mkt_owner, mkt_repo, *, gh_bin=None):
        sync_calls.append((mkt_owner, mkt_repo))
        # The retry on the synchronous call succeeds — this models a
        # transient failure on the prefetch but a recovered network by
        # the time Gate 5 runs.
        return {
            "plugins": [
                {
                    "name": "fake-plugin",
                    "source": {"source": "github", "repo": "Emasoft/fake-plugin"},
                }
            ]
        }

    monkeypatch.setattr(publish, "_fetch_remote_marketplace_json", fake_fetch_sync)
    monkeypatch.setattr(publish, "_gh_secret_exists", lambda *_a, **_kw: True)
    monkeypatch.setattr(publish, "_remote_has_receiver_workflow", lambda *_a, **_kw: True)
    monkeypatch.setattr(publish, "_read_plugin_name", lambda _root: "fake-plugin")
    monkeypatch.setattr(publish, "_current_repo_slug", lambda _root: "Emasoft/fake-plugin")
    monkeypatch.setattr(publish.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(Path, "is_file", lambda _self: True)

    rc = publish._check_layout_a(plugin_root, details, prefetch=prefetch)

    assert rc == 0, f"Expected layout-A check to pass after sync fallback, got rc={rc}"
    assert sync_calls == [("Emasoft", "test-marketplace")], (
        f"Expected EXACTLY one sync fallback call to _fetch_remote_marketplace_json, got {sync_calls}"
    )


def test_prefetch_transient_failure_falls_back_to_sync_gh_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When the gh-auth prefetch raised an UNEXPECTED exception (not
    SystemExit — that's the permanent-failure path), Gate 12 must fall
    back to the synchronous call so a clean current error from
    _ensure_gh_auth surfaces.

    A genuine SystemExit is asserted in test_prefetch_systemexit_re_raises.
    """
    plugin_root = tmp_path

    # Simulate an unexpected exception (e.g. a network library
    # short-circuited with a TimeoutError instead of SystemExit).
    prefetch = publish._PrefetchResults(
        gh_auth=_resolved_future(exception=TimeoutError("network timeout")),
        gh_auth_target=("Emasoft", "fake-plugin"),
    )

    sync_calls: list[tuple] = []

    def fake_ensure_sync(owner, repo):
        sync_calls.append((owner, repo))

    monkeypatch.setattr(publish, "_ensure_gh_auth", fake_ensure_sync)
    monkeypatch.setattr(publish, "_resolve_owner_repo", lambda _root: ("Emasoft", "fake-plugin"))
    monkeypatch.setattr(publish, "_head_commit_message", lambda _root: "chore(release): v1.2.3")
    monkeypatch.setattr(publish, "_git_porcelain_clean", lambda _root: True)
    monkeypatch.setattr(publish, "_local_tag_exists", lambda _root, _tag: True)
    monkeypatch.setattr(publish, "_remote_tag_exists", lambda _root, _tag: True)

    def fake_run(_cmd, *_args, **_kw):
        cp = MagicMock()
        cp.returncode = 0
        cp.stdout = ""
        cp.stderr = ""
        return cp

    monkeypatch.setattr(publish, "run", fake_run)
    monkeypatch.setattr(publish, "_ensure_submodules_pushed", lambda _root: None)

    def fake_git_with_retry(*_args, **_kw):
        cp = MagicMock()
        cp.returncode = 0
        return cp

    monkeypatch.setattr(publish, "git_with_retry", fake_git_with_retry)

    rc = publish.stage_commit_tag_push(plugin_root, "v1.2.3", prefetch=prefetch)

    assert rc == 0, f"Expected Gate 12 to succeed via sync fallback, got rc={rc}"
    assert sync_calls == [("Emasoft", "fake-plugin")], (
        f"Expected EXACTLY one sync fallback call to _ensure_gh_auth, got {sync_calls}"
    )


# ---------------------------------------------------------------------------
# 3. SystemExit from prefetch must re-raise on the main thread.
# ---------------------------------------------------------------------------


def test_prefetch_systemexit_re_raises_on_main_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When the gh-auth prefetch raised SystemExit (permanent auth
    failure: gh CLI not installed, no push perm, etc.), Gate 12 MUST
    re-raise it on the main thread.

    Without this, the SystemExit dies in the worker thread and Gate 12
    silently skips the auth check, defeating the precheck — the user
    would only discover the auth failure when `git push` itself fails
    later, defeating the whole point of TRDD-bbff5bc5.
    """
    plugin_root = tmp_path

    prefetch = publish._PrefetchResults(
        gh_auth=_resolved_future(exception=SystemExit(1)),
        gh_auth_target=("Emasoft", "fake-plugin"),
    )

    sync_calls: list[tuple] = []

    def fake_ensure_sync(owner, repo):
        sync_calls.append((owner, repo))

    monkeypatch.setattr(publish, "_ensure_gh_auth", fake_ensure_sync)
    monkeypatch.setattr(publish, "_resolve_owner_repo", lambda _root: ("Emasoft", "fake-plugin"))
    monkeypatch.setattr(publish, "_head_commit_message", lambda _root: "chore(release): v1.2.3")
    monkeypatch.setattr(publish, "_git_porcelain_clean", lambda _root: True)
    monkeypatch.setattr(publish, "_local_tag_exists", lambda _root, _tag: True)

    def fake_run(_cmd, *_args, **_kw):
        cp = MagicMock()
        cp.returncode = 0
        cp.stdout = ""
        cp.stderr = ""
        return cp

    monkeypatch.setattr(publish, "run", fake_run)

    with pytest.raises(SystemExit) as excinfo:
        publish.stage_commit_tag_push(plugin_root, "v1.2.3", prefetch=prefetch)
    assert excinfo.value.code == 1
    # And the synchronous fallback must NOT have been called — re-raising
    # the prefetched SystemExit is the canonical fail-fast path.
    assert sync_calls == [], (
        f"_ensure_gh_auth sync fallback was called despite prefetch raising SystemExit. Got {sync_calls}"
    )


# ---------------------------------------------------------------------------
# 4. Mismatched target → ignore prefetch and fall back to sync.
# ---------------------------------------------------------------------------


def test_prefetch_mismatched_target_falls_back_to_sync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """If the prefetch was scoped to a different (owner, repo) than the
    gate is now operating on (defensive: catches a future refactor that
    might add remote-rename handling between prefetch start and gate
    consumption), the cached result is rejected and the synchronous
    fetch runs.
    """
    plugin_root = tmp_path
    details = _layout_a_details()  # mkt_owner="Emasoft", mkt_repo="test-marketplace"

    cached_mkt = {"plugins": []}
    prefetch = publish._PrefetchResults(
        marketplace_json=_resolved_future(cached_mkt),
        # Different target — should be ignored.
        marketplace_target=("Other", "different-marketplace"),
    )

    sync_calls: list[tuple] = []

    def fake_fetch_sync(mkt_owner, mkt_repo, *, gh_bin=None):
        sync_calls.append((mkt_owner, mkt_repo))
        return {
            "plugins": [
                {
                    "name": "fake-plugin",
                    "source": {"source": "github", "repo": "Emasoft/fake-plugin"},
                }
            ]
        }

    monkeypatch.setattr(publish, "_fetch_remote_marketplace_json", fake_fetch_sync)
    monkeypatch.setattr(publish, "_gh_secret_exists", lambda *_a, **_kw: True)
    monkeypatch.setattr(publish, "_remote_has_receiver_workflow", lambda *_a, **_kw: True)
    monkeypatch.setattr(publish, "_read_plugin_name", lambda _root: "fake-plugin")
    monkeypatch.setattr(publish, "_current_repo_slug", lambda _root: "Emasoft/fake-plugin")
    monkeypatch.setattr(publish.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(Path, "is_file", lambda _self: True)

    rc = publish._check_layout_a(plugin_root, details, prefetch=prefetch)

    assert rc == 0
    assert sync_calls == [("Emasoft", "test-marketplace")], (
        f"Expected sync fallback when prefetch target mismatches, got {sync_calls}"
    )


# ---------------------------------------------------------------------------
# 5. No prefetch (None) → consuming gates work exactly like pre-Phase-E.
# ---------------------------------------------------------------------------


def test_no_prefetch_falls_back_to_sync_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Pre-Phase-E callers (e.g. cpv-plugin-fixer-agent running just `gh release create`
    out-of-band) pass nothing for ``prefetch``. The consuming gates must
    behave identically to the pre-Phase-E pipeline — i.e. always do their
    synchronous calls.
    """
    plugin_root = tmp_path
    details = _layout_a_details()

    sync_calls: list[tuple] = []

    def fake_fetch_sync(mkt_owner, mkt_repo, *, gh_bin=None):
        sync_calls.append((mkt_owner, mkt_repo))
        return {
            "plugins": [
                {
                    "name": "fake-plugin",
                    "source": {"source": "github", "repo": "Emasoft/fake-plugin"},
                }
            ]
        }

    monkeypatch.setattr(publish, "_fetch_remote_marketplace_json", fake_fetch_sync)
    monkeypatch.setattr(publish, "_gh_secret_exists", lambda *_a, **_kw: True)
    monkeypatch.setattr(publish, "_remote_has_receiver_workflow", lambda *_a, **_kw: True)
    monkeypatch.setattr(publish, "_read_plugin_name", lambda _root: "fake-plugin")
    monkeypatch.setattr(publish, "_current_repo_slug", lambda _root: "Emasoft/fake-plugin")
    monkeypatch.setattr(publish.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(Path, "is_file", lambda _self: True)

    # Don't pass prefetch — should behave like pre-Phase-E.
    rc = publish._check_layout_a(plugin_root, details)

    assert rc == 0
    assert sync_calls == [("Emasoft", "test-marketplace")], (
        f"With prefetch=None, must do exactly one sync fetch, got {sync_calls}"
    )


# ---------------------------------------------------------------------------
# 6. Layout="none" skips the prefetch entirely.
# ---------------------------------------------------------------------------


def test_layout_none_skips_prefetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When detect_layout returns "none" (no marketplace wired), neither
    prefetch fires. Both futures stay None and no executor is created.

    The consuming gates take their existing sequential paths because
    Gate 5 short-circuits to a WARNING in no-layout mode, and Gate 12
    still runs its synchronous _ensure_gh_auth (we don't ALSO skip
    that in no-layout mode — gh-auth is still needed for `git push`
    regardless of marketplace state).
    """
    plugin_root = tmp_path

    # Even though we're in layout="none", the helper must not crash and
    # must not start any threads.
    submitted: list[str] = []

    class _RecordingExecutor:
        def __init__(self, *_args, **_kw):
            submitted.append("__init__")

        def submit(self, *_args, **_kw):
            submitted.append("submit")
            raise AssertionError("submit() should never be called when layout='none'")

        def shutdown(self, **_kw):
            pass

    monkeypatch.setattr(publish, "ThreadPoolExecutor", _RecordingExecutor)

    prefetch = publish._start_prefetch(plugin_root, "none", {})

    assert prefetch.gh_auth is None, "gh_auth future must be None for layout='none'"
    assert prefetch.marketplace_json is None, "marketplace_json future must be None for layout='none'"
    assert prefetch.executor is None, "executor must be None for layout='none' (no threads spawned)"
    assert submitted == [], f"ThreadPoolExecutor was instantiated/used despite layout='none': {submitted}"


# ---------------------------------------------------------------------------
# 7. Layout B starts only the gh-auth prefetch (no remote marketplace fetch).
# ---------------------------------------------------------------------------


def test_layout_b_starts_only_gh_auth_prefetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Layout B (nested plugin in marketplace repo) reads marketplace.json
    from disk in the parent repo — no remote fetch needed. Only the
    gh-auth prefetch runs.
    """
    plugin_root = tmp_path
    monkeypatch.setattr(publish, "_current_repo_slug", lambda _root: "Emasoft/fake-plugin")
    # Stub the real prefetch worker so we don't fire a real network call
    # against the unsuspecting Emasoft/fake-plugin repo.
    monkeypatch.setattr(publish, "_ensure_gh_auth", lambda _o, _r: None)

    prefetch = publish._start_prefetch(plugin_root, "B", {"marketplace_root": tmp_path, "plugin_name": "x"})

    try:
        assert prefetch.gh_auth is not None, "Layout B must still start gh-auth prefetch"
        assert prefetch.marketplace_json is None, "Layout B must NOT start marketplace.json prefetch (parent on disk)"
        assert prefetch.gh_auth_target == ("Emasoft", "fake-plugin")
        assert prefetch.marketplace_target is None
        # Wait for the (stubbed) prefetch to finish so the worker thread
        # exits cleanly before shutdown(wait=False) returns.
        prefetch.gh_auth.result(timeout=5)
    finally:
        prefetch.shutdown()


# ---------------------------------------------------------------------------
# 8. Thread cleanup on early failure — prefetch.shutdown() releases workers.
# ---------------------------------------------------------------------------


def test_prefetch_shutdown_releases_executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When a Gate 6+ failure short-circuits before the prefetch threads
    have been consumed, ``prefetch.shutdown()`` must be called on every
    exit path so the worker threads don't block process exit.

    We assert this directly: after shutdown(), the executor reference is
    cleared and a second shutdown is a no-op (idempotent).
    """

    # Use the real _start_prefetch but make the prefetch worker block
    # on an event so we can verify shutdown signals it.
    block = threading.Event()

    def slow_gh_auth(_owner, _repo):
        # Wait on the event so the prefetch is still in-flight when we
        # call shutdown(). Must NOT take longer than the test's overall
        # timeout — the .set() in finally guarantees this.
        block.wait(timeout=2.0)

    monkeypatch.setattr(publish, "_ensure_gh_auth", slow_gh_auth)
    monkeypatch.setattr(publish, "_current_repo_slug", lambda _root: "Emasoft/fake-plugin")

    prefetch = publish._start_prefetch(plugin_root=tmp_path, layout="B", layout_details={})

    try:
        assert prefetch.executor is not None
        executor_ref = prefetch.executor

        # Idempotent shutdown
        prefetch.shutdown()
        assert prefetch.executor is None
        prefetch.shutdown()  # second call must not raise
        assert prefetch.executor is None

        # The original executor object should still be valid; it's
        # waiting on the event. Releasing the event lets the worker
        # finish quickly.
        block.set()

        # After the event releases, the worker thread should finish and
        # the executor should be ready to clean up. We don't assert
        # join() here — shutdown(wait=False) was the intentional design
        # choice for snappy main() exit.
        assert executor_ref is not None  # smoke: we held the reference
    finally:
        # Belt-and-braces: even if something raised above, unblock the
        # worker so the test process can exit cleanly.
        block.set()


# ---------------------------------------------------------------------------
# 9. No double-call — successful prefetch means the synchronous call NEVER
#    runs (across both consumers, the total count is 1: just the prefetch).
# ---------------------------------------------------------------------------


def test_no_double_call_marketplace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """End-to-end: when the prefetch succeeded, the total number of
    _fetch_remote_marketplace_json invocations across BOTH the prefetch
    AND the gate is exactly 1 (the prefetch). The gate's sync call must
    NOT also fire.

    This pins the behaviour the user actually cares about: Phase E
    saves a network round-trip, not just shifts it earlier.
    """
    plugin_root = tmp_path
    details = _layout_a_details()

    fetch_call_count = 0
    cached_mkt = {
        "plugins": [
            {
                "name": "fake-plugin",
                "source": {"source": "github", "repo": "Emasoft/fake-plugin"},
            }
        ]
    }

    def counting_fetch(_mkt_owner, _mkt_repo, *, gh_bin=None):
        nonlocal fetch_call_count
        fetch_call_count += 1
        return cached_mkt

    monkeypatch.setattr(publish, "_fetch_remote_marketplace_json", counting_fetch)
    monkeypatch.setattr(publish, "_gh_secret_exists", lambda *_a, **_kw: True)
    monkeypatch.setattr(publish, "_remote_has_receiver_workflow", lambda *_a, **_kw: True)
    monkeypatch.setattr(publish, "_read_plugin_name", lambda _root: "fake-plugin")
    monkeypatch.setattr(publish, "_current_repo_slug", lambda _root: "Emasoft/fake-plugin")
    # Stub the gh-auth prefetch so it doesn't actually run gh against
    # the unsuspecting Emasoft/fake-plugin repo (which would emit a
    # noisy permission-denied error to stderr).
    monkeypatch.setattr(publish, "_ensure_gh_auth", lambda _o, _r: None)
    monkeypatch.setattr(publish.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(Path, "is_file", lambda _self: True)

    # Run the full prefetch + consume cycle.
    prefetch = publish._start_prefetch(plugin_root, "A", details)
    try:
        # Wait for the prefetch to actually finish so we count its call
        # before the gate runs (otherwise the gate could see the
        # in-flight future and we'd race).
        if prefetch.marketplace_json is not None:
            prefetch.marketplace_json.result(timeout=5)
        if prefetch.gh_auth is not None:
            prefetch.gh_auth.result(timeout=5)
        # Now run the gate — it should reuse the cached value, NOT call
        # the sync fetch.
        rc = publish._check_layout_a(plugin_root, details, prefetch=prefetch)
    finally:
        prefetch.shutdown()

    assert rc == 0, f"Layout-A check failed: rc={rc}"
    assert fetch_call_count == 1, (
        f"Expected exactly 1 _fetch_remote_marketplace_json call across "
        f"prefetch + gate (the prefetch's only), got {fetch_call_count}"
    )


# ---------------------------------------------------------------------------
# 10. Smoke — _start_prefetch returns valid results with both futures set.
# ---------------------------------------------------------------------------


def test_start_prefetch_layout_a_populates_both_futures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """For Layout A with valid mkt_owner/mkt_repo and a resolvable git
    origin, both futures are populated with the expected targets.
    """
    plugin_root = tmp_path
    details = _layout_a_details()

    monkeypatch.setattr(publish, "_current_repo_slug", lambda _root: "Emasoft/fake-plugin")

    # Replace the worker functions with cheap synchronous shims so the
    # prefetch resolves quickly.
    monkeypatch.setattr(publish, "_ensure_gh_auth", lambda _o, _r: None)
    monkeypatch.setattr(publish, "_fetch_remote_marketplace_json", lambda _o, _r, *, gh_bin=None: {"plugins": []})

    prefetch = publish._start_prefetch(plugin_root, "A", details)

    try:
        assert prefetch.gh_auth is not None, "gh_auth future must be populated"
        assert prefetch.marketplace_json is not None, "marketplace_json future must be populated"
        assert prefetch.gh_auth_target == ("Emasoft", "fake-plugin")
        assert prefetch.marketplace_target == ("Emasoft", "test-marketplace")

        # Wait for both to resolve so we don't leak in-flight work into
        # the next test.
        prefetch.gh_auth.result(timeout=5)
        prefetch.marketplace_json.result(timeout=5)
    finally:
        prefetch.shutdown()


# ---------------------------------------------------------------------------
# 11. _PrefetchResults default (no prefetch started) is safe to use.
# ---------------------------------------------------------------------------


def test_default_prefetch_results_is_safe():
    """The default _PrefetchResults() (no prefetch started) must be safe
    to pass into the consuming gates. shutdown() must be a no-op when
    no executor was created.
    """
    pf = publish._PrefetchResults()
    assert pf.gh_auth is None
    assert pf.marketplace_json is None
    assert pf.executor is None
    pf.shutdown()  # must not raise
    pf.shutdown()  # must remain idempotent


# ---------------------------------------------------------------------------
# 12. Layout-A without resolvable owner/repo → mkpl prefetch only.
# ---------------------------------------------------------------------------


def test_layout_a_without_origin_only_starts_marketplace_prefetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """If `git remote get-url origin` fails (no remote configured), we
    skip the gh-auth prefetch — Gate 12 will surface the failure via
    its existing _resolve_owner_repo path with the same error message.

    The marketplace.json prefetch can still run because mkt_owner/
    mkt_repo come from the notify-marketplace.yml workflow on disk,
    not from git origin.
    """
    plugin_root = tmp_path
    details = _layout_a_details()

    # Simulate `git remote get-url origin` failing.
    monkeypatch.setattr(publish, "_current_repo_slug", lambda _root: None)
    monkeypatch.setattr(publish, "_fetch_remote_marketplace_json", lambda _o, _r, *, gh_bin=None: {"plugins": []})

    prefetch = publish._start_prefetch(plugin_root, "A", details)

    try:
        assert prefetch.gh_auth is None, "gh_auth future must be None when origin is unresolvable"
        assert prefetch.marketplace_json is not None, (
            "marketplace_json future must still populate (independent of git origin)"
        )
        prefetch.marketplace_json.result(timeout=5)
    finally:
        prefetch.shutdown()
