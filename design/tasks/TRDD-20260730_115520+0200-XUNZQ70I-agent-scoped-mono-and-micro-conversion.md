---
trdd-id: XUNZQ70I
title: Convert one agent into an all-in-one mono agent or a micro-subagent graph
column: dev
created: 2026-07-30T11:55:20+0200
updated: 2026-07-30T11:55:20+0200
current-owner: cpv-main-session
task-type: feature
approval-tier: 0
parent-trdd: 7KS7KP7U
eht: [I5X0TY2F]
relevant-rules: []
---

# Convert one agent into an all-in-one mono agent or a micro-subagent graph

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-07-30

- **Design:** `design/specs/agent-closure-and-variants.md` §5 is NORMATIVE
  (§1–§2 for the closure surface this consumes).
- **NEXT ACTION:** add `scripts/convert_agent.py`, then extend the two existing
  skills with the agent-scoped path.

## The gap

Both generators already exist and BOTH ARE PLUGIN-WIDE — verified by reading them:

- `create_mono_agent.py` inlines **every non-meta skill of a plugin** into one new
  agent.
- `create_micro_agents_workflow.py` builds a launcher plus a workflow whose
  palette is **the plugin's whole skill set**.

Neither can take an EXISTING agent and produce the two variants OF THAT AGENT.
That is what is missing: the conversion is agent-scoped, and the skill set to
inline or to graph is the agent's own reachable closure, not the plugin's palette.

## Scope

```
convert_agent.py <agent.md> --to mono  [--out DIR] [--name NAME] [--force]
convert_agent.py <agent.md> --to micro [--out DIR] [--name NAME] [--force]
```

- **mono** — `<name>-mono.md`: the source body with every REACHABLE skill inlined
  under `## Skill: <name>` (frontmatter stripped, headings demoted so one H1
  survives). Turn-1 ready, no runtime load, union of the source's tool grants, no
  `model:` pin (CA-04).
- **micro** — `<name>-launcher.md` + `workflows/<name>-micro.ts`: a thin launcher
  plus a Workflow-tool graph whose nodes are the closure's skills, each a
  near-empty micro-agent, output threaded into the next input, per-step verify.

Then extend `cpv-create-mono-agent` and `cpv-create-micro-agents-workflow` with
the agent-scoped path, delegating here. Their plugin-wide path is UNCHANGED.

## Invariants

- Both modes read the closure through the T1 SSOT. Neither may re-derive the
  skill set — a second derivation would disagree with what the validator checks.
- Never overwrite without `--force`.
- Both emitted agents must pass `validate_agent`; the emitted `.ts` must land
  under an existing `known_dirs` entry (`workflows/`) so it draws no structural
  finding and is still security-scanned.

## Pass criteria

- Round-trip on a real multi-skill agent: both variants generated, both pass
  `validate_agent` with 0 blocking findings.
- The mono body contains every reachable skill and no unreachable one.
- The micro graph's node set equals the closure's reachable skill set.
- Two-sided: `--force`-less re-run refuses; a source agent with an empty closure
  is reported, not silently emitted as an empty shell.
- `ruff` + `mypy` clean; cold strict self-validate 0/0/0/0.

## Approval log

- 2026-07-30T11:55:20+0200 — Tier 0, authored directly as authorized work: the
  USER directed this feature explicitly ("improve the commands/skills to convert
  agents to all in one agents or micro subagents graphs pipelines").
