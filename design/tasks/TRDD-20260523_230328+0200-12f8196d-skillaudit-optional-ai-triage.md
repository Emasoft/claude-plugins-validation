---
trdd-id: 12f8196d-3482-486d-91ef-d809faeab747
title: Optional cheap-model AI triage for SkillAudit residual findings
status: not-started
created: 2026-05-23T23:03:28+0200
updated: 2026-05-23T23:09:02+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-12f8196d — Optional AI triage for SkillAudit residuals

**Filename:** `design/tasks/TRDD-20260523_230328+0200-12f8196d-skillaudit-optional-ai-triage.md`
**Tracked in:** this repo (`design/tasks/` is git-tracked)

## Blocked on (external)

This work depends on the **llm-externalizer** feature request
**Emasoft/llm-externalizer-plugin#6** — the dedicated **`security_scan`**
tool (consolidated spec in that issue: one call, all prep + post-processing
delegated to llm-externalizer). Do NOT start implementation until that tool
ships. (Not a `blocked-by:` frontmatter entry because that field takes TRDD
UIDs, not external issues — the blocker is the GitHub FR above.)

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
2. A new `--ai-triage` opt-in path builds the `targets[]` + `category_rubrics`
   request, issues ONE `security_scan` call, reads the report path, and
   demotes only high-confidence `not_threat` items.
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
