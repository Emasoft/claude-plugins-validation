---
trdd-id: 2ZIX9O9J
title: Adopt new CC features in CPV itself
column: superseded
created: 2026-09-24T13:02:58+0200
updated: 2026-09-24T13:23:12+0200
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
superseded-by: [Q3CPZL0X]
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
- 2026-09-24T13:23:12+0200 — SUPERSEDED by emanuelesabetta. Doctor/pricing scope absorbed into TRDD-Q3CPZL0X WP9; omitClaudeMd+effort:low proposal refused per verified-facts decision recorded in STATE (2026-09-24)..

## STATE

DECISIONS 2026-09-24 (user directive verbatim: 'decide yourself. base your decisions on verified facts and tests.'): doctor --json and pricing moved INTO the spec-sync work (WP9, card Q3CPZL0X). omitClaudeMd and effort:low on CPV's own launcher agents (cpv-plugin-validator-agent, cpv-skill-validation-agent) REFUSED -- both agents run Bash and would lose the user's global CLAUDE.md safety rules (RULE 0/RULE 1) with no test able to prove that safe, and their bodies carry only 3 of the 5 mandatory output items (measured); effort:low has no measured benefit. Card superseded by TRDD-Q3CPZL0X (WP9) for the doctor/pricing scope; the omitClaudeMd/effort:low proposal is refused outright.
