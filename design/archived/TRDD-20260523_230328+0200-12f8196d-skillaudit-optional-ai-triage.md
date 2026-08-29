---
trdd-id: 12f8196d-3482-486d-91ef-d809faeab747
title: Optional cheap-model AI triage for SkillAudit residual findings
column: complete
blocked-by: []
created: 2026-05-23T23:03:28+0200
updated: 2026-08-29T23:06:20+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-12f8196d — Optional AI triage for SkillAudit residuals

**Filename:** `design/tasks/TRDD-20260523_230328+0200-12f8196d-skillaudit-optional-ai-triage.md`
**Tracked in:** this repo (`design/tasks/` is git-tracked)

## Blocker CLEARED — 2026-08-29 (was: llm-externalizer-plugin#6)

The blocking FR **Emasoft/llm-externalizer-plugin#6** is **CLOSED (2026-05-24)**
and `llm-ext scan security` ships. Verified empirically this session at $0 (the
tool routed through a free model), not read off the issue title:

- `--targets` takes a JSON array; item `{id, category, file_path, line, context_lines}` parses.
- `--budget_usd` is a hard pre-flight gate; `--category_rubrics` places per-category
  rubrics in the SYSTEM prompt (snippet content can never alter them).
- Per-item verdict `threat | not_threat | uncertain` + confidence + reason +
  `injection_observed`; the `id` round-trips, so it is the join key back to the finding.
- Dedup, windowing and over-size skipping happen server-side (`deduped=`, `skipped_too_big=`).
- **Exit code is 0 EVEN WHEN THE JOB IS REFUSED** — the success signal is the
  `budget_gate=` / `job_id=` line on stdout, never `$?`. Any consumer that reads the
  exit code will report a refused scan as a clean one.
  *Provenance note:* this was FIRST cited from a run piped into `tail`, where `$?` is
  `tail`'s status and the tool's own is unmeasured — the exact proxy-read this claim
  warns about, committed while establishing it. Re-measured unpiped
  (`llm-ext … > file 2>&1; echo $?`) → `REFUSAL_EXIT=0` alongside
  `budget_gate=refused`. The claim holds; the first measurement did not establish it.

**Verified delta — 5 of the FR's 6 stages shipped; stage 3 did NOT.** Confirmed by the
llm-externalizer maintainer session reading its own source, not inferred from the help
text: glob/windowing, dedup+redaction, budget gating, per-snippet adjudication with
category prompts, and parsing/aggregation/export are all in-tool. **Bin-packing /
bucketing by category is NOT PRESENT** — `judge.ts` maps over dedup groups and calls the
judge once per group, so K groups always cost K calls. That is concurrency, not packing.

That delta does NOT re-block this card. The FR's load-bearing requirement was *the caller
does not orchestrate* — CPV makes ONE call and receives counts plus a report path, so the
context cost is the request JSON plus a path, which is satisfied. Packing was a
cost-optimisation inside llm-ext, is invisible to this caller, and belongs to that repo.
If per-call cost later matters, raise it there rather than re-opening this card.

Two behaviours worth knowing for future work here: the judge runs a prompt-injection
pre-scan per group, and a consecutive-failure circuit breaker switches remaining groups
to fail-safe verdicts rather than aborting — so a degraded run yields `uncertain`, never a
silent `not_threat`.

The remaining work is the CPV-side caller.

## Implementation status — 2026-08-29 — WITH A DELIBERATE DEVIATION FROM §Design

Shipped: `scripts/cpv_ai_triage.py`, `check_ai_triage` as Check 29 in
`validate_security.py` (immediately after the Check-27 SkillAudit block), and
`tests/test_cpv_ai_triage.py`. Opt-in via `CPV_AI_TRIAGE_BUDGET_USD` — one env var that
both enables the step AND sets the hard budget, so it is structurally impossible to run
it without naming a spend ceiling.

**DEVIATION — the triage does NOT demote anything. Acceptance criterion 2 is NOT met as
written, on purpose.** §Design says the AI pass "may only ever DEMOTE/suppress a finding
it judges `not_threat` with high confidence". As built, `report_verdicts` calls
`report.info(...)` and nothing else: it cannot remove, downgrade or suppress any
SkillAudit finding. The verdict is attached as advisory context beside the finding, which
keeps its original severity.

Why: the standing project rule is *never suppress a rule, never relax `--strict`; the only
auto-clear is provably-inert data*. A remote model's `not_threat` is a judgement, not a
proof of inertness, and wiring it to demote would make an external service's opinion able
to clear CPV's own gate — including on a plugin that is itself the attacker. The
confidence threshold does not fix that; it only sets the price of getting past it.

The card's design predates that rule hardening. **Do not "finish" criterion 2 by adding
demotion without an explicit user ruling** — that is the security-relevant half of this
card and it is deliberately unbuilt. If demotion is ever wanted, it needs its own decision
recorded here, and the safer shape is a separate opt-in that only ever demotes to a
VISIBLE lower tier, never to silence.

Criteria 1, 3, 4, 5 are met: the tool exists; the default run is unchanged (no env var ⇒
no subprocess is spawned, pinned by a test); every error / timeout / refusal / unparseable
path returns "skipped, with the reason" and leaves findings visible; and CPV spends only
the request JSON plus a returned report path.

## Criterion-2 ruling — 2026-08-29 — the demotion path MUST NEVER BE BUILT

**Ruled: acceptance criterion 2 is WRONG and is amended; the implementation's deviation is
correct.** This is NOT unbuilt work deferred to a later release. A future reader who finds a
struck-through acceptance box and re-derives "the demotion is still owed" would be rebuilding
a hole this card deliberately closed — hence the reason is recorded here and not merely deleted.

**1 — The measurement that settles it without appealing to any policy.** This card's own
§Blocker-cleared section records, from a live run: *"Exit code is 0 EVEN WHEN THE JOB IS
REFUSED — the success signal is the `budget_gate=` / `job_id=` line on stdout, never `$?`."*
So a refused scan and a clean scan are indistinguishable by exit code. Wiring that to severity
demotion does not merely add risk: it converts an INFRASTRUCTURE FAILURE into a SILENT FALSE
NEGATIVE, which is the one failure mode a security gate must never have. The same section also
records that llm-ext's circuit breaker degrades to `uncertain` verdicts rather than aborting —
so a degraded run is a run whose verdicts are worth strictly less, arriving through the same
channel as a healthy one.

**2 — The standing USER directive it would violate** (2026-06-21, verbatim): *"never suppress!
and never relax the push gate! but you must tell the user to edit their plugin and make those
dangerous code become passive, never executed. if there is the slightest possibility that that
code can be executed and not just compared to a string, then you must not let it pass."* Its
"How to apply" is explicit: **do NOT add suppression mechanisms — no allowlists, no blanket
context demotions — for execution-class findings.** A directive outranks a TRDD acceptance
criterion: the criterion is downstream of it and gets amended, not honoured.

**3 — An LLM verdict is not the one thing CPV may auto-clear.** The only sanctioned auto-clear
in this project is PROVABLY-INERT data (a pattern string in a rule table that never reaches a
sink) or a non-instruction-loadable context. A cheap remote model's probabilistic opinion is
neither.

**Consequence:** `report_verdicts` calls `report.info(...)` and nothing else, by design and
permanently. The step's status vocabulary is RAN/SKIPPED only — never FAILED — because a triage
that could not run is advisory context withheld, not a validation failure. Criteria 1, 3, 4 and
5 are met as written (verified: 13/13 `test_cpv_ai_triage.py` + 85/85 `test_validate_security.py`
green with the env var unset; `run_ai_triage` returns `invoked=False`, reason
`"CPV_AI_TRIAGE_BUDGET_USD not set (opt-in)"`). Consulted `ai-maestro-d7`, which concurred.

Card moves to `complete`.

*Column note:* a review subagent moved this to `testing` at 15:34 and did not reconcile the
body, leaving the card asserting two different states at once. Restored to `dev`, because
`testing` claims the implementation is complete and awaiting verification, which is false
while a named acceptance criterion is deliberately unbuilt. `dev → testing` is a legitimate
mechanical transition, so this is a correctness reconciliation and not a governance
objection — but the column must not overstate readiness ahead of the body.

## Problem

The static SkillAudit (TRDD-b13fbdd6, v2.105.0) drives FPs down 95%+ with
Python/AST certainty, but a small residual genuinely **cannot be certified
statically** — it needs semantic judgement:

- INSECURE_CRYPTO: SHA1/MD5 as a security primitive vs a non-security
  fingerprint/cache-key.
- TOOL_SHADOW: `override.*tool` greedy span across unrelated identifiers.
- SSRF_ADVANCED: `urlopen(url)` where `url`'s provenance isn't local.
- ENV_INJECTION in shell-script help text (no shell-file classifier).
- RESOURCE_ABUSE / INTENT in README/SKILL.md prose (borderline).

## Design

Consumes the dedicated **`security_scan`** tool (FR #6 consolidated spec).
CPV makes **ONE call** — all prep (glob resolution, windowing, dedup,
redaction, bin-packing, bucketing, budget gating) AND post-processing
(parsing, aggregation, export) happen INSIDE llm-externalizer. CPV spends
only the request JSON + the returned report-path tokens. Volume is NOT a
constraint (thousands of items / whole fleet are fine).

1. **Heuristics (done)** — eliminate the certain FPs in the context
   classifiers. Already shipped (v2.105.0).
2. **Categorise the residuals** — every finding that survives heuristics but
   is NOT certifiable becomes a `security_scan` target: a `category`
   (command_injection, ssrf, insecure_crypto, cross_tool_access, obfuscation,
   privilege_escalation, path_traversal, env_injection, prompt_injection,
   data_exfil, …) + the minimal snippet OR `file_path`+`line`+`context_lines`.
   (CPV may even hand `path_glob` targets and let llm-externalizer resolve +
   window them — maximum delegation.)
3. **One `security_scan` call** — pass `targets[]` + `category_rubrics` +
   `budget_usd` + `profile` + `default_verdict_on_error: uncertain`. Read back
   the report path; consume the per-item verdicts. Demote only high-confidence
   `not_threat`.

### Hard invariants (user mandate)

- **Opt-in only.** Default OFF. A flag (e.g. `--ai-triage`) or a
  `cpv.ai_triage` config enables it. Never on by default.
- **Maximum delegation.** CPV NEVER does file-prep or post-processing in its
  own tokens — `security_scan` does globbing, windowing, dedup, redaction,
  packing, budget, parsing, aggregation, export. CPV issues one call and
  reads one report path.
- **Cheap models + budget cap.** `profile: free` / local / cheap-ensemble;
  `budget_usd` bounds spend even on huge batches.
- **Fail-safe to VISIBLE.** `uncertain`, model-unconfigured, or any error ⇒
  the finding STAYS VISIBLE (its pre-triage severity). The AI pass may only
  ever DEMOTE/suppress a finding it judges `not_threat` with high confidence;
  it must NEVER escalate a clean result into a block, and must NEVER silently
  drop on error. Preserves the strict gate's integrity.
- **Heuristics-first for precision, not cost.** Heuristics still run first so
  the model only adjudicates genuinely ambiguous cases (better signal), but
  there is no hard "keep it tiny" cap — volume is `security_scan`'s job.

### Per-category rubric authoring

CPV owns the `category_rubrics` text. Each: a one-paragraph "THREAT when X /
NOT_THREAT when Y / UNCERTAIN otherwise" rubric specific to that rule's
real-world benign-vs-malicious split. These live in `scripts/rules/` (the
only wheel-shipped data dir, per the catalog-location rule).

## Acceptance (when unblocked)

1. The dedicated `security_scan` tool (FR #6) is available.
2. ~~A new `--ai-triage` opt-in path builds the `targets[]` + `category_rubrics`
   request, issues ONE `security_scan` call, reads the report path, and
   demotes only high-confidence `not_threat` items.~~
   **AMENDED 2026-08-29 — the demotion clause MUST NEVER BE BUILT.** See
   §"Criterion-2 ruling" below. The criterion as satisfied: an opt-in path
   (`CPV_AI_TRIAGE_BUDGET_USD`) builds the request, issues ONE `security_scan`
   call, reads the report path, and **attaches the verdicts as advisory INFO
   beside each finding, never altering severity and never suppressing anything.**
3. Default run (no flag) is byte-for-byte unchanged.
4. Error / no-model / `uncertain` ⇒ finding visible (regression test with a
   deliberately-vulnerable fixture that the model is NOT consulted on still
   blocks).
5. Tokens spent by CPV for an N-item triage ≈ the request JSON + the
   report-path string only, regardless of N.

## Files (when unblocked)

- `scripts/rules/ai_triage_prompts.json` (NEW) — per-category `category_rubrics`
  text CPV passes to `security_scan`.
- `scripts/validate_security.py` — optional triage stage after the heuristic
  classifier, gated on the opt-in flag; builds the request, issues one
  `security_scan` call, consumes the report.
- `scripts/cpv_skillaudit_native.py` — emit `category` + snippet/window on
  residual (non-suppressed, non-certified) findings so they become
  `security_scan` targets.
- tests — opt-in on/off, fail-safe-to-visible, request shape, budget cap.

## Approval log

- 2026-08-29T23:06:20+0200 — COMPLETED. Criterion 2's demotion clause ruled MUST-NEVER-BE-BUILT
  and amended (see the ruling section); criteria 1/3/4/5 verified green. Authorized by the USER
  directive of this session ("complete all pending tasks and TRDDs. decide by yourself. base your
  decisions on verified facts and tests"). Peer `ai-maestro-d7` consulted and concurred. The
  fable-advisor was UNAVAILABLE (`agentlenspro model-headroom fable` = exhausted, 100% of its
  weekly window) — no advisor verdict was obtained for this ruling.
- 2026-08-29T23:20:00+0200 — CORRECTION to the entry above, from an adversarial review of the
  closing turn. Two claims were overstated and are restated here rather than left standing:
  1. **Criterion 3 is NOT met "as written".** It says the default run is "byte-for-byte
     unchanged", and this change adds a `_record_step(29, …, "SKIPPED")` row to every default
     run — so byte-identity is provably false. 85 passing tests prove only that no test asserts
     on step count; a passing test is not a byte measurement. Criterion 3 is met in the AMENDED
     sense: **the verdict, the findings and their severities are unchanged, plus one SKIPPED
     step row** — the same shape Check 28 (Snyk, also opt-in and token-gated) has shipped for
     releases. That is a house-style precedent, not a byte-identity claim, and it is recorded
     as such.
  2. **The "cannot demote" claim was first asserted from a grep, and has since been settled
     properly.** The whole 365-line `scripts/cpv_ai_triage.py` was read: `report_verdicts`
     calls `report.info(...)` twice and nothing else — no `.level=`/`.severity=` write, no
     `.remove()`/`.pop()`/`del`, no `setattr`, no rebind of the report's results. The claim
     holds, and is stronger than stated: lines 347-356 append an explicit "an LLM not_threat
     verdict is NOT grounds to suppress, downgrade or close this finding" warning to every
     `not_threat` line, because the fixer agent resolves findings mechanically from the report
     and would otherwise turn a model opinion into a suppression instruction by workflow.
  Also corrected: the session's "0 TRDDs in LOCAL and USER scope" was measured with a `find`
  whose `2>/dev/null` hid the only signal separating a clean scope from a non-existent path.
  Re-measured: neither scope root's `design/` directory exists, so the conclusion stands, but
  on the directory-absence evidence rather than on an empty result.
- 2026-08-29T23:32:00+0200 — THE SYMMETRIC RISK, measured. A second review observed that every
  claim above concerns DEMOTION, and that the opposite direction had never been checked: if
  `INFO` were a blocking tier, then merely SETTING `CPV_AI_TRIAGE_BUDGET_USD` would flip a clean
  plugin to INVALID — a gate change cleared on the unexamined assumption that the module's own
  output tier is inert. The 85 green tests could not have caught it: they all run the opt-OUT
  path, where `run_ai_triage` returns `_skipped()` and `report_verdicts` emits nothing, so they
  say nothing about the invoked path.
  Settled by reading the predicate rather than the prose: `ValidationReport.exit_code`
  (`cpv_validation_common.py:5989`) branches on CRITICAL, then MAJOR, then MINOR, else
  `EXIT_OK`; `exit_code_strict` (:6004) adds only NIT. **INFO appears in no blocking branch in
  either mode**, and `score` (:6017) documents that WARNING, INFO and PASSED do not affect it.
  So the triage is verdict-neutral in BOTH directions — it cannot demote (it emits INFO only)
  and it cannot promote (INFO can never change the exit code). That is stronger than the
  original criterion-2 ruling claimed, and it is now a measured fact rather than an assumption.
  *Doc note, re-measured after being overstated once:* `CLAUDE.md` carries the phrase "the ONLY
  non-blocking tier" EXACTLY ONCE (line 390, the v3.18.0 paragraph), not in "several paragraphs"
  as first written here. In its own context it is making a narrower and correct point — that an
  ADVISORY finding should be WARNING rather than MINOR/NIT — so it is not wrong so much as
  quotable out of context. The code above is authoritative either way.
- 2026-08-29T23:41:00+0200 — THE PREDICATE IS NOW EXERCISED, NOT JUST READ. The entry above
  reached the right answer by reading `exit_code`/`exit_code_strict` and inferring three things
  it never checked: that `has_critical`/`has_major`/`has_minor` test their own levels (assumed
  by analogy with the one sibling actually read), that those are the predicates the validator's
  exit status really comes from (the call graph was never traced), and that `report.info()`
  writes level INFO (inferred from the method NAME). A derived conclusion was being recorded as
  a measured one, in a frozen card, about a security gate.
  Replaced with an executable check that exercises all three at once through the real objects —
  `tests/test_cpv_ai_triage.py::test_invoked_triage_cannot_change_the_verdict`: build a real
  `ValidationReport`, call `report_verdicts` with `invoked=True` and two verdicts (one
  `not_threat`, one `threat`), then assert the emitted levels are exactly `{"INFO"}` and that
  `exit_code`/`exit_code_strict` are byte-identical to the pre-call values. **Measured: 14/14
  green; levels `{'INFO'}`; `(0, 0)` before and after.** Non-vacuity proven separately — a
  planted MAJOR moves the same report to `exit_code 2 / strict 2`, so the assertion can fail.
  The invoked path had NO test before this: every other test in that file runs the opt-OUT path,
  where `report_verdicts` emits nothing and therefore proves nothing about the verdict.
- 2026-08-29T23:52:00+0200 — THE UNIT TEST ABOVE DID NOT COVER THE CALLER, and the gap was
  exactly where a future change would land. It drives `report_verdicts` directly, but the
  production caller is `check_ai_triage` — and that is the more natural place for someone to
  later add `if v.injection_observed: report.major(...)`. Such a change leaves the unit test
  GREEN while the gate has moved, i.e. the test did not guard the regression it named. Worse,
  the fixture already hands that trigger over (`injection_observed=True`).
  Two more tests close it, both through the REAL caller:
  - `test_check_ai_triage_is_verdict_neutral_through_the_real_caller` — drives
    `validate_security.check_ai_triage` against a report that ALREADY CARRIES A MAJOR, because
    an empty fixture cannot show a demotion (there is nothing there to demote). One assertion
    then covers both directions. **Measured: `before (2,2) after (2,2)`, levels
    `['INFO','MAJOR']`** — the pre-existing MAJOR survives (no demotion) and nothing escalates
    (no promotion).
  - `test_a_triage_that_ran_is_not_recorded_as_a_failure` — pins the RAN/SKIPPED status
    vocabulary: a NON-INVOKED result reports SKIPPED, never FAILED. That contract lived only in
    a docstring, which is not a thing that fails when broken. (Scope stated precisely because an
    earlier draft of this entry said "a TIMEOUT reports SKIPPED", which implies the real
    `subprocess.TimeoutExpired` path was exercised. It was not — the test injects a
    `TriageResult(invoked=False)`; that path's own mapping is covered by the parsing tests.)
  **Both MUTATION-PROVEN rather than merely green:** injecting the escalation into the caller
  moves the verdict `(2,2) → (1,1)` with a CRITICAL appearing, and forcing the step status to
  FAILED is detected — so each test demonstrably fails for the reason it exists. 16/16 green,
  ruff clean.
- 2026-08-30T00:07:00+0200 — A DEFECT I SHIPPED IN THE GUARD ITSELF, and the single-file run is
  what hid it. The status test above mutated the module-global `validate_security._scan_step_log`
  in place with `.clear()`, twice, and never restored it. That pollutes in BOTH directions —
  destroying a log a previous test populated, and leaving a stale step-29 row for a later one —
  and a `-p no:xdist` single-file run is precisely the run that cannot observe it.
  Fixed with `monkeypatch.setattr(vs, "_scan_step_log", [])`, which swaps the binding and RESTORES
  the original on teardown. Verified by running the file together with all three consumers in ONE
  process: **109 passed**.
  **CORRECTION, same day — an earlier draft of this entry called the pollution "not theoretical".
  That was asserted from grep hits and is WRONG.** All three consumers are immune by construction,
  established by reading the call sites rather than by running anything:
  - `test_validate_security.py:1218` calls `validate_security()`, which calls
    `_reset_scan_step_log()` at its top — it populates its own log before asserting.
    **Verified on the CALL, not the docstring:** `validate_security` is defined at
    `scripts/validate_security.py:10239` and the `_reset_scan_step_log()` call is at
    **:10300**. The function's own docstring asserts that call, and a docstring asserting
    behaviour is exactly what this repo has been bitten by twice (CLAUDE.md v5.1.2, v5.8.0) —
    so it is cited by line here, not taken on the comment's word.
  - `test_security_parallelization.py:319` goes through the same reset path — its
    `run_validate_security` is an ALIAS, `validate_security as run_validate_security` at
    that file's line 41, not a local helper — and additionally filters to steps 22-25, so a
    step-29 row could never have matched.
  - `test_issues_213_216_scan_and_tag_honesty.py:39` passes an explicit list literal to
    `format_scan_step_table([...])` and never reads the global at all.
  So no collision was demonstrable, and the 109-green run proved less than it appeared to: a
  post-fix green run contains no failing state, so it cannot distinguish "the pollution was real
  and is fixed" from "nothing ever collided". **The fix is still correct** — an unrestored
  mutation of a module global is a real hazard shape, and the immunity above is incidental
  (it depends on every present consumer happening to reset first), not guaranteed for the next
  one. But the severity was overstated, in a frozen card, from a grep.
  Also corrected: `_reset_scan_step_log` REBINDS (`global _scan_step_log; _scan_step_log = []`),
  it does not mutate in place — so it is the right idiom for production, where
  `validate_security()` calls it at the top of a scan. It is still the WRONG tool for a test,
  because it never restores; `monkeypatch` is right precisely because it does.
  *The lesson, for whoever reads this next:* a green single-file run is not an isolation check —
  it is the one run shaped so that it cannot fail for the reason you need it to. And its mirror
  image, learned one turn later: a green run after a fix is not evidence the bug was ever there.
  Reading the three call sites answered in seconds what a five-minute re-run would only have
  hinted at, because it explains WHY the run is green instead of merely observing that it is.
