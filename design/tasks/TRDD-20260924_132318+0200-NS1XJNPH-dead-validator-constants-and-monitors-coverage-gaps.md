---
trdd-id: NS1XJNPH
title: Dead validator constants and monitors coverage gaps
column: backburner
created: 2026-09-24T13:23:18+0200
updated: 2026-09-24T13:23:18+0200
current-owner: emanuelesabetta
created-by: emanuelesabetta
task-type: bugfix
min-approval-requirement: none
assignee: emanuelesabetta
mandate: true
mandated-by: none
approved: true
approval-judge: emanuelesabetta
approval-datetime: 2026-09-24T13:23:18+0200
---

# Dead validator constants and monitors coverage gaps

KNOWN_SETTINGS_KEYS (cc_scope_rules.py) and OPTIONAL_MARKETPLACE_TOP_LEVEL_FIELDS (validate_marketplace.py) are defined but read by no validator (verified by grep 2026-09-24), so the settings 'typo detector' and the marketplace top-level unknown-field check described in past release notes do not exist; wiring them could newly flag working files, so it needs its own measurement first. Also: monitors are validated only when declared in plugin.json -- the default monitors/monitors.json and experimental.monitors are not walked by validate_monitors_entries.

## Approval log

- 2026-09-24T13:23:18+0200 — MANDATE issued by emanuelesabetta (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
