#!/usr/bin/env python3
"""Fork-safety SSOT: never fork a multithreaded process (TRDD-4KQXN8ZW).

## The defect class this exists to make impossible

Forking a multithreaded process copies mutex STATE but not the threads that
own it. If any thread holds a lock at the instant of the fork — ``sys.stderr``'s
internal lock, the logging lock, a malloc arena, the import lock — the child
inherits that lock **held by a thread that does not exist there** and blocks
forever the first time it needs it.

CPV runs validators on a ``ThreadPoolExecutor`` and those validators fan out to
process pools, so it forks from a multithreaded process on any platform whose
default start method is ``fork``. **Linux defaults to fork; macOS defaults to
spawn.** That asymmetry is invisible on a developer's Mac and fatal in CI:

    v3.23.0 emitted progress markers from worker threads. Locally (spawn) the
    full 11,484-test suite was green. On Linux (fork) a single
    ``validate_plugin.py --json <tiny fixture>`` went from 8.7s to a >300s
    timeout, failing BOTH CI and Release.

v3.23.1 removed that one emit site. That fixed the INSTANCE, not the CLASS —
the next latent deadlock was one careless ``print()`` on a worker thread away.

## The fix

Stop inheriting the platform default. Every process pool in CPV is created from
:func:`safe_mp_context`, which pins **spawn** — a brand new interpreter per
worker, so nothing is inherited and no lock can be copied. ``fork`` is never
selected, on any platform.

**This is not a speed/safety trade.** Measured on CPV's own tree (full
self-validate, cold cache): **fork 178.1s, spawn 120.2s**. Fork was the *slower*
of the two.

Because the choice no longer depends on the OS, the start method a developer
exercises locally is the one CI exercises — which is what makes local testing
representative at all. macOS already defaulted to spawn, so pinning spawn means
Linux now behaves like the platform CPV has been tested on for its whole life,
rather than the other way round.

## Why NOT forkserver (measured, not assumed)

``forkserver`` is the textbook answer — its server is spawned fresh and stays
single threaded, and CPython 3.14 made it the Linux default for exactly this
reason. It was tried first, and it was **faster still** (94.7s). It was rejected
because it **broke 5 tests** that pass under both spawn and the old default:

    The forkserver server process is started ONCE and REUSED. Children are
    forked from that server, so they inherit the environment and imported module
    state captured when the server started — NOT the caller's current state.

CPV is configured through environment variables (``CPV_SCAN_CACHE``,
``CLAUDE_PRIVATE_USERNAMES``, the parallelism escape hatch). Under forkserver a
variable set after the first pool was created would silently fail to reach any
worker, which is a config-silently-ignored bug far nastier than the deadlock it
was meant to prevent — it fails quietly and produces wrong results instead of
hanging. Each failing test passed in ISOLATION and failed in sequence, which is
the signature of exactly that reuse.

25 seconds is not worth a stale-environment class of bug. If forkserver is ever
reconsidered, the blocker to solve first is propagating current env/state to a
reused server.

## Regression guards

* ``tests/test_fork_safety.py`` asserts at SOURCE level that no pool is built
  from the platform default (no bare ``mp.get_context()``, no
  ``ProcessPoolExecutor`` without ``mp_context=``). A source invariant is the
  right shape here: the deadlock needs a fork race to reproduce, so a
  behavioural test would be flaky while the invariant is exact.
* ``publish.py`` runs a fork-parity probe that re-runs the fork-sensitive tests
  with the Linux default forced, so the class is caught even in code that still
  inherits the default (a dependency, or a future call site).
"""

from __future__ import annotations

import multiprocessing as mp
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from multiprocessing.context import BaseContext

__all__ = ["FORK_SAFE_METHODS", "safe_mp_context", "safe_start_method"]

# ``fork`` is deliberately absent and must never be added — that is the entire
# point of this module. ``forkserver`` is ALSO deliberately absent: it is safe
# against this deadlock but its reused server process serves children a stale
# environment, which broke 5 tests. See "Why NOT forkserver" above before
# adding anything here.
FORK_SAFE_METHODS: tuple[str, ...] = ("spawn",)


def safe_start_method() -> str:
    """Return the preferred start method that never forks a multithreaded parent.

    Prefers ``forkserver`` (fast: one spawned server, cheap forks from it), then
    ``spawn`` (universal). Never returns ``fork``.

    Falls back to ``"spawn"`` if the platform reports neither — ``spawn`` is
    available on every platform CPython supports, so this is a belt-and-braces
    path rather than a reachable one. Failing SAFE matters more than failing
    fast here: the wrong answer is a deadlock in someone else's CI.
    """
    try:
        available = set(mp.get_all_start_methods())
    except Exception:  # pragma: no cover - defensive; get_all_start_methods is total
        return "spawn"
    for method in FORK_SAFE_METHODS:
        if method in available:
            return method
    return "spawn"


def safe_mp_context() -> BaseContext:
    """Return a multiprocessing context that never forks a multithreaded process.

    Use this INSTEAD of ``mp.get_context()`` (which inherits the platform
    default, i.e. ``fork`` on Linux) and pass it as ``mp_context=`` to every
    ``ProcessPoolExecutor``. See the module docstring for why.
    """
    method = safe_start_method()
    try:
        return mp.get_context(method)
    except ValueError:  # pragma: no cover - only if the platform lied about support
        return mp.get_context("spawn")
