"""#211 — a `[cpv-phase] DONE` duration must be the PHASE's, not the BATCH's.

THE DEFECT, as reported and as reproduced here on CPV's own tree: 27 phases
dispatched concurrently all reported ~601.2s, and three unrelated phases all
reported exactly 70.4s. `validate_readme` taking ten minutes is not credible;
the batch taking ten minutes is. The instrumentation therefore could not answer
the one question it exists to answer — WHICH phase was slow — which is also why
#148/#162 ("[REPO LINT] phase hangs ~30 min on CI, root cause unknown") were
both closed without a root cause.

TWO causes, both fixed and both pinned below:

1. `started_at[idx]` was stamped at SUBMIT time and the elapsed was computed
   when the COLLECT loop reached that future. The loop iterated in SUBMIT
   order, so `future.result()` on task 0 blocked until the slowest task in the
   batch finished; every later future was already complete by then and got the
   batch's wall time. The worker now measures its own t0/t1 and RETURNS the
   number (`_run_one_validator_timed`).
2. The collect loop now uses `as_completed`, so a DONE is emitted when its
   phase finishes rather than in one burst at the end of the batch.

THE HARD CONSTRAINT this fix had to respect (v3.23.0 shipped the naive version
and deadlocked every Linux CI run): NO worker thread may write to stderr.
Forking a multithreaded process copies mutex state, so a worker holding
sys.stderr's lock at fork time hangs the child. Hence the split — measure on
the worker, EMIT on the dispatcher — pinned by a source-level assertion, the
same shape `test_issue_180_phase_progress.py` uses and for the same reason (the
deadlock needs a fork race, so a behavioural test would be flaky while the
invariant is exact).

Every timing test here is two-sided: the fast phases must report their OWN
small durations AND must not sit within a whisker of the slow one, because a
change that simply reported 0.0 for everything would satisfy a one-sided
assertion while destroying the measurement.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_plugin as vp  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402

# One slow phase, deliberately far longer than a no-op, so "the batch's wall
# time" and "this phase's own time" cannot be confused by scheduler noise.
_SLOW_S = 0.6
# Generous: the duration is measured INSIDE the worker, after it starts, so a
# scheduling delay is not counted — only GIL contention is, and a no-op cannot
# spend a quarter of a second on that.
_FAST_CEILING_S = 0.25

_MARKER_RE = re.compile(r"^(?P<name>\S+) \[w(?P<slot>\d{2,})\]$")


def _sleeper(seconds: float):
    """A validator-shaped callable that costs a known amount of wall time."""

    def _fn(plugin_root: Path, report: ValidationReport) -> None:
        if seconds:
            time.sleep(seconds)
        report.passed("ok")

    return _fn


def _tasks(spec: list[tuple[str, float]]):
    return [(name, _sleeper(sec), ((), {})) for name, sec in spec]


class _Markers:
    """Capture what the dispatcher emitted, in the order it emitted it."""

    def __init__(self) -> None:
        self.starts: list[str] = []
        self.dones: list[tuple[str, float]] = []

    def start(self, name: str) -> None:
        self.starts.append(name)

    def done(self, name: str, elapsed: float) -> None:
        self.dones.append((name, elapsed))

    @staticmethod
    def phase_of(marker: str) -> str:
        """The phase name a marker carries, slot id stripped."""
        match = _MARKER_RE.match(marker)
        return match.group("name") if match else marker

    def by_phase(self) -> dict[str, float]:
        return {self.phase_of(marker): elapsed for marker, elapsed in self.dones}

    def done_order(self) -> list[str]:
        return [self.phase_of(marker) for marker, _elapsed in self.dones]


@pytest.fixture
def markers(monkeypatch: pytest.MonkeyPatch) -> _Markers:
    captured = _Markers()
    monkeypatch.setattr(vp, "emit_phase_start", captured.start)
    monkeypatch.setattr(vp, "emit_phase_done", captured.done)
    return captured


# ---------------------------------------------------------------------------
# The defect itself
# ---------------------------------------------------------------------------


def test_a_fast_phase_does_not_inherit_the_batch_wall_time(
    tmp_path: Path, markers: _Markers
) -> None:
    """THE REPORTED SIGNATURE, pinned.

    One slow phase submitted FIRST plus six no-ops. Before the fix every no-op
    reported the slow phase's duration, because the collect loop stamped t1 when
    it reached that future and it could not reach any of them until task 0 was
    done. Each no-op must now report its own cost.
    """
    spec = [("slow", _SLOW_S)] + [(f"fast_{i}", 0.0) for i in range(6)]
    vp._run_parallel_batch(_tasks(spec), tmp_path, ValidationReport())

    elapsed = markers.by_phase()
    assert elapsed["slow"] >= _SLOW_S * 0.8, elapsed
    for i in range(6):
        assert elapsed[f"fast_{i}"] < _FAST_CEILING_S, elapsed
        # The two-sided half: not merely "small", but demonstrably NOT the
        # slow phase's number. A regression that reported the batch time again
        # would fail here even if the ceiling above were loosened.
        assert abs(elapsed[f"fast_{i}"] - elapsed["slow"]) > _SLOW_S / 2, elapsed


def test_the_identical_cluster_signature_is_gone(tmp_path: Path, markers: _Markers) -> None:
    """The reporter tallied durations and saw 20 phases at 601.2s and 7 at
    601.1s — clusters of identical values are the fingerprint of a shared
    measurement. Phases with genuinely different costs must land on genuinely
    different numbers."""
    spec = [("slow", _SLOW_S), ("middle", _SLOW_S / 2), ("quick", 0.0)]
    vp._run_parallel_batch(_tasks(spec), tmp_path, ValidationReport())

    elapsed = markers.by_phase()
    rounded = {name: round(value, 1) for name, value in elapsed.items()}
    assert len(set(rounded.values())) == len(rounded), rounded
    assert elapsed["quick"] < elapsed["middle"] < elapsed["slow"], elapsed


def test_a_done_is_emitted_when_its_phase_finishes_not_at_the_end_of_the_batch(
    tmp_path: Path, markers: _Markers
) -> None:
    """The `as_completed` half. With submit-order collection the slow phase's
    DONE came first because it was submitted first, and the whole batch's DONEs
    landed in one burst — so even a correct duration would have arrived too late
    to watch a run progress."""
    spec = [("slow", _SLOW_S), ("fast", 0.0)]
    vp._run_parallel_batch(_tasks(spec), tmp_path, ValidationReport())

    order = markers.done_order()
    assert order.index("fast") < order.index("slow"), order


# ---------------------------------------------------------------------------
# The worker id (#211's cheap second ask)
# ---------------------------------------------------------------------------


def test_start_and_done_carry_a_worker_slot_id(tmp_path: Path, markers: _Markers) -> None:
    """"log a phase's START/DONE with its worker id, so a hang localises to a
    worker even if durations stay grouped"."""
    spec = [(f"phase_{i}", 0.0) for i in range(4)]
    vp._run_parallel_batch(_tasks(spec), tmp_path, ValidationReport())

    assert len(markers.starts) == 4
    slots = []
    for marker in markers.starts:
        match = _MARKER_RE.match(marker)
        assert match, f"START marker carries no worker slot: {marker!r}"
        slots.append(match.group("slot"))
    assert len(set(slots)) == 4, "slot ids must be unique within a batch"

    # A START and its DONE must carry the SAME marker string, or a killed run
    # cannot be reconciled into "these slots never finished".
    assert sorted(markers.starts) == sorted(m for m, _e in markers.dones)


def test_the_phase_name_is_still_the_first_field_of_both_markers(
    tmp_path: Path, markers: _Markers
) -> None:
    """LOAD-BEARING COMPATIBILITY: every existing consumer parses these with
    `START (\\S+)` / `DONE  (\\S+)` (see test_issue_180_phase_progress.py, and
    the reporter's own tally). Appending the slot must not move the name."""
    vp._run_parallel_batch(_tasks([("validate_readme", 0.0)]), tmp_path, ValidationReport())

    assert markers.starts[0].split()[0] == "validate_readme"
    assert markers.dones[0][0].split()[0] == "validate_readme"


# ---------------------------------------------------------------------------
# The diagnostic contract must survive the change
# ---------------------------------------------------------------------------


def test_a_crashed_validator_still_reports_done(tmp_path: Path, markers: _Markers) -> None:
    """A validator that crashed still FINISHED, and is not what a killed job was
    stuck on. Leaving its START unmatched would point triage at the wrong
    phase — and the crash must still surface as a blocking MAJOR."""

    def _boom(plugin_root: Path, report: ValidationReport) -> None:
        raise ValueError("boom")

    umbrella = ValidationReport()
    vp._run_parallel_batch(
        [("boom", _boom, ((), {})), ("ok", _sleeper(0.0), ((), {}))], tmp_path, umbrella
    )

    finished = {name for name, _e in markers.by_phase().items()}
    assert finished == {"boom", "ok"}
    assert any("crashed" in r.message and r.level == "MAJOR" for r in umbrella.results), [
        (r.level, r.message) for r in umbrella.results
    ]


def test_every_start_gets_exactly_one_done(tmp_path: Path, markers: _Markers) -> None:
    """A completed run must leave no phase in flight — the property that makes
    "STARTs without DONEs" mean "these were stuck"."""
    spec = [(f"p{i}", 0.0) for i in range(8)]
    vp._run_parallel_batch(_tasks(spec), tmp_path, ValidationReport())
    assert sorted(markers.starts) == sorted(m for m, _e in markers.dones)


def test_results_are_still_merged_in_input_order(tmp_path: Path) -> None:
    """CONTROL for the `as_completed` switch: collection order changed, MERGE
    order must not. The umbrella's result sequence has to stay identical to the
    serial baseline or the parity gate breaks."""

    def _named(tag: str, seconds: float = 0.0):
        def _fn(plugin_root: Path, report: ValidationReport) -> None:
            if seconds:
                time.sleep(seconds)
            report.passed(tag)

        return _fn

    # Deliberately finish out of submit order: task 0 is the slowest, so
    # completion order is c/b/a while input order is a/b/c.
    tasks: list[tuple[str, Any, tuple[tuple, dict]]] = [
        ("a", _named("a", 0.3), ((), {})),
        ("b", _named("b"), ((), {})),
        ("c", _named("c"), ((), {})),
    ]
    umbrella = ValidationReport()
    vp._run_parallel_batch(tasks, tmp_path, umbrella)
    assert [r.message for r in umbrella.results] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# The unit that does the measuring
# ---------------------------------------------------------------------------


def test_timed_wrapper_returns_the_workers_own_duration(tmp_path: Path) -> None:
    result, elapsed = vp._run_one_validator_timed("x", _sleeper(0.3), tmp_path)
    name, sub_report, exc = result
    assert name == "x"
    assert exc is None
    assert isinstance(sub_report, ValidationReport)
    assert 0.25 <= elapsed < 5.0, elapsed


def test_timed_wrapper_still_reports_a_duration_for_a_crash(tmp_path: Path) -> None:
    """A crash is a phase that finished; it must carry a real number, not None."""

    def _boom(plugin_root: Path, report: ValidationReport) -> None:
        raise ValueError("boom")

    (name, _report, exc), elapsed = vp._run_one_validator_timed("boom", _boom, tmp_path)
    assert name == "boom"
    assert isinstance(exc, ValueError)
    assert elapsed >= 0.0


# ---------------------------------------------------------------------------
# FORK SAFETY — the constraint that shaped the whole fix
# ---------------------------------------------------------------------------


def test_the_timing_wrapper_never_touches_stderr() -> None:
    """v3.23.0's regression, one layer down.

    `_run_one_validator_timed` runs ON A WORKER THREAD. Several validators fork
    process pools, and on Linux multiprocessing defaults to FORK — a worker
    holding sys.stderr's lock at fork time leaves the child with a lock held by
    a thread that does not exist there, and the child hangs on its first write.
    Measuring here while EMITTING on the dispatcher is the only shape that is
    both accurate (#211) and fork-safe.

    Source-level for the same reason the sibling assertion in
    test_issue_180_phase_progress.py is: the deadlock needs a fork race, so a
    behavioural test would flake, while this invariant is exact.
    """
    src = (REPO_ROOT / "scripts" / "validate_plugin.py").read_text(encoding="utf-8")
    body = src.split("def _run_one_validator_timed(", 1)[1].split("\ndef ", 1)[0]
    assert "emit_phase_start" not in body, "progress marker emitted from a worker thread"
    assert "emit_phase_done" not in body, "progress marker emitted from a worker thread"

    # Positive control: the dispatcher DOES emit, so this is not passing merely
    # because the markers were deleted.
    batch = src.split("def _run_parallel_batch(", 1)[1].split("\ndef ", 1)[0]
    assert "emit_phase_start" in batch and "emit_phase_done" in batch
