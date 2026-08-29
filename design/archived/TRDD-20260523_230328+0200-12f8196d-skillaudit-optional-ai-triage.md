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
