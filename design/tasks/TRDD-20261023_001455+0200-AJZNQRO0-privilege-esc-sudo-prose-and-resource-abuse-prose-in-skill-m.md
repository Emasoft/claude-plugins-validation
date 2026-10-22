---
trdd-id: AJZNQRO0
title: PRIVILEGE_ESC sudo prose and RESOURCE_ABUSE prose in SKILL.md are suppressed
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

# PRIVILEGE_ESC sudo prose and RESOURCE_ABUSE prose in SKILL.md are suppressed

## Symptom
sudo prose (PRIVILEGE_ESC) and RESOURCE_ABUSE prose in SKILL.md are suppressed, but SKILL.md prose is executable intent for the model.

## Code location
scripts/cpv_skillaudit_native.py (prose and doc-context suppression, around :566-630 and :2595-2630). Canon: .claude/project/memory/wikimem/prose-vs-executable-intent-canon.md.

## Proposed fix
Re-evaluate against the prose-vs-executable-intent canon; decide with two-sided fixtures (benign how-to prose vs instructive prose). Default if undecided: fail toward flagging.

Search words: a SKILL.md that tells the model to run sudo or to exhaust resources produces no finding.
Line numbers as of the 5.23.0 hotfix tree.
Found during the 5.23.0 release review.
Fail toward flagging; no plugin-side exception (owner policy).

## Approval log

- 2026-10-23T00:14:55+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- Backlog record only; not approved as a work order. Owner approval required before dev.
- 2026-10-23 — Backlog record only; not approved as a work order.
