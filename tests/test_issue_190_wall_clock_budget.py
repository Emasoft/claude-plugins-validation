#!/usr/bin/env python3
"""Issue #190 — a hung validate never exits and orphans itself.

THE DEFECT. `_serial_phase` wraps every serial step in progress markers and, as
its own docstring says, "this only observes" — nothing imposes a deadline on the
run as a whole. Individual phases bound their own work, but a wait nobody thought
to bound hangs the interpreter permanently. Its parent then dies, the process is
reparented to launchd/init, and it waits at 0.0% CPU forever. The reporter found
two on one host, 3h and 11h old, and nothing had surfaced them.

WHY THE CENTRAL TEST SPAWNS A REAL SUBPROCESS. The failure being fixed is a main
thread that cannot be rescued from inside the interpreter: it is parked in
`pthread_cond_wait`, so an exception raised from a watchdog thread would never be
observed. Only a real process, really blocked, really killed, demonstrates that
`os._exit` reaches it — an in-process test of the timer would pass against a
polite `sys.exit` implementation that leaves the reported hang exactly as it was.

TWO-SIDED. `test_unarmed_child_really_hangs` is the control: the same blocking
child WITHOUT the watchdog must NOT terminate. Without it, every assertion here
would still pass against a child that was never going to hang in the first place,
and the suite would prove nothing about the bug — the exact methodological error
this project made twice on issue #189.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cpv_watchdog  # noqa: E402


# A child that blocks the MAIN thread on a lock nobody will ever release — the
# shape the reporter sampled (`__psynch_cvwait`, 3317/3317 samples).
_BLOCK_FOREVER = """
import sys, threading
sys.path.insert(0, {scripts!r})
{arm}
lock = threading.Lock()
lock.acquire()
lock.acquire()          # blocks forever in pthread_cond_wait
print("UNREACHABLE")
"""


def _child(arm: str) -> str:
    return textwrap.dedent(_BLOCK_FOREVER).format(scripts=str(SCRIPTS), arm=arm)


def _run_child(arm: str, timeout: float):
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", _child(arm)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


# ── The defect itself ────────────────────────────────────────────────────────


def test_blocked_main_thread_is_killed_and_exits_non_zero():
    """A main thread stuck on a lock is terminated with the budget exit code."""
    proc = _run_child("import cpv_watchdog; cpv_watchdog.arm(budget_s=1.0)", timeout=30)
    assert proc.returncode == cpv_watchdog.EXIT_BUDGET_EXCEEDED, proc.stderr
    assert "UNREACHABLE" not in proc.stdout


def test_expiry_never_exits_zero():
    """The budget must never expire into a pass.

    A clean exit here would convert a hang into a silent VALID verdict — worse
    than the hang, because nothing would announce that the run never finished.
    """
    proc = _run_child("import cpv_watchdog; cpv_watchdog.arm(budget_s=1.0)", timeout=30)
    assert proc.returncode != 0


def test_abort_diagnostic_is_loud_and_names_the_phase():
    """The operator must be told what was stuck, not merely that something was."""
    arm = (
        "import cpv_watchdog; "
        "cpv_watchdog.note_phase_start('security_execclass_gate'); "
        "cpv_watchdog.arm(budget_s=1.0)"
    )
    proc = _run_child(arm, timeout=30)
    assert "security_execclass_gate" in proc.stderr
    assert "budget" in proc.stderr.lower()
    # Must not be mistakable for a verdict on the plugin.
    assert "do not read this as clean" in proc.stderr.lower()


def test_unarmed_child_really_hangs():
    """CONTROL — without the watchdog the same child never returns.

    This is what makes the tests above evidence. If this child terminated on its
    own, every assertion above would pass for reasons unrelated to the fix.
    """
    with pytest.raises(subprocess.TimeoutExpired):
        _run_child("pass", timeout=6)


# ── FN-safety: the guard must not fire on healthy runs ───────────────────────


def test_normal_run_is_not_killed_and_is_not_held_open():
    """A run that finishes promptly exits 0 and is not delayed by the timer."""
    src = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(SCRIPTS)!r})
        import cpv_watchdog
        cpv_watchdog.arm(budget_s=45.0)
        print("DONE")
    """)
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", src], capture_output=True, text=True, timeout=30, check=False
    )
    assert proc.returncode == 0
    assert "DONE" in proc.stdout


# ── The budget can only be made STRICTER ─────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    ["0", "-1", "0.0", "", "   ", "off", "nonsense", "5"],
    ids=["zero", "negative", "zero-float", "empty", "blank", "word", "garbage", "below-min"],
)
def test_env_cannot_disable_or_shorten_below_floor(monkeypatch, raw):
    """No environment value may switch the guard off or set a nonsense budget.

    `CPV_VALIDATE_BUDGET_S=0` is the one that matters: honouring it would disable
    the guard through the very mechanism meant to be un-disableable.
    """
    monkeypatch.setenv("CPV_VALIDATE_BUDGET_S", raw)
    assert cpv_watchdog.resolve_budget_s() == cpv_watchdog._DEFAULT_BUDGET_S


def test_env_cannot_raise_the_budget(monkeypatch):
    monkeypatch.setenv("CPV_VALIDATE_BUDGET_S", "999999")
    assert cpv_watchdog.resolve_budget_s() == cpv_watchdog._DEFAULT_BUDGET_S


def test_env_may_lower_the_budget(monkeypatch):
    monkeypatch.setenv("CPV_VALIDATE_BUDGET_S", "60")
    assert cpv_watchdog.resolve_budget_s() == 60.0


def test_default_when_unset(monkeypatch):
    monkeypatch.delenv("CPV_VALIDATE_BUDGET_S", raising=False)
    assert cpv_watchdog.resolve_budget_s() == cpv_watchdog._DEFAULT_BUDGET_S


# ── Phase register ───────────────────────────────────────────────────────────


def test_nested_phase_done_does_not_clear_the_outer_phase():
    """An inner phase finishing must not erase the outer phase still running.

    Otherwise an expiry inside a long outer phase would report "no phase in
    flight" for a run that is very much inside one — losing exactly the
    attribution the diagnostic exists to give.
    """
    cpv_watchdog.note_phase_start("outer")
    cpv_watchdog.note_phase_start("inner")
    cpv_watchdog.note_phase_done("inner")
    cpv_watchdog.note_phase_start("outer")
    cpv_watchdog.note_phase_done("outer")
    assert cpv_watchdog._current_phase is None


def test_phase_start_is_recorded_even_with_progress_disabled(monkeypatch):
    """PLUGIN_PROGRESS=0 must not blind the abort diagnostic.

    That run is precisely the one whose hang is otherwise unattributable.
    """
    monkeypatch.setenv("PLUGIN_PROGRESS", "0")
    from cpv_validation_common import emit_phase_start

    emit_phase_start("phase_under_test")
    assert cpv_watchdog._current_phase == "phase_under_test"
    cpv_watchdog.note_phase_done("phase_under_test")


def test_arm_is_idempotent():
    """A second arm must not start a second timer that would fire early."""
    cpv_watchdog._armed = False
    try:
        assert cpv_watchdog.arm(budget_s=3600.0) is True
        assert cpv_watchdog.arm(budget_s=3600.0) is False
    finally:
        cpv_watchdog._armed = False
