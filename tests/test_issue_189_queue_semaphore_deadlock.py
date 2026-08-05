#!/usr/bin/env python3
"""Issue #189 — the real mechanism: mp.Queue's slot semaphore, not a race.

WHAT ACTUALLY HAPPENED. `supervised_scan` filled `task_q` with every work item
BEFORE spawning a single worker. A default `multiprocessing.Queue` is not
unbounded — its slot semaphore is capped at `SemLock.SEM_VALUE_MAX`, 32767 on
macOS and Linux — so `put()` blocks once that many items are outstanding. With
no consumer yet in existence, nothing could ever drain it, and the parent wedged
in `put()` permanently with its feeder thread stuck in `connection._send` on a
full pipe.

Captured on the reporter's own corpus at their reported commit:

    Phase in flight : security_execclass_gate      (stuck 1710s of a 1800s budget)
    main thread   -> queues.py:89 in put
                     cpv_scan_supervisor.py:498 in supervised_scan
    feeder thread -> connection.py:384 in _send    (blocked writing a full pipe)
    336s CPU across 1800s wall — asleep 81% of the time.

Their tree held 86222 files against 1479 tracked (a Rust build dir, a .venv,
caches). 86222 > 32767, so this fired every single run — which is why they got a
clean bisect, and why three earlier probes "disproved" it: every one of them used
a corpus under 32767 files, where the bug cannot occur.

WHY THE TEST IS SHAPED LIKE THIS. The threshold IS the bug. A test with a
handful of items passes against the original code, so it would prove nothing —
the same vacuity that made the first #189 fix's tests worthless. These tests
therefore push a real >SEM_VALUE_MAX item count through a real queue.
"""

from __future__ import annotations

import multiprocessing as mp
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import _multiprocessing  # noqa: E402

import cpv_scan_supervisor as sup  # noqa: E402

SEM_MAX = _multiprocessing.SemLock.SEM_VALUE_MAX


def test_the_ceiling_that_causes_this_is_real():
    """Pin the premise. If this ever stops being true, the rest is theatre."""
    assert SEM_MAX == mp.get_context("spawn").Queue()._maxsize
    assert SEM_MAX < 86222, "the reporter's tree exceeded the cap; that IS the bug"


def test_unbounded_put_past_the_cap_blocks_forever():
    """CONTROL — reproduce the original defect directly.

    This is what `supervised_scan` used to do: fill the queue with every item
    before any consumer exists. It must hang, or the fix is a fix for nothing.

    Run in a SUBPROCESS, deliberately. A thread blocked in `put()` cannot be
    killed, and `mp.Queue` joins its feeder thread at interpreter exit — so
    doing this in-process wedges the whole pytest session at teardown, with the
    suite appearing to pass right up until it never returns. (Observed: a 20+
    minute hang on the first draft of this file. The leak was mine, and a test
    that leaks a blocked thread is a worse bug than the one it documents.)
    A subprocess bounds it absolutely: kill the process, kill the problem.
    """
    src = textwrap.dedent(f"""
        import multiprocessing as mp
        q = mp.get_context("spawn").Queue()
        for i in range({SEM_MAX} + 50):
            q.put((i, "f%d" % i))
        print("COMPLETED")
    """)
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(  # noqa: S603
            [sys.executable, "-c", src], capture_output=True, text=True, timeout=45, check=False
        )


def test_supervisor_feeds_on_a_thread_not_the_main_loop():
    """The fix's structure: the feed must not be able to block the loop.

    Asserted structurally as well as behaviourally because the behavioural test
    above needs a >32767 queue to fail — expensive enough that a future edit
    could plausibly delete it and leave the regression uncovered.
    """
    src = (SCRIPTS / "cpv_scan_supervisor.py").read_text(encoding="utf-8")
    assert "cpv-task-feeder" in src, "the feeder thread is gone"
    assert "feeder_stop" in src
    # Every put in the supervisor must be bounded; an unbounded one anywhere
    # re-opens the deadlock through a different door.
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("task_q.put(") and "None" not in stripped:
            assert "timeout=" in stripped, f"unbounded put reintroduced: {stripped}"


def test_workers_are_spawned_before_the_feed_starts():
    """Ordering is half the fix: a producer with no consumer cannot drain.

    The feeder thread must be started AFTER the spawn loop. If the feed came
    first the deadlock returns even with the thread, for any tree over the cap.
    """
    src = (SCRIPTS / "cpv_scan_supervisor.py").read_text(encoding="utf-8")
    spawn_loop = src.index("for _ in range(n_workers):")
    feeder_start = src.index("feeder.start()")
    assert spawn_loop < feeder_start, "the feed starts before workers exist"


def test_feeder_is_stopped_before_the_sentinels():
    """A sentinel must never overtake a real work item.

    Retiring a worker while files are still queued converts the deadlock into
    silently unscanned files — worse, because it reads as a completed scan
    instead of announcing itself.
    """
    src = (SCRIPTS / "cpv_scan_supervisor.py").read_text(encoding="utf-8")
    assert src.index("feeder_stop.set()") < src.index("task_q.put(None)")


def test_scan_over_the_cap_terminates_and_scans_every_file(tmp_path):
    """End-to-end: more work items than the semaphore allows must still finish.

    Unmarked and un-skipped deliberately. It costs ~4s, and it is the only test
    that exercises the actual defect end to end — a deadlock that cost a user
    two multi-hour hangs does not get to be opt-in.

    The whole point. Every file must come back — terminating by dropping the
    overflow would pass a termination test while silently reducing coverage.
    """
    files = [str(tmp_path / f"f{i}.txt") for i in range(SEM_MAX + 25)]
    for p in files[:5]:
        Path(p).write_text("x", encoding="utf-8")

    results = sup.supervised_scan(
        files,
        _noop_scan,
        n_workers=2,
        hard_kill_after_s=600.0,
    )
    assert len(results) == len(files)


def _noop_scan(path: str):
    """Module-level so it is pickleable for a spawn-context worker."""
    from cpv_parallel_runner import ScanResult  # noqa: PLC0415

    return ScanResult(file=path, findings=[], error=None)
