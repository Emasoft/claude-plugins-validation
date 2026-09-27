---
trdd-id: L8LIHYPA
title: publish.py Gate 1 verifies self-hash manifest freshness before the self-scan
column: backburner
status: tasked
created: 2026-09-28T00:44:30+0200
updated: 2026-09-28T00:45:26+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-28T00:44:30+0200
---

# publish.py Gate 1 verifies self-hash manifest freshness before the self-scan

## Approval log

- 2026-09-28T00:44:30+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Problem

publish.py Gate 3's self-scan exempted files listed in .cpv-self-hashes.json only while their SHA matched; when the manifest was stale (edits landed after the last regen), the exemption silently disarmed and CPV flagged its own release-notes prose in CLAUDE.md as 4 CRITICAL + 2 MAJOR, blocking the v5.21.0 publish at Gate 3 (attempt 1, 2026-09-27). [[lesson-regen-hashes-last-markdown-poison]] hit again.

## Proposed fix

In publish.py Gate 1 (working-tree check), after confirming the tree is clean, re-hash the files listed in .cpv-self-hashes.json against the manifest; on mismatch, fail Gate 1 with the regen command instead of letting the stale manifest silently disarm the Gate 3 self-scan exemption and surface as unrelated CRITICAL findings.
