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

The fix agent logs to `$MAIN_ROOT/reports/plugin-fixer/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md` at the **main-repo root** (first entry of `git worktree list`), never a linked worktree. Per-component subfolder + local-time+GMT-offset timestamp mandatory. Both `reports/` and `reports_dev/` gitignored.

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
  > validate_plugin · validate_skill · validate_skill_comprehensive · validate_hook · validate_agent · validate_command · validate_mcp · validate_lsp · validate_security · validate_rules · validate_xref · validate_settings_marketplace · validate_documentation · validate_encoding · validate_enterprise · validate_scoring
- [Marketplace Error Index](references/marketplace-error-index.md)
  > validate_marketplace · validate_marketplace_pipeline · Architecture / Layout Migration Warnings (7 signals)
- [Schema-Parity Contract](references/schema-parity-contract.md)
  > What CPV does · The contract · What this contract does NOT say · What IS covered · Validator-gap protocol · Historical incidents · Related
- [Iterative Fix Loop](references/iterative-fix-loop.md)
  > Why a loop · Algorithm · Entry points · Termination · WARNING evaluation · Publish-blocking warnings · Output contract
- [Empirical Loading Bugs](references/empirical-loading-bugs.md)
  > 5 silent footguns + fix recipes (agents folder · hooks default-file cascade · MCP/LSP cross-source · MCP redundancy)

## MCP Server Bundling

Place executables in `servers/`, reference via `${CLAUDE_PLUGIN_ROOT}/servers/<name>`. Names unique across sources.

## Token Optimization

Use `mcp__plugin_llm-externalizer_llm-externalizer__*` (chat / code_task / scan_folder / check_references) for bounded analysis. Always pass file paths via `input_files_paths`, never paste content.
