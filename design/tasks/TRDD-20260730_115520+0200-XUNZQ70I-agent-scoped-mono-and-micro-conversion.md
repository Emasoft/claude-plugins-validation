---
trdd-id: XUNZQ70I
title: Convert one agent into an ALL-IN-ONE or ONE-FOR-ALL or PLUGIN-OMNI agent
column: dev
created: 2026-07-30T11:55:20+0200
updated: 2026-07-30T12:34:00+0200
current-owner: cpv-main-session
task-type: feature
approval-tier: 0
parent-trdd: 7KS7KP7U
eht: [I5X0TY2F]
relevant-rules: []
---

# Convert one agent into an ALL-IN-ONE or ONE-FOR-ALL or PLUGIN-OMNI agent

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-07-30

- **Design:** `design/specs/agent-closure-and-variants.md` §1.1, §1.2 and §5 are
  NORMATIVE (§1–§2 for the closure surface this consumes).
- **NEXT ACTION:** add `scripts/convert_agent.py`, then extend the two existing
  skills with the agent-scoped path.
- **SUPERSEDED — do NOT carry forward:** the first draft of this TRDD described
  two variants named "mono" and "micro", with mono CONCATENATING skill bodies
  into the agent body. Both the count and the mechanism were wrong. The USER
  fixed the vocabulary on 2026-07-30: there are THREE architectures, and
  ALL-IN-ONE is a FRONTMATTER strategy, not body inlining.

## The three architectures (canonical vocabulary — spec §1.1)

| architecture | `skills:` frontmatter | body carries |
|---|---|---|
| **ALL-IN-ONE** | every skill it needs | how to use each skill, at the right time and in the right choice branch |
| **ONE-FOR-ALL** | the micro-agents it dispatches | ONLY the graph / choice tree / skeleton; each node is a one-skill-agent (a skill with `agent:` in frontmatter, minimal context) |
| **PLUGIN-OMNI** | exactly ONE skill — the plugin's `the-skills-menu` | routing through that menu |

All three pair their strategy with the **`Skill` tool**, so all three require the
§1 reachability gate to be OPEN.

## The gap

Both generators already exist and BOTH ARE PLUGIN-WIDE — verified by reading them:
`create_mono_agent.py` inlines **every non-meta skill of a plugin** into one new
agent; `create_micro_agents_workflow.py` builds a launcher whose palette is **the
plugin's whole skill set**. Neither can take an EXISTING agent and produce a
variant OF THAT AGENT, and neither implements the frontmatter-based ALL-IN-ONE
shape at all.

## Two facts that constrain the implementation, both verified not assumed

1. **`agent:` IS a valid skill frontmatter field** (checked against
   `cpv_validation_common.SKILL_FRONTMATTER_FIELDS`) — so a one-skill-agent is a
   spec-valid primitive.
2. **`skills:` is NOT a valid skill frontmatter field** — agent-only. So a
   micro-agent node cannot declare its own skill list, and the ONE-FOR-ALL choice
   tree must live in the agent's body. Any design nesting `skills:` in a skill is
   invalid.

## The AC1 interaction that makes §1.2 load-bearing

Every generated variant must carry `verification-before-completion` in `skills:`.
But AC1 (TRDD-7KS7KP7U) makes an unresolvable preload a MAJOR — so a generator
that adds the name without ensuring the skill exists emits agents that fail CPV's
own validator. The generator MUST therefore write
`skills/verification-before-completion/SKILL.md` from
`design/specs/verification-before-completion.template.md` when absent, and NEVER
overwrite an existing one (the user may have adapted it).

## Pass criteria

- All three variants generate from one real multi-skill source agent and each
  passes `validate_agent` with **zero blocking findings, AC1–AC4 included** —
  that is the real acceptance test, since a generator emitting an unresolvable
  preload has produced a broken agent.
- ALL-IN-ONE lists every reachable skill and no unreachable one, and does NOT
  concatenate skill bodies.
- ONE-FOR-ALL emits one `agent:`-carrying node skill per closure node, and its
  agent body carries the graph only — no step contents.
- PLUGIN-OMNI lists exactly the menu + the companion; a target plugin lacking
  `the-skills-menu` gets one generated from the REAL inventory, never empty.
- Two-sided: a `--force`-less re-run refuses; an empty closure is reported, not
  silently emitted as a shell.
- `ruff` + `mypy` clean; cold strict self-validate 0/0/0/0.

## Approval log

- 2026-07-30T11:55:20+0200 — Tier 0, authored directly as authorized work: the
  USER directed this feature explicitly ("improve the commands/skills to convert
  agents to all in one agents or micro subagents graphs pipelines").
- 2026-07-30T12:34:00+0200 — Scope CORRECTED by the USER: three named
  architectures, ALL-IN-ONE is frontmatter-based, and every generated variant
  carries `verification-before-completion`.
