---
trdd-id: Z5NYE7ZI
title: skillaudit TOKEN_STEAL is demoted near the word example
column: backburner
status: tasked
created: 2026-10-23T00:14:55+0200
updated: 2026-10-23T00:19:30+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: security
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: false
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-10-23T00:14:55+0200
parent-trdd: N4GLZXZ6
---

# skillaudit TOKEN_STEAL is demoted near the word example

## Symptom
A doc-context demotion lets a plugin evade the TOKEN_STEAL finding by placing the word example nearby.

## Code location
scripts/cpv_skillaudit_native.py:566-570 (_DOC_CONTEXT_WORDS includes example) and :2621-2628 (demotion of CRED_ENV_READ and TOKEN_STEAL).

## Proposed fix
Never apply doc-context demotion in executable files (.py .rs .sh .js .ts); apply it only inside fenced blocks in .md files.

Search words: a credential-theft line is not flagged when the word example is nearby.
Line numbers as of the 5.23.0 hotfix tree.
Found during the 5.23.0 release review.
Fail toward flagging; no plugin-side exception (owner policy).

## Approval log

- 2026-10-23T00:14:55+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- Backlog record only; not approved as a work order. Owner approval required before dev.
- 2026-10-23 — Backlog record only; not approved as a work order.
