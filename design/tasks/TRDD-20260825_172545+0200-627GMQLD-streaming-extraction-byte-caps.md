---
trdd-id: 627GMQLD
title: Archive extraction switches to streaming decompression with running byte caps
column: dev
created: 2026-08-25T17:25:45+0200
updated: 2026-08-25T17:25:45+0200
current-owner: cpv-session
task-type: security
min-approval-requirement: none
parent-trdd: TRDD-6116ab4c
---

# TRDD-627GMQLD — Streaming extraction byte caps (zip-bomb hardening)

Accepted from proposal TRDD-6116ab4c (Proposal 5) under user-delegated authority
2026-08-25. Verified first-hand: `cpv_management_common._extract_zip` /
`_extract_tar` preflight quotas using the ARCHIVE-DECLARED uncompressed size, so
a crafted archive that under-reports `info.file_size` / `member.size` bypasses
`max_bytes` / `max_per_file_bytes` / `max_ratio` while extraction still writes
the real data.

## Scope

Replace the declared-size trust with streaming decompression enforcing a running
byte cap per file AND in aggregate: copy in bounded chunks, count actual bytes
written, abort (and remove the partial file) the moment a cap is exceeded —
independent of declared sizes. Keep the existing entry-count, nesting, and
path-traversal defenses intact. Keep the declared-size preflight as a cheap
early reject (it is still a valid fast path for honest archives).

## Acceptance

- Two-sided tests: (a) a zip whose central-directory declares tiny sizes but
  inflates past the cap is ABORTED with the quota error and leaves no partial
  file behind; (b) an honest archive within caps extracts byte-identical to
  before; (c) a tar member under-reporting size likewise aborted.
- Existing extraction tests stay green; fail-fast error propagation unchanged.

## Approval log

- 2026-08-25T17:25:45+0200 — ACCEPTED from TRDD-6116ab4c P5 and moved to dev by
  the CPV session (authority delegated by USER 2026-08-25).
