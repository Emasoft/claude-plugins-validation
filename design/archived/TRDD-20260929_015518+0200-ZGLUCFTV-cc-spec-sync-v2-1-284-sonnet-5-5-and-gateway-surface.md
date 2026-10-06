---
trdd-id: ZGLUCFTV
title: CC spec sync v2.1.284 sonnet-5-5 and gateway surface
column: complete
status: archived
created: 2026-09-29T01:55:18+0200
updated: 2026-09-29T02:32:33+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-29T01:55:18+0200
---

# CC spec sync v2.1.284 sonnet-5-5 and gateway surface

Window v2.1.284 (single release after the v5.21.0 sync point of v2.1.283). Raw-docs mechanical set-diff per cc-spec-drift-check-method: claude-sonnet-5-5 model id into VALID_MODELS/_FULL_MODEL_ID_RE/_SHORT_MODEL_RE; sweep settings/tools/hooks/commands/env/marketplace tables for any other 2.1.284 additions; changelog bullets triaged CPV-surface vs runtime-only. Two-sided tests per repo convention; regen hashes last.

## Approval log

- 2026-09-29T01:55:18+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- 2026-09-29T02:32:33+0200 — COMPLETE by main-agent@claude-plugins-validation. All 4 stage-2 items applied, centrally verified end-to-end, acceptance checklist complete.

## Outcome

Stage 2 applied + centrally verified 2026-09-29 (commit ea464276): slides +6 env vars, +14/-4 MANAGED_ONLY_KEYS (all 4 removals per-key doc-verified, worker + orchestrator independently), +settings known_fields. 52 new two-sided tests; end-to-end probed through validate_settings_json_project_scope. Inverted spec wording recorded by worker (adding a builtin MAKES the collision warning draw). T13 allowClaudeInChromeWithManagedMcp anomaly flagged for follow-up card.

## Acceptance checklist

- [x] slides in BUILTIN_SLASH_COMMANDS, collision warning draws end-to-end (TRDD-ZGLUCFTV)
- [x] 6 in-window env vars in VALID_PLUGIN_ENV_VARS, probed through is_valid_plugin_env_var (TRDD-ZGLUCFTV)
- [x] MANAGED_ONLY_KEYS +14 (each doc-verified Managed row) and -4 (scope row AND body agree; orchestrator re-verified 2 against durable snapshot) (TRDD-ZGLUCFTV)
- [x] known_fields +settings; 52 two-sided tests green centrally (TRDD-ZGLUCFTV)
