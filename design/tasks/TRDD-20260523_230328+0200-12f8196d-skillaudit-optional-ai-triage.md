---
trdd-id: 12f8196d-3482-486d-91ef-d809faeab747
title: Optional cheap-model AI triage for SkillAudit residual findings
status: not-started
created: 2026-05-23T23:03:28+0200
updated: 2026-05-23T23:03:28+0200
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

Three-stage pipeline, each stage shrinking the set handed to the next:

1. **Heuristics (done)** — eliminate the certain FPs in the context
   classifiers. Already shipped.
2. **Categorise the residuals** — every finding that survives heuristics but
   is NOT certifiable carries a `category` (command_injection, ssrf,
   insecure_crypto, cross_tool_access, obfuscation, privilege_escalation,
   path_traversal, env_injection, prompt_injection, data_exfil, …) and the
   minimal snippet + ±context lines.
3. **Optional cheap-model adjudication** — batch the categorised residuals to
   llm-externalizer's `security_triage` (FR #6) with a CPV-authored
   per-category prompt that states EXACTLY what makes the pattern a threat vs
   benign. Consume the structured per-item verdict.

### Hard invariants (user mandate)

- **Opt-in only.** Default OFF. A flag (e.g. `--ai-triage`) or a
  `cpv.ai_triage` config enables it. Never on by default — cost control.
- **Cheap models.** Triage uses `free` / local / cheap-ensemble profiles,
  never the main agent. CPV passes paths/snippets + category; llm-externalizer
  does all file-prep/batching/redaction/parsing (≈ zero CPV tokens).
- **Fail-safe to VISIBLE.** `uncertain`, model-unconfigured, or any error ⇒
  the finding STAYS VISIBLE (its pre-triage severity). The AI pass may only
  ever DEMOTE/suppress a finding it judges `not_threat` with high confidence;
  it must NEVER escalate a clean result into a block, and must NEVER silently
  drop on error. This preserves the strict gate's integrity.
- **Minimise the AI set.** Only findings that heuristics flag AND cannot
  certify are eligible. The goal is single-digit items per plugin.

### Per-category prompt authoring

CPV owns the `instructions_by_category` text (FR #6 lets the caller override
the built-in prompt). Each prompt: a one-paragraph "THREAT when X / NOT_THREAT
when Y / UNCERTAIN otherwise" rubric specific to that rule's real-world
benign-vs-malicious split. These live in `scripts/rules/` (the only wheel-
shipped data dir, per the catalog-location rule).

## Acceptance (when unblocked)

1. `security_triage` (FR #6) is available.
2. A new `--ai-triage` opt-in path collects categorised residuals, batches
   them to the tool, and demotes only high-confidence `not_threat` items.
3. Default run (no flag) is byte-for-byte unchanged.
4. Error / no-model / `uncertain` ⇒ finding visible (regression test with a
   deliberately-vulnerable fixture that the model is NOT consulted on still
   blocks).
5. Tokens spent by CPV for an N-item triage ≈ the report-path string only.

## Files (when unblocked)

- `scripts/rules/ai_triage_prompts.json` (NEW) — per-category rubrics.
- `scripts/validate_security.py` — optional triage stage after the heuristic
  classifier, gated on the opt-in flag.
- `scripts/cpv_skillaudit_native.py` — emit `category` + snippet + window on
  residual (non-suppressed, non-certified) findings.
- tests — opt-in on/off, fail-safe-to-visible, batch shape.
