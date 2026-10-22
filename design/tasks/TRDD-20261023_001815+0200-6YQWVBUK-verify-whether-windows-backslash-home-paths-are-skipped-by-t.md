---
trdd-id: 6YQWVBUK
title: Verify whether Windows backslash home paths are skipped by the absolute-path and private-info scanners
column: backburner
status: tasked
created: 2026-10-23T00:18:15+0200
updated: 2026-10-23T00:30:04+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: spike
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: false
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-10-23T00:18:15+0200
parent-trdd: N4GLZXZ6
---

# Verify whether Windows backslash home paths are skipped by the absolute-path and private-info scanners

## Symptom
Search words: a Windows home path with backslashes (a drive letter, then Users, then a login name, then a project folder) is reportedly not reported (claim refuted for single-backslash paths in Markdown prose on 2026-10-23; see Evidence). Reported claim: a backslash in the FIRST path segment still counts as regex syntax, so the path is skipped. Pre-existing false negative. NOT reproduced in isolation on the hotfix tree: _path_prefix_looks_like_regex returns False for a backslash Windows home path with and without a project folder, a backslash path on a non-home drive, and a UNC share, so verify the skip with a real scan of a file first; it may come from another step (for example the caller at :8204 or :8521, or the matcher).

## Code location
scripts/cpv_validation_common.py:2463 (_path_prefix_looks_like_regex), :2454 (_FIRST_SEGMENT_RE), :2453 (_HOME_PREFIX_RE); callers at :8204 and :8521.

## Proposed fix
Reproduce with an end-to-end scan; then make separators never count as regex syntax in any segment and add a regression test for a backslash Windows home path and its escaped form in source strings.

Line numbers as of the 5.23.0 hotfix tree.
Found during the 5.23.0 release review.
Fail toward flagging; no plugin-side exception (owner policy).

## Approval log

- 2026-10-23T00:18:15+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- Backlog record only; not approved as a work order. Owner approval required before dev.
- 2026-10-23 — Backlog record only; not approved as a work order.

## Evidence 2026-10-23

The 5.23.0 release dry run (validate_plugin.py --strict on the candidate tree) reported this card's own literal single-backslash Windows home paths as MAJOR Absolute path found findings: three on the Symptom line and one on the Proposed fix line; the non-home drive example was not reported, as expected. This refutes the false negative for unescaped single-backslash home paths in Markdown prose through the absolute-path rule only. Not covered: the private-info scanner, code files, and the escaped double-backslash form inside source-code strings. The literal examples were then reworded so the card does not fail the release gate.
