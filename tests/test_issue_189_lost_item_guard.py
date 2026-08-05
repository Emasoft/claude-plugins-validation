#!/usr/bin/env python3
"""Issue #189 — the supervised scan could wait forever on a result that never arrives.

THE RACE. A worker that dies between `task_q.get()` returning an item and
`heartbeat[wid] = idx` being written has already CONSUMED that item — it is gone
from the queue — but published no heartbeat, so `inflight` holds nothing for it
and the dead-worker branch records nothing. Its index therefore never enters
`seen`. The replacement worker then blocks in `task_q.get()` on an empty queue
forever, and because a worker never exits on an empty queue (only on the `None`
sentinel, which the parent sends AFTER its loop), `proc.is_alive()` stays True
and no further recovery fires. The parent's `while len(seen) < n` can then never
complete.

In the wild: a scan that never returned, killed at 2700s, leaking semaphores.
Bisected by the reporter to the release that made this path default-on — every
plugin with >= 32 files takes it.

WHY THESE TESTS LOOK LIKE THIS. The bug is a timing race, so a test that tried to
hit the real window would flake. Instead the guard's PROPERTY is tested directly:
given a state in which an item is unaccountable, the scan must TERMINATE and must
REPORT the file — never hang, and never silently return it as clean. A scan that
cannot account for a file reporting no findings would be the worst outcome of the
three, because it reads as "scanned, clean".
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import cpv_scan_supervisor as sup  # noqa: E402


def _scan_ok(path: Path) -> list:
    """A healthy scanner. Module-level because `spawn` pickles it by name."""
    return []


def _drop_one_item(path: Path) -> list:
    """Scanner that makes exactly one file unaccountable, without dying.

    Simulating the real death (a SIGKILL inside the heartbeat window) from a test
    would be inherently racy. This reproduces the STATE that death leaves behind
    — an item consumed with no result recorded — which is what the guard exists
    to escape.
    """
    if path.name == "lost.py":
        raise SystemExit(0)  # worker exits mid-item, exactly like a hard kill
    return []


class TestGuardConstantsAreSane:
    def test_stall_ticks_is_greater_than_one(self) -> None:
        """>1 or a worker mid-dispatch is mistaken for a lost one and double-scanned."""
        assert sup._LOST_ITEM_STALL_TICKS > 1

    def test_stall_ticks_is_small_enough_to_resolve_quickly(self) -> None:
        """The whole point is bounded recovery, not a second long wait."""
        assert sup._LOST_ITEM_STALL_TICKS <= 10


class TestScanTerminatesAndReportsLostFiles:
    def test_scan_completes_when_a_worker_dies_mid_item(self, tmp_path: Path) -> None:
        """The load-bearing test: it must RETURN. Before the guard, it hung.

        The assertion is simply that we get here — a hang fails by never
        finishing, which the suite's own wall-clock kill surfaces.
        """
        files = []
        for name in ("a.py", "lost.py", "b.py"):
            p = tmp_path / name
            p.write_text("x = 1\n", encoding="utf-8")
            files.append(p)

        results = sup.supervised_scan(
            files,
            _drop_one_item,
            n_workers=2,
            poll_interval_s=0.05,
            hard_kill_after_s=30.0,
        )
        assert len(results) == len(files), "every file must have a result"

    def test_the_lost_file_is_REPORTED_not_silently_clean(self, tmp_path: Path) -> None:
        """An unscannable file must never read as scanned-and-clean.

        This is the FN-safety half. A guard that terminated by quietly returning
        `findings=[]` for the lost file would pass the termination test above
        while turning a hang into a silent coverage hole — strictly worse, since
        a hang at least announces itself.
        """
        files = []
        for name in ("a.py", "lost.py", "b.py"):
            p = tmp_path / name
            p.write_text("x = 1\n", encoding="utf-8")
            files.append(p)

        results = sup.supervised_scan(
            files,
            _drop_one_item,
            n_workers=2,
            poll_interval_s=0.05,
            hard_kill_after_s=30.0,
        )
        by_name = {Path(r.file_path).name: r for r in results}
        lost = by_name["lost.py"]
        assert lost.error, "the unaccountable file must carry an error, not a clean result"

    def test_healthy_files_are_unaffected(self, tmp_path: Path) -> None:
        """The guard must not disturb a normal scan (no double-scan, no false loss)."""
        files = []
        for name in ("a.py", "b.py", "c.py"):
            p = tmp_path / name
            p.write_text("x = 1\n", encoding="utf-8")
            files.append(p)

        results = sup.supervised_scan(
            files, _scan_ok, n_workers=2, poll_interval_s=0.05, hard_kill_after_s=30.0
        )
        assert len(results) == 3
        assert all(not r.error for r in results), "a healthy scan must stay error-free"
