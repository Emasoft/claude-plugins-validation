---
trdd-id: HQF9MW6T
title: Integrity follow-ups re-export shim pycache and wheel fetch
column: backburner
status: tasked
created: 2026-10-23T00:14:57+0200
updated: 2026-10-23T00:19:32+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: bugfix
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: false
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-10-23T00:14:57+0200
parent-trdd: N4GLZXZ6
---

# Integrity follow-ups re-export shim pycache and wheel fetch

## Symptom
1. REPO_RAW_MAIN_URL re-export shim is still present. 2. Planted __pycache__ files are not detected by the added-files check. 3. The wheel-path fetch does not catch HTTPException.

## Code location
scripts/cpv_integrity.py:39 and :56 (import and __all__ entry) plus its pinning tests; scripts/_plugin_verify_hashes.py:477 (pycache open item) and :774-792; scripts/_plugin_verify_hashes.py:713-745 (_fetch_wheel_manifest except clause lacks HTTPException, unlike _fetch_one at :194).

## Proposed fix
Remove the shim and its pinning tests; make the added-files check flag planted __pycache__ files; add HTTPException to the wheel-path except clause.

Search words: the integrity check does not notice a planted __pycache__ file, and a truncated wheel download raises instead of failing cleanly.
Line numbers as of the 5.23.0 hotfix tree.
Found during the 5.23.0 release review.
Fail toward flagging; no plugin-side exception (owner policy).

## Approval log

- 2026-10-23T00:14:57+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- Backlog record only; not approved as a work order. Owner approval required before dev.
- 2026-10-23 — Backlog record only; not approved as a work order.
