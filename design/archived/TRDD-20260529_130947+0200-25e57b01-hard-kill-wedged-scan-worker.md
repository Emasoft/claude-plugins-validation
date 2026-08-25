---
trdd-id: 25e57b01-db7a-4cac-aa12-4b017fee6077
title: Hard process-kill for a scan worker wedged in a C-level regex (issue #52, deferred robustness)
column: complete
created: 2026-05-29T13:09:47+0200
updated: 2026-08-25T17:25:16+0200
---

> **IMPLEMENTED 2026-05-31 (v2.111.0).** Change A + A' shipped; Change B
> deferred (see below). New module `scripts/cpv_scan_supervisor.py`
> (`supervised_scan`) — a queue-fed worker pool with a single-threaded parent
> drain+watchdog loop that `SIGKILL`s a worker whose in-flight file exceeds
> `hard_kill_after_s`, records the file `TIMED_OUT`, and respawns. Kill-safe by
> construction: tasks flow through one `Queue` (worker holds no queue lock
> during the long scan), results/heartbeats live in a `Manager` dict (a SIGKILL
> can't corrupt a separate manager process), and the watchdog times each
> in-flight file on the PARENT's clock (monotonic isn't comparable across
> processes). `Process.kill()` is cross-platform, so this also fixes the old
> SIGALRM path's POSIX-only limitation. **Change A** — `parallel_scan` gains
> `hard_kill_after_s` (+ supervision args) and delegates to the supervisor when
> set (opt-in; the default executor path is unchanged). **Change A'** —
> `scan_one_target` drops the unfixable SIGALRM and threads a PER-FILE hard-kill
> through `scan_all_files` → `parallel_scan`; a wedged file is recorded + the
> scan CONTINUES (no abort). **Change B (route validate_security per-line loops
> through HybridMatcher) deferred** — #53's per-line input cap + Change A's
> hard-kill together already guarantee termination; B is a perf optimisation,
> not a correctness requirement, and is a behaviour-sensitive refactor better
> done in its own focused session. Tests: `tests/test_issue_52_hard_timeout_kill.py`
> (16, incl. a busy-loop wedge + `mp.active_children()` leak assertions →
> ZERO leaked PIDs). Issue #56's supervision layer (progress / stuck-warn /
> resume / inspect / notify + `CPV_SCAN_*` env knobs) ships in the SAME module.

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-25e57b01 — Hard process-kill for a wedged scan worker

**Filename:** `design/tasks/TRDD-20260529_130947+0200-25e57b01-hard-kill-wedged-scan-worker.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

Deferred robustness hardening for GitHub issue #52. The reporter's ACTUAL
symptom (a worker pinned at 100% CPU for 1h01m on one file, past the 20-min
timeout) is already mitigated by issue #53's fix (committed): the per-line
skillaudit scan is capped at `_MAX_SCAN_LINE = 2000` chars before `re.search`,
so the catastrophic backtracking on a pathological LONG line — the trigger of
the wedge — can no longer blow up. This TRDD tracks the REMAINING defense-in-
depth: a hard, regex-engine-independent termination guarantee.

## Why deferred (NOT rushed at the tail of a long session)

Both remaining changes are high-risk core-infra refactors where a botched
version is WORSE than the gap it closes:

- **Change A (process-kill watchdog)** re-architects the
  `ProcessPoolExecutor` harness to track per-worker PIDs and `SIGKILL` a
  wedged worker. Failure modes: zombie processes, races (killing a worker
  mid-result-write), cross-platform signal differences (`SIGKILL` semantics
  differ on Windows), breaking the existing `shutdown(wait=True)` contract,
  and interaction with batch mode. This is exactly the class of concurrency
  code that needs a focused session with process-table snapshot tests
  (per `~/.claude/rules/browser-ui-test-techniques.md` §14-16: snapshot PIDs
  before/after, assert zero leaked workers), a busy-loop fixture, and a
  bounded-wall-clock regression guard — NOT a tail-end addition.
- **Change B (route core scanners through RE2)** is a behaviour-sensitive
  refactor of `validate_security.py`'s per-line secret/command/path loops; it
  must produce byte-identical detection results, RE2 is optional (zero benefit
  if `google-re2` is absent), and ~9 lookaround patterns stay in the Python
  `re` fallback regardless.

The fail-fast principle argues AGAINST a half-working SIGKILL: a partial
implementation that sometimes kills healthy workers or leaks zombies is a
worse failure than the rare residual backtracking #53 already mostly closes.

## Root cause (✓ VERIFIED in the issue-#52 triage report)

1. `validate_security.scan_one_target` arms `signal.alarm()` + a SIGALRM
   handler, but CPython only runs the Python handler at a bytecode boundary —
   while the C `_sre` engine is inside `re.Pattern.search()` holding the GIL,
   no boundary is reached, so the queued `TimeoutError` fires only AFTER the
   pathological match completes (open-ended). SIGALRM CANNOT preempt a C call.
2. `cpv_parallel_runner.parallel_scan` uses `future.result(timeout=...)`; on
   expiry it records `TIMED_OUT` but the worker keeps running
   (`future.cancel()` is a no-op on a started task), and the
   `with ProcessPoolExecutor(...)` block's `shutdown(wait=True)` then BLOCKS
   until the wedged worker finishes — hanging the whole call. `_run_in_batches`
   has the identical defect at batch granularity.
3. `MAX_SCAN_BYTES` bounds whole-file size, not per-line regex TIME — a few-KB
   file with one pathological line sails past it (this is the gap #53 narrowed
   with the per-line length cap).

## Proposed implementation (when picked up — from the triage)

- **Change A (REQUIRED, load-bearing):** in `cpv_parallel_runner.py`, on
  per-file/per-batch timeout, hard-kill the OS worker rather than recording
  the timeout and letting `shutdown(wait=True)` block. Preferred: a parent-side
  WATCHDOG thread tracking `(future -> worker_pid, submit_time)` that
  `SIGKILL`s any worker whose wall-clock exceeds `timeout * kill_grace_factor`
  (e.g. 1.5x); least-coupled to CPython internals. Add a configurable
  `kill_grace_factor` / `hard_kill_after_s` threaded through
  `parallel_scan_aggregated`. (NOTE: the "no hardcoded iteration caps" rule
  does NOT apply — this is a wall-clock timeout, not a fix-loop cap.)
- **Change A' (REQUIRED):** REMOVE the unfixable SIGALRM block in
  `scan_one_target` and re-point it at the killable harness; keep
  `MAX_SCAN_BYTES` as a cheap pre-filter; drop the docstring's false promise.
- **Change B (defense-in-depth):** route the `validate_security.py` per-line
  secret/command/path loops through the existing `cpv_re2_matcher.HybridMatcher`
  (as `cpv_skillaudit_native.py` already does) for RE2's linear-time guarantee
  on the patterns RE2 accepts; keep Change A as the backstop for the
  lookaround-fallback patterns.

## Tests (when picked up — two-sided + leak-free)

New `tests/test_issue_52_hard_timeout_kill.py`:
- `test_parallel_scan_hard_kills_wedged_worker` — a `while True: pass` worker +
  fast files; assert the call RETURNS within `timeout*factor+ε` (no shutdown
  hang), the wedged file's `ScanResult.error` is the timeout string, fast files
  produced findings, and a `ps -eo` snapshot before/after shows ZERO leaked
  worker PIDs.
- `test_parallel_scan_busy_loop_does_not_block_shutdown` — bounded wall-clock
  regression guard.
- `test_batch_mode_hard_kills_wedged_worker` — same for `_run_in_batches`.
- `test_scan_one_target_timeout_terminates_pathological_regex` — replace the
  current SIGALRM tests; a catastrophic-backtracking fixture line vs a real CPV
  secret/command pattern returns `TIMED_OUT` within budget.
- `test_validate_security_uses_hybrid_matcher` (Change B) — detection results
  byte-identical to the per-line loop on existing positive/negative fixtures.

## Acceptance

- A wedged worker is hard-killed; `parallel_scan` returns within the grace
  budget; ZERO leaked worker PIDs (process-table snapshot identical pre/post).
- `scan_one_target` no longer relies on SIGALRM.
- CPV self-scan 0/0/0/0; full suite green.

## Cross-component note

CPV-owned (the harness `cpv_parallel_runner.py` + scanner
`validate_security.py` are CPV's, not any scanned plugin's — per
`~/.claude/rules/plugin-tests-are-the-plugins-job.md`).

## Approval log

- 2026-08-25T17:25:16+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED v2.111.0 — supervised_scan(hard_kill_after_s=...) live (batch_ae)
