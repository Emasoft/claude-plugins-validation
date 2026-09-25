---
trdd-id: 8Z7QGYHU
title: Lint nesting residuals from the 1VU6Y5MS review — caller-aware budget, phase offset, env-override re-entry, phase-level reachability
column: todo
status: tasked
created: 2026-09-25T20:09:41+0200
updated: 2026-09-25T20:21:10+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: docs
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
- 2026-09-25T20:19:25+0200 — column → todo by main-agent@claude-plugins-validation. review: R2/R3 name cheap concrete guards; backburner would park them — board is the instruction
2026-09-25 REVIEW ROUND 2 (adversarial fork on this card's creation): verdict — writes directionally right, three amendments demanded. ALL APPLIED: (1) R1 re-scoped — real-work measurements (mypy/pyright) separated from --version startup probes (sqlfluff/clippy), cold-CI caveat added (CI pyright 2-4x warm can exceed 110s; CPV's own gate caller is in the risk population); (2) missing residuals 3b and 5d from the first review landed as R5 and R6; (3) card moved backburner → todo (R2/R3 name cheap concrete guards — not deferrable) and task-type corrected refactor → docs. Minor noted and accepted: 3c (per-file sites reframed into the fix) is cosmetic and folded into R4's honesty note.

## The residuals

- 1VU6Y5MS box 1 landed as 6b3d5141 (all 17 _run_linter sites on _LINTER_TIMEOUT_DEFAULT=110 / _LINTER_PER_FILE_TIMEOUT_DEFAULT=60, strictly below the 120s harness outer bound; invariant pinned by two tests). The adversarial review fork found four residuals the archived card cannot carry (archived = immutable). This card owns them.
- R1 CALLER-AWARE BUDGET: one constant cannot serve both a 120s harness caller and a 600s gate caller. Measured on CPV's own tree, warm cache: REAL-WORK numbers are mypy 9.3s (11.8x headroom) and pyright 46.8s (2.35x headroom — thin); sqlfluff 7.0s and clippy 0.3s were --version STARTUP PROBES only, they do not bound a real lint (sqlfluff scales per SQL file, clippy per crate). COLD-CI CAVEAT: CI pyright runs without its analysis cache (typically 2-4x warm), so a cold-CI pyright over a big scripts/ tree can plausibly exceed 110s and silently skip where 180s previously completed — CPV's own gate caller is already in the risk population, not just downstream trees. Any tree needing >110s now silently skips with a non-blocking WARNING (cannot-check-is-not-clean class). Principled shape when that population materialises — inner = min(default, remaining outer budget) — needs an outer-budget plumbing path first.
- R2 PHASE-START OFFSET: REPO LINT starts after prior validate phases, so the true inner window is outer_deadline − phase_start_offset, not outer_deadline. If the offset exceeds 10s under a 120s harness caller, a 110s budget is STILL unreachable — the invariant test pins the constant against the bound, not the offset. An offset-deriving test (or budget-relative-to-remaining design) would close this.
- R3 ENV-OVERRIDE RE-ENTRY: PLUGIN_REPO_LINT_TIMEOUT can RAISE every spawn budget above the outer bound and recreate the exact deadlock by design (issue #148's override semantics deliberately kept). The nesting invariant holds for DEFAULTS only; a doc note or a clamp-vs-outer check is the cheap guard.
- R4 PHASE-LEVEL REACHABILITY: the fix makes per-SPAWN handlers reachable; the per-FILE 60s x N aggregate is bounded by the 600s phase budget (#162), which itself exceeds a 120s harness caller — so phase-level graceful handlers remain unreachable under the smallest harness caller. Out of 1VU6Y5MS's per-spawn scope; recorded so nobody believes the phase budget was fixed.
- R5 (review 3b) MESSAGE DURATION STRIPPED: 'after 120s' / 'after 180s' was dropped from four timeout WARNING messages (ruff, eslint, pyright, mypy) in 6b3d5141 — a diagnosability loss with no compensating mechanism recorded. Cheap guard: restore the number via the constant (f-string with the resolved timeout) so the message stays true if the constant moves.
- R6 (review 5d) ARBITRARY 5s MARGIN: the invariant test's >=5s margin below the 120s outer bound is derived from no teardown-cost measurement, and R2's 10s-offset arithmetic depends on the same headroom. Either measure a teardown budget or document the margin as a convention.
