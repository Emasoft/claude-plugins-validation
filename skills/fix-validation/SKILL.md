---
name: fix-validation
description: >
  Maps CPV validation errors to mechanical per-error fix guides. Use when looking up
  remediation steps for CRITICAL/MAJOR/MINOR/NIT findings. Architectural migrations
  are handled by migrate-marketplace-architecture. Used dynamically via the-skills-menu (TRDD-478d9687).
user-invocable: false
---

# Fix Validation — Error-to-Fix Index

## Overview

Central error-to-fix lookup. Maps validation errors to fix guides in `references/`.

## Prerequisites

- Report from `/cpv-validate-*`
- Access to `references/`

## Instructions

1. Read report → pick `plugin-error-index.md` or `marketplace-error-index.md`.
2. Match error → jump to fix guide via TOC.
3. For `category: architecture`, use `migrate-marketplace-architecture`.

Checklist: read report → match → apply → re-validate.

## Output

Fix log → `$MAIN_ROOT/reports/plugin-fixer/<ts±tz>-<slug>.md`.

## Error Handling

No match → keyword search; flag missing references.

## Examples

```
Input:  [MAJOR] Missing plugin.json
Output: plugin-structure-fixes §1
```

## Resources

- [Plugin Error Index](references/plugin-error-index.md)
  > validate_plugin.py · validate_skill.py · validate_skill_comprehensive.py · validate_hook.py · validate_agent.py · validate_command.py · validate_mcp.py · validate_lsp.py · validate_security.py · validate_rules.py · validate_xref.py · validate_settings_marketplace.py · validate_documentation.py · validate_encoding.py · validate_enterprise.py · validate_scoring.py · validate_cache.py · validate_telemetry.py — plugin-shipped env-var hazards · Semantic pillar — Channel MCP Server Source-Code Security · validate_marketplace cross-validation rules
- [Marketplace Error Index](references/marketplace-error-index.md)
  > validate_marketplace.py · 1 RC-MKPL-* upstream cross-validation codes (v2.81.0+) · validate_marketplace_pipeline.py · Architecture / Layout Migration Warnings (7 signals)
- [Schema-Parity Contract](references/schema-parity-contract.md)
  > What CPV does · The contract · What this contract does NOT say · What IS covered · Validator-gap protocol · Historical incidents · Related
- [Iterative Fix Loop](references/iterative-fix-loop.md)
  > Why a loop · Algorithm · Entry points — plugin path vs report path · Termination and safety · WARNING evaluation rules · Publish-blocking warning categories · Truly advisory warnings · Output contract
- [Parallel scanning (v2.103.0+)](../canonical-pipeline/references/parallelism.md) — `validate → fix → re-validate` loops were dominated by validator wall-time pre-rewrite (each pass ≈ 198 s on the CPV repo); v2.103.0+ rewrite makes each pass ≈ 17 s, so 10-iteration fix loops cost less than ONE pre-rewrite pass. No need to bundle/batch fixes for time reasons.
  > Table of contents · Performance summary · Environment knobs (disable selectively for debugging) · Scaffolded plugins (created via `create-plugin` / `setup-plugin-repo`) · Batch commands (`cpv-batch-*`) · Remote validation (`cpv` remote-mode + scaffolded `publish.py`) · When to disable parallelism · See also
- [Empirical Loading Bugs](references/empirical-loading-bugs.md)
  > Path-form acceptance matrix · Override-vs-default semantics · Three silent footguns CC does NOT catch · CPV validators added 2026-04-18 · Anthropic docs corrections · Round 2 confirmations · Tests added · Untestable in headless mode · v2.23.2 false-positive sweep
- [Cache-Audit Fixes](references/cache-fixes.md)
  > Overview · CA-01 — Static prefix violation in cached content · CA-02 — Hook writes to cached files (CLAUDE.md / settings.json) · CA-03 — Hook flips MCP server enabled/disabled or permission allow/deny · CA-04 — `model:` frontmatter forces an in-line model switch (any component) · CA-05 — Hook script runs unbounded output commands · CA-06 — Compaction/SubagentStart hook does not preserve cached prefix · CA-07 — `context: fork`/`branch` re-primes the cache from cold · (all seven are WARNING)
- [Telemetry Hazard Fixes](references/telemetry-hazard-fixes.md)
  > Overview · CRITICAL: Plugin ships CLAUDE_CODE_PLUGIN_SEED_DIR · CRITICAL: Plugin ships CLAUDE_CODE_SHELL_PREFIX · CRITICAL: Plugin ships CLAUDE_CONFIG_DIR · CRITICAL: Plugin ships BETA_TRACING_ENDPOINT pointing at external host · CRITICAL: Plugin ships OTEL_LOG_RAW_API_BODIES set to a file URL · MAJOR: Plugin ships third-party-provider bypass env var · Reference: env vars plugins MUST NEVER ship
- [Pipeline Migration](references/pipeline-migration.md)
  > §0 — Detect canonical pipeline drift via RC-PIPELINE-DRIFT-001 · §0b — Remove legacy pipeline scripts via RC-LEGACY-PIPELINE-001 · §1 — Fix dangling script references · §2 — Migrate to whole-repo lint via cpv_lint_engine · §3 — Cross-platform Python — bash to Python, os.path to pathlib · §4 — Make publish.py idempotent — interrupted-publish recovery · §5 — Sanitize every script-input parameter against injection
- [Marketplace Upstream Drift](references/marketplace-upstream-drift.md)
  > Name mismatch — RC-MKPL-NAME-MISMATCH · Version drift — RC-MKPL-VERSION-DRIFT · Unknown entry field — RC-MKPL-UNKNOWN-FIELD · Unknown source sub-field — RC-MKPL-UNKNOWN-SOURCE-FIELD · Source unreachable — RC-MKPL-UPSTREAM-UNREACHABLE · Description / author / keywords drift — RC-MKPL-METADATA-DRIFT · Per-batch bulk align — consolidated marketplace patch · Opt-out flags — when drift IS intentional
- Migration only: `canonical-pipeline-migration-checklist.md` at plugin root — 82-check exit gate.
- MCP bundling: executables go in `servers/`, referenced as `${CLAUDE_PLUGIN_ROOT}/servers/<name>` (unique names).
