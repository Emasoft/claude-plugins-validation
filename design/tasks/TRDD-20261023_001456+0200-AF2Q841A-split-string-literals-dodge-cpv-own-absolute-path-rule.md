---
trdd-id: AF2Q841A
title: Split string literals dodge CPV own absolute-path rule
column: backburner
status: tasked
created: 2026-10-23T00:14:56+0200
updated: 2026-10-23T00:19:31+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: refactor
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: false
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-10-23T00:14:56+0200
parent-trdd: N4GLZXZ6
---

# Split string literals dodge CPV own absolute-path rule

## Symptom
String literals are split to dodge CPV's own absolute-path rule.

## Code location
scripts/validate_security.py:1164 and :1168 (RC-36 messages, "/" + "etc/passwd..."); scripts/validate_telemetry.py:313-319 (managed-settings marker, joined path parts); scripts/validate_plugin.py:2414-2416 (managed-settings message).

## Proposed fix
Add a CPV-defined constant of canonical OS and Claude Code system paths that the absolute-path rule recognises for every plugin, then revert the splits.

Search words: CPV source splits /etc paths into two strings so its own scanner does not see them.
Line numbers as of the 5.23.0 hotfix tree.
Plain words: a CPV source file writes system paths as two joined strings.
Verified against the same tree.
Found during the 5.23.0 release review.
Fail toward flagging; no plugin-side exception (owner policy).

## Approval log

- 2026-10-23T00:14:56+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- Backlog record only; not approved as a work order. Owner approval required before dev.
- 2026-10-23 — Backlog record only; not approved as a work order.
