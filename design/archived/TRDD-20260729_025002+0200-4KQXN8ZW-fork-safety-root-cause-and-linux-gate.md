---
trdd-id: 4KQXN8ZW
title: Fork-safety root cause plus a pre-publish Linux parity gate
column: complete
created: 2026-07-29T02:50:02+0200
updated: 2026-07-29T02:50:02+0200
current-owner: claude-plugins-validation
task-type: bugfix
approval-tier: 0
relevant-rules: []
---

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-07-29

- **Shipped in v3.24.0.** Root cause fixed, three safeguards in place, canon updated.
- **NEXT ACTION:** none. The one deliberate gap is recorded under "Not done" below.
- Full serial suite **11535 passed / 3 skipped / 0 failed** (`PYTEST_EXIT=0`).

## Why

v3.23.1 fixed the **instance** of the v3.23.0 Linux deadlock (progress markers
emitted from `ThreadPoolExecutor` workers). It did not fix the **class**: CPV
inherited the platform default multiprocessing start method, which is `fork` on
Linux and `spawn` on macOS. Forking a multithreaded process copies mutex state,
so a child can inherit a lock — `sys.stderr`'s, `logging`'s, a malloc arena, the
import lock — held by a thread that does not exist there, and hang forever.

The next latent deadlock was one careless `print()` on a worker thread away, and
no local gate could see it, because the only platform that exhibits the bug is
the one nobody develops on.

## Root cause

Two sites built pools from the platform default:

- `scripts/cpv_parallel_runner.py` — `ProcessPoolExecutor(...)` with no `mp_context=`
- `scripts/cpv_scan_supervisor.py` — bare `mp.get_context()`

Both now resolve through `scripts/cpv_fork_safety.py::safe_mp_context()`, which
pins **`spawn`** and can never return `fork`.

## Measurements (all first-hand)

| Question | Result |
|---|---|
| Does the deadlock reproduce on macOS if we force fork? | **Yes** — 3/3 children hung; spawn: 300 rounds, 0 hung |
| Cold self-validate under each method | fork **178.1s** · spawn **120.2s** · forkserver **94.7s** |
| Full suite under forced fork | 11510 passed, 162s |
| Does the probe catch the real v3.23.0 defect? | buggy+spawn **16 passed (blind)** · buggy+fork **FAILED, CI's exact `TimeoutExpired`** · fixed+fork **37 passed / 15.4s** |

Fork was the **slowest**, so the fix costs nothing.

## Why NOT forkserver

Textbook-correct, safe against this deadlock, CPython 3.14's Linux default, and
**measured fastest**. Rejected: its server process is started **once and
reused**, so children inherit the environment captured at server start, not the
caller's current environment. CPV is configured through env vars, so a variable
set after the first pool existed would silently never reach a worker — **quiet
wrong results instead of a loud hang**. It broke 5 tests, each of which passed in
isolation and failed in sequence: the signature of server reuse. Recorded in the
module and regression-locked, because a faster textbook-blessed option is a
standing temptation.

## Safeguards shipped

1. **Root cause** — explicit fork-safe context at both pool sites.
2. **Source invariant** (`tests/test_fork_safety.py`) — no `ProcessPoolExecutor`
   without `mp_context=`, no bare `mp.get_context()`, no `multiprocessing.Pool`
   in `scripts/`. A source check because the deadlock needs a fork race, so a
   behavioural test would flake while the invariant is exact (same reasoning as
   v3.19.2's `threading.Barrier`). Proven two-sided by reintroducing the
   regression, and guarded against vacuity.
3. **Publish Gate 3c** (`scripts/cpv_fork_parity.py`) — re-runs the suite with
   the default forced to `fork` via a temporary `sitecustomize.py` on
   `PYTHONPATH`, so the forcing reaches SUBPROCESSES (load-bearing: the v3.23.0
   deadlock happened in a subprocess the suite spawned). Chains to a project's
   own sitecustomize rather than shadowing it. Runs serially, strictly before
   bump/commit/tag/push.
4. **Canon** — self-detecting `G4b` in the generated `publish.py`, the
   `.cpv-forkparity/` gitignore entry, and a Fork-safety section in
   `skills/cpv-canonical-pipeline/references/parallelism.md`.

## Refusal contract of the gate

- Linux → `already-native`, skips (never doubles CI).
- No fork available (Windows) → WARNING, never blocks.
- No `tests/` suite → skips and says so. Running pytest against an empty tree
  yields "no tests collected", which the gate would otherwise report as a fork
  failure — a **fabricated finding**. Gate 2 already blocks a publish with no
  tests. (Found by the full serial suite, not by inspection.)
- **A timeout IS a failure**, because a hang is this defect's signature. "Cannot
  check" is never reported as clean, and never blocks either.

## Not done (deliberate)

`G4b` only reaches plugins that REGENERATE their pipeline. Already-deployed
plugins get nothing. A `validate_plugin` rule flagging default-context pools
would reach every plugin CPV scans, and the detector already exists
(`find_default_context_pools`). Left out to avoid silently expanding scope and
adding a new false-positive surface; it is the obvious next increment.
