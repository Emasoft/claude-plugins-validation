---
trdd-id: XUNZQ70I
title: Convert one agent into an ALL-IN-ONE or ONE-FOR-ALL or PLUGIN-OMNI agent
column: complete
created: 2026-07-30T11:55:20+0200
updated: 2026-08-25T17:25:22+0200
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

## THE INLINING PROHIBITION (the rule everything else follows from)

**A skill's content is NEVER copied into an agent** — not concatenated, not
duplicated, not embedded. An agent REFERENCES skills by name in `skills:`
frontmatter and nowhere else. The reason is single-source-of-truth: a skill must
stay INDEPENDENT so it can be shared by many agents and edited/fixed/updated ONCE.
An inlined copy is a second source that rots the moment the original changes, and
with N agents inlining it there are N stale copies and no signal that any drifted.

## The three architectures (canonical vocabulary — spec §1.1)

| architecture | `skills:` frontmatter lists | body carries | skills execute in |
|---|---|---|---|
| **ALL-IN-ONE** | every skill it needs | how to use each skill, at the right time and in the right choice branch | **the same agent** |
| **ONE-FOR-ALL** | every skill it needs | the SAME routing / choice tree | **a separate subagent per skill** (one-skill-agent, minimal context) |
| **PLUGIN-OMNI** | exactly ONE skill — the plugin's `the-skills-menu` | routing through that menu | resolved at runtime from the menu |

**ALL-IN-ONE and ONE-FOR-ALL are otherwise essentially the same construction** —
the ONLY difference is WHERE a skill runs. Any implementation making them differ
in more than that has misread the spec.

All three pair their list with the **`Skill` tool** (that is how skills /
micro-agents get launched), so all three require the §1 gate to be OPEN.

## The gap

Both generators already exist and BOTH ARE PLUGIN-WIDE — verified by reading them:
`create_mono_agent.py` inlines **every non-meta skill of a plugin** into one new
agent; `create_micro_agents_workflow.py` builds a launcher whose palette is **the
plugin's whole skill set**. Neither can take an EXISTING agent and produce a
variant OF THAT AGENT, and neither implements the frontmatter-based ALL-IN-ONE
shape at all.

## Three facts that constrain the implementation, all verified not assumed

1. **`agent:` IS a valid skill frontmatter field** (checked against
   `cpv_validation_common.SKILL_FRONTMATTER_FIELDS`) — so a one-skill-agent is a
   spec-valid primitive. ONE-FOR-ALL is therefore an IN-PLACE `agent:` addition to
   the existing shared skill, never a copy of it.
2. **`skills:` is NOT a valid skill frontmatter field** — agent-only. So a
   micro-agent node cannot declare its own skill list, and the ONE-FOR-ALL choice
   tree must live in the agent's body. Any design nesting `skills:` in a skill is
   invalid.
3. **Adding `agent:` to a SHARED skill changes its execution for EVERY agent that
   lists it.** This follows from the no-copy rule and is the one genuine cost of a
   ONE-FOR-ALL conversion. So the mode MUST report each shared skill it would
   convert plus how many other agents list it, and MUST NOT mutate a shared skill
   without `--force`. A silent mutation would change behaviour for agents nobody
   was looking at.

## Supersession — `create_mono_agent.py` must CHANGE, not gain a flag

It currently concatenates every non-meta skill body into one agent, which is
exactly what the inlining prohibition forbids. Convert it to the frontmatter
model. Deliberate breaking change to published behaviour (MAJOR bump), and ONE
version of the mechanism only — no inlining path retained behind a compatibility
flag.

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
- **No mode copies skill content anywhere.** Asserted mechanically: no emitted
  agent body may contain a substring of any closure skill's body beyond its NAME.
  This is the acceptance test for the inlining prohibition.
- ALL-IN-ONE lists every reachable skill and no unreachable one.
- ONE-FOR-ALL emits the same routing body as ALL-IN-ONE and differs ONLY by the
  in-place `agent:` addition making each listed skill run in a subagent; it
  refuses to mutate a shared skill without `--force` and reports the other agents
  affected.
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
- 2026-08-25T17:25:22+0200 — CLOSED as complete by the CPV session (board drain;
  authority delegated by USER 2026-08-25). SHIPPED v4.0.0 — scripts/convert_agent.py
  live, verified first-hand (batch_ag).
