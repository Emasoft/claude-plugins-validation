---
name: fix-validation
description: >
  Maps CPV validation errors to mechanical per-error fix guides. Use when looking up
  remediation steps for CRITICAL/MAJOR/MINOR/NIT findings. Architectural migrations
  are handled by migrate-marketplace-architecture. Loaded by plugin-fixer agent.
allowed-tools: Read, Edit, Write, Glob, Grep
user-invocable: false
---

# Fix Validation — Error-to-Fix Index

## Overview

Central lookup for the fix agent. Given a validation error, find the reference file with fix instructions. All fix guides are in `references/`.

## Prerequisites

- A validation report from `/cpv-validate-plugin` or `/cpv-validate-skill`
- Access to the `references/` directory

## Instructions

1. Read the validation report for error messages with severity and file path
2. Pick the correct split index based on which validator produced the report:
   - `plugin-error-index.md` — for reports from `validate_plugin.py`, `validate_skill*.py`, `validate_hook.py`, `validate_agent.py`, `validate_command.py`, `validate_mcp.py`, `validate_lsp.py`, `validate_security.py`, `validate_rules.py`, `validate_xref.py`, `validate_settings_marketplace.py`, `validate_documentation.py`, `validate_encoding.py`, `validate_enterprise.py`, `validate_scoring.py`
   - `marketplace-error-index.md` — for reports from `validate_marketplace.py` and `validate_marketplace_pipeline.py`
3. Match the error in that index to the fix reference file it points to
4. Open the fix guide and use its TOC to jump to the exact section for the error

For `category: architecture` findings, do NOT use this skill — defer to the `migrate-marketplace-architecture` skill, which owns the Layout A/B conversion workflow.

Copy this checklist and track your progress:

- [ ] Read validation report
- [ ] Match errors to validators
- [ ] Open fix guide and apply fixes
- [ ] Re-run validation to confirm clean

## Output

The fix agent uses this index to locate the correct fix guide, then applies the fix and logs it to `docs_dev/fix-log_<name>_YYYYMMDD.md`.

## Error Handling

If no matching section is found in the reference file, search by error message keywords. If the reference file is missing, report the gap.

## Examples

**Input:** `[MAJOR] Missing plugin.json`
**Output:** plugin-error-index → validate_plugin.py → plugin-structure-fixes §1 → apply fix

**Input:** `[MAJOR] Description too short`
**Output:** plugin-error-index → validate_skill_comprehensive.py → skill-fixes §4 → apply fix

**Input:** `[MAJOR] userConfig.<key> missing/invalid 'type'` (install-breaking)
**Output:** plugin-structure-fixes "userConfig schema invalid" → infer from key name. Only `{string, number, boolean, directory, file}` are valid.

**Input:** `[WARNING] architecture/recommend-restructure (7-signal)`
**Output:** marketplace-error-index → §3 → marketplace-fixes §9 → per-signal mechanical fix

## Schema-parity contract

CPV validates plugin sources; it does not install them. Schema rules mirror Claude Code's install-time validators, so a source with zero CPV findings should not trip a runtime schema error. Install can still fail for non-schema reasons. Full contract: [schema-parity-contract.md](references/schema-parity-contract.md).

## Resources

- [Plugin Error Index](references/plugin-error-index.md)
  > validate_plugin.py · validate_skill.py · validate_skill_comprehensive.py · validate_hook.py · validate_agent.py · validate_command.py · validate_mcp.py · validate_lsp.py · validate_security.py · validate_rules.py · validate_xref.py · validate_settings_marketplace.py · validate_documentation.py · validate_encoding.py · validate_enterprise.py · validate_scoring.py
- [Marketplace Error Index](references/marketplace-error-index.md)
  > validate_marketplace.py · validate_marketplace_pipeline.py · Architecture / Layout Migration Warnings (7 signals)
- [Schema-Parity Contract](references/schema-parity-contract.md)
  > What CPV does · The contract · What this contract does NOT say · What IS covered · Validator-gap protocol · Historical incidents · Related
- [Iterative Fix Loop](references/iterative-fix-loop.md)
  > Why a loop · Algorithm · Entry points — plugin path vs report path · Termination and safety · WARNING evaluation rules · Publish-blocking warning categories · Truly advisory warnings · Output contract

## Token Optimization

When LLM Externalizer MCP is available, use it to save context tokens:
- `mcp__plugin_llm-externalizer_llm-externalizer__chat` — analyze validation reports, summarize fix guides
- `mcp__plugin_llm-externalizer_llm-externalizer__code_task` with `answer_mode=0, max_retries=3` — scan multiple fix guide files for relevant sections
- `mcp__plugin_llm-externalizer_llm-externalizer__scan_folder` — discover all reference files in a directory
- `mcp__plugin_llm-externalizer_llm-externalizer__check_references` — verify symbol references after applying fixes
- Always pass file paths via `input_files_paths`, never paste content into your context
