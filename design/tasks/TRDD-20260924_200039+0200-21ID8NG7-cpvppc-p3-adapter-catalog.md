---
trdd-id: 21ID8NG7
title: CPVPPC P3 - Adapter catalog
column: backburner
status: tasked
created: 2026-09-24T20:00:39+0200
updated: 2026-09-24T20:00:39+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:00:39+0200
---

# CPVPPC P3 - Adapter catalog

Files: scripts/cpvppc/adapters/{python_uv,node_ts,rust_cargo,c_cpp,go,shell}.py. Key tests: exact argv per command and option; config-file args overridden; pytest collected-vs-discovered check on a fixture with addopts=-k nothing. Done-when: argv snapshots match section 3.2. Plan: design/specs/cpvppc-plan.md section 6, row P3. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:00:39+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
