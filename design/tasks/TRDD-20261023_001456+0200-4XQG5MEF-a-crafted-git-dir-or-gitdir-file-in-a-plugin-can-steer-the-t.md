---
trdd-id: 4XQG5MEF
title: A crafted .git dir or gitdir file in a plugin can steer the tracked set
column: backburner
status: tasked
created: 2026-10-23T00:14:56+0200
updated: 2026-10-23T00:19:31+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: security
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: false
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-10-23T00:14:56+0200
parent-trdd: N4GLZXZ6
---

# A crafted .git dir or gitdir file in a plugin can steer the tracked set

## Symptom
A plugin artifact that ships a crafted .git directory or gitdir: file can steer the tracked set, so non-security rules skip vendored content.

## Code location
scripts/cpv_validation_common.py:1963 (_git_index_path) and :1985 (_probe_git_index_path).

## Proposed fix
Accept the repo only when git rev-parse --absolute-git-dir equals <toplevel>/.git and that path is a real directory.

Search words: vendored files are skipped in a zip or tarball plugin that ships its own .git, or contains a tracked symlink named vendor.
Line numbers as of the 5.23.0 hotfix tree.
Found during the 5.23.0 release review.
Fail toward flagging; no plugin-side exception (owner policy).

## Approval log

- 2026-10-23T00:14:56+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- Backlog record only; not approved as a work order. Owner approval required before dev.
- 2026-10-23 — Backlog record only; not approved as a work order.
