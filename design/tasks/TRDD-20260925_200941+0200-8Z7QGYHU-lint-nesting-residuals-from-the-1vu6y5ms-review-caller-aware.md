---
trdd-id: 8Z7QGYHU
title: Lint nesting residuals from the 1VU6Y5MS review — caller-aware budget, phase offset, env-override re-entry, phase-level reachability
column: backburner
status: tasked
created: 2026-09-25T20:09:41+0200
updated: 2026-09-25T20:10:50+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: refactor
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-25T20:09:41+0200
---

# Lint nesting residuals from the 1VU6Y5MS review — caller-aware budget, phase offset, env-override re-entry, phase-level reachability

## Approval log

- 2026-09-25T20:09:41+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## The residuals

- 1VU6Y5MS box 1 landed as 6b3d5141 (all 17 _run_linter sites on _LINTER_TIMEOUT_DEFAULT=110 / _LINTER_PER_FILE_TIMEOUT_DEFAULT=60, strictly below the 120s harness outer bound; invariant pinned by two tests). The adversarial review fork found four residuals the archived card cannot carry (archived = immutable). This card owns them.
- R1 CALLER-AWARE BUDGET: one constant cannot serve both a 120s harness caller and a 600s gate caller. The 180→110 cut is measured safe on CPV's own tree (mypy 9.3s, pyright 46.8s heaviest, sqlfluff --version 7.0s cold, clippy --version 0.3s — all ≥ 2x headroom), but a downstream tree needing >110s of mypy/clippy now silently skips with a non-blocking WARNING (cannot-check-is-not-clean class). Principled shape when that population materialises — inner = min(default, remaining outer budget) — needs an outer-budget plumbing path first.
- R2 PHASE-START OFFSET: REPO LINT starts after prior validate phases, so the true inner window is outer_deadline − phase_start_offset, not outer_deadline. If the offset exceeds 10s under a 120s harness caller, a 110s budget is STILL unreachable — the invariant test pins the constant against the bound, not the offset. An offset-deriving test (or budget-relative-to-remaining design) would close this.
- R3 ENV-OVERRIDE RE-ENTRY: PLUGIN_REPO_LINT_TIMEOUT can RAISE every spawn budget above the outer bound and recreate the exact deadlock by design (issue #148's override semantics deliberately kept). The nesting invariant holds for DEFAULTS only; a doc note or a clamp-vs-outer check is the cheap guard.
- R4 PHASE-LEVEL REACHABILITY: the fix makes per-SPAWN handlers reachable; the per-FILE 60s x N aggregate is bounded by the 600s phase budget (#162), which itself exceeds a 120s harness caller — so phase-level graceful handlers remain unreachable under the smallest harness caller. Out of 1VU6Y5MS's per-spawn scope; recorded so nobody believes the phase budget was fixed.
