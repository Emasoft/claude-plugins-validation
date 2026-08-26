---
trdd-id: WT0FLTMM
title: GitHub-Actions expression-injection detector plus hardened auto-notify receiver snippet
column: complete
created: 2026-08-25T17:25:45+0200
updated: 2026-08-26T05:54:23+0200
current-owner: cpv-session
task-type: security
min-approval-requirement: none
parent-trdd: TRDD-6116ab4c
---

# TRDD-WT0FLTMM — GHA expression-injection detector + receiver hardening

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-08-25T22:40:58+0200

Resumed job, finished this session. Both scope items done:

- Task 1 — `validate_workflow_expression_injection` (RC-WORKFLOW-EXPR-INJECT,
  MAJOR) shipped in `scripts/validate_plugin.py`, registered in the validator
  dispatch list. Reuses the report/severity/message-style machinery already in
  the module (no parallel path). `tests/test_workflow_expression_injection.py`
  (18 tests, two-sided) green. CPV's own `.github/workflows/*.yml` scan clean
  (0 findings).
- Task 2 — `notify-workflow-template.md` receiver snippet hardened: every
  event/step value passed via `env:` (never inline `${{ }}` in a `run:`/`with`
  script body) in both the curl and `gh` fallback forms, AND (this session's
  addition, was still missing) `plugin`/`version` are now regex-validated
  right after extraction (slug pattern for name, semver pattern for version)
  in BOTH Template A and Template B, failing the step with `::error::` before
  the value ever reaches an `env:` var or a dispatch payload.
- No skillaudit catalog patterns touched → no re2-audit / self-hash regen
  owed.

Verification run this session: `pytest tests/test_workflow_expression_injection.py`
18 passed; `ruff check` clean on both changed scripts/test files; `mypy
--ignore-missing-imports` clean on both. Full report:
`reports/board-drain-impl/` (dated file this session).

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
- 2026-08-26T05:54:23+0200 — COMPLETE by the CPV session. Verified first-hand at
  closure rather than trusting the STATE block: `validate_workflow_expression_injection`
  is defined at `scripts/validate_plugin.py:6133`, `RC-WORKFLOW-EXPR-INJECT`
  appears 4× in that module, and `tests/test_workflow_expression_injection.py`
  is 18/18 green re-run this session. Gate proof: serial suite 13,076 passed /
  3 skipped (`PYTEST4_EXIT=0`); cache-cold strict self-validate 0/0/0/0
  (`SELFVAL4_EXIT=0`).
