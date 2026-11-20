---
trdd-id: 8OI25ZJ4
title: Gate 12 release push carries no release-candidate anchor so the pre-push hook refuses an unreleased version
column: dev
status: tasked
created: 2026-11-20T02:06:35+0100
updated: 2026-11-20T02:11:30+0100
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: bugfix
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-11-20T02:06:35+0100
---

# Gate 12 release push carries no release-candidate anchor so the pre-push hook refuses an unreleased version

## Approval log

- 2026-11-20T02:06:35+0100 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Symptom

Search words: publish.py Gate 12 push failed; CPV refused to run: its own integrity could not be verified; no hash manifest for tag vX.Y.Z could be fetched; error: failed to push some refs. Found while releasing TRDD-ZO54IL46 (5.23.2 attempt, 2026-11-20): Gates 1-11 passed, the atomic push was refused, nothing reached GitHub.

## Cause (observed)

The tracked git-hooks/pre-push runs validate_plugin.py . --verbose --strict. Gate 12 starts git push with env=os.environ.copy(), so the hook validator has no PLUGIN_INTEGRITY_RELEASE_CANDIDATE and falls back to fetching the GitHub manifest of tag v<new version>, which cannot exist before the push; the fail-closed integrity check (issue 255) refuses. The refusal text and git's error were printed to stderr by publish.py, but they appeared earlier in a redirected log than the push line, because stdout is block-buffered when redirected and stderr is not. Integrity audit entry: 2026-11-20T01:44:07, event refused, version 5.23.2.

## Fix

Gate 12 passes the push an environment with the skip variables removed and a fresh anchor from _compute_release_anchor at the release commit (Gate 1's anchor names the pre-bump HEAD). On a failed push it flushes stdout and prints both stdout and stderr of git push.

## Related

Blocks TRDD-ZO54IL46 (its release).

## Evidence

Before the fix, tests/test_trdd_8oi25zj4_gate12_push.py failed 2 of 2 (push env had no anchor; the stdout marker was not printed). After the fix it passes 2 of 2. Neighbour files test_publish, test_publish_idempotency, test_publish_background_prefetch, test_trdd_ezhm759t_own_publish, test_cpv_network_resilience pass. ruff check and mypy on publish.py and the new test: clean.

## Acceptance checklist

- [x] Gate 12 push env carries the anchor of the release commit, skip variables removed
- [x] A failed push prints both stdout and stderr of git push, after flushing stdout
- [ ] A real publish.py release passes Gate 12 with the hook installed
