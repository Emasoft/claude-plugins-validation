---
trdd-id: DZS5K34A
title: plugin-fixer migration/CI-green loop burns 16-25M tokens — lean output capture + non-progress circuit-breaker + local-verify-before-republish
column: complete
created: 2026-06-30T11:23:53+0200
updated: 2026-06-30T11:36:52+0200
current-owner: cpv-main-session
assignee: cpv-main-session
priority: 0
severity: HIGH
effort: M
labels: [token-economy, plugin-fixer, fix-loop, ci-green-loop, cost-guard]
task-type: bugfix
parent-trdd: null
npt: []
eht: []
blocked-by: []
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
merge-strategy: squash
test-requirements: [unit, lint, typecheck]
audit-requirements: [security-scan]
review-requirements: [code-review]
runtime-targets: [macos, linux]
impacts: []
external-refs: []
attempts: 1
implementation-commits: []
published-version: null
published-at: null
---

# TRDD-DZS5K34A — plugin-fixer token-burn (16-25M/run) — lean capture + non-progress guard + local-verify

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-30

**User report (verbatim):** "a complete disaster.. the agent fixer/upgrader of the
cpv burned a whole month of tokens!!!" — two `claude-plugins-validation:plugin-fixer`
(Opus 4.8) runs: "CPV pipeline upgrade on AMAMA" **25.5M tok / ~$8.96 / 97 tools**
and "CPV fixer: dedupe tests → green CI" **16.3M tok / ~$6.16 / 97 tools**. User
chose **Full fix: A+B+C** via AskUserQuestion.

**ROOT CAUSE (verified from the loop source, NOT the un-readable 25M-tok transcripts):**
The two runaway runs both took the **canonical-pipeline migration / CI-green** path
(`agents/plugin-fixer.md` steps 7c-7d + `iterative-fix-loop.md` §"Publish/upgrade —
loop until CI green"). That path runs `publish.py --patch` (which runs the ENTIRE
test suite as Gate 4 + validation + every gate, all printed) then `gh run watch` /
`gh run view` (full CI job logs), **looping until green CI**. The loop instructions
**nowhere** specify capturing that output LEANLY — so the full pipeline+CI output
lands RAW in the agent's context every cycle, and (cost ≈ turns × per-turn-context)
each raw dump rides forward and is re-charged on every later turn → tens of millions
of tokens across several red→fix→re-publish cycles. Confirmed by grep: **zero
error-only output-capture pattern anywhere in the loop refs.** The per-finding
safe-ceiling guard (runbook §12) bounds the FIX-findings path, not this CI-output-
volume path. The oscillation guard (`cpv_fix_loop_state.py`) trips only on an
EXACT-repeat finding/job multiset — a CI failing-set that churns (shifting test
names) never exact-repeats → never trips → unbounded expensive cycles.

**THE FIX — three parts (all gate-neutral waste-reduction; NO gate relaxed; honors
the no-hardcoded-iteration-cap rule):**

- **A — Lean output capture (L9, the dominant lever).** Add a MANDATORY lean-capture
  discipline to the publish/CI loop: redirect `publish.py` / `pytest` / `gh run
  watch|view` to a FILE, read back ONLY the failed-gate + failing-test/job lines
  (grep + head cap), NEVER ingest the raw stream into context. Use `gh run view
  --log-failed` (failed-step logs only), not the whole run. Edit: runbook §1 step 3,
  `iterative-fix-loop.md` §publish-loop, agent §7d pointer.
- **B — Non-progress circuit-breaker (`STALLED` verdict).** Extend
  `cpv_fix_loop_state.py` with an OPT-IN `--stall-window K` flag + a `STALLED`
  verdict (new exit code 3): fires when the finding/job count has NOT strictly
  improved on the best-so-far for K consecutive iterations (a PROGRESS gate, not an
  iteration count — resets on any strict improvement). Applied to the **CI-publish
  loop** (the second `--state` file), where each cycle is a real, expensive
  release+CI attempt; the cheap inner validate→fix loop keeps its no-cap, FN-safe
  CONVERGED/CYCLE/PROGRESS behavior (no `--stall-window` → STALLED never fires).
  `STALLED` → return `[PARTIAL]` for human review.
- **C — Local-verify-before-republish.** Step 7d: after a red CI, reproduce + fix
  the failing job LOCALLY (run the specific failing test + `validate --strict`,
  lean-captured) and confirm green LOCALLY **before** spending a full `publish.py` +
  CI cycle. Don't burn a whole release+CI cycle to discover a one-line fix didn't land.

**WHY B is FN-safe on the CI loop but NOT used on the inner loop.** On the inner
validate→fix loop, count can plateau while the SET productively churns (fix a
CRITICAL → exposes a MINOR) — a count-stall would falsely stop it, so the inner loop
stays signature-based (CONVERGED/CYCLE only). On the CI loop, "progress" IS fewer
failing jobs; K real release+CI cycles with no net reduction is a genuine "fixes
aren't converging CI — human, look" signal. So STALLED is opt-in and only the CI
loop opts in.

## Plan steps (runnable as written)
1. `scripts/cpv_fix_loop_state.py`: add `STALLED` verdict + `--stall-window K` (record),
   track `best_count`, precedence CONVERGED > CYCLE > STALLED > PROGRESS, exit code 3.
2. `tests/`: two-sided tests — STALLED fires after K non-improving; does NOT fire on
   strict improvement; absent `--stall-window` → never STALLED (back-compat); exit codes.
3. `references/plugin-fixer-runbook.md` §1 step 3: lean-capture block + local-verify-before-republish.
4. `skills/fix-validation/references/iterative-fix-loop.md` §publish-loop + §termination:
   lean-capture + `--stall-window` on the CI state file + local-verify.
5. `agents/plugin-fixer.md` §7d: concise pointers (lean-capture, `--stall-window`, local-verify).
6. Re-validate cache-cold (`CPV_SCAN_CACHE=0` … --strict) → 0/0/0/0; regen self-hashes LAST.
7. Publish via publish.py (plugin) — confirm with user given cost-sensitivity.

## Verification gates
- New STALLED logic: two-sided unit tests (fires / does-not-fire / back-compat / exit codes).
- ruff + mypy clean on `cpv_fix_loop_state.py`.
- Cache-cold (`CPV_SCAN_CACHE=0`) self-validate 0/0/0/0 (markdown NITs included).
- Self-hashes regenerated LAST (after every .py + .md + this TRDD edit).
- NO gate relaxed; NO iteration cap added to the inner fix loop.

## Durable artifacts to read before acting
- `agents/plugin-fixer.md` §7d (lines ~119-124), `skills/fix-validation/references/iterative-fix-loop.md`
  (§"Publish / upgrade — loop until CI green" ~175-209, §"Termination and safety" ~79-87),
  `references/plugin-fixer-runbook.md` §1 (lines 36-61), `scripts/cpv_fix_loop_state.py` (record()/CLI).
