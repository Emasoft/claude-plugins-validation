---
trdd-id: AI6QSSXH
title: Test-suite validator subprocesses write integrity-skip lines into the real home cache audit log
column: backburner
status: tasked
created: 2026-10-23T00:18:15+0200
updated: 2026-10-23T00:19:30+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: bugfix
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: false
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-10-23T00:18:15+0200
parent-trdd: N4GLZXZ6
---

# Test-suite validator subprocesses write integrity-skip lines into the real home cache audit log

## Symptom
Search words: running the CPV test suite adds integrity-skip lines to the real ~/.cache/cpv/integrity-audit.log, polluting the trail a bypass leaves.

## Code location
scripts/_plugin_verify_hashes.py:94 (CACHE_DIR = Path.home() / .cache / cpv), :481 (_AUDIT_LOG), :484 (_audit writer; pytest_session label is self-reported).

## Proposed fix
The suite points the audit log at a tmp dir (for example by setting HOME for the validator subprocesses). CPV code must not offer a configurable path that a scanned plugin could use, so do not add a runtime env override read by production code; prefer a HOME override set by the test harness, or if an override is unavoidable verify it is test-only and cannot be set at runtime by a scanned plugin.

Line numbers as of the 5.23.0 hotfix tree.
Found during the 5.23.0 release review.
Fail toward flagging; no plugin-side exception (owner policy).

## Approval log

- 2026-10-23T00:18:15+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- Backlog record only; not approved as a work order. Owner approval required before dev.
- 2026-10-23 — Backlog record only; not approved as a work order.
