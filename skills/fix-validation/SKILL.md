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

1. Read the validation report for severity, message, file path
2. Pick the index based on the validator that produced the report:
   - `plugin-error-index.md` — for any plugin-scope validator (validate_plugin/skill/hook/agent/command/mcp/lsp/security/rules/xref/settings_marketplace/documentation/encoding/enterprise/scoring/cache/telemetry)
   - `marketplace-error-index.md` — for `validate_marketplace.py` and `validate_marketplace_pipeline.py`
3. Match the error to the fix reference file it points to
4. Open the fix guide; use its TOC to jump to the exact section

For `category: architecture` findings, defer to the `migrate-marketplace-architecture` skill (Layout A/B/C conversion).

Copy this checklist and track your progress:

- [ ] Read validation report
- [ ] Match errors to validators
- [ ] Open fix guide and apply fixes
- [ ] Re-run validation to confirm clean

## Output

Fix log → `$MAIN_ROOT/reports/plugin-fixer/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md` (main-repo root via `git worktree list`, not a worktree).

## Error Handling

If no matching section is found, search the reference by message keywords. If the reference file is missing, report the gap.

## Examples

**Input:** validation report line `[MAJOR] Missing plugin.json`
**Output:** open `plugin-structure-fixes.md §1` and apply the fix

**Input:** `[CA-01] Static prefix violation`
**Output:** open `cache-fixes.md` CA-01

| Error | Fix guide |
|---|---|
| `[MAJOR] userConfig.<key> missing 'type'` | plugin-structure-fixes "userConfig schema invalid" |
| `[CRITICAL] Plugin ships CLAUDE_CODE_PLUGIN_SEED_DIR` | telemetry-hazard-fixes |
| `[CRITICAL] Layout C: self-entry source != "./"` | plugin-structure-fixes §15 |
| `[WARNING] Command 'clear' collides with built-in` | plugin-structure-fixes §16 |
| `[WARNING] architecture/recommend-restructure` | marketplace-fixes §9 |

## Schema-parity

CPV mirrors CC's install-time schema; zero findings should not trip a runtime schema error. Contract: [schema-parity-contract.md](references/schema-parity-contract.md).

## Resources

- [Plugin Error Index](references/plugin-error-index.md)
  > validate_plugin · validate_skill · validate_skill_comprehensive · validate_hook · validate_agent · validate_command · validate_mcp · validate_lsp · validate_security · validate_rules · validate_xref · validate_settings_marketplace · validate_documentation · validate_encoding · validate_enterprise · validate_scoring · validate_cache · validate_telemetry
- [Marketplace Error Index](references/marketplace-error-index.md)
  > 1. validate_marketplace.py · 2. validate_marketplace_pipeline.py · 3. Architecture / Layout Migration Warnings (7 signals)
- [Schema-Parity Contract](references/schema-parity-contract.md)
  > What CPV does · The contract · What this contract does NOT say · What IS covered · Validator-gap protocol · Historical incidents · Related
- [Iterative Fix Loop](references/iterative-fix-loop.md)
  > Why a loop · Algorithm · Entry points (plugin path vs report path) · Termination and safety · WARNING evaluation rules · Publish-blocking warning categories · Truly advisory warnings · Output contract
- [Empirical Loading Bugs](references/empirical-loading-bugs.md)
  > Path-form acceptance · Override-vs-default semantics · Three silent footguns · Validators added 2026-04-18 · Docs corrections · Round 2 confirmations · Tests added · Untestable in headless · v2.23.2 FP sweep
- [Cache-Audit Fixes](references/cache-fixes.md)
  > Overview · CA-01 Static prefix violation · CA-02 Hook writes to cached files · CA-03 Hook flips MCP/permission state · CA-04 SKILL.md `model:` forces switch · CA-05 Unbounded hook output · CA-06 Compaction hook does not preserve prefix
- [Telemetry Hazard Fixes](references/telemetry-hazard-fixes.md)
  > Overview · CRITICAL: PLUGIN_SEED_DIR · CRITICAL: SHELL_PREFIX · CRITICAL: CLAUDE_CONFIG_DIR · CRITICAL: BETA_TRACING_ENDPOINT (external) · CRITICAL: OTEL_LOG_RAW_API_BODIES=file:* · MAJOR: third-party-provider bypass · Reference: env vars plugins MUST NEVER ship

## MCP Server Bundling

Executables in `servers/`, reference `${CLAUDE_PLUGIN_ROOT}/servers/<name>`. Unique names.

## Token Optimization

Use `mcp__plugin_llm-externalizer_llm-externalizer__*` for bounded analysis.
