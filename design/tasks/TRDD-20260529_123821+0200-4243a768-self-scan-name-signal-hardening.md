---
trdd-id: 4243a768-a7b8-4bba-96a6-143a88100f85
title: Self-scan name-signal hardening + dead-code removal of the *_self_scan_eligible helpers
column: dev
created: 2026-05-29T12:38:21+0200
updated: 2026-08-25T17:25:45+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-4243a768 — Self-scan name-signal hardening + dead-code removal

**Filename:** `design/tasks/TRDD-20260529_123821+0200-4243a768-self-scan-name-signal-hardening.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

Security-hygiene follow-up to [[exhaustive-sha-self-recognition]]
(TRDD-b8c6d04f). Two independent items; both are defense-in-depth /
cleanup, NOT functional security holes (the SHA gate already makes a skip
require a hash match, and spoofing is defeated by `_set_cpv_self_scan`'s
GitHub-anchored trust).

## Item 1 — harden the `is_cpv_self_scan` name-signal (Category-C)

### Background

`is_cpv_self_scan` is the cheap name/signature gate that flips the
validator into self-scan MODE. The actual per-file skip is SHA-gated
(`cpv_self_scan_skip` — only a manifest hash match skips), and spoofing is
defeated upstream by `_set_cpv_self_scan` (trusts the local manifest ONLY
for the running, GitHub-verified CPV; otherwise fetches the canonical
manifest from GitHub and REFUSES to skip if it can't reach it). So the
name-signal cannot, by itself, grant a skip.

### The residual concern

The name-signal is still a *mode* trigger derived from path/name shape. A
plugin that mimics CPV's directory shape (a `scripts/validate_*.py`, a
`.claude-plugin/plugin.json` with `name == "claude-plugins-validation"`,
etc.) can flip CPV into self-scan MODE for ITS OWN tree. Even though no
file gets skipped without a SHA match, mode-flipping is an unnecessary
attack surface and a source of confusion.

### Proposed hardening (deterministic, no opt-out)

- Require MULTIPLE corroborating signals before `is_cpv_self_scan` returns
  True (name + a structural fingerprint, e.g. the presence of the two
  manifest files AND a `scripts/validate_security.py` whose module-level
  symbol set matches a known fingerprint), rather than any single
  name/path match.
- Consider tying the MODE flip to the same GitHub-anchored identity check
  `_set_cpv_self_scan` already performs, so mode and skip share one trust
  root.
- Keep it fail-closed: ambiguous identity → NOT self-scan → scan everything.

### Acceptance

- A third-party plugin shaped like CPV does NOT flip self-scan mode.
- The running, GitHub-verified CPV still self-scans cleanly (0/0/0/0).
- Two-sided tests (genuine-CPV → mode on; lookalike → mode off).

## Item 2 — remove the dead `*_self_scan_eligible` helpers

### Background

After TRDD-b8c6d04f changes 1+2, neither
`_plugin_compute_hashes.is_self_scan_eligible` nor
`validate_security._is_self_scan_eligible` has a live caller —
`compute_manifest` hashes ALL git-tracked files and `cpv_self_scan_skip`
is fully SHA-gated. They survive only as:

- a re-export in the legacy `compute_cpv_self_hashes.py` shim, and
- the "lockstep" audit invariant in `tests/test_audit_caches_token.py`
  (`TestSelfScanEligibilityLockstep`, cache #3) plus surface assertions in
  `test_audit_integrity_nits.py` and `test_legacy_integrity_fallback.py`.

The lockstep invariant ("the two implementations must agree") is now
**obsolete** — neither drives the skip decision, so they no longer need to
agree.

### Proposed cleanup

1. Delete `_is_self_scan_eligible` from `validate_security.py` and
   `is_self_scan_eligible` from `_plugin_compute_hashes.py`.
2. Drop the re-export from `compute_cpv_self_hashes.py` `__all__` + import.
   (Or retire the whole shim — its own removal was deferred from v2.53.0;
   evaluate doing it here.)
3. Delete `TestSelfScanEligibilityLockstep` (cache #3) from
   `test_audit_caches_token.py`; remove the surface assertions in
   `test_audit_integrity_nits.py` / `test_legacy_integrity_fallback.py`.
4. Grep for every other reference (comments in both modules that say "stay
   in lockstep with …") and update them.

### Risk / verify

- These functions look like they gate self-scan but do not — leaving them
  is a security-readability hazard a future maintainer could trip on. That
  is the motivation; there is no runtime risk in leaving OR removing them.
- After removal: full suite green, CPV self-scan 0/0/0/0, manifest still
  exhaustive (the removal changes only `.py` content → regen manifest).

## Why deferred from TRDD-b8c6d04f

Both items are hygiene / defense-in-depth, not functional security gaps
(verified: `cpv_self_scan_skip` is SHA-gated and ignores the name-signal
for the actual skip). The exhaustive-SHA TRDD's acceptance criteria are met
without them. Splitting them out keeps that TRDD's diff focused and avoids a
multi-file audit-test teardown at the tail of a long working session.
