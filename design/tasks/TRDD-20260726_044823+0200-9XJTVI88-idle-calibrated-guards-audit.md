---
trdd-id: 9XJTVI88
title: Audit and harden idle-calibrated guards that false-block releases
column: complete
created: 2026-07-26T04:48:23+0200
updated: 2026-07-26T04:48:23+0200
current-owner: cpv-session
task-type: infra
scope: project
release-via: publish
relevant-rules: []
implementation-commits: [a95c2166, b6b6a525, eb9108d8]
---

# Audit and harden idle-calibrated guards that false-block releases

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-07-26

- **Done and shipped:** v3.19.1 (hook budget + instrumentation, stale-hook reinstall,
  ReDoS inner CPU-time bound, ReDoS outer budget, `.git`-scoped tree snapshot).
- **Done, pending release:** the ruff blind-spot fix (`extend-include` for the
  extensionless git hooks) and the parallelism-test rewrite (peak concurrency).
  Both committed; ship as v3.19.2.
- **NEXT ACTION:** `CPV_SKIP_GITHUB_INTEGRITY=1 uv run python scripts/publish.py --patch`,
  then confirm CI + Release green.
- **The audit is CLOSED.** A suite-wide sweep found no further defective guards — see
  "Sweep result" below. Do NOT rewrite the ratio-based timing tests; they are correct.

## Problem

Six guards false-blocked releases on healthy code across one session. Each was a budget
or snapshot calibrated on an idle machine, then enforced inside a pipeline that
deliberately saturates the machine (or queries git) one step earlier. None was a defect
in the thing being measured; every one reported a failure that did not exist.

| # | Guard | Wrong unit / scope | Fix |
|---|---|---|---|
| 1 | Gate 2 pytest budget (600s) | idle-calibrated | none needed — cold-cache artefact |
| 2 | pre-push `run_script` (180s) | idle-calibrated | 600s + always report elapsed |
| 3 | ReDoS inner assertion | wall clock | CPU time (`process_time`) |
| 4 | ReDoS outer kill (20s) | wall clock, no margin | 120s (still wall clock — correct there) |
| 5 | `test_diagnose_does_not_mutate_tree` | snapshot included `.git/` | scope to plugin files |
| 6 | `test_scanner_block_wall_time...` | duration vs a CONSTANT | peak concurrency >= 2 |

Root enabler for the worst one: **ruff never linted the git hooks.** Git requires
extensionless filenames, so ruff's discovery skipped them and printed
`No Python files found` followed by `All checks passed` — a checker inspecting ZERO files
emits the same green as a clean one. A `NameError` in the hook that gates every push
therefore passed ruff, mypy, 11k tests, `--strict` self-validation and a whole publish
pipeline.

## Sweep result (facts, not assumptions)

Enumerated every timing assertion in `tests/` (20 assertions across 9 files, enumerated to
a file, not a truncated pipe). Classified and stress-tested each shape 3x under
`pytest -n auto` alongside the heaviest neighbour suites:

- **Ratio against a same-run baseline** (`speedup >= 1.5`, serial and parallel timed in the
  same run) — 3/3 pass under load. **SELF-NORMALIZING**: contention inflates both sides, so
  the ratio survives. Correct as written; leaving them alone.
- **Promptness / timeout constants** (`elapsed < 12`, `< 3.0`, `< 2.0`) — 3/3 pass, 141
  tests per round, while round wall time rose 63s -> 100s under increasing load. Wall clock
  is the semantically correct unit for "did the timeout fire", and the margins hold.
- **Duration vs a hard-coded constant asserting PARALLELISM** — the only genuinely broken
  shape, because a constant cannot scale with the machine. Exactly one instance; fixed.

So the discriminator is **not** "wall clock vs not". It is **ratio-to-a-same-run-baseline
(robust) vs duration-against-a-constant (fragile)**.

## Decisions

1. **Patience, never strictness.** Every budget change leaves the check running in full and
   still failing closed. Pinned by tests, so a budget raise cannot silently become a
   strictness drop.
2. **Assert discovery, not silence.** The hook-lint test asserts ruff LISTS the file
   (`--show-files`), because "no errors" and "no files" are indistinguishable — the whole
   bug. It fails if `extend-include` is removed.
3. **Prefer structural facts over durations.** Interval overlap / peak concurrency answers
   "did these run concurrently" in a way contention cannot invert; CPU time answers "did
   this backtrack" for the same reason.
4. **Do not assert a property the code does not have.** The first overlap attempt required
   all four scanners in flight at one instant; measurement showed a roughly constant ~2.4s
   of serialized per-scanner pre-work, so all-four-overlap was an artefact of the old 4.0s
   sleep, never a property of the implementation. Asserting it would have pinned a false
   premise.

## Verification

- 11328 tests pass serially, `REAL_PYTEST_EXIT=0`, zero `FAILED`/`ERROR`.
- Cold `CPV_SCAN_CACHE=0 --strict` self-validate: 0/0/0/0.
- Every fix verified two-sided by making it fail on purpose first: injected `F821` names
  now caught (previously "All checks passed"); `max_workers=1` correctly yields "peak
  concurrency 1"; a timed-out script still returns non-zero; a real plugin-file mutation is
  still detected while git-internal churn is ignored.
- The hook instrumentation produced the datum five failed publishes never yielded:
  `validate_plugin.py completed in 100.9s (budget 600s)` — a real ~2.4x in-situ penalty
  against the old 180s budget's <2x headroom.

## Open

The ~2.4x in-situ slowdown of `validate_plugin.py` is now MEASURED but not EXPLAINED. Four
hypotheses were measured and ruled out: machine load, the post-Gate-10 tree shape, a
`uv lock` re-sync, and a captured-pipe deadlock (`subprocess.run` drains via
`communicate()`). The elapsed-time reporting means the next occurrence yields evidence
rather than a mystery; do not invent a cause before then.
