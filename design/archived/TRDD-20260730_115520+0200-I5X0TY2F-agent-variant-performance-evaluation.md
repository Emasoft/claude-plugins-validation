---
trdd-id: I5X0TY2F
title: Selectively evaluate the original agent against its converted variants
column: complete
created: 2026-07-30T11:55:20+0200
updated: 2026-08-25T17:25:22+0200
current-owner: cpv-main-session
task-type: feature
approval-tier: 0
parent-trdd: XUNZQ70I
relevant-rules: []
---

# Selectively evaluate the original agent against its converted variants

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-07-30

- **Design:** `design/specs/agent-closure-and-variants.md` §6 is NORMATIVE;
  §1.1 fixes the variant vocabulary.
- **NEXT ACTION:** add `scripts/cpv_agent_eval.py` + its skill.
- **SUPERSEDED — do NOT carry forward:** the first draft named the variants
  "mono" and "micro". The USER fixed the vocabulary on 2026-07-30 to
  **ALL-IN-ONE**, **ONE-FOR-ALL**, **PLUGIN-OMNI** (spec §1.1).

## The gap

There is no way to tell whether converting an agent HELPED. The three
architectures trade in opposite directions — ALL-IN-ONE pays one large
cache-creation for turn-1 readiness; ONE-FOR-ALL pays more turns for a near-empty
context per node; PLUGIN-OMNI pays a menu lookup to keep the prefix tiny — so
which wins is an empirical question about a SPECIFIC agent, and CPV currently
offers no answer.

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

`--variants` selects which rows to evaluate from
`original,all-in-one,one-for-all,plugin-omni` — the "selectively" in the
requirement. "The original vs the 2 new versions" is
`--variants original,all-in-one,one-for-all`. A named variant whose file is not
supplied is reported NOT-EVALUATED, never silently dropped from the table.

## Pass criteria

- Tier 1 produces a deterministic table for any subset of the four rows
  (`original` + the three architectures), with no LLM call (assertable, and
  asserted).
- Tier 2 is unreachable without `--live`; absent `--tasks` it errors.
- Every reported number carries its tier; no static figure is labelled measured.
- `--json` plus a report under `reports/cpv-agent-eval/`.
- `ruff` + `mypy` clean; cold strict self-validate 0/0/0/0.

## Approval log

- 2026-07-30T11:55:20+0200 — Tier 0, authored directly as authorized work: the
  USER directed this feature explicitly ("the option to selectively evaluate the
  performances of the original agent compared to the 2 new versions").
- 2026-08-25T17:25:22+0200 — CLOSED as complete by the CPV session (board drain;
  authority delegated by USER 2026-08-25). SHIPPED v4.0.0 — scripts/cpv_agent_eval.py
  live, verified first-hand (batch_ag).
