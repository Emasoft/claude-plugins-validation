---
trdd-id: 6UW0KZVY
title: publish version gate fails open when the remote is unreadable
column: dev
created: 2026-08-18T23:41:02+0200
updated: 2026-08-18T23:41:02+0200
current-owner: cpv-session
task-type: bugfix
priority: 2
labels: [phase-2, hub-dispatch, canonical-pipeline]
external-refs: [TRDD-BRRJK57P]
---

# Version gate fails open on unreadable remote (P2, CANONICAL — hub 4th card)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-08-18

Hub relay of amvcp TRDD-YY5ISKCJ (fixed there in v1.5.1 commit 807fbbc): an
ls-remote non-zero collapsed into "no tags" made the gate pass vacuously.
Fleet: 14/22 copies have no remote-tag read; 7/22 read but unaudited; 1/22
fail-closed. CPV probe (first-hand, 2026-08-18): `_remote_tag_exists` in BOTH
publish.py and the emitted canon returns bool, and a read FAILURE is
indistinguishable from "tag absent". Two pre-push consumers act destructively
on that False:

- publish.py:2640 — recovery consolidation: unreadable remote → treats the
  prior chore(release) commit as NOT published → `git reset --soft HEAD~1`
  + deletes the local tag. If the tag IS published, this mangles local state
  against the public source of truth.
- publish.py:2694 — stale-tag guard: unreadable remote → moves the local tag.

The POST-push verify sites (publish.py:2823, template :3930) are already
fail-safe (failure → UNVERIFIED, never green) — leave them.

**NEXT ACTION:** make `_remote_tag_exists` three-valued (True / False /
None=UNREADABLE) in both copies, amvcp shape: None reserved for
could-not-read; read-succeeded-zero-tags stays False so first-publish is
unaffected. The two destructive pre-push consumers REFUSE (manual
intervention message) on None. Real-git tests, no mocks for the git layer.

`_read_remote_version` audited and left as-is: it reads LOCAL tracking refs
via `git show` (offline op, no network read to fail open on); its None
fallback is the legitimate first-publish path.

## Acceptance criteria

- [ ] Unreadable remote (ls-remote non-zero) at either pre-push recovery
      site REFUSES the publish rather than taking the destructive branch.
- [ ] Read-succeeded-zero-tags still returns False — first publish unaffected.
- [ ] Post-push verify keeps UNVERIFIED (never green, never newly blocking).
- [ ] Emitted canon carries the same shape; real-git tests both-sided.

## Approval log

- 2026-08-18T23:41:02+0200 — Authored at `dev` under the USER delegation
  recorded in TRDD-BRRJK57P (hub dispatch, 4th canonical card).
