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

Central lookup for the fix agent. Maps each validation error to the reference file with fix instructions. All fix guides are in `references/`.

## Prerequisites

- A validation report from a `/cpv-validate-*` command
- Access to `references/`

## Instructions

1. Read the validation report for severity, message, file path.
2. Pick the index: `plugin-error-index.md` for plugin-scope validators; `marketplace-error-index.md` for marketplace.
3. Match the error to the fix reference file it points to.
4. Open the fix guide; use its TOC to jump to the exact section.

For `category: architecture`, defer to `migrate-marketplace-architecture`.

Copy this checklist and track your progress:

- [ ] Read validation report
- [ ] Match errors to validators
- [ ] Open fix guide and apply fixes
- [ ] Re-run validation to confirm clean

## Output

Fix log → `$MAIN_ROOT/reports/plugin-fixer/<ts±tz>-<slug>.md`.

## Error Handling

If no match is found, search by keywords. Report missing reference files as gaps.

## Examples

```
Input:  [MAJOR] Missing plugin.json
Output: open plugin-structure-fixes §1, follow the recipe
```

| Error | Fix guide |
|---|---|
| `[MAJOR] Missing plugin.json` | plugin-structure-fixes §1 |
| `[CA-01] Static prefix violation` | cache-fixes CA-01 |
| `[MAJOR] userConfig.<key> missing 'type'` | plugin-structure-fixes |
| `[CRITICAL] PLUGIN_SEED_DIR` | telemetry-hazard-fixes |
| `[CRITICAL] Layout C self-entry` | plugin-structure-fixes §15 |
| `[MAJOR] Dangling scripts/<name>.py ref` | pipeline-migration §1 |
| `[WARNING] recommend-restructure` | marketplace-fixes §9 |

## Schema-parity

CPV mirrors CC's install-time schema. Contract: [schema-parity-contract.md](references/schema-parity-contract.md).

## Resources

- [Plugin Error Index](references/plugin-error-index.md)
  > 1. validate_plugin.py · 2. validate_skill.py · 3. validate_skill_comprehensive.py · 4. validate_hook.py · 5. validate_agent.py · 6. validate_command.py · 7. validate_mcp.py · 8. validate_lsp.py · 9. validate_security.py · 10. validate_rules.py · 11. validate_xref.py · 12. validate_settings_marketplace.py · 13. validate_documentation.py · 14. validate_encoding.py · 15. validate_enterprise.py · 16. validate_scoring.py · 17. validate_cache.py · 18. validate_telemetry.py — plugin-shipped env-var hazards
- [Marketplace Error Index](references/marketplace-error-index.md)
  > 1. validate_marketplace.py · 2. validate_marketplace_pipeline.py · 3. Architecture / Layout Migration Warnings (7 signals)
- [Schema-Parity Contract](references/schema-parity-contract.md)
  > What CPV does · The contract · What this contract does NOT say · What IS covered · Validator-gap protocol · Historical incidents · Related
- [Iterative Fix Loop](references/iterative-fix-loop.md)
  > Why a loop · Algorithm · Entry points — plugin path vs report path · Termination and safety · WARNING evaluation rules · Publish-blocking warning categories · Truly advisory warnings · Output contract
- [Empirical Loading Bugs](references/empirical-loading-bugs.md)
  > Path-form acceptance matrix · Override-vs-default semantics · Three silent footguns CC does NOT catch · CPV validators added 2026-04-18 · Anthropic docs corrections · Round 2 confirmations · Tests added · Untestable in headless mode · v2.23.2 false-positive sweep
- [Cache-Audit Fixes](references/cache-fixes.md)
  > Overview · CA-01 — Static prefix violation in cached content · CA-02 — Hook writes to cached files (CLAUDE.md / settings.json) · CA-03 — Hook flips MCP server enabled/disabled or permission allow/deny · CA-04 — SKILL.md `model:` frontmatter forces in-line model switch · CA-05 — Hook script runs unbounded output commands · CA-06 — Compaction/SubagentStart hook does not preserve cached prefix
- [Telemetry Hazard Fixes](references/telemetry-hazard-fixes.md)
  > Overview · CRITICAL: Plugin ships CLAUDE_CODE_PLUGIN_SEED_DIR · CRITICAL: Plugin ships CLAUDE_CODE_SHELL_PREFIX · CRITICAL: Plugin ships CLAUDE_CONFIG_DIR · CRITICAL: Plugin ships BETA_TRACING_ENDPOINT pointing at external host · CRITICAL: Plugin ships OTEL_LOG_RAW_API_BODIES set to a file URL · MAJOR: Plugin ships third-party-provider bypass env var · Reference: env vars plugins MUST NEVER ship
- [Pipeline Migration](references/pipeline-migration.md)
  > §1 — Fix dangling script references · §2 — Migrate to whole-repo lint via cpv_lint_engine · §3 — Make publish.py idempotent · Combined verification

## MCP Server Bundling

Bundled MCP executables go in `servers/`, referenced as `${CLAUDE_PLUGIN_ROOT}/servers/<name>`. Unique names.
