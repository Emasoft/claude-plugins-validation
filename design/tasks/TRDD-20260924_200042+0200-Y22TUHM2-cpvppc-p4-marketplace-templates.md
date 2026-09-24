---
trdd-id: Y22TUHM2
title: CPVPPC P4 - Marketplace templates
column: backburner
status: tasked
created: 2026-09-24T20:00:42+0200
updated: 2026-09-24T20:00:42+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:00:42+0200
---

# CPVPPC P4 - Marketplace templates

Files: templates/0.x/marketplace/{update.yml,validate-readme-table.yml,render_readme_table.py}; generate_marketplace_repo.py, standardize_marketplace.py write config + lock, insert missing markers, never overwrite a non-canon file without a diff. Key tests: Layout A/B/C fixtures verify COMPLIANT (static); unknown plugin dispatch fails loudly; D5/D6/D7/D8 regressions. Done-when: MKT-007 green on the scaffold. Plan: design/specs/cpvppc-plan.md section 6, row P4. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:00:42+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
