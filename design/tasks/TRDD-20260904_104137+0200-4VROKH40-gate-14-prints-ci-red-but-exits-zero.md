---
trdd-id: 4VROKH40
title: Gate 14 prints CI IS RED but publish.py still exits 0
column: todo
created: 2026-09-04T10:41:37+0200
updated: 2026-09-04T11:11:24+0200
current-owner: cpv-main-session
task-type: bugfix
min-approval-requirement: none
relevant-rules: []
---

# Gate 14 prints CI IS RED but publish.py exits 0

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-09-04

**NEXT ACTION:** confirm the Gate 14 code path in `scripts/publish.py` — read the
function that emits `✗ CI IS RED` and trace exactly how its return value (or lack
of one) flows into the pipeline's final exit code. Nothing below has been verified
against the actual source yet; it is a report of observed release behaviour only.

## The problem

During the v5.17.0 release, `scripts/publish.py` Gate 14 printed `✗ CI IS RED` and
the pipeline still exited 0, so the release completed and shipped with a red CI on
the released commit `f1882af9`.

CI is real and still red as of this writing: GitHub Actions run `33847216240` on
`f1882af9`, job `Test Shard 2`, one failure.

## The defect

A gate that prints a failure marker (`✗ CI IS RED`) but does not affect the process
exit status is decoration, not a gate. Two acceptable outcomes exist and neither is
what shipped:

1. Gate 14 is made **fatal** — a red CI verdict causes `publish.py` to exit non-zero,
   so an automated caller (or a human reading `$?`) is told the release did not
   fully succeed.
2. Gate 14 stays **advisory** but its own output stops reading as a failed gate —
   e.g. relabel it something like `[advisory] CI status: RED (release already
   shipped; this does not block)`, so a reader is never shown a `✗` and then handed
   exit 0 in the same breath.

Whichever is chosen, the fix must land with a test that proves the exit status
matches the printed verdict — i.e. a red-CI scenario and a green-CI scenario both
drive the process exit code deterministically and observably.

## Ownership

This is a CPV-owned pipeline defect: `scripts/publish.py` is CPV's own canonical
release pipeline, not something a plugin author controls. Per
`~/.claude/rules/plugin-tests-are-the-plugins-job.md`, a bug in the publisher's own
gate logic is CPV's to fix, not deflected as a downstream plugin's test problem.

## Acceptance criteria

- [ ] The Gate 14 code path in `scripts/publish.py` is read and the current
      exit-code behavior (fatal vs advisory vs silently-ignored) is stated as a
      verified fact, not inferred from release-log prose.
- [ ] A decision is made and recorded: Gate 14 becomes fatal, OR Gate 14's output is
      relabeled so it cannot be misread as a failed gate that was ignored.
- [ ] A test exists that asserts the process exit code matches the printed CI
      verdict for both a red-CI and a green-CI case; the test fails against the
      pre-fix code and passes after the fix (mutation-proven, not just green).
- [ ] The existing `f1882af9` red CI is either fixed or explicitly triaged as a
      separate, already-tracked issue (see `TRDD-MHCFOCBV` in the same
      `design/tasks/` directory, which tracks the underlying timeout — the REPO
      LINT phase outliving the 120 s subprocess budget of the test that spawns
      it; it is NOT Linux-only, it was reproduced on macOS) — this card is about
      the gate's exit-status honesty, not about diagnosing that specific CI
      failure.
