---
trdd-id: 9UIUK9XA
title: The dead-link phase was bounded per URL but unbounded in aggregate
column: complete
created: 2026-07-28T17:13:28+0200
updated: 2026-07-28T17:13:28+0200
current-owner: cpv-session
task-type: bugfix
scope: project
release-via: publish
relevant-rules: []
implementation-commits: [7498103b]
external-refs: [https://github.com/Emasoft/claude-plugins-validation/issues/180]
---

# The dead-link phase was bounded per URL but unbounded in aggregate

## Problem

A downstream plugin's CI validate step ran 25-30 minutes and was killed by its
own `timeout-minutes`, having produced **no output at all**. It shipped a release
whose binaries never built, because `release.yml` gates asset staging behind that
step.

The reporter's timestamps disprove the cause CPV's own comments blamed: CPV
**built 4 seconds into the step**. A wheel, a warmer cache, or a different pin
would have saved 4 seconds and fixed nothing.

## Root cause (verified first-hand, not taken from the report)

`validate_md_urls` is called **once per markdown file** (`validate_plugin.py`),
and its per-host semaphores are **scoped to a single call** — so the throttling
that would pace a burst resets on every file, and the phase grows with
(files × URLs). Each request is bounded (`timeout` 8s, bounded retries with
backoff); their **sum was not bounded anywhere**.

That is precisely the "bounded per item, unbounded in aggregate" defect issue
**#162** fixed for REPO LINT — it was simply still open for this phase, which is
why #162's remedy did not help and why the failure looked like an unexplained
hang. It also explains every observed property: network-bound (so high variance
on a fresh runner), intermittent, silent, and fast locally.

Ruled out by reading the code, not by assumption: REPO LINT is capped at 600s
since v2.157.2 (the reporter's pin has it), and the external security scanners
are not in the `validate_plugin` path at all (`validate_plugin.py:2552` — "ZERO
external scanners").

## Design

One deadline spans **every file**, not each call — a per-call bound would reset
on each file and leave the aggregate exactly as unbounded as before. Default
300s, overridable via `PLUGIN_URL_CHECK_PHASE_TIMEOUT`, falling back to the
default on a zero / negative / unparseable value so a typo can never disable the
guard or shorten it to nothing. This mirrors `_phase_timeout` in the lint engine
deliberately: a second, differently-shaped budget would drift from the first.

**A skipped URL is reported as SKIPPED, never as DEAD**, and is deliberately not
cached. We did not contact it, so calling it dead would be a fabricated finding,
and caching it as alive would let a later file trust a check that never ran. The
overrun is ONE `WARNING` for the whole phase — a budget overrun is one fact about
the run — and WARNING never blocks, so an unchecked link cannot fail a plugin
that may well have none broken.

## Diagnosability (the reporter's second ask, and the costlier one)

- The validate step now **`tee`s** instead of `> file 2>&1` plus a trailing
  `cat`. With the redirect, a healthy run and a hung one were byte-identical in
  the log for the entire window, and a job killed at its cap never reached the
  `cat` — so the log said nothing about what was in flight.
  `${PIPESTATUS[0]}` keeps the exit code the VALIDATOR's; reading `$?` after a
  pipeline would report `tee`'s status and green every failed validation.
- Every `$exit_code` use is **quoted**. shellcheck could infer numeric-ness
  through `exit_code=$?` and stayed silent, but cannot through `PIPESTATUS` —
  verified against a stashed baseline that the unquoted form NEWLY trips SC2086,
  which the generated Lint job's actionlint would surface as red CI on every
  scaffolded plugin.
- The four "cold-install ceiling" comments are corrected. They told the reader
  the cap absorbs a 12-20 min cold build and a transient git-fetch, so triage
  went to the ref and the pin first; the caches they describe are already present
  and the build is seconds.

## Verification

- 15 tests, every firing case paired with a silence case.
- FN-safe both directions, proven: an expired budget produces NO dead-link
  warning and does not poison the shared cache; a live budget still reports a
  real dead link and still leaves a live one alone; no deadline at all preserves
  legacy behaviour for every existing caller.
- actionlint on the freshly emitted `ci.yml` + `release.yml`: **exit 0, 0
  findings** — measured against a baseline that was also 0, so the SC2086
  regression is proven introduced-and-closed rather than assumed.
- ruff clean; mypy clean across 133 source files.

## Notes

The two RC-8 positive controls in `test_canon_rc1_rc8_rc9_template.py` were
re-pinned to the quoted literal. The gate they assert — exit 1-4 **and** a
SUMMARY line — is unchanged; only the quoting moved.

[[lesson-cannot-check-is-not-clean]] applies to the shape of the old failure: a
step that is killed before it reports is not a clean step, and the redirect made
those two indistinguishable.
