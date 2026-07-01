---
trdd-id: GVMOKJBB
title: Fix-pipeline token-economy redesign — ledger + MECH-auto-split + fix-as-you-go + fork fan-out
column: dev
created: 2026-07-01T13:00:39+0200
updated: 2026-07-01T13:00:39+0200
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
implementation-commits: []
published-version: null
published-at: null
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

**NEXT ACTION:** Phase 1 — write `scripts/cpv_fix_ledger.py` + `tests/test_cpv_fix_ledger.py`
(consume validator JSON → compact by-file ledger w/ MECH/INTEL split + blocking tag). Verify
(ruff+mypy+pytest), commit locally.

**Progress:** none yet (plan just approved).

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
  idempotent ledger/loop-state = no double-work); **verify CPV's own CA-07 detector +
  cache-fixes docs distinguish skill `context: fork` (cold → flag) from Agent-tool
  `subagent_type:"fork"` mentions (warm → do NOT flag)** — serves the "optimize the cache" goal
  and CPV's detection-accuracy mandate; docs/README/CLAUDE.md; regen hashes LAST;
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
