#!/usr/bin/env python3
"""Killable, resumable, progress-emitting scan supervisor.

Closes GitHub issues #52 (hard process-kill of a scan worker wedged in a
C-level regex `match()`/`search()`) and #56 (resumable + skippable batch scans
with per-file progress, stuck-element detection, and notifications).

WHY a new module instead of extending `cpv_parallel_runner.parallel_scan`:
the existing harness uses `ProcessPoolExecutor`, where `future.result(timeout=)`
records a timeout but CANNOT cancel a started task — the worker keeps running
and the pool's `shutdown(wait=True)` then blocks until the wedged worker
finishes (open-ended). `signal.alarm()`/`SIGALRM` cannot help either: CPython
only runs the Python signal handler at a bytecode boundary, and the C `_sre`
engine holds the GIL with no boundary until `search()` returns — so the queued
`TimeoutError` fires only AFTER the pathological match completes. The ONLY
regex-engine-independent guarantee is to kill the OS process. (TRDD-25e57b01.)

Design (kill-safe by construction):

  * Tasks are distributed through one `multiprocessing.Queue`. A worker
    `get()`s an item (releasing the queue lock immediately), then runs the
    LONG scan lock-free, then writes its result to a `Manager().dict`. A
    SIGKILL therefore lands while the worker holds NO queue lock and is NOT
    mid-`put` — so it cannot corrupt the task queue, and the result dict lives
    in a SEPARATE manager process that stays consistent regardless.
  * The parent runs a SINGLE-THREADED drain+watchdog loop (no watchdog
    thread): each tick it sleeps `poll_interval_s`, collects any new results
    from the manager dict, then inspects each worker's heartbeat
    `(file_index, start_monotonic)`. A worker whose current file has run past
    `hard_kill_after_s` is `SIGKILL`ed, its file recorded `TIMED_OUT`, and a
    fresh worker is respawned to drain the rest. Single-threaded means respawn
    (`fork`/`spawn`) happens with no other threads alive — sidestepping the
    classic fork-with-threads deadlock.
  * `mp.Process.kill()` is cross-platform (SIGKILL on POSIX, TerminateProcess
    on Windows) — so unlike the old SIGALRM path this also works on Windows.

The supervisor is OPT-IN: `parallel_scan` only routes here when
`hard_kill_after_s` is set, so the default per-plugin scan path is unchanged
and pays none of the manager-process overhead.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import queue as _queue
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from cpv_parallel_runner import ScanResult

__all__ = [
    "EVENT_FINISH",
    "EVENT_KILLED",
    "EVENT_RESUMED",
    "EVENT_START",
    "EVENT_STUCK",
    "default_notifier",
    "inspect_state",
    "stderr_progress_printer",
    "supervised_scan",
]

# Event `type` values passed to the `on_event` callback. The parent owns the
# global (index / total) view, so progress is emitted parent-side — the worker
# only reports raw timings.
EVENT_START = "start"
EVENT_FINISH = "finish"
EVENT_STUCK = "stuck_warn"
EVENT_KILLED = "killed"
EVENT_RESUMED = "resumed"

# Heartbeat value for an idle worker (between files / blocked on the task queue).
# A tuple `(file_index, start_monotonic)` means the worker is mid-scan. We never
# kill an idle worker — only one whose IN-FLIGHT file exceeded the budget — so a
# kill never lands while the worker is inside `task_q.get()`.
_IDLE = "idle"


def _worker_main(
    wid: int,
    task_q: Any,
    heartbeat: Any,
    results: Any,
    scan_func: Callable[[Any], list],
) -> None:
    """Top-level worker loop (picklable for `spawn`). Pulls `(idx, path)` items,
    publishes a heartbeat before each scan, runs `scan_func` lock-free, then
    records `(findings, error, duration)` in the manager dict. Exits on the
    `None` sentinel."""
    while True:
        try:
            item = task_q.get(timeout=0.5)
        except (_queue.Empty, OSError):
            # OSError can surface if the queue is being torn down during
            # shutdown; treat it like "no work yet" and re-check the sentinel.
            continue
        if item is None:  # graceful-exit sentinel
            return
        idx, path = item
        # Publish ONLY the index. The PARENT times each in-flight file on its
        # own monotonic clock — `time.monotonic()` reference points are not
        # guaranteed comparable across processes, so a worker-set timestamp
        # compared against the parent's clock would be meaningless.
        # (issue #52 — cross-process watchdog timing)
        heartbeat[wid] = idx
        t0 = time.monotonic()
        try:
            findings = list(scan_func(path))
            err: str | None = None
        except Exception as exc:  # noqa: BLE001 — report ANY scan failure, never crash the worker silently
            findings = []
            err = f"{type(exc).__name__}: {exc}"
        results[idx] = (findings, err, time.monotonic() - t0)
        heartbeat[wid] = _IDLE


def _hard_kill(proc: Any) -> None:
    """SIGKILL (POSIX) / TerminateProcess (Windows) a worker and reap it.

    `kill()` is the hard, regex-engine-independent termination guarantee — it
    does NOT rely on the worker reaching a Python bytecode boundary, so it
    preempts a wedged C-level `re.search()`. We `join` briefly to avoid a
    zombie; the shutdown path force-reaps any straggler."""
    try:
        proc.kill()
    except (OSError, ValueError, AttributeError):
        return
    try:
        proc.join(timeout=2.0)
    except (OSError, ValueError):
        pass


def _verdict(findings: list, error: str | None) -> str:
    if error and error.startswith("TimeoutError"):
        return "TIMED_OUT"
    if error and error.startswith("WorkerDied"):
        return "WORKER_DIED"
    if error:
        return "ERROR"
    return "FINDINGS" if findings else "CLEAN"


def _emit(on_event: Callable[[dict], None] | None, event: dict) -> None:
    if on_event is None:
        return
    try:
        on_event(event)
    except Exception:  # noqa: BLE001 — a broken progress callback must NOT abort the scan
        pass


def _safe_notify(notify: Callable[[str], None] | None, message: str) -> None:
    if notify is None:
        return
    try:
        notify(message)
    except Exception:  # noqa: BLE001 — a broken notifier must NOT abort the scan
        pass


def stderr_progress_printer() -> Callable[[dict], None]:
    """Return an `on_event` callback that prints human progress to stderr in the
    `[batch worker] file i/n ...` shape the issue asks for. Default-silent unless
    `CPV_SCAN_PROGRESS` is set, so quiet runs stay quiet."""

    def _printer(ev: dict) -> None:
        if not os.environ.get("CPV_SCAN_PROGRESS"):
            return
        typ = ev.get("type")
        i = ev.get("index")
        n = ev.get("total")
        path = ev.get("path")
        if typ == EVENT_START:
            print(f"[cpv-scan w{ev.get('worker')}] start  {i}/{n}: {path}", file=sys.stderr, flush=True)
        elif typ == EVENT_FINISH:
            print(
                f"[cpv-scan w{ev.get('worker')}] done   {i}/{n}: "
                f"{ev.get('verdict')} {ev.get('duration_s')}s ({ev.get('findings')} finding(s)) {path}",
                file=sys.stderr,
                flush=True,
            )
        elif typ == EVENT_STUCK:
            print(
                f"[cpv-scan w{ev.get('worker')}] WARNING: file {i}/{n} stuck "
                f">{ev.get('elapsed_s')}s: {path}",
                file=sys.stderr,
                flush=True,
            )
        elif typ == EVENT_KILLED:
            print(
                f"[cpv-scan w{ev.get('worker')}] SIGKILL file {i}/{n} after "
                f"{ev.get('elapsed_s')}s — marked TIMED_OUT, respawning worker: {path}",
                file=sys.stderr,
                flush=True,
            )
        elif typ == EVENT_RESUMED:
            print(f"[cpv-scan] resume: skipping already-scanned {i}/{n}: {path}", file=sys.stderr, flush=True)

    return _printer


def default_notifier(message: str) -> None:
    """Best-effort desktop notification (issue #56.5). macOS → `osascript`,
    Linux → `notify-send`; on any platform also append to `~/.cpv/notify.fifo`
    if it exists. Never raises — notification is advisory."""
    import shutil
    import subprocess

    try:
        if sys.platform == "darwin" and shutil.which("osascript"):
            subprocess.run(
                ["osascript", "-e", f'display notification {json.dumps(message)} with title "cpv scan"'],
                check=False,
                timeout=5,
            )
        elif sys.platform.startswith("linux") and shutil.which("notify-send"):
            subprocess.run(["notify-send", "cpv scan", message], check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass
    fifo = Path.home() / ".cpv" / "notify.fifo"
    try:
        if fifo.exists():
            # Non-blocking open so a FIFO with no reader does not hang the scan.
            fd = os.open(str(fifo), os.O_WRONLY | os.O_NONBLOCK)
            try:
                os.write(fd, (message + "\n").encode("utf-8", "replace"))
            finally:
                os.close(fd)
    except OSError:
        pass


# ── Resume / inspect state (#56.4) ───────────────────────────────────────────
#
# The durable record is a JSONL sidecar (`<state_path>.results.jsonl`), one line
# per finished file — appended as results are produced so a killed run loses
# nothing. The `state_path` JSON itself is a small, periodically-rewritten
# pointer the operator can `--inspect`.


def _results_jsonl(state_path: Path) -> Path:
    return state_path.with_suffix(state_path.suffix + ".results.jsonl")


def _append_result_record(state_path: Path | None, record: dict) -> None:
    if state_path is None:
        return
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with _results_jsonl(state_path).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _write_state(state_path: Path | None, state: dict) -> None:
    if state_path is None:
        return
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(state_path)  # atomic on POSIX/Windows
    except OSError:
        pass


def _load_resume(state_path: Path, files: list) -> dict[int, ScanResult]:
    """Return `{index: ScanResult}` for files already recorded in the JSONL
    sidecar whose path still matches the current file list at that index. Index
    + path BOTH must match, so a reordered file list never resumes the wrong
    file."""
    done: dict[int, ScanResult] = {}
    jsonl = _results_jsonl(state_path)
    if not jsonl.exists():
        return done
    try:
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            idx = rec.get("index")
            if not isinstance(idx, int) or not (0 <= idx < len(files)):
                continue
            if str(rec.get("path")) != str(files[idx]):
                continue
            done[idx] = ScanResult(
                file_path=files[idx],
                findings=rec.get("findings") or [],
                error=rec.get("error"),
            )
    except (OSError, ValueError):
        return {}
    return done


def inspect_state(state_path: str | os.PathLike) -> dict[str, Any]:
    """Read-only `--inspect`: return the persisted state dict (or an `{}`-ish
    stub if absent). Pure read, spawns nothing."""
    p = Path(state_path)
    if not p.exists():
        return {"exists": False, "path": str(p)}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"exists": True, "path": str(p), "error": "unreadable"}
    if not isinstance(raw, dict):
        return {"exists": True, "path": str(p), "error": "malformed"}
    state: dict[str, Any] = raw
    state["exists"] = True
    state["path"] = str(p)
    return state


def supervised_scan(
    file_list: Sequence[Any],
    scan_func: Callable[[Any], list],
    *,
    n_workers: int | None = None,
    hard_kill_after_s: float | None = None,
    stuck_warn_after_s: float | None = None,
    on_event: Callable[[dict], None] | None = None,
    poll_interval_s: float = 0.25,
    state_path: str | os.PathLike | None = None,
    resume: bool = False,
    notify: Callable[[str], None] | None = None,
) -> list[ScanResult]:
    """Scan every item in `file_list` with `scan_func`, hard-killing any worker
    wedged past `hard_kill_after_s` and recording that file as `TIMED_OUT`.

    Args:
        file_list: items to scan (paths or work-units; forwarded verbatim).
        scan_func: top-level picklable `(item) -> list[finding]`.
        n_workers: pool size (default `os.cpu_count()`, clamped to `len`).
        hard_kill_after_s: SIGKILL a worker whose CURRENT file has run this many
            wall-clock seconds; the file becomes `TIMED_OUT` and a replacement
            worker drains the rest. `None` → never kill (termination then relies
            on the scan finishing on its own, as before).
        stuck_warn_after_s: emit an `EVENT_STUCK` (and `notify`) once when a
            file passes this threshold (default: `hard_kill_after_s/2` when a
            kill budget is set, else off).
        on_event: progress callback; receives one dict per start/finish/stuck/
            killed/resumed event. Use `stderr_progress_printer()` for CLI output.
        poll_interval_s: drain+watchdog tick. Smaller = finer progress + tighter
            kill latency, at the cost of more wakeups.
        state_path: persist per-file verdicts (JSONL sidecar) + a state pointer
            here for `--resume` / `--inspect`. `None` → no persistence.
        resume: skip files already recorded in `state_path`'s sidecar.
        notify: callable invoked with a message when a file goes stuck.

    Returns:
        `list[ScanResult]` in input order — every item produces exactly one
        result (success, error, `TIMED_OUT`, or `WORKER_DIED`).
    """
    files = list(file_list)
    n = len(files)
    if n == 0:
        return []
    if n_workers is None:
        n_workers = os.cpu_count() or 1
    n_workers = max(1, min(n_workers, n))
    if stuck_warn_after_s is None and hard_kill_after_s is not None:
        stuck_warn_after_s = hard_kill_after_s / 2.0

    state_p = Path(state_path) if state_path is not None else None

    out: list[ScanResult | None] = [None] * n
    seen: set[int] = set()

    # Resume: pre-fill already-completed files and skip re-scanning them.
    if resume and state_p is not None:
        for idx, res in _load_resume(state_p, files).items():
            out[idx] = res
            seen.add(idx)
            _emit(on_event, {"type": EVENT_RESUMED, "index": idx, "total": n, "path": str(files[idx])})

    pending = [(i, files[i]) for i in range(n) if i not in seen]
    if not pending:
        return [r if r is not None else ScanResult(files[i], [], "WORKER_DIED: not scanned") for i, r in enumerate(out)]

    started_at = time.time()

    def _persist_finished(idx: int, res: ScanResult, dur: float | None) -> None:
        if state_p is None:
            return
        _append_result_record(
            state_p,
            {
                "index": idx,
                "path": str(files[idx]),
                "verdict": _verdict(res.findings, res.error),
                "findings": len(res.findings),
                "error": res.error,
                "duration_s": round(dur, 4) if dur is not None else None,
            },
        )
        _write_state(
            state_p,
            {
                "started_at": started_at,
                "updated_at": time.time(),
                "total": n,
                "completed": len(seen),
                "remaining": n - len(seen),
                "last_index": idx,
            },
        )

    ctx = mp.get_context()
    manager = ctx.Manager()
    heartbeat = manager.dict()
    results = manager.dict()
    task_q: Any = ctx.Queue()
    for item in pending:
        task_q.put(item)

    workers: dict[int, Any] = {}
    next_wid = 0
    warned: set[tuple[int, int]] = set()  # (wid, file_index) already warned

    def _spawn() -> None:
        nonlocal next_wid
        wid = next_wid
        next_wid += 1
        heartbeat[wid] = _IDLE
        proc = ctx.Process(
            target=_worker_main,
            args=(wid, task_q, heartbeat, results, scan_func),
            daemon=True,  # die with the parent — no leaked workers on parent crash
        )
        proc.start()
        workers[wid] = proc

    for _ in range(n_workers):
        _spawn()

    started_seen: set[int] = set()
    # wid -> (file_index, parent_monotonic_when_first_observed). Timed entirely
    # on the PARENT's clock so the watchdog never compares clocks across
    # processes. The elapsed is conservative by at most one poll + queue latency
    # (we start the clock when we first SEE the file in-flight, slightly after
    # the worker actually began it) — which can only DELAY a kill, never bring
    # one forward onto a healthy worker.
    inflight: dict[int, tuple[int, float]] = {}
    try:
        while len(seen) < n:
            time.sleep(poll_interval_s)

            # 1. Collect any results produced since the last tick.
            for idx in list(results.keys()):
                if idx in seen:
                    continue
                findings, err, dur = results[idx]
                res = ScanResult(file_path=files[idx], findings=findings, error=err)
                out[idx] = res
                seen.add(idx)
                _emit(
                    on_event,
                    {
                        "type": EVENT_FINISH,
                        "index": idx,
                        "total": n,
                        "path": str(files[idx]),
                        "verdict": _verdict(findings, err),
                        "findings": len(findings),
                        "error": err,
                        "duration_s": round(dur, 4),
                    },
                )
                _persist_finished(idx, res, dur)

            # 2. Watchdog + liveness over every worker, in one pass.
            now = time.monotonic()
            for wid, proc in list(workers.items()):
                hb = heartbeat.get(wid, _IDLE)
                cur_idx = hb if isinstance(hb, int) else None

                # Maintain the parent-timed in-flight map. Reset the clock when a
                # worker moves to a NEW file; clear it when the worker goes idle.
                prev = inflight.get(wid)
                if cur_idx is None:
                    inflight.pop(wid, None)
                elif prev is None or prev[0] != cur_idx:
                    inflight[wid] = (cur_idx, now)
                    if cur_idx not in started_seen and cur_idx not in seen:
                        started_seen.add(cur_idx)
                        _emit(
                            on_event,
                            {"type": EVENT_START, "index": cur_idx, "total": n, "path": str(files[cur_idx]), "worker": wid},
                        )

                # Dead worker (crashed mid-scan, e.g. segfault) → record + respawn.
                if not proc.is_alive():
                    fi = inflight.get(wid)
                    if fi is not None and fi[0] not in seen:
                        idx = fi[0]
                        res = ScanResult(files[idx], [], "WorkerDied: scan process exited before producing a result")
                        out[idx] = res
                        seen.add(idx)
                        _emit(
                            on_event,
                            {"type": EVENT_KILLED, "index": idx, "total": n, "path": str(files[idx]), "worker": wid, "reason": "died"},
                        )
                        _persist_finished(idx, res, None)
                    workers.pop(wid, None)
                    inflight.pop(wid, None)
                    if any(i not in seen for i, _ in pending):
                        _spawn()
                    continue

                fi = inflight.get(wid)
                if fi is None:
                    continue
                idx, started = fi
                if idx in seen:
                    continue
                elapsed = now - started

                if stuck_warn_after_s is not None and elapsed >= stuck_warn_after_s and (wid, idx) not in warned:
                    warned.add((wid, idx))
                    _emit(
                        on_event,
                        {"type": EVENT_STUCK, "index": idx, "total": n, "path": str(files[idx]), "elapsed_s": round(elapsed, 2), "worker": wid},
                    )
                    _safe_notify(notify, f"cpv scan: file {idx + 1}/{n} stuck >{int(elapsed)}s — {files[idx]}")

                if hard_kill_after_s is not None and elapsed >= hard_kill_after_s:
                    _hard_kill(proc)
                    res = ScanResult(
                        files[idx],
                        [],
                        f"TimeoutError: scan exceeded {hard_kill_after_s}s (worker hard-killed)",
                    )
                    out[idx] = res
                    seen.add(idx)
                    _emit(
                        on_event,
                        {"type": EVENT_KILLED, "index": idx, "total": n, "path": str(files[idx]), "elapsed_s": round(elapsed, 2), "worker": wid, "reason": "timeout"},
                    )
                    _persist_finished(idx, res, elapsed)
                    workers.pop(wid, None)
                    inflight.pop(wid, None)
                    _spawn()  # keep draining the remaining files
    finally:
        # Graceful shutdown: sentinels first, then force-reap any straggler so a
        # process-table snapshot before/after the call is identical (no leak).
        for _ in workers:
            try:
                task_q.put(None)
            except (OSError, ValueError):
                break
        for proc in workers.values():
            try:
                proc.join(timeout=2.0)
            except (OSError, ValueError):
                pass
        for proc in workers.values():
            if proc.is_alive():
                _hard_kill(proc)
        try:
            task_q.close()
            task_q.join_thread()
        except (OSError, ValueError):
            pass
        try:
            manager.shutdown()
        except Exception:  # noqa: BLE001 — best-effort manager teardown
            pass

    # Every input file MUST have produced a result — guard the invariant.
    for i in range(n):
        if out[i] is None:
            out[i] = ScanResult(files[i], [], "WorkerDied: scan ended before this file produced a result")
    return [r for r in out if r is not None]
