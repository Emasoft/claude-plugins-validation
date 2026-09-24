---
trdd-id: 2ZIX9O9J
title: Adopt new CC features in CPV itself
column: backburner
created: 2026-09-24T13:02:58+0200
updated: 2026-09-24T13:03:17+0200
current-owner: emanuelesabetta
created-by: emanuelesabetta
task-type: feature
min-approval-requirement: none
assignee: emanuelesabetta
mandate: true
mandated-by: none
approved: true
approval-judge: emanuelesabetta
approval-datetime: 2026-09-24T13:02:58+0200
blocked-by: 
---

# Adopt new CC features in CPV itself

Adopt, inside CPV itself, the newer CC capabilities surfaced by the 2.1.258-2.1.281 spec sync
(Card 1: CC spec sync 2.1.258 to 2.1.281):

- doctor switches to `claude plugin validate --json` and reports UNKNOWN when the CLI is
  missing, fails, or is too old — manage_doctor.py's `_run_claude_validate` scrapes plain text
  today and cannot distinguish "clean" from "could not check".
- omitClaudeMd:true + effort:low on cpv-plugin-validator-agent and cpv-skill-validation-agent —
  NEEDS USER DECISION: this drops the user's own CLAUDE.md and rules for every CPV user, and
  the two agent bodies do not currently carry the full 5-item mandatory output format on their
  own, so omitting CLAUDE.md could silently drop that format too. Do not apply until decided.
- Opus 5.5 / Fable 5.1 pricing entries in cpv_token_cost.py's MODEL_PRICING table.

Depends on Card 1 landing first (the plugin-shipped omitClaudeMd allowlist / field support must
exist before this card can turn it on).

## Approval log

- 2026-09-24T13:02:58+0200 — MANDATE issued by emanuelesabetta (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
