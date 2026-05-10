"""Phase C regression tests: parallel Gates 2/3/4/5 in publish.py preflight.

These tests pin the contract:

  - Gate 1 (working tree) runs sequentially BEFORE the parallel block. If
    Gate 1 fails, Gates 2-5 don't run at all.
  - Gates 2/3/4/5 run concurrently in a 4-worker thread pool. Total wall
    time is dominated by the slowest gate (max), not the sum of all four.
  - Per-gate stdout/stderr is captured and replayed in fixed canonical
    order: tests → validate → mkpl_validate → mkpl_reg. Replay is
    deterministic regardless of which gate finishes first.
  - First non-zero return code (in canonical order) propagates to the
    caller, so the publish exit code is stable across runs even when
    multiple gates fail simultaneously.
  - Gate 6 (version consistency) still runs sequentially AFTER the
    parallel block.

Wall-time assertions use a generous 5x slack so the suite stays green on
slow CI runners. The point is to catch a regression to serial execution
(where wall time would equal the SUM of per-task sleeps), not to assert
nanosecond-level timing.
"""

from __future__ import annotations

import io
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

# Defensive: tests/conftest.py adds scripts/ to sys.path; this duplicate
# guard makes the file work when collected in isolation.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import publish  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sleeping_stage(name: str, sleep_s: float, *, rc: int = 0, log: list[str] | None = None):
    """Build a fake stage_*() function that sleeps then returns a code.

    The stage emits a "═══ Gate X ═══" header line on entry (so the
    replay-ordering test can see the markers) and a status line on exit.
    """

    def fn(*_args, **_kwargs) -> int:
        if log is not None:
            log.append(f"{name}:enter")
        # The header line every real stage prints — synthesised here so
        # captured output looks realistic.
        print(f"\n[{name}] header line")
        time.sleep(sleep_s)
        if rc == 0:
            print(f"[{name}] OK")
        else:
            print(f"[{name}] FAIL", file=sys.stderr)
        if log is not None:
            log.append(f"{name}:exit")
        return rc

    return fn


# ---------------------------------------------------------------------------
# 1. All four gates run concurrently — wall time ≈ slowest, not sum
# ---------------------------------------------------------------------------


def test_gates_2_3_4_5_run_concurrently(tmp_path: Path):
    """Each of the 4 gates sleeps 1.0s. Serial execution would take ~4s.

    Parallel execution should finish in ~1.0s. We allow up to 2.5s before
    flagging a regression to serial dispatch — that gives ample slack for
    thread-startup overhead, GIL contention, and slow CI runners while
    still catching a 4x serial regression.
    """
    plugin_root = tmp_path
    layout = "A"

    fake_tests = _make_sleeping_stage("tests", 1.0)
    fake_validate = _make_sleeping_stage("validate", 1.0)
    fake_mkpl_v = _make_sleeping_stage("mkpl_validate", 1.0)
    fake_mkpl_r = _make_sleeping_stage("mkpl_reg", 1.0)

    with patch.object(publish, "stage_run_tests", fake_tests), \
         patch.object(publish, "stage_validate_plugin", fake_validate), \
         patch.object(publish, "stage_validate_marketplace", fake_mkpl_v), \
         patch.object(publish, "stage_marketplace_registration_check", fake_mkpl_r):
        t0 = time.monotonic()
        rc = publish.run_preflight_parallel(plugin_root, layout)
        elapsed = time.monotonic() - t0

    assert rc == 0, f"All gates returned 0 but orchestrator returned {rc}"
    assert elapsed < 2.5, (
        f"Parallel preflight took {elapsed:.2f}s — expected ~1.0s. "
        f"Likely regressed to serial dispatch (would take ~4s)."
    )


# ---------------------------------------------------------------------------
# 2. Failure in any gate returns its rc (not the first OK or last)
# ---------------------------------------------------------------------------


def test_failure_in_validate_gate_propagates_rc(tmp_path: Path):
    """Gate 3 (validate) fails with rc=2 (MAJOR) while the other 3 pass.

    The orchestrator must return 2 — not 0 from successful Gate 2, and
    not the rc of whichever gate finished last by thread scheduling.
    """
    plugin_root = tmp_path
    layout = "A"

    fake_tests = _make_sleeping_stage("tests", 0.05, rc=0)
    fake_validate = _make_sleeping_stage("validate", 0.05, rc=2)  # MAJOR
    fake_mkpl_v = _make_sleeping_stage("mkpl_validate", 0.05, rc=0)
    fake_mkpl_r = _make_sleeping_stage("mkpl_reg", 0.05, rc=0)

    with patch.object(publish, "stage_run_tests", fake_tests), \
         patch.object(publish, "stage_validate_plugin", fake_validate), \
         patch.object(publish, "stage_validate_marketplace", fake_mkpl_v), \
         patch.object(publish, "stage_marketplace_registration_check", fake_mkpl_r):
        rc = publish.run_preflight_parallel(plugin_root, layout)

    assert rc == 2, f"Expected rc=2 from failed validate gate, got {rc}"


def test_first_failure_in_canonical_order_wins(tmp_path: Path):
    """Two gates fail simultaneously: tests with rc=1 (CRITICAL) and
    validate with rc=2 (MAJOR). Canonical order is tests → validate, so
    the orchestrator must return 1, not 2.

    This pins the determinism contract: thread scheduling MUST NOT affect
    the reported severity. CRITICAL always wins over MAJOR even when
    MAJOR happens to finish first.
    """
    plugin_root = tmp_path
    layout = "A"

    fake_tests = _make_sleeping_stage("tests", 0.30, rc=1)        # slow CRITICAL
    fake_validate = _make_sleeping_stage("validate", 0.05, rc=2)  # fast MAJOR
    fake_mkpl_v = _make_sleeping_stage("mkpl_validate", 0.05, rc=0)
    fake_mkpl_r = _make_sleeping_stage("mkpl_reg", 0.05, rc=0)

    with patch.object(publish, "stage_run_tests", fake_tests), \
         patch.object(publish, "stage_validate_plugin", fake_validate), \
         patch.object(publish, "stage_validate_marketplace", fake_mkpl_v), \
         patch.object(publish, "stage_marketplace_registration_check", fake_mkpl_r):
        rc = publish.run_preflight_parallel(plugin_root, layout)

    assert rc == 1, (
        f"Expected canonical-first failure rc=1 (tests/CRITICAL), got {rc}. "
        f"Did the orchestrator stop returning the first-in-order rc?"
    )


# ---------------------------------------------------------------------------
# 3. Output ordering preserved — replay in canonical order
# ---------------------------------------------------------------------------


def test_output_replayed_in_canonical_order(tmp_path: Path, capfd):
    """Stages finish in REVERSE order (mkpl_reg first, tests last) but
    replay must still be tests → validate → mkpl_validate → mkpl_reg.

    Captured stdout MUST contain the four header lines in canonical
    order, regardless of when each gate emitted them.
    """
    plugin_root = tmp_path
    layout = "A"

    # tests sleeps the longest, mkpl_reg the shortest — completion order
    # is mkpl_reg → mkpl_validate → validate → tests.
    fake_tests = _make_sleeping_stage("tests", 0.40, rc=0)
    fake_validate = _make_sleeping_stage("validate", 0.30, rc=0)
    fake_mkpl_v = _make_sleeping_stage("mkpl_validate", 0.20, rc=0)
    fake_mkpl_r = _make_sleeping_stage("mkpl_reg", 0.10, rc=0)

    with patch.object(publish, "stage_run_tests", fake_tests), \
         patch.object(publish, "stage_validate_plugin", fake_validate), \
         patch.object(publish, "stage_validate_marketplace", fake_mkpl_v), \
         patch.object(publish, "stage_marketplace_registration_check", fake_mkpl_r):
        rc = publish.run_preflight_parallel(plugin_root, layout)

    assert rc == 0
    out, _err = capfd.readouterr()
    # Find the index of each header line in the captured stdout.
    indices = {
        "tests": out.find("[tests] header"),
        "validate": out.find("[validate] header"),
        "mkpl_validate": out.find("[mkpl_validate] header"),
        "mkpl_reg": out.find("[mkpl_reg] header"),
    }
    for name, idx in indices.items():
        assert idx >= 0, f"Captured output missing [{name}] header. Got:\n{out}"
    # Canonical order — index of each header strictly increases.
    canonical_indices = [
        indices["tests"],
        indices["validate"],
        indices["mkpl_validate"],
        indices["mkpl_reg"],
    ]
    assert canonical_indices == sorted(canonical_indices), (
        f"Replay out of canonical order. Indices: {indices}.\n"
        f"Captured stdout:\n{out}"
    )


# ---------------------------------------------------------------------------
# 4. Gate 1 still runs first sequentially. If Gate 1 fails, Gates 2-5
#    must NOT run at all.
# ---------------------------------------------------------------------------


def test_gate1_failure_short_circuits_main(tmp_path: Path, monkeypatch):
    """Gate 1 returns rc=1 (working tree dirty). The full main()
    pipeline must surface that rc and never invoke run_preflight_parallel.
    """
    plugin_root = tmp_path

    # Patch the things main() calls before the preflight block.
    monkeypatch.setattr(publish, "stage_bypass_guard", lambda: 0)
    monkeypatch.setattr(publish, "get_plugin_root", lambda: plugin_root)
    monkeypatch.setattr(
        publish, "detect_bump_type", lambda _root: "patch"
    )
    monkeypatch.setattr(publish, "stage_check_working_tree", lambda _root: 1)

    # If main() incorrectly reaches the parallel block, this would be
    # called — assert it isn't.
    parallel_called = []
    monkeypatch.setattr(
        publish,
        "run_preflight_parallel",
        lambda _root, _layout: parallel_called.append(True) or 0,
    )

    # Drive main() with a synthetic argv that bypasses argparse failures.
    monkeypatch.setattr(sys, "argv", ["publish.py", "--patch"])
    rc = publish.main()

    assert rc == 1, f"Expected rc=1 from Gate 1 failure, got {rc}"
    assert not parallel_called, (
        "run_preflight_parallel was invoked despite Gate 1 failing — "
        "Gate 1 short-circuit broken."
    )


# ---------------------------------------------------------------------------
# 5. Bonus: Gate 6 still runs AFTER the parallel block on success
# ---------------------------------------------------------------------------


def test_gate6_runs_after_parallel_block(tmp_path: Path, monkeypatch):
    """If Gates 1-5 pass, Gate 6 (version consistency) MUST run.

    Verifies that the parallel block did not accidentally consume the
    Gate 6 dispatch or break the post-parallel sequential flow.
    """
    plugin_root = tmp_path

    monkeypatch.setattr(publish, "stage_bypass_guard", lambda: 0)
    monkeypatch.setattr(publish, "get_plugin_root", lambda: plugin_root)
    monkeypatch.setattr(publish, "detect_bump_type", lambda _root: "patch")
    monkeypatch.setattr(publish, "stage_check_working_tree", lambda _root: 0)
    monkeypatch.setattr(publish, "detect_layout", lambda _root: ("A", {}))
    monkeypatch.setattr(
        publish,
        "run_preflight_parallel",
        lambda _root, _layout: 0,
    )

    gate6_calls = []

    def fake_gate6(root):
        gate6_calls.append(root)
        # Force a non-zero rc so main() exits before the bump/changelog
        # gates (which would require a real git repo and tags).
        return 99

    monkeypatch.setattr(publish, "stage_version_consistency", fake_gate6)
    monkeypatch.setattr(sys, "argv", ["publish.py", "--patch"])

    rc = publish.main()

    assert gate6_calls, "Gate 6 (version consistency) did not run after parallel block"
    assert gate6_calls[0] == plugin_root
    assert rc == 99, f"Expected rc=99 propagated from Gate 6, got {rc}"


# ---------------------------------------------------------------------------
# 6. Stream router thread-safety: per-thread buffers are isolated
# ---------------------------------------------------------------------------


def test_thread_aware_stream_isolates_per_thread_writes():
    """Two concurrent threads each install their own buffer on the SAME
    _ThreadAwareStream instance. Writes from thread A must NOT bleed
    into thread B's buffer (the exact bug `contextlib.redirect_stdout`
    causes — see the comment in publish.py around _ThreadAwareStream).
    """
    real_target = io.StringIO()
    router = publish._ThreadAwareStream(real_target)

    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def worker(label: str, message: str) -> None:
        buf = io.StringIO()
        router._set_buffer(buf)
        try:
            barrier.wait(timeout=5)  # both threads start writes simultaneously
            for _ in range(50):
                router.write(message)
                time.sleep(0.001)
        finally:
            results[label] = buf.getvalue()
            router._set_buffer(None)

    t1 = threading.Thread(target=worker, args=("a", "AAA"))
    t2 = threading.Thread(target=worker, args=("b", "BBB"))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Every chunk in buffer A must be "AAA" — no "BBB" must leak in.
    assert "BBB" not in results["a"], (
        f"Thread A buffer contaminated by thread B writes. Got: {results['a'][:200]!r}"
    )
    assert "AAA" not in results["b"], (
        f"Thread B buffer contaminated by thread A writes. Got: {results['b'][:200]!r}"
    )
    # And nothing leaked to the real target either, since both threads
    # had buffers installed for the entire write window.
    assert real_target.getvalue() == "", (
        f"Real stream received writes despite both threads having "
        f"buffers installed. Got: {real_target.getvalue()[:200]!r}"
    )


def test_thread_aware_stream_falls_through_when_no_buffer():
    """When no buffer is set on a thread, writes pass through to the
    real underlying stream. This is the orchestrator path — the main
    thread never installs a buffer, so its prints land on the terminal.
    """
    real_target = io.StringIO()
    router = publish._ThreadAwareStream(real_target)

    router.write("orchestrator print\n")

    assert real_target.getvalue() == "orchestrator print\n"


# ---------------------------------------------------------------------------
# 7. SystemExit conversion — stage_run_tests check=True semantics
# ---------------------------------------------------------------------------


def test_run_stage_captured_converts_systemexit_to_rc():
    """`_print_result` calls sys.exit(returncode) on subprocess failure
    when check=True. In a worker thread, SystemExit must be caught and
    converted to a plain return code so the orchestrator can act on it
    instead of having an uncaught exception bubble through future.result().
    """

    def stage_that_exits():
        print("about to exit")
        raise SystemExit(7)

    rc, out, _err = publish._run_stage_captured(stage_that_exits)
    assert rc == 7, f"Expected SystemExit(7) → rc=7, got {rc}"
    assert "about to exit" in out


def test_run_stage_captured_passes_through_normal_return():
    """Normal stage returning rc=0 round-trips correctly."""

    def stage_normal():
        print("hello")
        print("world", file=sys.stderr)
        return 0

    rc, out, err = publish._run_stage_captured(stage_normal)
    assert rc == 0
    assert "hello" in out
    assert "world" in err
