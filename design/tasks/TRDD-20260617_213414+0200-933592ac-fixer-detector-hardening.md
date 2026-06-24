---
trdd-id: 933592ac-98f0-498c-9e7c-54742acaa76c
title: Fixer/detector hardening — amvcp field report (htmlhint FP, doc-context NITs, TOC catch-22, fixer-agent robustness)
column: published
created: 2026-06-17T21:34:14+0200
updated: 2026-06-24T03:27:35+0200
current-owner: claude-plugins-validation
assignee: claude-plugins-validation
priority: 2
severity: MEDIUM
effort: XL
labels: [false-positive, fixer-agent, devitalizer, toc-contract, lint-engine, skillaudit]
task-type: bugfix
parent-trdd: null
npt: []
eht: []
blocked-by: []
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
test-requirements: [unit, integration, lint, typecheck]
audit-requirements: []
review-requirements: []
impacts: [public-api]
external-refs: ["github.com/Emasoft/claude-plugins-validation/issues/132"]
---

# TRDD-933592ac — Fixer/detector hardening (amvcp field report)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-17

**Trigger:** the user reported that CPV's fixer/devitalizer agents "fail to fix
validation issues, crash, exhaust context, leave a corrupt state." Field report
(another plugin's Claude) at
`reports_dev/20260617_121158+0200-cpv-scan-issues-for-fixer-and-devitalizer.md`.

**Verification done (user chose "re-verify first"):** cloned amvcp
(the amvcp working tree under `~/Code`, source READ-ONLY) to
`/tmp/amvcp-clone`, checked out the report's commit `4d96866` IN THE CLONE, ran
the real `remote_validation.py plugin … --strict`. Faithful reproduction:
**0 CRITICAL · 0 MAJOR · 40 MINOR · 13 NIT · 12 WARNING** (EXIT 3). Raw report:
`/tmp/amvcp-val.txt`. The report's claims were OVERSTATED — verify each before
fixing (claim-verification): several were already fixed on 2.129.0, one was my
own `example.com` (RFC 2606) false-alarm.

**Verified root cause of the agent failures:** the fixer is told to reach
0/0/0/0, but ~19 findings are CPV-side and UNFIXABLE by the plugin (10 htmlhint
banner FP + 8 doc-example demoted-NIT + 1 defensible REGEX_DOS) PLUS a catch-22
TOC contract with an unrecognized embed format. So the agent thrashes on the
unfixable (context exhaustion) and mangles correct code trying to "fix"
detector-FPs (corrupt state). Thesis CONFIRMED.

**PROGRESS:**
- **A1 DONE** (uncommitted→committing): `cpv_lint_engine._lint_html` now (a) strips
  the `Config loaded:` banner + `Scanned N file…` summary before building findings,
  (b) strips ANSI color escapes, (c) takes the first 20 REAL lines so genuine errors
  aren't crowded out. FN-safe (a real htmlhint tag-pair error still surfaces).
  Verified end-to-end on amvcp: 10 banner MINORs → 0, ANSI → 0, MINOR 40→33.
- **CACHE-REV BUG found + fixed (the bigger win):** the lint cache key
  (`_build_cache_key`) was keyed on file-content + EXTERNAL-tool-version only, NOT
  CPV's own engine code — so ANY lint-engine fix was MASKED for warm-cache users
  after a CPV upgrade (and fed agents stale findings). Fixed by folding a
  `_LINT_ENGINE_CODE_REV` (sha256 of `cpv_lint_engine.py`) into the key. Proven
  end-to-end: a re-validate with NO cache clear re-linted (val3 had ANSI, val4
  didn't). +5 tests; lint-engine + cache suites 293 pass.

**A2 INSPECTION (done — all 8 are doc-over-match FPs of known families):**
- 6 in `references/*.md` (node-network-cloud:139 SHELL_EXEC on a prose bullet of
  backticked product names — CONTEXT-DEPENDENT, single-line probe misses it, use the
  real multi-line file per the #124 lesson; the others — tabbed-code-samples:5,
  20-layout-code-focus:94, group-collapse-handles:29, inline-svg-illustration-controls:262,
  tokenizer-contract:131 — are displayed example code/prose). Doc-context suppress, per #76/#83.
- 2 in SKILL.md BODIES (modal-comments:17 + graph-diagrams:17) CMD_INJECTION on a `;`
  in PROSE (Prerequisites sentences "Mermaid CDN**;** Graphviz…"), NOT a shell separator.
  → a PRECISE prose-`;` discriminator (same family as #86/#87/#88), FN-safe (a real
  `; rm -rf` / `; curl|sh` still fires). NOT a SKILL.md-body blanket suppression.

**NEXT ACTION:** A2 (doc-context suppressors for the 8 demoted-NIT doc examples),
B1/B2 (TOC converge + dual embed format), B3/B4/B5 (fixer-agent hardening) — delegate
to opus agents, central-verify each (own probe + full serial suite + self-validate
`--strict`, per the NIT-catch lesson — not just pytest). Ship incrementally.

**A2 IN FLIGHT (Phase 2 started):** all 8 firing sites re-read from the clone and
classified (every one a genuine doc/prose FP — see table below; the 2 EXFIL on
`amvcp-runtime.js` are A4 not A2, the `amvcp-code-highlight.js:133` REGEX_DOS is the
A3 MINOR not A2). Spec at `reports_dev/a2-doc-context-suppressors-spec.md`. Opus
impl agent dispatched (edits ONLY `scripts/_skillaudit_markdown_context.py` +
`tests/test_a2_doc_context_suppressors.py`; amvcp read-only). My INDEPENDENT
two-sided probe drafted at `reports_dev/a2_independent_probe.py` — 8 real FP sites +
my own malicious siblings incl. 2 over-broad-shortcut TRAPS (`os.system` in a ```json
fence for case 5; inline-code `(a+)+` for case 4). Run the probe + full serial suite +
self-validate `--strict` BEFORE shipping (per the #131 silent-FN lesson — do not trust
the agent's self-report). Engine = the existing `classify()`→{safe_literal=SUPPRESS,
safe_doc/code_fence_neutral=DEMOTE-NIT}; A2 reclassifies the 8 to safe_literal where
provably inert, respecting the Signal-0 instruction-loadable guard's INTENT (the 2
SKILL.md cases use a precise prose-`;` discriminator, FN-safe by shape — NOT a blanket).

**Re-dispatch note (rate-limit):** the FIRST opus agent died on a server rate-limit
(~2649s) with NOTHING on disk; its killed-summary captured a sound prose-`;`
discriminator design (RHS-not-command-shaped gate) which I folded into the spec's
"Head-start" section. A FRESH opus agent was re-dispatched (incremental-disk-writes
so a re-limit can't wipe progress). If the fresh agent also dies with zero disk
progress after a ~10-min window, fall back to implementing A2 inline (the spec +
classification are complete). Do NOT wait indefinitely on a dead agent's notification
— check disk (`git diff --stat scripts/_skillaudit_markdown_context.py`, the test
file, the `reports/plugin-fixer/*a2-doc-context*` report).

**A2 VERIFIED + SHIP-PENDING (2026-06-18):** the 2nd opus agent shipped 5 helpers
(`_is_inert_prose_clause_separator` 1/2/6, `_is_inert_backtick_product_name` 3,
`_is_inert_count_quantifier_prose` 4, `_is_inert_rust_async_spawn` 5,
`_is_inert_inline_code_doc_signature` 7/8) + 46 two-sided tests
(`tests/test_a2_doc_context_suppressors.py`). CENTRAL-ADVERSARIAL-VERIFIED:
(a) end-to-end amvcp re-validate (cache off) → **NIT 13→5**, all 8 A2 targets gone,
nothing else moved; (b) my independent probe (`reports_dev/a2_independent_probe.py`)
**20/20** — 8 FP suppressed + 12 malicious siblings stay visible incl. both traps
(`os.system('curl|sh')` in a ```json fence + `function(\`rm -rf\`)`); (c) the
inline-prose `(a+)+` suppression was confirmed **PRE-EXISTING** (baseline stash-vs-fix
identical), not an A2 regression. **MY FIX (case-5 co-located-exec FN hole I caught
reading the diff):** `_is_inert_rust_async_spawn` used a LINE-level
`_RUST_ASYNC_SPAWN_RE.search(line)`, so a real sink-bearing exec co-located with a
`tokio::spawn` on one line (`os.system('curl|sh'); tokio::spawn(x)`) had its
SHELL_EXEC wrongly suppressed — added `_CASE5_COMPETING_EXEC_SINK_RE` decline +
`tests/test_a2_case5_colocation_fn.py` (6 two-sided). ruff+mypy clean; 52 A2 tests
pass; self-validate VALID 0/0/0/0/5W; hashes regen (929). full serial re-run
**9583 pass/2 skip/0 fail**. **SHIPPED v2.131.0** (commit 0dd73be + release 965cf1b,
pushed atomically, GitHub release live, Notify-Marketplace green, CI green ✓ 7m25s). NEXT was B1/B2 + B3/B4/B5.

## ⏵ B-SERIES DECISION (2026-06-18, USER) — detector is WAD; only fix the FIXER

**Claim-verification OVERTURNED the B1/B2 mechanism diagnosis.** Read the REAL
`validate_toc_embedding` (`cpv_validation_common.py:7733`) + `extract_toc_headings`
(7655) + the live amvcp clone: the validator has **NO format recognition** (no
`>`-blockquote vs `-`-list special-case) — it does pure heading-text **substring
matching** (`_heading_matches`, 7875). amvcp's house style is a faithful **condensed**
index per ref link (`> TOC A1.1–A1.13: what-it-does · dual-export · …`, covering all
13 headings abbreviated; each ref carries its own full `## Table of Contents`).
The condensed form can't substring-match the verbatim headings → **0/N**. Systematic:
**25 TOC-embed findings across 12 amvcp skills, 20 are 0/N.** Real catch-22 (≈20 refs ×
≈12 headings embedded verbatim blows the SKILL.md body cap → no clean `--strict`).

**USER DECISION (AskUserQuestion + heartbeat directive):** **"Leave as-is (WAD) — only
fix the fixer."** + "always enforce the rules strictly. any change necessary to pass
the validation are changes of the SCANNED-PLUGIN side. You can help providing improved
fix/devitalizing agents capable of fixing the issues, e.g. simplifying the reference
.md TOC headings or merging them in more broad-scoped chapters." → **B1/B2 detector
change DROPPED.** `validate_toc_embedding` UNCHANGED. CPV stays strict; the FIXER must
change the PLUGIN to comply.

**REFINED B-SERIES (fixer robustness + capability; each maps to a field-report symptom):**
- **B-cycle (ROOT of "crash / exhaust context"):** the oscillation guard compares only
  to the IMMEDIATELY-previous iteration (`N==N-1`; iterative-fix-loop.md 40/62/68/93 +
  plugin-fixer.md 109). The TOC catch-22 is a **2-cycle** (sigA `TOC-MINOR×18` → embed →
  sigB `SIZE-MAJOR+NIT×18` → shrink → sigA again → A,B,A,B…) so consecutive iters always
  DIFFER and `N==N-1` NEVER fires → infinite loop → context exhaustion. FIX: detect a
  repeat vs **ANY prior** iteration (full `seen_signatures` set). FN-safe (a progressing
  loop never revisits an exact prior state; finite finding-space ⇒ pigeonhole guarantees
  termination), rule-compliant (oscillation, NOT a numeric cap). Back it with a real
  testable helper `scripts/cpv_fix_loop_state.py` (record signature → CONVERGED/CYCLE/
  PROGRESS) so termination no longer depends on the agent hand-tracking a set across 20+
  iters. **+ unit tests** (the win-pattern probe applies to THIS, since it IS Python).
- **B-scope/preserve (ROOT of "corrupt state / mangle correct code"):** add to
  plugin-fixer preservation guardrails — (a) STRICT FILE-SCOPE (only edit files named in
  the CURRENT findings), (b) POST-BATCH CONTENT-PRESERVATION invariant
  (`words(SKILL+refs) >= words@batch-start` unless a finding mandates a deletion), (c)
  NEVER delete a contract-satisfying section.
- **B-capability (ROOT of "fail to fix"; the user's explicit ask):** new fix-validation
  reference `toc-embed-catch22-remediation.md` — the prescribed PLUGIN-SIDE fix: when a
  TOC-embed finding can't be satisfied because the full TOC would blow the body cap,
  MERGE the ref file's granular headings into fewer broad-scoped chapters
  (content-preserving: keep all prose, reduce heading granularity, update the ref's own
  TOC + anchors) so the shortened TOC fits → THEN embed it verbatim. Exactly what the
  finding message (7941-7948) + the user prescribe. Route it from plugin-error-index.md +
  index it in fix-validation/SKILL.md.

## ⏵ B-SERIES EXTENDED (2026-06-18, USER) — loop-until-PASS for ALL three loops

Two refining directives (mid-implementation):
1. **"the agent fixer must fix and loop/iterate until the validation passes."** → the
   CYCLE verdict is NOT "give up". It means the CURRENT fix is futile (oscillating);
   the fixer must SWITCH to the deeper plugin-side remediation that breaks the root
   tension (e.g. the TOC catch-22: stop re-embedding the verbatim TOC — apply Fix B,
   MERGE the ref headings so the TOC both fits AND stays under the body cap, resolving
   the MINOR↔MAJOR tension at once) and KEEP LOOPING. `[BLOCKED]` only as the LAST
   resort — when even the alternative remediation re-cycles (genuinely no plugin-side
   fix breaks it → human/CPV decision).
2. **"the publish agent or the upgrade agent must loop/iterate until the ci/cd on
   github passes without failures."** → the publish flow (publish.py + `gh run watch`)
   and the upgrade/migration flow must LOOP: publish → watch CI → on red, READ the
   failing job, FIX the cause, re-publish, re-watch → until GREEN. Same oscillation
   guard (`cpv_fix_loop_state.py`, signature = the set of failing CI jobs/steps) so a
   non-landing CI fix halts instead of looping forever. Currently plugin-fixer §7d
   returns `[PARTIAL]` on first CI red — that becomes a loop-until-green.

`cpv_fix_loop_state.py` is the SHARED guard for all three loops (validation findings,
blocking-warnings, CI-job failures — each is "a failure-set that must shrink to zero;
detect oscillation vs any prior iteration"). Touch: publish flow lives in
`agents/plugin-fixer.md` §7d + `skills/canonical-pipeline` + the `cpv-upgrade-plugin`
command/upgrade path — apply the loop-until-CI-green discipline there too.

**FILES:** `scripts/cpv_fix_loop_state.py` (NEW + tests — DONE, 26 tests green), `skills/fix-validation/references/iterative-fix-loop.md`,
`agents/plugin-fixer.md`, `skills/fix-validation/references/toc-embed-catch22-remediation.md` (NEW),
`skills/fix-validation/SKILL.md`, `skills/fix-validation/references/plugin-error-index.md`. Lockstep:
update any test that pins plugin-fixer.md / iterative-fix-loop.md strings (e.g. agent-preflight
content tests). **VERIFY:** unit tests for cpv_fix_loop_state.py + full serial 9583+ +
self-validate `--strict` VALID + hashes regen. **AUTHORING NOTE:** the prose half
(agent/skill markdown) is load-bearing + deeply context-coupled (no executable probe) →
author inline + careful two-perspective read-through; the Python half (loop-state helper)
IS probe-verifiable. amvcp stays READ-ONLY (validate via /tmp clone only).

## ⏵ ARCHITECTURE DIRECTIVE (2026-06-18, USER) — loop is a BEHAVIOUR → agent, not skill

"the fix loop is a behaviour, not a skill. it belongs to the agents prompts, not to
the skills. The agents must load the skills needed to fix the issues reported by the
validation scan, and they can load only the skills from the-skills-menu that are
needed to fix the issues found … the loop/iterative logic is a behaviour and then it
belongs to the agents not to the skills." → The loop CONTROL FLOW (validate→fix→
re-validate, oscillation detection, loop-until-PASS, CYCLE→switch-strategy,
publish-until-CI-green) is OWNED BY THE AGENT PROMPTS (`agents/plugin-fixer.md`,
`agents/marketplace-fixer.md`), self-contained — the agent runs the loop from its own
prompt and loads fix-recipe skills ON DEMAND per finding. The skill ref
`iterative-fix-loop.md` is DEMOTED to the SUPPORTING DATA the loop consults
(WARNING-blocking categories, advisory list, migration 7c/7d step details, output
contract) — the behavioral pseudocode I added there is being MOVED to the agent.

**TEST CONSTRAINTS (lockstep, MUST stay green):**
- `test_migration_agent_contract.py::test_iterative_fix_loop_describes_migration_extra_steps`
  — iterative-fix-loop.md MUST still contain `run_all_checks|82-check|Pre-completion verification` (keep 7c/7d as reference data).
- `test_batch_fix_v291.py::TestNoHardcodedIterationCaps` — scans plugin-fixer.md + marketplace-fixer.md + iterative-fix-loop.md; NO `max N iter` / `capped at N min` (oscillation-only). My prose complies.
- `test_audit_fix_b17.py` — pins an oscillation-termination phrase (read before editing).

**REMAINING STEPS (crash-safe checklist):**
1. ✅ DONE — plugin-fixer.md `## The loop` is SELF-CONTAINED: heading "this agent OWNS
   the loop behaviour"; reset+record cpv_fix_loop_state.py wired into step 1; step 7d =
   publish-until-CI-green LOOP (read failing job → fix cause → re-publish → re-watch, 2nd
   state file, [PARTIAL] only on CI-set oscillation); closing line reframes
   iterative-fix-loop.md as "supporting DATA, loaded only if needed".
2. ✅ DONE — plugin-fixer.md guardrails: intro now "Four"; **G3 strict file-scope** (edit
   only files named in CURRENT findings; un-named file only if it's the prescribed
   remediation for a named finding) + **G4 content-preservation** (`git diff --numstat`;
   words(SKILL+refs) >= pre-batch; never delete a contract-satisfying section) ADDED.
3. ✅ DONE — iterative-fix-loop.md: the `while True:` Algorithm pseudocode + the
   publish-until-green pseudocode block were REMOVED and reframed as "supporting DATA;
   control flow owned by the agent". KEPT migration 7c/7d + warning tables + output
   contract (test-required); "Algorithm" heading kept verbatim (it is a SKILL.md TOC entry).
4. ✅ DONE — marketplace-fixer.md: full-history oscillation via cpv_fix_loop_state.py
   (reset+record wired into the bash block), self-contained loop, publish-until-CI-green;
   test-pinned strings ("NO hardcoded iteration cap", "oscillates…[BLOCKED]") preserved.
5. ✅ DONE — skill-fixes.md §8: Fix-B-merge is MANDATORY (content-preserving) over DROP;
   Fix-A `·`-list must be VERBATIM (cited the exact amvcp bug `what-it-does`≠`A1.1 What it
   does`); corrected the search window "~50"→"~100 lines".
6. ✅ DONE — test_fixer_loop_behaviour.py (12 architecture-lock tests) + the 3 lockstep
   tests (test_migration_agent_contract / test_batch_fix_v291 / test_audit_fix_b17) green.
7. ✅ DONE (verify) — FULL SERIAL **9621 pass / 2 skip / 0 fail** (baseline 9583 + 26
   loop-state + 12 loop-behaviour). First `publish.py --minor` BLOCKED at Gate 2 (parallel
   `-n auto`) on a PRE-EXISTING scan-cache flake → fixed (see §"Publish-gate finding" below);
   re-verified **9630 pass / 2 skip / 0 fail** under the exact xdist gate. Re-shipping:
   regen hashes → self-validate --strict VALID → publish.py --minor (→ v2.132.0) → CI green
   → comment #132 self-id'd (keep OPEN).

## Publish-gate finding: scan-cache wipe-on-lock (destructive concurrency bug, FIXED)

`publish.py` Gate 2 runs the suite under `pytest -n auto --dist=worksteal`. Under that
14-worker parallel load `tests/test_cpv_scan_cache.py::test_concurrent_writers_dont_corrupt`
failed ("Lost entry for t0-i0") — it passed serially (the 9621 run). ROOT CAUSE (read, not
guessed): `cpv_scan_cache.put`/`get` caught **any** `sqlite3.DatabaseError` from
`_open_connection`/the query and called `_wipe_and_recreate`, which **unlinks the cache file**.
But `OperationalError("database is locked")` IS a `DatabaseError` — so a *transient* lock
(the barrier-synchronised first-creation race; the immediate-`SQLITE_BUSY` lock-upgrade /
lost-WAL-switch bypass) WIPED the whole cache, destroying entries other writers/readers had
already committed. A best-effort cache must skip ONE op, never nuke everything. FIX
(`scripts/cpv_scan_cache.py`): classify `_is_transient_lock` vs `_is_corruption` — wipe ONLY
on genuine corruption (`malformed`/`not a database`/…); a transient lock is a best-effort
skip (put) / MISS (get), NEVER a wipe. Plus a BOUNDED `_retry_on_lock` (12×50 ms) around the
schema DDL, the WAL switch, the INSERT and the SELECT so a contended op LANDS instead of
dropping, + an explicit `PRAGMA busy_timeout`. Two-sided proof: 9 new tests in
`test_cpv_scan_cache.py` — transient lock → cache NOT wiped + committed entry survives
(deterministic, monkeypatched), corruption → STILL wipes (recovery preserved), `_retry_on_lock`
retries-then-succeeds / re-raises-non-lock-immediately / re-raises-after-bounded-budget. Stress:
24/24 green under 6× CPU load; full xdist suite **9630 pass**. ruff+mypy clean. Also corrected
3 stale docstrings (the false "BEGIN IMMEDIATE" claim) to the real autocommit+busy_timeout+
retry mechanism. Ships in v2.132.0 alongside the B-series.

## Verified findings (amvcp@4d96866)

| Item | Count | Finding | Verdict | Owner |
|---|---|---|---|---|
| A1 | 10 MINOR | `htmlhint: Config loaded: .htmlhintrc` | REAL FP — banner dumped as findings on non-zero exit (`cpv_lint_engine.py:1535`) | CPV detector |
| A2 | 8 NIT | skillaudit on doc examples (SHELL_EXEC/CMD_INJECTION/REGEX_DOS/INSECURE_CRYPTO/OBFUSCATION) in `references/*.md` + 2 in SKILL.md bodies; "demoted, needs review" | likely FP — demoted-NIT blocks `--strict`, no plugin-side fix | CPV detector + policy |
| B1/B2 | ~21 MINOR | TOC-embed (`N/M headings embedded`); 18 show `0/N` on `amvcp-code-syntax/SKILL.md` | REAL — embed format not recognized (B2) + catch-22 (B1) | CPV detector + fixer |
| A4 | 2 NIT | EXFIL_COVERT on `amvcp-runtime.js:2538/:2545` (sendBeacon/fetch to a VARIABLE endpoint) | mostly WAD — can't prove the variable local; conservative demote defensible; escape = audit-consent sentinel | CPV (minor) |
| A3 | 1 MINOR | REGEX_DOS on `new RegExp('(?:'+alt.join('\|')+')')` | defensible-conservative, NOT a clear FP (can't statically prove the joined alternation safe; catastrophic `(x+)+` sibling fires identically) | CPV (low) |

**NOT CPV's (do not touch):** 4 mypy "Returning Any" MINORs, 4 own-quality
WARNINGs (no-checklist, dead URL 404, no dep-installer hook), 8
RC-PIPELINE-DRIFT WARNINGs (non-blocking; the ci/release/notify ones correctly
show the v2.128.0 "AHEAD of canon — do NOT downgrade" message).

## Plan (priority order; each FN-safe two-sided, central-verified, shipped)

1. **A1 — htmlhint banner strip** (`cpv_lint_engine._lint_html`): filter
   `Config loaded:` + `Scanned N file…` summary lines BEFORE converting stdout
   to findings, then take the first 20 REAL lines (so genuine error lines aren't
   crowded out by banners). FN-safe: real htmlhint errors still become MINORs.
   Clears 10 MINOR. Zero judgment — do first. (#132)
2. **A2 — doc-context suppressors** for the 8 remaining doc-example shapes
   (SHELL_EXEC/CMD_INJECTION/REGEX_DOS/INSECURE_CRYPTO/OBFUSCATION in
   `references/*.md`; the 2 CMD_INJECTION in SKILL.md bodies need per-line
   inspection — instruction-loadable surface, so verify they are DISPLAYED not
   INSTRUCTED). Same pattern as #76/#83: suppress provably-inert displayed code
   to `safe_literal` (full, non-blocking), NOT a policy relax. Two-sided: the
   doc example clears AND a real executable of the same rule still fires.
3. **B1/B2 — TOC contract**: make the embed counter accept the nested-`-`-list
   form (not only the `>`-blockquote-with-`·` form), DOCUMENT the accepted
   format, and make the contract converge (treat the two halves — ref-TOC
   section + SKILL.md embed — as ONE atomic fix) instead of a catch-22.
   Detector (`validate_toc_embedding`) + fixer-agent prompt.
4. **B3/B4/B5 — plugin-fixer hardening**: post-move invariant (destination ref
   non-empty AND `words(SKILL+refs) >= words@HEAD`); strict file-scope (only the
   files named in findings); NEVER delete a contract-satisfying section;
   exclusive-tree / owned-worktree isolation for long runs.
5. **A3/A4 — small/defensible**: A4 document the audit-consent-sentinel escape;
   A3 leave conservative unless a safe linearity check (flat alternation, no
   trailing quantifier) is clearly FN-safe.

## Decomposition (delegate to opus agents; disjoint files)

- **W1 (A1)** — `cpv_lint_engine.py` `_lint_html` + a test. Small; inline or 1 agent.
- **W2 (A2)** — `_skillaudit_markdown_context.py` (+ the SKILL.md-body cases) doc-context discriminators + tests. The big detection-accuracy piece.
- **W3 (B1/B2)** — `cpv_validation_common.py` `validate_toc_embedding` (accept both embed shapes; converge) + the fixer/skill prompt docs + tests.
- **W4 (B3/B4/B5)** — `agents/plugin-fixer.md` + `skills/fix-validation` + `skills/canonical-pipeline` (post-move invariant, strict scope, no contract-satisfying deletion, worktree isolation). Prompt/logic hardening + any verifier-script support.

Each: I write the precise spec, delegate to a `model: opus` agent, then
central-adversarial-verify (own probe + full serial suite + self-validate
`--strict`) before shipping. Ship incrementally (A1 first).

## Notes / gotchas

- Verification fixtures must use a REAL external domain, not `example.com`
  (RFC 2606 reserved → scanner correctly ignores it; gave me a false FN alarm).
- A2's 2 SKILL.md-body CMD_INJECTION findings are on an instruction-loadable
  surface — do NOT blanket-suppress; confirm they are displayed code, else they
  stay (a SKILL.md that INSTRUCTS an agent to run a command is not a doc example).
- amvcp is ANOTHER project — never edit it; only validate (read-only).
