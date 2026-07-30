---
trdd-id: I5X0TY2F
title: Selectively evaluate the original agent against its mono and micro variants
column: dev
created: 2026-07-30T11:55:20+0200
updated: 2026-07-30T11:55:20+0200
current-owner: cpv-main-session
task-type: feature
approval-tier: 0
parent-trdd: XUNZQ70I
relevant-rules: []
---

# Selectively evaluate the original agent against its mono and micro variants

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-07-30

- **Design:** `design/specs/agent-closure-and-variants.md` §6 is NORMATIVE.
- **NEXT ACTION:** add `scripts/cpv_agent_eval.py` + its skill.

## The gap

There is no way to tell whether converting an agent HELPED. The mono and micro
architectures trade opposite ways — mono pays one large cache-creation for
turn-1 readiness; micro pays more turns for a near-empty context each — so which
wins is an empirical question about a specific agent, and CPV currently offers no
answer.

## The honesty constraint that shapes the design

A comparison built on invented numbers is worse than no comparison, so the two
tiers are kept strictly apart and every number is labelled with the tier that
produced it:

- **Tier 1 — static cost model. Always runs, zero LLM calls, real measurements
  over real files.** Per variant: cached-prefix token estimate (the agent body
  plus injected preload content), per-invocation injected tokens, tool-schema
  surface count, closure size in files and bytes, turn-1 readiness, and the
  projected cost of N turns at the prefix-cache read rate. Deterministic, so this
  is the tier the test suite asserts.
- **Tier 2 — live A/B/C. OPT-IN via `--live`, never implied.** Runs each selected
  variant on a real task set and records REAL tokens, turns, wall time, outcome.
  **No mocks and no simulated numbers.** If the harness cannot run it reports
  UNKNOWN and exits non-zero; a missing `--tasks` file is an error, never an
  empty pass. A static estimate must never be presented as a measured result.

`--variants original,mono,micro` selects which to evaluate — the "selectively"
in the requirement.

## Pass criteria

- Tier 1 produces a deterministic table for any subset of the three variants,
  with no LLM call (assertable, and asserted).
- Tier 2 is unreachable without `--live`; absent `--tasks` it errors.
- Every reported number carries its tier; no static figure is labelled measured.
- `--json` plus a report under `reports/cpv-agent-eval/`.
- `ruff` + `mypy` clean; cold strict self-validate 0/0/0/0.

## Approval log

- 2026-07-30T11:55:20+0200 — Tier 0, authored directly as authorized work: the
  USER directed this feature explicitly ("the option to selectively evaluate the
  performances of the original agent compared to the 2 new versions").
