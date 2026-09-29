---
trdd-id: L8LIHYPA
title: publish.py Gate 1 verifies self-hash manifest freshness before the self-scan
column: complete
status: archived
created: 2026-09-28T00:44:30+0200
updated: 2026-09-29T02:23:30+0200
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
- 2026-09-29T02:23:30+0200 — COMPLETE by main-agent@claude-plugins-validation. Per-card criteria met and centrally verified; batch full-suite gate carried by the publish task (review R4#B: an untrue WORK column is worse than an unstarted card)..

## Problem

publish.py Gate 3's self-scan exempted files listed in .cpv-self-hashes.json only while their SHA matched; when the manifest was stale (edits landed after the last regen), the exemption silently disarmed and CPV flagged its own release-notes prose in CLAUDE.md as 4 CRITICAL + 2 MAJOR, blocking the v5.21.0 publish at Gate 3 (attempt 1, 2026-09-27). [[lesson-regen-hashes-last-markdown-poison]] hit again.

## Proposed fix

In publish.py Gate 1 (working-tree check), after confirming the tree is clean, re-hash the files listed in .cpv-self-hashes.json against the manifest; on mismatch, fail Gate 1 with the regen command instead of letting the stale manifest silently disarm the Gate 3 self-scan exemption and surface as unrelated CRITICAL findings.

## Review scope notes (2026-09-28 adversarial review)

1. The fix message MUST include the commit step — 'regen, commit the manifest, re-publish' — or a publisher obeying 'fail with the regen command' literally hits Gate 1's dirty-tree refusal on the next run (regen dirties the manifest, a tracked file).
2. Decide staleness-only vs staleness+completeness and say which: _plugin_compute_hashes.py enumerates git ls-files, so a NEW tracked file absent from the manifest passes every listed-SHA check — a staleness-only check misses the same FP-noise symptom by a different mechanism. If the check is built, assert manifest-membership == tracked-file-set in the same pass, or record the completeness half as out of scope.
3. Consider the belt-and-braces shape: the exemption actually disarms inside the Gate 3 self-scan arming path (_set_cpv_self_scan / cpv_self_scan_skip), where a per-file SHA mismatch could emit a WARNING naming the exact files — cheap, and it covers non-publish entry points (agent-security had its own missed-arming defect in v4.0.0). Gate 1 catches earlier but only on the publish path; warn at arming, fail at Gate 1.

## Acceptance checklist

- [x] Gate 1 fails on staleness (sha256 drift) and completeness (tracked file absent) naming every file, capped with explicit overflow — 6 tests incl. legacy-name and message-contents (regen + commit step per review note 1); mutation-proven both halves; central verify: diff read against spec, 79 tests green, wiring call-site confirmed
