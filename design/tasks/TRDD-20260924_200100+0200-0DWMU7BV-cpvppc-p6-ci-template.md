---
trdd-id: 0DWMU7BV
title: CPVPPC P6 - CI template
column: backburner
status: tasked
created: 2026-09-24T20:01:00+0200
updated: 2026-09-24T20:01:00+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:01:00+0200
---

# CPVPPC P6 - CI template

Files: templates/0.x/plugin/ci.yml. Key tests: gates 1-9 as jobs; per-target build on push/PR with SHA-pinned caches; permissions: {}; runner-label check; custom sandbox (a custom step writing publish.py fails). Done-when: actionlint clean; fixture matrix green. Plan: design/specs/cpvppc-plan.md section 6, row P6. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:01:00+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
