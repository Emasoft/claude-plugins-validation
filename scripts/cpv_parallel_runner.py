#!/usr/bin/env python3
"""CPV parallel-scan harness (task #384).

Validators that scan many files (validate_security, validate_skill,
validate_cache, validate_xref, …) historically iterate serially:

    for file in files:
        scan_one_file(file)

That makes a 200-file plugin's lint loop a 200×(per-file-cost) wall-clock cost.
This harness wraps the per-file callable in a ``ProcessPoolExecutor`` so the
loop becomes parallel-by-CPU-count without changing the per-file logic.

## Contract

* ``scan_func`` MUST be a TOP-LEVEL importable function (closures and
  lambdas are NOT pickleable by ``ProcessPoolExecutor``).
* ``scan_func`` takes exactly ONE arg (a ``Path``). For validators that need
  extra context, capture it in module-level globals OR refactor the
  validator to be context-free at the per-file level.
* Findings returned by ``scan_func`` MUST be pickleable (dicts of primitives
  or simple dataclasses — NOT report objects holding open file handles).
* Result order is PRESERVED (input order in == output order out).
* Exceptions raised by ``scan_func`` are CAPTURED into ``ScanResult.error``
  by default (``on_error="collect"``); the harness does NOT crash.

This module is INTENTIONALLY minimal. It owns concurrency primitives and
nothing else; validators retain their domain logic.
"""

from __future__ import annotations

import os
import sys
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence, TypeVar

from cpv_fork_safety import safe_mp_context

T = TypeVar("T")


# NOTE: the ``files`` parameter and ``ScanResult.file_path`` were initially
# typed as ``Sequence[Path]`` / ``Path`` because the first 5 validators
# (security, skill, command, agent, hook) all walked file trees. Wave A6
# (validate_hook) and A8 (validate_cache) introduced "work unit" dataclasses
# that bundle a file path with parse metadata the worker needs at construct
# time (avoiding a re-parse in the worker process). The harness itself
# never inspects the element type — it just forwards each item through to
# ``scan_func`` — so we generalise the API to ``Sequence[Any]`` /
# ``Any``. Callers retain full type safety at THEIR boundary because they
# control both ``files`` and ``scan_func``; mypy at the call site still
# enforces that ``scan_func`` accepts the element type.


@dataclass(frozen=True)
class ScanResult:
    """One file's scan outcome. ``findings`` is empty when ``error`` is set.

    ``file_path`` is typed ``Any`` to support both plain ``Path`` and
    bespoke per-validator work-unit dataclasses (e.g. ``_HookWorkUnit``,
    ``_CacheWorkUnit``). At the call site the validator knows what it
    passed in and casts appropriately.
    """

    file_path: Any
    findings: list
    error: str | None = None


def _emit_scan_progress(path: Any) -> None:
    """#54: env-gated per-file scan progress. When CPV_SCAN_PROGRESS is set in
    the environment, emit a flushed ``scanning <path>`` line to stderr from
    INSIDE the worker — the parent cannot name the in-flight file because the
    pool scans N files concurrently. Default-silent (no output, negligible
    cost) when the env var is unset, so a hung/stuck file can be identified on
    demand without changing default behaviour. Pairs with the #53 ReDoS bound.
    """
    if os.environ.get("CPV_SCAN_PROGRESS"):
        print(f"[cpv-scan] scanning {path}", file=sys.stderr, flush=True)


def _run_one(scan_func: Callable[[Any], list], path: Any) -> list:
    """Top-level worker shim for ``chunk_size == 1`` (one file per task).

    ProcessPoolExecutor needs pickleable callables at the submission boundary.
    ``scan_func`` itself must already be top-level (per the contract), but
    pairing it with the path through this shim keeps the per-task call site
    uniform and lets us add instrumentation later without touching every
    validator.

    Kept intentionally trivial — any exception propagates back through the
    future to ``parallel_scan``, which decides whether to collect or re-raise.
    """
    _emit_scan_progress(path)
    return scan_func(path)


def _run_batch(scan_func: Callable[[Any], list], paths: list[Any]) -> list[tuple[int, list, str | None]]:
    """Top-level worker shim for ``chunk_size > 1`` (N files per task).

    Each file in the batch is scanned independently and produces a tuple
    ``(local_index, findings, error_str_or_None)``. The batch ITSELF never
    raises — per-file exceptions are captured into the tuple so that ONE
    bad file in a batch doesn't poison the other ``chunk_size - 1`` scans
    that were already done. The dispatch loop in ``parallel_scan`` then
    decides whether to re-raise (on_error="raise") or surface as
    ``ScanResult.error`` (on_error="collect").

    ``local_index`` is the file's position within the batch — the caller
    knows the batch's starting global index and adds them together to
    restore input-order placement.
    """
    out: list[tuple[int, list, str | None]] = []
    for i, path in enumerate(paths):
        _emit_scan_progress(path)
        try:
            out.append((i, scan_func(path), None))
        except Exception as exc:
            out.append((i, [], f"{type(exc).__name__}: {exc}"))
    return out


def parallel_scan(
    files: Sequence[Any],
    scan_func: Callable[[Any], list],
    *,
    n_workers: int | None = None,
    chunk_size: int = 1,
    on_error: str = "collect",
    timeout_per_file: float | None = None,
    hard_kill_after_s: float | None = None,
    stuck_warn_after_s: float | None = None,
    on_event: Callable[[dict], None] | None = None,
    state_path: Any = None,
    resume: bool = False,
    notify: Callable[[str], None] | None = None,
) -> list[ScanResult]:
    """Run ``scan_func(file)`` for each file in parallel via ``ProcessPoolExecutor``.

    Args:
        files: Sequence of ``Path`` objects to scan. Empty sequence → empty list.
        scan_func: Top-level importable callable ``(Path) -> list``. Closures
            and lambdas are rejected by ``ProcessPoolExecutor`` (not pickleable).
        n_workers: Process pool size. ``None`` → ``os.cpu_count()`` (or 1 if
            ``os.cpu_count()`` returns ``None``, which happens on exotic
            platforms / containers without scheduling info).
        chunk_size: Number of files submitted as ONE task to the pool. ``1``
            (default) = one-task-per-file, maximally parallel. ``> 1`` =
            batched submission, lower per-task overhead, useful when
            per-file work is tiny vs. the pickling/IPC cost.
        on_error: ``"collect"`` (default) → capture worker exceptions as
            ``str(exc)`` into ``ScanResult.error``, no crash. ``"raise"`` →
            re-raise the FIRST exception from any worker (others may have
            already started but their results are discarded).
        timeout_per_file: Per-file deadline in seconds. ``None`` → no
            timeout. On expiry, the file's ``ScanResult.error`` is set to
            ``"TimeoutError: scan exceeded {N}s"`` (under ``on_error="collect"``);
            under ``on_error="raise"`` a ``TimeoutError`` is re-raised.

    Returns:
        List of ``ScanResult`` in INPUT ORDER. The spec mandates order
        preservation so downstream aggregators don't need to sort.

    Notes:
        * ``ProcessPoolExecutor`` is mandatory (per spec): CPV's per-file
          work is CPU-bound (regex scanning, AST walking, hashing) and the
          GIL prevents thread-level parallelism. Process pools also isolate
          worker crashes — a segfault in one file's scanner doesn't take
          down the whole validator.
        * Unpickleable callables fail at SUBMIT time with a clear
          ``TypeError`` from the executor; no need to validate up front.
        * When ``files`` is empty, we short-circuit without spawning the
          pool — pool creation is a few-millisecond cost we shouldn't pay
          for a no-op.
    """
    if on_error not in ("collect", "raise"):
        raise ValueError(f"on_error must be 'collect' or 'raise', got {on_error!r}")

    # Materialise to a list so we can index by position for order-preservation
    # AND so we don't iterate a generator twice. ``files`` is typed as
    # ``Sequence`` (random-access) but callers occasionally hand us tuples,
    # lists, or path-like iterables; ``list()`` normalises.
    file_list: list[Path] = list(files)
    if not file_list:
        return []

    # Issue #52 / #56 — when a HARD-KILL budget (or any supervision feature) is
    # requested, route through the killable supervisor. ``ProcessPoolExecutor``
    # cannot cancel a started task, so a worker wedged in a C-level regex would
    # block ``shutdown(wait=True)`` open-endedly; the supervisor SIGKILLs the
    # wedged worker, records the file ``TIMED_OUT``, and drains the rest. This
    # path is OPT-IN, so the default per-plugin scan keeps the lean executor and
    # pays none of the manager-process overhead.
    if hard_kill_after_s is not None or on_event is not None or state_path is not None or notify is not None:
        from cpv_scan_supervisor import supervised_scan  # noqa: PLC0415

        return supervised_scan(
            file_list,
            scan_func,
            n_workers=n_workers,
            hard_kill_after_s=hard_kill_after_s,
            stuck_warn_after_s=stuck_warn_after_s,
            on_event=on_event,
            state_path=state_path,
            resume=resume,
            notify=notify,
        )

    if n_workers is None:
        # ``os.cpu_count()`` returns ``None`` on platforms that can't
        # determine CPU count (very rare; some container runtimes). Fall back
        # to single-worker rather than crashing the validator.
        n_workers = os.cpu_count() or 1

    # ``chunk_size`` of 0 or negative would silently break ``map``-style
    # submission; reject loudly so the caller fixes their call site.
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")

    # Results buffer indexed by input position. We pre-fill with sentinels and
    # overwrite as futures complete; this lets us submit-then-wait in any order
    # without needing as_completed + a sort-by-index pass.
    results: list[ScanResult | None] = [None] * len(file_list)

    # mp_context is MANDATORY, never the platform default: the default is
    # ``fork`` on Linux, and this harness is driven from validator worker
    # THREADS, so a default-context pool forks a multithreaded process and can
    # inherit a held stderr/logging lock into a child that then hangs forever
    # (v3.23.0 shipped exactly that). See scripts/cpv_fork_safety.py.
    with ProcessPoolExecutor(max_workers=n_workers, mp_context=safe_mp_context()) as executor:
        if chunk_size == 1:
            _run_one_per_file(
                executor=executor,
                file_list=file_list,
                scan_func=scan_func,
                results=results,
                on_error=on_error,
                timeout_per_file=timeout_per_file,
            )
        else:
            _run_in_batches(
                executor=executor,
                file_list=file_list,
                scan_func=scan_func,
                results=results,
                on_error=on_error,
                timeout_per_file=timeout_per_file,
                chunk_size=chunk_size,
            )

    # By construction every slot is filled — assert it loudly during dev so
    # we catch any future refactor that breaks the invariant. Once the slot
    # is written, type-narrow to ``list[ScanResult]``.
    assert all(r is not None for r in results), (
        "parallel_scan internal invariant: every input file must produce a ScanResult (success, error, or timeout)"
    )
    return [r for r in results if r is not None]


def _run_one_per_file(
    *,
    executor: ProcessPoolExecutor,
    file_list: list[Path],
    scan_func: Callable[[Path], list],
    results: list[ScanResult | None],
    on_error: str,
    timeout_per_file: float | None,
) -> None:
    """``chunk_size == 1`` dispatcher: one task per file, per-file timeout
    semantics. Writes into ``results`` by input index."""
    # Submit one future per file. The shim ``_run_one`` is the pickled
    # callable; ``scan_func`` rides along as its first argument. This
    # uniformity matters when we later want to add per-task timing or
    # progress callbacks — only one submission site to change.
    future_to_index = {executor.submit(_run_one, scan_func, path): idx for idx, path in enumerate(file_list)}

    # Iterate futures in submission order — NOT as_completed order — so
    # that timeout_per_file gives each file its OWN deadline rather than
    # a shared global one. (If we used as_completed we'd have to manage
    # remaining time across futures, which is fiddly.)
    for future, idx in future_to_index.items():
        path = file_list[idx]
        try:
            findings = future.result(timeout=timeout_per_file)
            results[idx] = ScanResult(file_path=path, findings=findings, error=None)
        except FutTimeoutError:
            # Per-file timeout. The future keeps running in the worker
            # (we can't actually cancel a running task in ProcessPoolExecutor
            # — only pending ones), but we move on and the executor
            # shutdown at the end of the ``with`` block will collect it.
            msg = f"TimeoutError: scan exceeded {timeout_per_file}s" if timeout_per_file is not None else "TimeoutError"
            if on_error == "raise":
                # Cancel any still-pending futures so we shut down fast.
                for f in future_to_index:
                    if not f.done():
                        f.cancel()
                raise TimeoutError(f"{path}: scan exceeded {timeout_per_file}s") from None
            results[idx] = ScanResult(file_path=path, findings=[], error=msg)
        except Exception as exc:
            # Any other worker-raised exception. We capture
            # ``{ExcClass}: {message}`` so callers can surface it as a
            # per-file WARNING without needing the full traceback.
            if on_error == "raise":
                for f in future_to_index:
                    if not f.done():
                        f.cancel()
                raise
            results[idx] = ScanResult(
                file_path=path,
                findings=[],
                error=f"{type(exc).__name__}: {exc}",
            )


def _run_in_batches(
    *,
    executor: ProcessPoolExecutor,
    file_list: list[Path],
    scan_func: Callable[[Path], list],
    results: list[ScanResult | None],
    on_error: str,
    timeout_per_file: float | None,
    chunk_size: int,
) -> None:
    """``chunk_size > 1`` dispatcher: N files per task. Trades per-file
    isolation for lower IPC overhead — useful when per-file work is small.

    Timeout semantics in batched mode: ``timeout_per_file`` is multiplied by
    the batch size to derive the batch deadline (the natural interpretation
    of a "per-file" budget when files travel together). If the deadline is
    hit, every file in the batch is marked as timed out — we can't know
    which file in the batch actually exceeded its share, only that the
    batch as a whole did.
    """
    # Split file_list into contiguous batches of size <= chunk_size. The last
    # batch may be smaller; that's fine — ProcessPoolExecutor doesn't care.
    batches: list[tuple[int, list[Path]]] = []
    for start in range(0, len(file_list), chunk_size):
        end = min(start + chunk_size, len(file_list))
        batches.append((start, file_list[start:end]))

    future_to_batch_meta = {executor.submit(_run_batch, scan_func, paths): (start, paths) for start, paths in batches}

    for future, (start, paths) in future_to_batch_meta.items():
        # Scale the per-file timeout to a per-batch budget. Without this
        # rescaling, a batch of 10 files would get the timeout meant for one.
        batch_timeout = timeout_per_file * len(paths) if timeout_per_file is not None else None
        try:
            batch_out = future.result(timeout=batch_timeout)
            # Each tuple is (local_index, findings, error_str_or_None). The
            # batch shim never raises — per-file errors come through here as
            # the third element. We still honor on_error="raise" by inspecting.
            for local_idx, findings, err in batch_out:
                global_idx = start + local_idx
                path = file_list[global_idx]
                if err is not None and on_error == "raise":
                    # Re-raise to surface the worker exception. We've lost
                    # the original traceback (it crossed a process boundary),
                    # but the string form preserves the class + message.
                    raise RuntimeError(f"{path}: {err}")
                results[global_idx] = ScanResult(file_path=path, findings=findings, error=err)
        except FutTimeoutError:
            msg = f"TimeoutError: batch scan exceeded {batch_timeout}s" if batch_timeout is not None else "TimeoutError"
            if on_error == "raise":
                for f in future_to_batch_meta:
                    if not f.done():
                        f.cancel()
                raise TimeoutError(f"batch starting at {file_list[start]}: scan exceeded {batch_timeout}s") from None
            # Mark every file in the timed-out batch with the error.
            for local_idx, path in enumerate(paths):
                global_idx = start + local_idx
                results[global_idx] = ScanResult(file_path=path, findings=[], error=msg)
        except Exception as exc:
            # _run_batch shouldn't raise (it catches per-file), so this path
            # only fires on a true infrastructure failure (e.g. the worker
            # process died, the result couldn't be unpickled). Mark the
            # whole batch.
            if on_error == "raise":
                for f in future_to_batch_meta:
                    if not f.done():
                        f.cancel()
                raise
            err_str = f"{type(exc).__name__}: {exc}"
            for local_idx, path in enumerate(paths):
                global_idx = start + local_idx
                results[global_idx] = ScanResult(file_path=path, findings=[], error=err_str)


def parallel_scan_aggregated(
    files: Sequence[Any],
    scan_func: Callable[[Any], list],
    aggregator: Callable[[list[ScanResult]], T],
    **kwargs,
) -> T:
    """Convenience: ``aggregator(parallel_scan(files, scan_func, **kwargs))``.

    The typical validator pattern is "scan every file → merge findings into a
    single report object". This wrapper saves the validator from writing the
    two-liner itself. Pass-through ``**kwargs`` reach ``parallel_scan``
    unchanged (``n_workers``, ``chunk_size``, ``on_error``, ``timeout_per_file``).
    """
    return aggregator(parallel_scan(files, scan_func, **kwargs))


# A timed-out result is reported with this prefix by BOTH dispatchers
# (``_run_one_per_file`` and ``_run_in_batches``). ``retry_failed_serially``
# keys on it to tell a WEDGE apart from a CRASH — see its docstring for why
# that distinction is load-bearing rather than cosmetic.
_TIMEOUT_ERROR_PREFIX = "TimeoutError"


def result_is_timeout(result: ScanResult) -> bool:
    """True when ``result`` failed because its deadline expired (not a crash)."""
    return result.error is not None and result.error.startswith(_TIMEOUT_ERROR_PREFIX)


def retry_failed_serially(
    results: list[ScanResult],
    items: Sequence[Any],
    scan_func: Callable[[Any], list],
) -> list[ScanResult]:
    """Re-run each CRASHED item once, in THIS process, and return the merged list.

    Callers treat a per-file scan failure as a BLOCKING finding — a file the
    scanner could not read is UNKNOWN, never clean. That is only fair if a
    *transient* failure cannot fail an honest plugin: a CI runner that OOM-kills
    one pool worker would otherwise block a plugin that has nothing wrong with
    it, which is issue #179's defect (a gate the work cannot satisfy) wearing a
    different hat. Retrying once in-process absorbs the transient case, so only
    a REPRODUCIBLE failure is reported.

    **A TIMEOUT is never retried.** Re-running a wedged scan in this process
    would hang the main thread with nothing left to preempt it — the pool at
    least isolates the wedge in a killable child. A timeout is already proof the
    work does not terminate, so a second attempt buys no information and costs
    the whole run. Crashes are the opposite: they end promptly and are usually
    environmental.

    Order is preserved; a retry that succeeds replaces the failed result, and a
    retry that fails again keeps the ORIGINAL error (the first failure is the
    more informative one — the retry may fail differently for a reason that has
    nothing to do with the file).
    """
    if len(results) != len(items):
        raise ValueError(f"results/items length mismatch: {len(results)} vs {len(items)}")

    out = list(results)
    for idx, result in enumerate(out):
        if result.error is None or result_is_timeout(result):
            continue
        try:
            findings = scan_func(items[idx])
        except Exception:
            # Reproducible failure — keep the original error verbatim so the
            # caller reports the first, most representative diagnosis.
            continue
        out[idx] = ScanResult(file_path=result.file_path, findings=findings, error=None)
    return out


# ── Default-on hard-kill bound: WHEN to pay for supervision (issue #182) ─────
#
# v4.2.0 made a file whose security scan did NOT complete BLOCK instead of pass.
# But the only thing that can actually BOUND a wedged scan stayed opt-in, so the
# default pool path was still unbounded. ``future.result(timeout=)`` does not
# bound a ``ProcessPoolExecutor``: it records a timeout but cannot cancel a
# STARTED task, so the pool's ``shutdown(wait=True)`` blocks on the wedged worker
# anyway — the hang is relocated, not removed. Only KILLING the worker bounds it,
# which is what ``cpv_scan_supervisor.supervised_scan(hard_kill_after_s=…)`` does.
#
# Supervision is not free. It spawns a Manager process, a task Queue, N workers
# and a watchdog loop — a FIXED cost of roughly a fifth of a second, independent
# of how many files are being scanned. Paying that on every three-file scan taxes
# every small run to guard against a wedge that cannot plausibly happen there.
#
# So the bound is ROUTED, not blanket. Supervise when EITHER
#
#   * ``len(items) >= CPV_SCAN_SUPERVISE_MIN_FILES`` (default 32), or
#   * any single item is a LARGE file — its size falls in
#     [CPV_SCAN_SUPERVISE_LARGE_FILE_MIN_BYTES, CPV_SCAN_SUPERVISE_LARGE_FILE_MAX_BYTES]
#     (default 1 MiB - 8 MiB).
#
# The window's LOWER edge is where a single file's scan time becomes measurable;
# the UPPER edge is where the scanners stop reading the file at all
# (``validate_security.MAX_SCAN_BYTES`` is 8 MiB and the skillaudit walker drops
# anything over 2 MB), so a file ABOVE the window returns immediately and cannot
# wedge a worker — there is nothing there to supervise.
#
# **Not supervising is never a MUTE.** An unsupervised wedge hangs the run until
# the CI job's own ``timeout-minutes`` kills it, and since v3.22.2 a killed job is
# a hard, fail-closed failure. The bound does not create the blocking outcome — it
# converts an opaque whole-job hang into a clean, attributable BLOCKING finding on
# the one file responsible, while the rest of the tree still gets scanned.

_SUPERVISE_MIN_FILES_ENV = "CPV_SCAN_SUPERVISE_MIN_FILES"
# 32 mirrors validate_security's own CPV_PARALLEL_SCAN_THRESHOLD default: at that
# size a pool is already being spawned, so the supervisor's extra fixed cost is
# a fraction of work the run was going to do regardless.
_DEFAULT_SUPERVISE_MIN_FILES = 32

_SUPERVISE_LARGE_FILE_MIN_BYTES_ENV = "CPV_SCAN_SUPERVISE_LARGE_FILE_MIN_BYTES"
_DEFAULT_SUPERVISE_LARGE_FILE_MIN_BYTES = 1024 * 1024  # 1 MiB

_SUPERVISE_LARGE_FILE_MAX_BYTES_ENV = "CPV_SCAN_SUPERVISE_LARGE_FILE_MAX_BYTES"
_DEFAULT_SUPERVISE_LARGE_FILE_MAX_BYTES = 8 * 1024 * 1024  # 8 MiB

# The budget itself keeps its existing operator knob so there is ONE spelling of
# "how long may a single file's scan take", not two.
_HARD_KILL_AFTER_ENV = "CPV_SCAN_SKIP_STUCK_AFTER"
# 300s. Measured on CPV's own 1083-file tree with the cache off (v4.2.0), the
# WORST single file took 27.13s with google-re2 and 28.25s without — so the
# default is ~11x the slowest scan this codebase has ever produced, while staying
# far under the 25-30 min ceiling a validate job runs with. A bound the work
# cannot finish inside is issue #179's own defect; this one has an order of
# magnitude of headroom.
DEFAULT_HARD_KILL_AFTER_S = 300.0


def _env_positive_number(name: str, default: Any, cast: Callable[[str], Any]) -> Any:
    """Typo-safe env override, matching ``cpv_lint_engine._phase_timeout``.

    Blank, unparseable, zero and negative values ALL fall back to ``default``.
    A misconfiguration therefore degrades to the shipped behaviour rather than to
    a near-zero (or absent) guard.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def supervise_min_files() -> int:
    """File-count threshold at or above which a scan is supervised.

    WIDEN-ONLY: an override may LOWER the bar (supervise more often) but never
    raise it. Raising it is how you would switch the bound off for an entire tree
    without ever writing the word "off" — precisely the disable this must not
    permit. Lowering only ever costs throughput, never safety.
    """
    value = int(_env_positive_number(_SUPERVISE_MIN_FILES_ENV, _DEFAULT_SUPERVISE_MIN_FILES, int))
    return min(value, _DEFAULT_SUPERVISE_MIN_FILES)


def supervise_size_window() -> tuple[int, int]:
    """``(min_bytes, max_bytes)`` size window that makes a single file "large".

    WIDEN-ONLY, for the same reason as :func:`supervise_min_files`: the floor may
    only be lowered and the ceiling may only be raised, so an override can only
    ever cause MORE files to be considered large. The returned window therefore
    always contains the shipped default window, and ``min_bytes <= max_bytes``
    holds by construction.
    """
    lo = int(
        _env_positive_number(
            _SUPERVISE_LARGE_FILE_MIN_BYTES_ENV,
            _DEFAULT_SUPERVISE_LARGE_FILE_MIN_BYTES,
            int,
        )
    )
    hi = int(
        _env_positive_number(
            _SUPERVISE_LARGE_FILE_MAX_BYTES_ENV,
            _DEFAULT_SUPERVISE_LARGE_FILE_MAX_BYTES,
            int,
        )
    )
    return min(lo, _DEFAULT_SUPERVISE_LARGE_FILE_MIN_BYTES), max(hi, _DEFAULT_SUPERVISE_LARGE_FILE_MAX_BYTES)


def hard_kill_after_s() -> float:
    """Per-file wall-clock budget after which the scanning worker is KILLED.

    ``CPV_SCAN_SKIP_STUCK_AFTER`` overrides it; a blank/unparseable/non-positive
    value falls back to :data:`DEFAULT_HARD_KILL_AFTER_S`. There is deliberately
    no value that means "no bound" — a SMALLER budget is stricter (it kills
    sooner, producing MORE blocking findings), so no setting of this variable can
    turn the guard off.
    """
    return float(_env_positive_number(_HARD_KILL_AFTER_ENV, DEFAULT_HARD_KILL_AFTER_S, float))


def supervision_is_warranted(items: Sequence[Any]) -> bool:
    """True when this work set is big enough that the fixed supervisor cost pays.

    ``items`` are the paths about to be scanned. A non-path item (a validator
    work-unit dataclass) is simply not measurable as a file and is skipped — the
    count arm still applies to it.
    """
    if len(items) >= supervise_min_files():
        return True
    lo, hi = supervise_size_window()
    for item in items:
        try:
            size = os.stat(item).st_size
        except (OSError, TypeError, ValueError):
            # Unreadable or not path-like. The scan itself reports an unreadable
            # file; routing must not raise on it.
            continue
        if lo <= size <= hi:
            return True
    return False


def resolve_hard_kill_after_s(items: Sequence[Any], explicit: float | None = None) -> float | None:
    """The per-file hard-kill budget to use for a security scan of ``items``.

    An ``explicit`` budget (a caller kwarg, or one an entry point already read
    from the environment) ALWAYS wins. Otherwise the routing above decides:
    a warranted shape gets :func:`hard_kill_after_s`, anything smaller gets
    ``None`` (the lean executor, exactly as before).
    """
    if explicit is not None:
        return explicit
    if not supervision_is_warranted(items):
        return None
    return hard_kill_after_s()
