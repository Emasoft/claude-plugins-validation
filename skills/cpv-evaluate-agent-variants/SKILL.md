---
name: cpv-evaluate-agent-variants
description: "Compare an ORIGINAL agent against any subset of its ALL-IN-ONE / ONE-FOR-ALL / PLUGIN-OMNI variants, in two strictly separated tiers — a static cost model that always runs with zero LLM calls, and an opt-in live A/B/C that records only REAL tokens, time and outcomes. Use when the question is whether converting an agent actually helped, which architecture a specific agent should keep, how much prefix a `skills:` preload costs, or after running convert_agent.py. Reports the delta and stops: it ranks nothing and declares no winner. Used dynamically via cpv-the-skills-menu."
when_to_use: When an agent exists in more than one architecture and the choice between them has to be made on measurements rather than intuition — the static tier answers the token/prefix/closure question immediately, and the live tier answers the pass-rate question only when real runs have been captured.
user-invocable: false
---

# cpv-evaluate-agent-variants

## Overview

Drives `scripts/cpv_agent_eval.py`, which compares the ORIGINAL agent against any
subset of the three architectures:

| Architecture | `skills:` lists | Skills execute in |
|---|---|---|
| **ALL-IN-ONE** | every skill it needs | the same agent |
| **ONE-FOR-ALL** | every skill it needs | a separate subagent per skill |
| **PLUGIN-OMNI** | the plugin's `the-skills-menu` + the companion | resolved at runtime from the menu |

They trade in opposite directions — ALL-IN-ONE pays one large cache creation for turn-1
readiness, ONE-FOR-ALL pays more turns for a near-empty context per node, PLUGIN-OMNI
pays a menu lookup to keep the prefix tiny — so which one wins is an empirical question
about a SPECIFIC agent. Two tiers answer it, and they are kept strictly apart.

**The honesty rule that governs every number: a static estimate must never be presented
as a measured result.** Every figure carries the tier that produced it — `tier1-static`
(computed from real files, no model involved) or `tier2-live` (measured from a real
run). A missing measurement is reported as UNKNOWN with a non-zero exit: never as 0,
never simulated, never folded into a passing result.

## Prerequisites

- `uv` on PATH.
- The ORIGINAL agent `.md` file (the baseline every delta is measured against).
- Any variant `.md` files to compare, normally produced by
  `scripts/convert_agent.py --to all-in-one|one-for-all|plugin-omni`.
- The skills the agents reference resolvable on disk — either inside the agent's own
  plugin (auto-resolved) or passed with `--skills-root`. A wrong root makes every
  preload unresolvable and the cost model vacuous, so a bad root is a hard error.
- For the live tier only: a task file and a directory of captured per-run timings.

## Instructions

### Tier 1 — static cost model (always runs, zero LLM calls)

1. Run the script with the ORIGINAL plus every variant file that exists, and select the
   rows with `--variants` (any subset of
   `original,all-in-one,one-for-all,plugin-omni`). "The original vs the two new
   versions" is `--variants original,all-in-one,one-for-all`.
2. Read the per-variant measurements, all taken over the real files:
   - **cached-prefix tokens** — the agent body PLUS the FULL content of every preloaded
     skill. A `skills:` entry injects that skill's whole `SKILL.md` into every
     invocation, so that content IS a prefix cost; listing a skill is never free.
   - **injected tokens per invocation** — the preload portion, i.e. what the `skills:`
     list adds on top of the body, and what would vanish if the same skill were reached
     at runtime instead.
   - **tool-schema surface** — the count of declared tools, or `inherited` when the
     agent declares no `tools:` field (never reported as a surface of 0).
   - **closure size** — files and bytes across every reachable skill's `SKILL.md`,
     `references/**` and `scripts/**`.
   - **turn-1 readiness** — whether every reachable skill is already in the prefix, or
     some arrive only after a `Skill` call.
   - **projected N-turn prefix cost** — turn 1 at the cache-write rate, each later turn
     at the cache-read rate.
3. Optional flags: `--skills-root PATH` (repeatable), `--max-depth N`, `--turns N`
   (repeatable), `--json`, `--report PATH`.

### Tier 2 — live A/B/C (opt-in via `--live`, real numbers only)

The schema is the ecosystem one, adopted unchanged except for the configuration names
(`original` / `all-in-one` / `one-for-all` / `plugin-omni`):

- **input** — `evals/evals.json`:
  `{skill_name, evals: [{id, prompt, expected_output, files}]}` (`--tasks` defaults to
  that path).
- **per run** — `<runs-dir>/<config>/<eval-id>[/<run-id>]/timing.json`:
  `{total_tokens, duration_ms}`, plus `passed` there or in a sibling `result.json`.
- **aggregate** — `benchmark.json`: `{run_summary: {<config>: {pass_rate,
  time_seconds, tokens}}, delta: {...}}`, each metric `{mean, stddev}`.

Procedure:

1. Build the task set at `evals/evals.json`. Vary the phrasing between cases rather
   than repeating one sentence, cover at least one boundary condition, and use realistic
   context — a suite of near-identical happy-path prompts measures nothing the static
   tier did not already answer.
2. For each selected config, for each eval, dispatch ONE fresh subagent on that eval's
   prompt against that config's agent file. **Every run starts from a CLEAN context.** A
   shared context leaks the first variant's work into the second and makes the whole
   comparison meaningless.
3. The moment a run reports back, write
   `<runs-dir>/<config>/<eval-id>/timing.json` with the notification's `total_tokens`
   and `duration_ms`, and record `passed` by comparing the result against
   `expected_output`. **Those two values arrive in the task-completion notification and
   are not persisted anywhere else**, so a run whose numbers were not written is lost —
   and lost is UNKNOWN, never 0.
4. To obtain `stddev`, repeat each eval into numbered run directories. With one run per
   eval the spread is meaningless, so it is omitted rather than printed as a fake 0.
5. Aggregate with `--live --tasks ... --runs-dir ...`.

## Output

- A findings-style report under `reports/cpv-agent-eval/` (or at `--report PATH`), with
  a numbered table per tier: the Tier-1 measurements, the projected N-turn cost, the
  Tier-1 delta, the Tier-1 notes, and — when `--live` ran — the Tier-2 `run_summary`,
  the Tier-2 delta, and any UNKNOWN rows with their reason.
- `--json` emits the same content as one object: `{original, variants, tier1, tier2}`,
  where `tier2` is `null` unless `--live` ran. Every row carries its `tier` field.
- With `--live`, a `benchmark.json` in the adopted ecosystem shape beside the report.

**The delta is the deliverable.** It states what a variant COSTS (time, tokens) and what
it BUYS (pass rate). Report both sides and stop — "a higher pass rate for more tokens"
is a trade-off for the human to weigh, so this skill never names a winner, never orders
the variants, and never recommends one. When asked which to keep, present the delta plus
the constraint that actually binds the case (latency budget, token budget, or pass rate)
and let the decision be made explicitly.

## Error Handling

Exit codes: `0` everything selected was produced, `1` input error (a path that is not a
file, a bad `--variants` name, a non-directory `--skills-root`, a missing or malformed
task file), `2` the live tier is UNKNOWN because a selected config was not fully
measured.

- **NOT-EVALUATED** — the variant was named in `--variants` but its file was not
  supplied. It stays in the table with its reason rather than disappearing. Generate it
  with `convert_agent.py --to <architecture>`, then re-run.
- **UNKNOWN** — the live tier could not measure a selected config. The report names the
  reason: no captured runs, a missing run for one eval, or a lost token / duration /
  outcome value. Fix the capture and re-run. A partially-measured table is never read as
  clean.
- **A missing task file under `--live` is an error, not an empty pass.** The same holds
  for a malformed one, an empty `evals` list, and an eval with no `id` or no `prompt`.
- **An unresolved preload** appears in the Tier-1 notes and counts as 0 prefix tokens.
  That is a real defect in the agent, not a quirk of the measurement — `validate_agent`
  reports it as AC1.

## Examples

Static comparison of the original against two generated variants:

```bash
uv run python scripts/cpv_agent_eval.py \
  --original agents/my-agent.md \
  --all-in-one agents/my-agent-all-in-one.md \
  --one-for-all agents/my-agent-one-for-all.md \
  --variants original,all-in-one,one-for-all
```

Same comparison as JSON, with explicit skill roots and a 200-turn projection:

```bash
uv run python scripts/cpv_agent_eval.py \
  --original agents/my-agent.md \
  --plugin-omni agents/my-agent-plugin-omni.md \
  --skills-root skills --turns 1 --turns 200 --json
```

Live A/B/C over captured runs:

```bash
uv run python scripts/cpv_agent_eval.py \
  --original agents/my-agent.md \
  --all-in-one agents/my-agent-all-in-one.md \
  --variants original,all-in-one \
  --tasks evals/evals.json --runs-dir evals/runs --live
```

## Resources

- `scripts/cpv_agent_eval.py` — the evaluator this skill drives.
- `scripts/convert_agent.py` — converts ONE agent to any of the three architectures.
- `scripts/cpv_agent_closure.py` — the closure SSOT both of the above read.
- `cpv-create-mono-agent` — build an ALL-IN-ONE agent.
- `cpv-create-micro-agents-workflow` — build a ONE-FOR-ALL agent.
