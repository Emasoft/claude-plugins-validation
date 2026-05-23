---
trdd-id: 12f8196d-3482-486d-91ef-d809faeab747
title: Optional cheap-model AI triage for SkillAudit residual findings
status: not-started
created: 2026-05-23T23:03:28+0200
updated: 2026-05-23T23:05:20+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-12f8196d — Optional AI triage for SkillAudit residuals

**Filename:** `design/tasks/TRDD-20260523_230328+0200-12f8196d-skillaudit-optional-ai-triage.md`
**Tracked in:** this repo (`design/tasks/` is git-tracked)

## Blocked on (external)

This work depends on the **llm-externalizer** feature request
**Emasoft/llm-externalizer-plugin#6** ("batch security-triage tool"). Do NOT
start implementation until that tool ships. (Not a `blocked-by:` frontmatter
entry because that field takes TRDD UIDs, not external issues — the blocker
is the GitHub FR above.)

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

Built on llm-externalizer's existing **mass-scout** massive-batch pipeline
(FR #6 refinement) — volume is NOT a constraint (thousands of items across a
fleet are fine; mass-scout is built for it). Heuristics-first remains for
**precision/quality**, not as a hard cost cap.

1. **Heuristics (done)** — eliminate the certain FPs in the context
   classifiers. Already shipped (v2.105.0).
2. **Categorise the residuals** — every finding that survives heuristics but
   is NOT certifiable carries a `category` (== a mass-scout **bucket**:
   command_injection, ssrf, insecure_crypto, cross_tool_access, obfuscation,
   privilege_escalation, path_traversal, env_injection, prompt_injection,
   data_exfil, …) plus the minimal snippet OR `file_path`+`line`+`context_lines`.
3. **Optional cheap-model adjudication via mass-scout** —
   `mass_scout_register` the residuals → `mass_scout_preclassify` into the
   security buckets → `mass_scout_estimate --budget-usd <cap>` → `mass_scout`
   with the **security fieldset** (`{verdict, confidence, reason}`) and the
   CPV-authored per-category prompt → `mass_scout_export` → consume verdicts.

### Hard invariants (user mandate)

- **Opt-in only.** Default OFF. A flag (e.g. `--ai-triage`) or a
  `cpv.ai_triage` config enables it. Never on by default.
- **Cheap models + budget cap.** Uses `free` / local / cheap-ensemble; the
  `mass_scout_estimate --budget-usd` ceiling bounds spend even on huge
  batches. CPV passes snippets/paths + bucket; llm-externalizer does all
  file-prep/batching/redaction/structured-output (≈ zero CPV tokens — only
  the report/export path crosses the boundary).
- **Fail-safe to VISIBLE.** `uncertain`, model-unconfigured, or any error ⇒
  the finding STAYS VISIBLE (its pre-triage severity). The AI pass may only
  ever DEMOTE/suppress a finding it judges `not_threat` with high confidence;
  it must NEVER escalate a clean result into a block, and must NEVER silently
  drop on error. Preserves the strict gate's integrity.
- **Heuristics-first for precision, not cost.** Volume is mass-scout's job;
  heuristics still run first so the model only adjudicates genuinely
  ambiguous cases (better signal, fewer model mistakes), but there is no
  hard "keep it tiny" cap.

### Per-category prompt authoring

CPV owns the per-category prompt (FR #6: a per-category prompt override on the
security fieldset). Each: a one-paragraph "THREAT when X / NOT_THREAT when Y /
UNCERTAIN otherwise" rubric specific to that rule's real-world
benign-vs-malicious split. These live in `scripts/rules/` (the only wheel-
shipped data dir, per the catalog-location rule).

## Acceptance (when unblocked)

1. The mass-scout security fieldset + per-category prompt hook (FR #6) is
   available.
2. A new `--ai-triage` opt-in path registers categorised residuals into
   mass-scout, preclassifies into buckets, estimates under a budget cap,
   runs the scout, and demotes only high-confidence `not_threat` items.
3. Default run (no flag) is byte-for-byte unchanged.
4. Error / no-model / `uncertain` ⇒ finding visible (regression test with a
   deliberately-vulnerable fixture that the model is NOT consulted on still
   blocks).
5. Tokens spent by CPV for an N-item triage ≈ the export-path string only,
   regardless of N (mass-scout handles thousands).

## Files (when unblocked)

- `scripts/rules/ai_triage_prompts.json` (NEW) — per-category rubrics +
  the security fieldset definition CPV passes to mass-scout.
- `scripts/validate_security.py` — optional triage stage after the heuristic
  classifier, gated on the opt-in flag; drives the mass-scout
  register/preclassify/estimate/scout/export sequence.
- `scripts/cpv_skillaudit_native.py` — emit `category` (bucket) + snippet +
  window on residual (non-suppressed, non-certified) findings.
- tests — opt-in on/off, fail-safe-to-visible, bucket mapping, budget cap.
