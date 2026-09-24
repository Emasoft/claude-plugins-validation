---
trdd-id: TINOOW8V
title: CPVPPC P8 - publish.py
column: backburner
status: tasked
created: 2026-09-24T20:01:05+0200
updated: 2026-09-24T20:01:05+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:01:05+0200
---

# CPVPPC P8 - publish.py

Files: templates/0.x/plugin/publish.py. Key tests: local preflight = gates 1-9; triggers the release workflow via gh workflow run and watches it; no stamp files, no process-tree checks, no tag pushes. Done-when: preflight on the fixture matrix; dry-run dispatch. Plan: design/specs/cpvppc-plan.md section 6, row P8. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:01:05+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
