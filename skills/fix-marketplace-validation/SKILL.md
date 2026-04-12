---
name: fix-marketplace-validation
description: >
  Maps marketplace validation errors to fix reference files. Loaded by marketplace-fixer agent.
  Use when a marketplace validation report has CRITICAL/MAJOR/MINOR/NIT findings from
  validate_marketplace.py or validate_marketplace_pipeline.py.
agent: marketplace-fixer
context: fork
user-invocable: false
allowed-tools: Read, Edit, Bash(git:*,gh:*,jq:*,uv:*), Glob, Grep
---

# Fix Marketplace Validation — Error-to-Fix Index

## Overview

Central lookup for the marketplace-fixer agent. Given a marketplace validation error, find the reference file with fix instructions. During the current transition the canonical guides live in the shared `skills/fix-validation/references/` directory — the Resources section below links to the README that records the current mapping.

This skill is scoped to **mechanical per-error fixes**. For architectural migration (converting a non-CPV marketplace to Layout A or Layout B), this skill is NOT the right tool — use `migrate-marketplace-architecture` instead.

## Prerequisites

- A marketplace validation report from `validate_marketplace.py` or `validate_marketplace_pipeline.py`
- Access to the shared fix guides listed in the Resources section below

## Instructions

1. Read the validation report for error messages with severity, file path, and optional `category` tag.
2. **Screen for architecture findings first.** If any finding has `category: architecture`, stop and delegate to `migrate-marketplace-architecture`. Mechanical fixes cannot repair architectural issues.
3. For each remaining mechanical finding, consult `marketplace-error-index.md` — the primary lookup covering both `validate_marketplace.py` (structure, plugin entries, source types, submodules) and `validate_marketplace_pipeline.py` (publish.py, cliff.toml, CI workflow, tagging, secrets).
4. Jump from the index entry to the specific section in the detailed guide:
   - `marketplace-fixes.md` — issues from `validate_marketplace.py`
   - `pipeline-fixes.md` — issues from `validate_marketplace_pipeline.py`
5. Apply the fix using Edit following the guide's step-by-step instructions. Never improvise structural changes.

Copy this checklist and track your progress:

- [ ] Read validation report
- [ ] Screen for `category: architecture` → hand off if present
- [ ] Match mechanical errors to fix guide sections
- [ ] Apply fixes in severity order (CRITICAL → MAJOR → MINOR → NIT)
- [ ] Re-run validation to confirm clean

## Separation From Architectural Migration

- `fix-marketplace-validation` (this skill): local Edit operations, minimal user interaction, safe to batch.
- `migrate-marketplace-architecture`: repository restructuring requiring extensive `AskUserQuestion` interrogation (target layout, owner, licenses, per-plugin metadata). Irreversible in practice.

If a report mixes both kinds, fix the mechanical findings first, then hand off the architectural ones.

## Output

The fixer agent uses this index to locate the correct fix guide, then applies the fix and logs it to `docs_dev/fix-log_<marketplace-name>_YYYYMMDD.md`.

## Error Handling

If no matching section is found for an error message, search by error-message keywords in `marketplace-fixes.md` or `pipeline-fixes.md`. If a guide is missing a section for the error (gap), report it in the fix log and continue with the next finding. Do NOT guess at the fix — report gaps upward so guides can be updated.

## Examples

**Input:** `[MAJOR] marketplace.json not found`
**Output:** marketplace-error-index → `validate_marketplace.py` → marketplace-fixes §1 → create file with required structure.

**Input:** `[MINOR] cliff.toml missing keepachangelog template`
**Output:** marketplace-error-index → `validate_marketplace_pipeline.py` → pipeline-fixes → scaffold cliff.toml with the canonical template.

## Resources

- [README (transition note + canonical fix guide locations)](references/README.md)
  > Purpose · Transition note · Canonical fix guide locations · Marketplace Error Index · Marketplace Fixes · Pipeline Fixes

Guides referenced by name (not linked — the split task will move canonical copies under this skill's own `references/`):

- `marketplace-error-index.md` — marketplace-scope error-to-fix mapping
- `marketplace-fixes.md` — detailed fixes for `validate_marketplace.py`
- `pipeline-fixes.md` — detailed fixes for `validate_marketplace_pipeline.py`

## Token Optimization

When LLM Externalizer MCP is available, offload bounded analysis:

- `mcp__plugin_llm-externalizer_llm-externalizer__chat` — summarize reports / fix guides
- `mcp__plugin_llm-externalizer_llm-externalizer__code_task` (`answer_mode=0, max_retries=3`) — scan fix guides for relevant sections
- Always pass file paths via `input_files_paths`; never paste content.
