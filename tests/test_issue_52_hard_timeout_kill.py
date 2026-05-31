#!/usr/bin/env python3
"""Issue #52 / #56 — the killable scan supervisor.

Verifies the load-bearing guarantee: a worker wedged in an unkillable-by-signal
busy loop is HARD-killed (SIGKILL), its file recorded ``TIMED_OUT``, the call
returns within the wall-clock budget, and ZERO worker processes leak (a
process-table snapshot before/after is identical — per
``~/.claude/rules/browser-ui-test-techniques.md`` §14-16).

Worker callables are module-level so they pickle under the ``spawn`` start
method (macOS default). ``_wedge_scan`` busy-loops forever on any path whose
name contains ``WEDGE`` — a stand-in for the catastrophic C-level regex the
issue describes; the OS-level SIGKILL preempts it regardless of whether it ever
reaches a Python bytecode boundary.
"""

from __future__ import annotations

import multiprocessing as mp
import sys
import time
from pathlib import Path

import pytest

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from cpv_scan_supervisor import (  # noqa: E402
    EVENT_FINISH,
    EVENT_KILLED,
    EVENT_RESUMED,
    EVENT_START,
    EVENT_STUCK,
    inspect_state,
    supervised_scan,
)

# ── module-level worker callables (picklable for spawn) ──────────────────────


def _fast_scan(path: object) -> list:
    """Return immediately with no findings."""
    return []


def _finding_scan(path: object) -> list:
    """Return one finding so the verdict is FINDINGS."""
    return [{"severity": "minor", "path": str(path)}]


def _wedge_scan(path: object) -> list:
    """Busy-loop forever on a WEDGE path; fast otherwise. The supervisor must
    SIGKILL the wedged worker."""
    if "WEDGE" in str(path):
        while True:  # unkillable by SIGALRM if it were C-level; killed by SIGKILL
            pass
    return []


def _slow_scan(path: object) -> list:
    """Sleep ~2s on a SLOW path (long enough to trip a low stuck-warn threshold
    but finish before a high kill threshold)."""
    if "SLOW" in str(path):
        time.sleep(2.0)
    return []


def _child_pids() -> set[int]:
    """Living multiprocessing children of THIS process (reaps as a side effect)."""
    return {p.pid for p in mp.active_children() if p.pid is not None}


# ── tests ────────────────────────────────────────────────────────────────────


class TestHardKill:
    def test_hard_kills_wedged_worker_within_budget_and_no_leak(self) -> None:
        """A wedged worker is SIGKILLed; the call returns within the budget;
        the wedged file is TIMED_OUT; healthy files succeed; zero leaked PIDs."""
        baseline = _child_pids()
        files = ["a.py", "b.py", "WEDGE.py", "c.py", "d.py"]
        t0 = time.monotonic()
        results = supervised_scan(
            files, _wedge_scan, n_workers=2, hard_kill_after_s=1.0, poll_interval_s=0.1
        )
        elapsed = time.monotonic() - t0

        # Returned at all (no shutdown(wait=True) hang) and within a sane bound.
        assert elapsed < 30.0, f"supervised_scan did not return promptly: {elapsed:.1f}s"
        # One result per file, in input order.
        assert len(results) == len(files)
        assert [str(r.file_path) for r in results] == files
        # The wedged file is TIMED_OUT; every other file is clean (error None).
        wedge = results[2]
        assert wedge.error is not None and "TimeoutError" in wedge.error
        for i in (0, 1, 3, 4):
            assert results[i].error is None, f"healthy file {files[i]} should not error: {results[i].error}"
        # No leaked worker / manager processes.
        time.sleep(0.5)
        leaked = _child_pids() - baseline
        assert not leaked, f"leaked child processes: {leaked}"

    def test_clean_run_all_fast_no_kill_no_leak(self) -> None:
        """All-fast files complete with no kills and no leaked processes."""
        baseline = _child_pids()
        files = [f"f{i}.py" for i in range(12)]
        results = supervised_scan(files, _fast_scan, n_workers=4, hard_kill_after_s=5.0, poll_interval_s=0.1)
        assert len(results) == 12
        assert all(r.error is None for r in results)
        time.sleep(0.5)
        assert not (_child_pids() - baseline)

    def test_findings_preserved(self) -> None:
        """Findings produced by a worker survive the manager-dict round-trip."""
        results = supervised_scan(["x.py", "y.py"], _finding_scan, n_workers=2, hard_kill_after_s=5.0, poll_interval_s=0.1)
        assert all(len(r.findings) == 1 for r in results)
        assert all(r.error is None for r in results)

    def test_no_kill_budget_still_completes_fast_corpus(self) -> None:
        """With hard_kill_after_s=None and no wedge, the scan still completes."""
        results = supervised_scan(["a.py", "b.py", "c.py"], _fast_scan, n_workers=2, hard_kill_after_s=None, poll_interval_s=0.1)
        assert len(results) == 3
        assert all(r.error is None for r in results)


class TestProgressAndStuck:
    def test_emits_start_and_finish_events_for_every_file(self) -> None:
        events: list[dict] = []
        files = ["a.py", "b.py", "c.py"]
        supervised_scan(files, _fast_scan, n_workers=2, hard_kill_after_s=5.0, poll_interval_s=0.1, on_event=events.append)
        finished = {e["index"] for e in events if e["type"] == EVENT_FINISH}
        assert finished == {0, 1, 2}
        # Every finish has a verdict + duration.
        for e in events:
            if e["type"] == EVENT_FINISH:
                assert e["verdict"] in {"CLEAN", "FINDINGS", "ERROR", "TIMED_OUT", "WORKER_DIED"}
                assert isinstance(e["duration_s"], (int, float))

    def test_stuck_warning_fires_without_killing(self) -> None:
        """A slow-but-not-killed file emits exactly one STUCK warn and still
        finishes CLEAN (warn threshold < scan time < kill threshold)."""
        events: list[dict] = []
        notes: list[str] = []
        results = supervised_scan(
            ["SLOW.py"],
            _slow_scan,
            n_workers=1,
            stuck_warn_after_s=0.4,
            hard_kill_after_s=10.0,
            poll_interval_s=0.1,
            on_event=events.append,
            notify=notes.append,
        )
        stuck = [e for e in events if e["type"] == EVENT_STUCK]
        assert len(stuck) == 1, f"expected exactly one stuck warn, got {len(stuck)}"
        assert stuck[0]["index"] == 0
        assert results[0].error is None  # finished, not killed
        assert len(notes) == 1  # notifier fired once

    def test_killed_event_on_timeout(self) -> None:
        events: list[dict] = []
        supervised_scan(
            ["WEDGE.py", "ok.py"], _wedge_scan, n_workers=2, hard_kill_after_s=0.6, poll_interval_s=0.1, on_event=events.append
        )
        killed = [e for e in events if e["type"] == EVENT_KILLED and e.get("reason") == "timeout"]
        assert len(killed) == 1
        assert killed[0]["index"] == 0


class TestResumeInspect:
    def test_resume_skips_completed_files(self, tmp_path: Path) -> None:
        state = tmp_path / "scan-state.json"
        files = ["a.py", "b.py", "c.py"]
        first = supervised_scan(files, _fast_scan, n_workers=2, hard_kill_after_s=5.0, poll_interval_s=0.1, state_path=state)
        assert all(r.error is None for r in first)
        assert state.exists()

        events: list[dict] = []
        second = supervised_scan(
            files, _fast_scan, n_workers=2, hard_kill_after_s=5.0, poll_interval_s=0.1, state_path=state, resume=True, on_event=events.append
        )
        # All three resumed — no fresh START events.
        resumed = {e["index"] for e in events if e["type"] == EVENT_RESUMED}
        assert resumed == {0, 1, 2}
        assert not [e for e in events if e["type"] == EVENT_START]
        assert len(second) == 3

    def test_inspect_state_is_readonly_snapshot(self, tmp_path: Path) -> None:
        state = tmp_path / "scan-state.json"
        supervised_scan(["a.py", "b.py"], _fast_scan, n_workers=2, hard_kill_after_s=5.0, poll_interval_s=0.1, state_path=state)
        snap = inspect_state(state)
        assert snap["exists"] is True
        assert snap["total"] == 2
        assert snap["completed"] == 2

    def test_inspect_missing_state(self, tmp_path: Path) -> None:
        snap = inspect_state(tmp_path / "nope.json")
        assert snap["exists"] is False


class TestParallelScanIntegration:
    def test_parallel_scan_hard_kill_delegates_to_supervisor(self) -> None:
        """``parallel_scan(hard_kill_after_s=)`` routes to the killable
        supervisor: a wedged file is TIMED_OUT, the call returns within budget,
        healthy files succeed, and no worker leaks."""
        from cpv_parallel_runner import parallel_scan

        baseline = _child_pids()
        t0 = time.monotonic()
        res = parallel_scan(["a.py", "WEDGE.py", "b.py"], _wedge_scan, n_workers=2, hard_kill_after_s=1.0)
        dt = time.monotonic() - t0
        assert dt < 30.0
        assert res[1].error is not None and "TimeoutError" in res[1].error
        assert res[0].error is None and res[2].error is None
        time.sleep(0.5)
        assert not (_child_pids() - baseline)

    def test_parallel_scan_default_path_unaffected(self) -> None:
        """Without supervision args, ``parallel_scan`` keeps the legacy executor
        path (and still returns one result per file)."""
        from cpv_parallel_runner import parallel_scan

        res = parallel_scan(["a.py", "b.py", "c.py"], _fast_scan, n_workers=2)
        assert len(res) == 3
        assert all(r.error is None for r in res)

    def test_scan_one_target_timeout_completes_on_clean_plugin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """``scan_one_target(timeout_seconds=)`` uses the killable path and
        completes normally on a non-wedging plugin — returns stats, no raise."""
        import validate_security as vs

        # Isolate the on-disk scan cache so this real scan does not write to the
        # shared default cache that the xdist-parallel cache-contract tests read.
        monkeypatch.setenv("CPV_SCAN_CACHE_DIR", str(tmp_path / "_scan_cache"))
        plugin = tmp_path / "p"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text('{"name":"p","version":"1.0.0"}')
        (plugin / "a.py").write_text("x = 1\n")
        (plugin / "b.py").write_text("y = 2\n")

        report = vs.ValidationReport()
        stats = vs.scan_one_target(plugin, report, timeout_seconds=30)
        assert isinstance(stats, dict)
        assert "files_scanned" in stats


class TestIssue56EnvControl:
    """#56 — env-var knobs enable supervision on the normal CPV scan path
    without a code change (mirrors CPV_SCAN_PROGRESS / CPV_MAX_SCAN_BYTES)."""

    @pytest.fixture(autouse=True)
    def _isolate_cache_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> object:
        """These tests run REAL scans; isolate the on-disk cache so they never
        pollute the shared default cache the xdist cache-contract tests read."""
        monkeypatch.setenv("CPV_SCAN_CACHE_DIR", str(tmp_path / "_scan_cache"))
        yield

    def _clean_plugin(self, tmp_path: Path) -> Path:
        plugin = tmp_path / "p"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text('{"name":"p","version":"1.0.0"}')
        (plugin / "a.py").write_text("x = 1\n")
        (plugin / "b.py").write_text("y = 2\n")
        return plugin

    def test_env_skip_stuck_after_runs_supervised_and_completes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CPV_SCAN_SKIP_STUCK_AFTER forces the killable path; a clean plugin
        still completes (no file trips the budget)."""
        import validate_security as vs

        monkeypatch.setenv("CPV_SCAN_SKIP_STUCK_AFTER", "30")
        report = vs.ValidationReport()
        stats = vs.scan_all_files(self._clean_plugin(tmp_path), report)
        assert "files_scanned" in stats

    def test_env_state_persists_and_inspect_reads_it(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CPV_SCAN_STATE persists per-file verdicts that inspect_state reads."""
        import validate_security as vs

        state = tmp_path / "state.json"
        monkeypatch.setenv("CPV_SCAN_STATE", str(state))
        report = vs.ValidationReport()
        vs.scan_all_files(self._clean_plugin(tmp_path), report)
        snap = inspect_state(state)
        assert snap["exists"] is True
        assert snap["total"] >= 1

    def test_env_unset_keeps_default_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no supervision env vars, the scan uses the legacy path."""
        for var in ("CPV_SCAN_SKIP_STUCK_AFTER", "CPV_SCAN_STATE", "CPV_SCAN_RESUME", "CPV_NOTIFY_ON_STUCK"):
            monkeypatch.delenv(var, raising=False)
        import validate_security as vs

        report = vs.ValidationReport()
        stats = vs.scan_all_files(self._clean_plugin(tmp_path), report)
        assert "files_scanned" in stats


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
