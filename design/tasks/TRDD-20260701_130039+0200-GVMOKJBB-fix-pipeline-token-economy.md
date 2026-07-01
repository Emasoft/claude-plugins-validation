---
trdd-id: GVMOKJBB
title: Fix-pipeline token-economy redesign — ledger + MECH-auto-split + fix-as-you-go + fork fan-out
column: published
created: 2026-07-01T13:00:39+0200
updated: 2026-07-01T15:12:00+0200
current-owner: cpv-main-session
assignee: cpv-main-session
priority: 0
severity: HIGH
effort: XL
labels: [token-economy, plugin-fixer, fix-loop, codemod, ledger, fork-dispatch, cache]
task-type: refactor
parent-trdd: null
npt: []
eht: []
blocked-by: []
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
merge-strategy: squash
test-requirements: [unit, integration, lint, typecheck]
audit-requirements: [security-scan]
review-requirements: [code-review]
runtime-targets: [macos, linux]
impacts: [ci-pipeline]
external-refs: []
attempts: 0
implementation-commits: [678cc56, f5b985b, ae338f0, 53228d4, a9e8765, b877c54, d26c987, 8e3d579]
published-version: 2.150.0
published-at: 2026-07-01T15:12:00+0200
---

# TRDD-GVMOKJBB — Fix-pipeline token-economy redesign

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-07-01

**Trigger:** two plugin-fixer runs burned 25.5M + 16.3M tok. DZS5K34A (commit `18ed40b`) already
fixed the CI-publish loop (lean-capture + STALLED + local-verify). This TRDD is the **broader
redesign** approved via plan mode; full design in `~/.claude/plans/iterative-crafting-pine.md`
(user-approved).

**User decisions (locked):** dispatch fan-out via **Agent-tool `subagent_type:"fork"` from a LEAN
dispatcher** (fork inherits the dispatcher's whole conversation + runs background + same model —
doc-confirmed; never fork the bloated main session); **one batched `publish.py --minor` at the
end** (commit each phase locally).

**Verified substrate (do not re-derive):**
- `scripts/cpv_codemod.py` = real zero-LLM codemod engine (dry-run default, per-file backup,
  skips vendored, idempotent) — ~6 markdown transforms today; EXTEND with fix_id→transform map.
- Validator `--json` (`ValidationResult.to_dict()` in `scripts/cpv_validation_common.py`) already
  carries `level, message, file, line, phase, fixable, fix_id, category, suggestion` → the
  MECH(`fixable:true`)/INTEL split + fix-as-you-go line ranges ALREADY EXIST, just underused.
- Loop invoked via `remote_validation.py plugin <root> --strict --report <md> --json <json>`;
  oscillation guard `scripts/cpv_fix_loop_state.py` (CONVERGED/CYCLE/STALLED).
- Token hotspots: full `.md` report read every iteration (redundant w/ JSON); fix-recipe re-read
  per-finding; loop is finding-centric not file-centric (files read repeatedly).

**CRITICAL fork gotcha (two opposite "forks" — same word):** Phase 5 fan-out MUST use the
**Agent-tool `subagent_type:"fork"`** — it inherits the parent conversation and REUSES the warm
cache (saves tokens). The **skill frontmatter `context: fork`** is the OPPOSITE: it runs the skill
in an ISOLATED fresh subagent with NO conversation history (docs: skills#run-skills-in-a-subagent,
line 491 "won't have access to your conversation history") → COLD-writes cache = wastes tokens.
NEVER implement fan-out (or any token-saving reuse) via a skill `context: fork`.

**Standing constraints:** never relax `--strict`, never suppress a security rule; each new codemod
transform FN-safe with two-sided tests; regen self-hashes LAST; number every table row.

**✅ COMPLETE — v2.150.0 PUBLISHED + CI GREEN** (2026-07-01; release `8e3d579`; CI run 28520021325 + Release run 28520021126 both `--exit-status` 0; serial suite 10398 passed/0 failed). All 8 phases shipped. The b13 cross-test break (my P2 `apply` subcommand vs test_audit_fix_b13's all-dispatch invariant) was caught by the pre-publish FULL SERIAL run + fixed architecturally in `d26c987`. Nothing pending. Historical NEXT ACTION below (now DONE).

**NEXT ACTION (DONE):** P8 (P7 COMPLETE — all 4 retrofits committed + central-verified: `53228d4` +
`b896db3`). P8 steps, in order: (1) choice-tree router in `skills/fix-validation` encoding
validate→ledger→MECH(zero-LLM)→INTEL-fix-as-you-go(read-once)→delta→terminate(CONVERGED/CYCLE/STALLED);
(2) TRIM `plugin-fixer.md` body 3691w→pointer — the §7d CI-green detail DUPLICATES
`iterative-fix-loop.md`; replace with a load-the-skill pointer BUT keep the loop-control the agent
OWNS (do NOT break the P3 loop I wrote); (3) docs/README/CLAUDE.md counts (scripts 122→123, test files
378→~380 — verify with `ls scripts/*.py | wc -l` / `ls tests/test_*.py | wc -l`); (4) regen hashes
LAST; (5) ONE batched `publish.py --minor` + watch CI green (lean-captured). Do NOT publish before
(1)-(4). Token-win already gated by `ae338f0` (ledger 78% smaller). SURFACE the fork finding to the
user at the end (forks save tokens only from a lean dispatcher — the batch fans out from bloated main).

**Fork finding (fact-based, surfaced to user):** forks save tokens ONLY from a LEAN dispatcher
(reusing its warm skill+ledger). The batch fans out from the BLOATED main session, where fresh lean
workers + on-disk slices are correct — forking bloated-main is NOT a win, and forking merely for
parallelism when one context fits costs N× tokens (WALL-CLOCK tradeoff, not a token saving). So the
token wins are P1-P3; forks are documented for the narrow lean-dispatcher case. NEVER a skill
`context: fork` (isolated, cold-write).

**Progress:**
- P1 DONE (`42e8c7c`) — `cpv_fix_ledger.py` + 35 tests. Ledger `{summary, mech:{file:[{line,level,
  category,fix_id,suggestion}]}, intel:{file:[{line,level,category,blocking,suggestion}]}}`;
  MECH=fixable:true, INTEL=fixable:false; `blocking` FN-safe (unknown WARNING → blocking). Real
  invocation grounded: `remote_validation.py plugin <root> --strict --json` = BOOLEAN flag → JSON on STDOUT.
- P2 DONE (`c9defd6`) — `fixable`/`fix_id` SSOT: tagged `chmod-exec` (shebang-gated → deterministic
  clear; others left INTEL); `cpv_codemod apply --json` = zero-LLM MECH applier; FIXED a relay bug
  (print_json silently dropped fixable/fix_id). 372+206 tests.
- P3/4/6 DONE (this commit) — loop rewrite in `agents/plugin-fixer.md §The loop` +
  `skills/fix-validation/references/iterative-fix-loop.md`: validate→JSON→compact ledger (read the
  LEDGER, NOT the full report); MECH-first `cpv_codemod apply`; INTEL fix-as-you-go, one file
  read-once fixed-same-turn; delta re-validate; optional free-mode llm-ext pinpoint. ALL gates
  preserved (oscillation guard, WARNING rule, DZS5K34A CI-loop/STALLED, final verify, 7c/7d).
  cache-cold 0/0/0/0.
- **P8 item:** `agents/plugin-fixer.md` body is now 3691w — the §7d CI-green detail DUPLICATES
  `iterative-fix-loop.md`; trim the agent body to a pointer in P8 (leaner fixer = lower per-turn
  cost = the redesign's own goal).
- P5 DONE (`f5b985b`) — batch_shard MECH-first + read-once; token-honest fork-vs-fresh choice tree.
  SURFACED + FIXED a real CPV FP: `RC-GHOST-DISPATCH-001` (validate_xref) falsely flagged the
  built-in `subagent_type:"fork"` as a ghost dispatch → added `fork` to `BUILTIN_AGENTS` +
  the `BUILTIN_AGENT_TYPES` SSOT; FN-safe two-sided (a real missing NAMED agent still fires).
- GATE DONE (`ae338f0`) — durable token-win test: ledger = 21.9% of the raw findings surface it
  replaces each iteration (78% smaller), lossless (compression, not truncation).
- P7 3/4 DONE (`53228d4`) — devitalizer/leaks/marketplace retrofit to compact ledger + file-centric
  read-once. CENTRAL-VERIFIED (read the diffs): ALL security invariants preserved (FLAG-not-suppress,
  never-mute/relax-`--strict`, provably-inert-or-FLAG, rotate+purge, clean-room success gate,
  oscillation guard, CI-green loop). Contract tests green (leaks 42, devitalizer 111, marketplace 5).
  +1 benign advisory body-size WARNING (leaks 2176w, non-blocking).
- P7 4/4 DONE (`b896db3`) — cache-optimizer retrofit: Phase-1 ledger + Phase-2 MECH-first zero-LLM
  codemod + INTEL fix-as-you-go read-once + Phase-3 delta-ledger loop-state. CENTRAL-VERIFIED:
  CA-01..07 recipes / priority order / CA-07 advisory / loop-state / Phase-4 user-approval all
  preserved; 102 tests green. **P7 COMPLETE.** Body-size advisory WARNINGs now 7 (all non-blocking;
  P8 trims plugin-fixer 3691w — the only clear duplication; the others' prose is necessary).

## Plan steps (each = local commit; ONE batched publish at end)

- **P1** `cpv_fix_ledger.py` + tests — compact by-file ledger from validator JSON; MECH/INTEL
  split; mechanical blocking-vs-advisory WARNING tag. Replaces reading the full `.md` each iter.
- **P2** Extend `cpv_codemod.py` — fix_id→deterministic-transform map; auto-apply the MECH set
  (zero-LLM) FIRST. Two-sided test per new transform; only genuinely-mechanical fix_ids.
- **P3+P4+P6** Rewrite the INTEL leg (`skills/fix-validation/references/iterative-fix-loop.md`,
  `agents/plugin-fixer.md §7`): one file at a time, read-only-the-ranges (`tldr slice` / offset),
  fix all in the SAME turn (`fastedit`/`Edit`), never re-read; recipe inline in ledger; delta
  re-validate feeds `cpv_fix_loop_state`; conditional free-mode llm-ext pinpoint (graceful when absent).
- **P5** Fork fan-out from a LEAN fixer (curated tools, no MCP; model at dispatch — Sonnet[1m]
  structural / Opus security); forks own disjoint file slices; ledger for slice+resume; fresh-worker
  fallback when `CLAUDE_CODE_FORK_SUBAGENT=0`.
- **P7** Retrofit `plugin-devitalizer`, `plugin-leaks-preventer`, `cache-optimizer-agent`,
  `marketplace-fixer` to ledger + fix-as-you-go + fork; STALLED on expensive outer loops.
  FLAG-not-suppress unchanged.
- **P8** Choice-tree router in `skills/fix-validation`; cache discipline (no model pins;
  idempotent ledger/loop-state = no double-work); **CA-07 fork-distinction VERIFIED 2026-07-01
  (no change needed):** `validate_cache.py:538-577` matches `context: fork` in FRONTMATTER ONLY →
  correctly flags skill `context: fork`, cannot mis-flag Agent-tool `subagent_type:"fork"`;
  `ca-rules.md`/`cache-fixes.md` accurate; docs/README/CLAUDE.md; regen hashes LAST;
  batched `publish.py --minor` + CI green.

## Verification
- Per script: ruff + mypy clean; two-sided unit tests.
- **Before/after token measurement on a synthetic fixture** (known MECH+INTEL mix): MECH clears
  with zero agent turns; each INTEL file read once; convergence; NO gate relaxed. Gate on the
  measured token RATIO, not "tests pass" (profile-before-parallelizing lesson).
- Cache-cold `CPV_SCAN_CACHE=0 … --strict` self-validate 0/0/0/0 per phase.
- Final: one batched `publish.py --minor`; CI green (lean-captured).

## Out of scope
Backburner `e9f13df1` (user-HELD) + `ETDWX70R` (separate spike); `cpv-main-menu-agent` opus pin
(verify-only — dispatches opus security agents, likely intentional; not a fix agent).
