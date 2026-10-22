---
trdd-id: 565D542B
title: GitignoreFilter prunes ignored dirs holding tracked files and tests dir is skipped
column: backburner
status: tasked
created: 2026-10-23T00:14:57+0200
updated: 2026-10-23T00:19:32+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: security
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: false
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-10-23T00:14:57+0200
parent-trdd: N4GLZXZ6
---

# GitignoreFilter prunes ignored dirs holding tracked files and tests dir is skipped

## Symptom
GitignoreFilter still prunes .gitignore'd directories even when they hold tracked files, and validate_plugin passes skip_dirs={tests} to validate_no_absolute_paths. Both contradict: everything git-tracked is scanned.

## Code location
scripts/gitignore_filter.py:166 (class GitignoreFilter, pruning near :385-411); scripts/validate_plugin.py:4549.

## Proposed fix
Never prune a directory that contains tracked files. For the skip_dirs half: decide whether tracked tests/ ship to users. If they ship, scan them, because tracked fixtures can leak private paths. If the decision is to keep the CPV-defined skip, record why.

Search words: a tracked file inside a gitignored folder is never scanned, and the tests folder is skipped by the absolute-path scan.
Line numbers as of the 5.23.0 hotfix tree.
Found during the 5.23.0 release review.
Fail toward flagging; no plugin-side exception (owner policy).

## Approval log

- 2026-10-23T00:14:57+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- Backlog record only; not approved as a work order. Owner approval required before dev.
- 2026-10-23 — Backlog record only; not approved as a work order.
