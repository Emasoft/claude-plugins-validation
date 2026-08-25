---
trdd-id: b8c6d04f-5bf6-4c6b-be70-f54f77725595
title: Exhaustive SHA self-recognition — every shipped (git-tracked) file is hashed; no file is skipped without a SHA match
column: complete
created: 2026-05-29T08:15:21+0200
updated: 2026-08-25T17:25:16+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-b8c6d04f — Exhaustive SHA self-recognition

**Filename:** `design/tasks/TRDD-20260529_081521+0200-b8c6d04f-exhaustive-sha-self-recognition.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

## User directives (verbatim)

1. "implement a strong self recognition, with sha validation so no tampered
   plugin or inoculated script would ever pass the self identity recognition test."
2. "be sure the sha check includes all files. even one file skipped is enough
   to poison the whole plugin."
3. "sha must be on the files shipped in the plugin, gitignored files are not
   part of the plugin"

## Settled decisions

- **File set = ALL git-tracked (shipped) files.** Gitignored files are not part
  of the plugin and are excluded. The 2 manifest files themselves
  (`.plugin-self-hashes.json`, `.cpv-self-hashes.json`) cannot hash their own
  content (chicken-and-egg) and are excluded; their integrity comes from the
  GitHub-anchored manifest comparison, not a self-hash.
- **No file is ever skipped without a SHA match.** Remove the Tier-0
  unconditional dev-scratch skip and the name-eligibility pre-filter; a
  self-scan skips a file ONLY when its SHA256 matches the trusted exhaustive
  manifest.
- Idea 1 (data-flow "safe usage" suppression) and Idea 2 (catalog
  decomposition) were REJECTED by the user — do not weaken detection.

## What already exists (verified, sound) — keep

- `is_cpv_self_scan` flips self-scan MODE on name/signature (cheap gate).
- `_set_cpv_self_scan` two-tier trust: target IS the running (GitHub-verified)
  CPV → trust local manifest; target merely CLAIMS to be CPV → fetch the
  GitHub canonical manifest for the claimed version; GitHub unreachable →
  REFUSE self-scan (scan everything). This already defeats spoofing.
- `cpv_self_scan_skip` already SHA-gates the skip (stage 3).
- `verify_self_integrity` fetches GitHub canonical manifest; catches MODIFIED
  and DELETED files.

## The gaps (this TRDD closes)

1. **Manifest is a skip-list, not all-files.** `compute_manifest` hashes only
   `is_self_scan_eligible` (name-based) files → most shipped files are unhashed.
2. **`verify_self_integrity` is not bidirectional.** It iterates manifest
   entries only → an ADDED shipped file (present locally, absent from manifest)
   is never checked.
3. **Tier-0 unconditional skip.** `cpv_self_scan_skip` skips
   `_is_dev_scratch_path` files with NO hash check (gated to running-CPV).

## Implementation (3 coordinated changes)

1. **`scripts/_plugin_compute_hashes.py::compute_manifest`** — hash EVERY
   git-tracked file via `_git_tracked_files` (drop the `is_self_scan_eligible`
   filter). Exclude only `_MANIFEST_BASENAMES`. Non-git fallback: walk minus
   build/cache/dev-scratch `skip_dirs`, still no eligibility filter.
   `is_self_scan_eligible` in this module becomes dead → remove if unreferenced.
2. **`scripts/validate_security.py::cpv_self_scan_skip`** — new logic: skip iff
   `_CPV_SELF_SCAN_ACTIVE` AND the file's SHA256 == `_CPV_SELF_HASH_MANIFEST[rel]`.
   Drop Tier-0 (`_is_dev_scratch_path` unconditional) and the
   `_is_self_scan_eligible` pre-filter. (Gitignored files are excluded from
   scanning upstream by gitignore-filtering — VERIFY self-scan stays 0/0/0/0.)
3. **`scripts/_plugin_verify_hashes.py::verify_self_integrity`** — after the
   manifest→local check, enumerate local git-tracked files and flag any NOT in
   the manifest (added-file detection), excluding `_MANIFEST_BASENAMES`. Reject
   modified + deleted + added.

## Risks / verify points

- Removing Tier-0 must NOT re-surface dev-scratch findings on self-scan. The
  scanner respects gitignore (v2.99.1), and design/tasks/ (git-tracked) is now
  in the exhaustive manifest → skipped on SHA match. VERIFY self-scan = 0/0/0/0
  after each change.
- GitHub manifest lag: until a version ships with the exhaustive manifest,
  GitHub holds the old skip-list manifest; verify_self_integrity uses GitHub's
  copy. Local testing uses `CPV_SKIP_GITHUB_INTEGRITY=1`. Exhaustiveness takes
  effect for users on the shipped version.
- Existing self-scan / integrity tests will need updates; ADD adversarial tests:
  added shipped file → integrity FAIL + self-scan does NOT skip it; modified
  file → not skipped; gitignored file → out of scope (not hashed, not shipped).

## Implementation status (2026-05-29)

- **Change 1 — DONE** (committed 4781215). `compute_manifest` hashes every
  git-tracked file; manifest now 796 entries. Enumeration refactored into a
  shared `enumerate_shipped_files(plugin_root)` (git-tracked when a repo, else
  walk minus `_SHIPPED_WALK_SKIP_DIRS` and runtime cruft).
- **Change 2 — DONE** (committed 4781215). `cpv_self_scan_skip` skips a file
  ONLY on SHA match; Tier-0 + name-eligibility pre-filter removed.
- **Change 3 — DONE** (this commit). `verify_self_integrity` now bidirectional:
  `_detect_added_files` diffs the on-disk shipped set (via the shared
  `enumerate_shipped_files`) against the manifest and reports any ADDED /
  inoculated file. Cruft (`.DS_Store`, `*.pyc`, `*~`, …) is excluded so real
  installs are not false-flagged. Adversarial two-sided tests in
  `tests/test_plugin_verify_hashes.py::TestChange3AddedFileDetection`.

### Deferred (NOT blocking this TRDD's acceptance)

The change-1 note "`is_self_scan_eligible` becomes dead → remove if
unreferenced" is **not yet actioned** because the two functions
(`_plugin_compute_hashes.is_self_scan_eligible`,
`validate_security._is_self_scan_eligible`) are STILL referenced — by the
legacy `compute_cpv_self_hashes.py` re-export shim and by three audit tests
(`test_audit_caches_token.py` cache #3, `test_audit_integrity_nits.py`,
`test_legacy_integrity_fallback.py`). The TRDD's own condition ("if
unreferenced") is therefore not met. They are harmless (no live caller —
`cpv_self_scan_skip` is fully SHA-gated, verified) but should be removed for
hygiene once the references are torn down. Tracked in the follow-up TRDD
[[self-scan-name-signal-hardening]] (TRDD-4243a768).

## Acceptance criteria

1. Manifest contains a hash for EVERY git-tracked file (minus the 2 manifest
   files). `git ls-files | wc -l` ≈ manifest entry count + 2.
2. `cpv_self_scan_skip` returns True ONLY on SHA match; no unconditional skip.
3. `verify_self_integrity` rejects added + modified + deleted tracked files.
4. Adversarial tests prove a tampered/added/inoculated shipped file is caught.
5. CPV self-scan 0/0/0/0; full suite green; ships next minor.

## Approval log

- 2026-08-25T17:25:16+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED v2.108.0 commit 47812154 — exhaustive SHA self-recognition live (batch_ad)
