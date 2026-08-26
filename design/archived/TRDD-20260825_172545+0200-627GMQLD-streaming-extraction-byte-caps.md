---
trdd-id: 627GMQLD
title: Archive extraction switches to streaming decompression with running byte caps
column: complete
created: 2026-08-25T17:25:45+0200
updated: 2026-08-26T05:54:23+0200
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

## ⏵ STATE — 2026-08-25T23:59:00+0200

Zip + tar streaming caps were already on disk from a prior interrupted run
(`_stream_entry`/`_write_member` in `scripts/cpv_management_common.py`).
This pass: found and fixed a real bug the tests immediately surfaced —
`_extract_zip`'s deliberately-lowered `member.file_size` override made
`zipfile.ZipExtFile` raise `zipfile.BadZipFile` (CRC mismatch on the
truncated read) instead of the clean quota abort, exactly on the exploit
path being tested. Added a `try/except zipfile.BadZipFile` around the
per-entry read that translates it to the existing `_abort_archive` idiom.
Added 4 tests to `tests/test_security_codex_review_2026_05_04.py`:
under-declared zip size still aborts (byte-verified, no partial file),
honest large file extracts byte-identical, tar aggregate cap trips
mid-stream via the new running counter, and a documentation test proving
tarfile's `extractfile()` hard-bounds reads to the declared size (so a
"declares small, real data is bigger" bypass — the (c) criterion as
literally worded — is not constructible against stdlib tarfile; verified
empirically, not assumed). 72/72 tests pass; ruff + mypy clean.

## Approval log

- 2026-08-25T17:25:45+0200 — ACCEPTED from TRDD-6116ab4c P5 and moved to dev by
  the CPV session (authority delegated by USER 2026-08-25).
- 2026-08-26T05:54:23+0200 — COMPLETE by the CPV session. Its 72-test suite was
  re-run centrally (72/72) and the worker also fixed a real `BadZipFile` crash.
  While closing the board a NEIGHBOURING defect in the same `extract_archive`
  code was found and fixed this session: the path-traversal and escaping-symlink
  aborts did a bare `sys.exit(1)`, leaving a half-extracted tree behind, unlike
  the quota aborts which route through `_abort_archive` (message + rmtree +
  exit 1). All five sites now route through `_abort_archive`. The ZIP call site
  carried the identical defect and had NO test at all — while the tar test's own
  docstring cited the ZIP path as the clean-abort reference — so a two-sided ZIP
  pair was added (`TestZipTraversalIsCleanAbort`). Non-vacuity proven from
  `git show HEAD`: the pre-fix ZIP site is `err(...); sys.exit(1)` with no
  cleanup, so the new `assert not dest.exists()` would have failed.
  Gate proof: serial suite 13,076 passed / 3 skipped (`PYTEST4_EXIT=0`);
  cache-cold strict self-validate 0/0/0/0 (`SELFVAL4_EXIT=0`).
