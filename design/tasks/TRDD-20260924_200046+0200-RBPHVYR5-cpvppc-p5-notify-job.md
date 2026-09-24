---
trdd-id: RBPHVYR5
title: CPVPPC P5 - Notify job
column: backburner
status: tasked
created: 2026-09-24T20:00:46+0200
updated: 2026-09-24T20:00:46+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:00:46+0200
---

# CPVPPC P5 - Notify job

Files: notify as a job template included in the release workflow (not a separate workflow, since token pushes trigger none); migrator in standardize_plugin.py removing the old separate notify-marketplace.yml. Key tests: one job per target, fail-fast: false; payload entry_name with plugin.json name fallback; receiver accepts both. Done-when: D9 regression - repo name != entry name updates the listing. Plan: design/specs/cpvppc-plan.md section 6, row P5. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:00:46+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
