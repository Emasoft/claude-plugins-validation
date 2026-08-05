"""Hard wall-clock budget for a validate run — issue #190.

A validate that stops returning currently never exits. Its parent dies (the
shell, the ``uvx`` wrapper, the CI step), the interpreter is reparented to
``launchd``/``init`` and waits forever at 0.0% CPU. Two were found on the
reporter's host at 3h and 11h old, and nothing surfaced them: they are not a
runaway that announces itself, they are a slow accumulation, one per hung
attempt.

``_serial_phase`` wraps every serial step in progress markers but, as its own
docstring says, "this only observes" — it imposes no deadline. Individual
phases bound their own work (the lint engine's ``phase_budget``, the per-file
hard-kill in ``cpv_parallel_runner``), yet the run as a whole has no upper
bound, so any wait nobody thought to bound hangs the process permanently.

Three design decisions, each load-bearing:

**It exits NON-ZERO, and that matters more than the timeout.** A budget that
expired into a pass would convert a hang into a silent clean — strictly worse
than the hang, because a hang at least announces itself. "Could not finish" and
"found no problems" are different claims and this must only ever make the first.

**It calls ``os._exit`` rather than raising.** The thread that fires is not the
thread that is stuck. A main thread parked in ``pthread_cond_wait`` — which is
exactly where the reported hangs sit — cannot be made to observe an exception
raised from a watchdog thread, so a polite abort would be silently ignored and
the process would keep hanging. ``os._exit`` cannot be blocked by any lock. The
diagnostic is flushed BEFORE it, because ``os._exit`` skips buffer flushing.

**The environment can only LOWER the budget, never raise or disable it.** Same
rule as the per-file hard-kill's ``min(value, 32)``: a guard an environment
variable can switch off is a guard an attacker can switch off, and a validator
that can be made to skip a phase under load is one that can be made to skip it
on purpose.
"""

from __future__ import annotations

import os
import sys
import threading
import time

# Distinct from 0 (clean) and 1 (findings) so a caller — a human, publish.py, or
# a CI step — can tell "the gate could not finish" apart from "the gate failed
# the plugin". Conflating them is how a budget turns into a false verdict.
EXIT_BUDGET_EXCEEDED = 3

_BUDGET_ENV = "CPV_VALIDATE_BUDGET_S"
# 30 minutes. The reporter's healthy full run is ~3.5 min and their pathological
# one blew a 900s harness timeout, so this is generous enough never to fire on
# working input while still bounding a wedge to something a human will sit for.
_DEFAULT_BUDGET_S = 1800.0
# Below this the budget would fire during ordinary large-plugin work, so a lower
# request is treated as a mistake rather than honoured into a broken run.
_MIN_BUDGET_S = 30.0

_lock = threading.Lock()
_current_phase: str | None = None
_phase_started_at: float = 0.0
_armed = False


def note_phase_start(name: str) -> None:
    """Record the phase now in flight, so an expiry can name it."""
    global _current_phase, _phase_started_at
    with _lock:
        _current_phase = name
        _phase_started_at = time.monotonic()


def note_phase_done(name: str) -> None:
    """Clear the in-flight phase if ``name`` is still the one recorded.

    Guarded by the name check because phases can nest: an inner phase's DONE
    must not erase the outer phase that is still running, or an expiry would
    report "no phase in flight" for a run that is very much inside one.
    """
    global _current_phase
    with _lock:
        if _current_phase == name:
            _current_phase = None


def resolve_budget_s() -> float:
    """Return the wall-clock budget in seconds.

    ``CPV_VALIDATE_BUDGET_S`` may only make the budget STRICTER. A value that is
    unparseable, non-positive, or below ``_MIN_BUDGET_S`` is ignored rather than
    honoured — silently accepting ``CPV_VALIDATE_BUDGET_S=0`` would disable the
    guard through the very mechanism that is supposed to be un-disableable.
    """
    raw = os.environ.get(_BUDGET_ENV, "").strip()
    if not raw:
        return _DEFAULT_BUDGET_S
    try:
        requested = float(raw)
    except ValueError:
        return _DEFAULT_BUDGET_S
    if requested < _MIN_BUDGET_S:
        return _DEFAULT_BUDGET_S
    return min(requested, _DEFAULT_BUDGET_S)


def _reap_children() -> None:
    """Best-effort kill of multiprocessing children before we exit.

    Without this the pool workers outlive the parent and become the orphans this
    issue is about, one generation down. Best-effort on purpose: we are already
    in the failure path and must reach ``os._exit`` regardless of what any child
    does, so no child's misbehaviour may raise here.
    """
    try:
        import multiprocessing  # noqa: PLC0415

        for child in multiprocessing.active_children():
            try:
                child.kill()
            except Exception:  # noqa: BLE001, S110 — failure path, must not raise
                pass
    except Exception:  # noqa: BLE001, S110 — ditto
        pass


def _diagnostic(budget_s: float, started_at: float) -> str:
    with _lock:
        phase = _current_phase
        phase_started = _phase_started_at
    elapsed = time.monotonic() - started_at
    lines = [
        "",
        "=" * 72,
        f"CPV ABORTED: validate exceeded its {budget_s:.0f}s wall-clock budget "
        f"(elapsed {elapsed:.0f}s).",
    ]
    if phase is None:
        lines.append("  No phase was in flight — the wait is outside any tracked phase.")
    else:
        stuck_for = time.monotonic() - phase_started
        lines.append(f"  Phase in flight : {phase}")
        lines.append(f"  Stuck for       : {stuck_for:.0f}s")
    lines += [
        "",
        "  This is NOT a verdict on the plugin. The run could not finish, so",
        "  nothing was proven about it either way — do not read this as clean.",
        "",
        f"  Lower the budget with {_BUDGET_ENV}=<seconds> (it can only be",
        "  lowered, never raised or disabled).",
        "=" * 72,
        "",
    ]
    return "\n".join(lines)


def _fire(budget_s: float, started_at: float) -> None:
    try:
        sys.stderr.write(_diagnostic(budget_s, started_at))
        sys.stderr.flush()
    except (OSError, ValueError):
        # Nobody is reading the log. Terminating is still the right outcome, and
        # is the half of this that the operator actually needs.
        pass
    _reap_children()
    # NOT sys.exit: that raises SystemExit in THIS thread, which the stuck main
    # thread would never observe, and the process would hang on regardless.
    os._exit(EXIT_BUDGET_EXCEEDED)


def arm(budget_s: float | None = None) -> bool:
    """Start the watchdog. Returns True if it armed, False if already armed.

    Idempotent because the validate entry point is reachable more than once in
    a single interpreter (the ``plugin`` subcommand also runs under the test
    suite in-process); a second timer would fire early on an unrelated run.
    """
    global _armed
    with _lock:
        if _armed:
            return False
        _armed = True

    budget = resolve_budget_s() if budget_s is None else budget_s
    started_at = time.monotonic()

    timer = threading.Timer(budget, _fire, args=(budget, started_at))
    # Daemon so a run that finishes normally is never held open by the pending
    # timer — the whole point is to bound a hang, not to add one.
    timer.daemon = True
    timer.start()
    return True
