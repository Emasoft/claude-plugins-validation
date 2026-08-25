---
trdd-id: WT0FLTMM
title: GitHub-Actions expression-injection detector plus hardened auto-notify receiver snippet
column: dev
created: 2026-08-25T17:25:45+0200
updated: 2026-08-25T17:25:45+0200
current-owner: cpv-session
task-type: security
min-approval-requirement: none
parent-trdd: TRDD-6116ab4c
---

# TRDD-WT0FLTMM — GHA expression-injection detector + receiver hardening

Accepted from proposal TRDD-6116ab4c (Proposal 4) under user-delegated authority
2026-08-25. Verified unshipped first-hand before acceptance (no `${{ ... }}`
shell-context rule in the skillaudit catalog or validate_security).

## Scope

1. New security rule: flag `${{ ... }}` GitHub-Actions expressions interpolated
   directly inside shell contexts of workflow YAML — `run:` blocks (and
   `script:` of github-script steps) — i.e. expression injection, CWE-94.
   Untrusted-context expressions (`github.event.*`, `github.head_ref`,
   `inputs.*`, `steps.*.outputs.*`) are the signal; `env:`-mediated use is the
   safe pattern and MUST NOT fire (two-sided).
2. Harden the marketplace auto-notify receiver snippet emitted by
   `cpv-setup-marketplace-auto-notification`: regex-validate `plugin`/`version`
   inputs, pass values via `env:` not inline interpolation.

## Acceptance

- Two-sided tests: direct `run: echo "${{ github.event.pull_request.title }}"`
  fires; the same value routed through `env:` does not; static expressions
  (`${{ matrix.os }}`, `${{ runner.os }}`) do not fire in shell context.
- CPV self-scan stays 0/0/0/0 (own workflows must already be env-mediated —
  fix them if the new rule finds real hits).
- Emitted receiver snippet passes the new rule.

## Approval log

- 2026-08-25T17:25:45+0200 — ACCEPTED from TRDD-6116ab4c P4 and moved to dev by
  the CPV session (authority delegated by USER 2026-08-25).
