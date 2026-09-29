---
trdd-id: D9ISDLOZ
title: Bulk env-var drift 233 documented vars missing from VALID_PLUGIN_ENV_VARS
column: backburner
status: tasked
created: 2026-09-29T02:19:00+0200
updated: 2026-09-29T02:22:50+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: bugfix
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-29T02:19:00+0200
---

# Bulk env-var drift 233 documented vars missing from VALID_PLUGIN_ENV_VARS

Scan docs_dev/cc-spec-284-diff.md (2026-09-29) row 10: 372 doc vars vs 187 in CPV; 233 doc-side vars absent and uncovered by is_valid_plugin_env_var (165 CLAUDE_/ANTHROPIC_ + 68 other-prefix incl. the 17-member VERTEX_REGION_CLAUDE_* family, ANTHROPIC_DEFAULT_MODEL, ANTHROPIC_MODEL, MCP_TIMEOUT, BASH_MAX_TIMEOUT_MS, USE_BUILTIN_RIPGREP). Each is a latent unknown-variable WARNING on a documented var. NOT bundled into the v2.1.284 window sync deliberately (window discipline; the in-window 6 were added instead). Add after an FP-measurement pass per repo precedent (v5.1.0 recorded the 13-var version of this same drift and deferred). Raw lists: /tmp/env_missing.txt and /tmp/doc_env_all.txt from the scan (regenerate from docs_dev/cc-docs-20260929/ if gone).

## Approval log

- 2026-09-29T02:19:00+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
DURABLE SOURCE (review R4#D): the /tmp file citations in the body die with the machine, and the docs_dev snapshot fallback is gitignored. The durable regeneration source is the fetch procedure: llms.txt-discovered raw pages at code.claude.com/docs/en/<page>.md — env-vars.md carries the full variable table; set-diff it against cpv_validation_common.VALID_PLUGIN_ENV_VARS (plus is_valid_plugin_env_var's pattern coverage) per the cc-spec-drift-check-method.
