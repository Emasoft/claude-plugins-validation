---
trdd-id: 478d9687-46df-45ae-9a67-2b57c1845c8e
title: Universal skills-index — drop per-agent preload lists, one shared catalog
column: superseded
created: 2026-05-19T14:06:53+0200
updated: 2026-08-25T17:25:14+0200
superseded-by: TRDD-9dd64dbf
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-478d9687 — Universal skills-index: drop per-agent preload lists, replace with one shared catalog

**Filename:** `design/tasks/TRDD-20260519_140653+0200-478d9687-skills-index-universal-loader.md`
**Tracked in:** this repo (`design/tasks/` is git-tracked)

## Origin (provenance)

User feedback 2026-05-19 (after reading the agent-consolidation
plan):

> "let's do it in an even simpler way, to avoid wasting tokens. since
> you said that the skills listed are not a limit and the agent can
> load any skill, then all you need to do is to inform the agent of
> the available skills. Remove the list of skills from the frontmatter
> of all agents. Then add one new skill only to the frontmatter of
> all agents, called `skills-index`. This skill-index must be a new
> skill completely dedicated to one function: teaching the agent what
> skills are available in the cpv plugin, and when to use them. With
> that skill every agent can load only the skills he needs, when it
> needs them, saving context memory and accessing all the skill
> functionalities in all specializations. Nothing must change in the
> agent body. Maybe just one line at the beginning, saying: 'You must
> load the skills you need dynamically with the Skill() tool. Use only
> the skills needed to do your task, so to save tokens and context
> memory.'"

This subsumes the heavier "agent consolidation" brainstorm — it gets
the same benefits (token saving, cross-specialisation flexibility)
with ~1/10 the file churn.

## Problem statement

Even after v2.92.0 (TRDD-14cc93a6) documented runtime routing via the
Skill tool, every CPV agent still **preloads** 1–8 skills via the
`skills:` frontmatter. The harness injects the full body of every
listed skill into the subagent's context at startup. Consequences:

| # | Symptom | Root cause |
|---|---------|------------|
| 1 | Most preloaded skills are NOT needed for the run actually performed (e.g. `plugin-fixer` preloads `canonical-pipeline` even when the user is fixing a single MINOR finding) | Static preload at frontmatter level |
| 2 | Agents have artificially-narrow skill access — the frontmatter list misleads readers into thinking other skills are "off-limits" | False ACL semantics |
| 3 | Adding a new skill requires hand-wiring it into N agent frontmatters | Lots of coupling churn |
| 4 | `Loaded by <agent>` claims in skill descriptions go stale every time an agent's `skills:` list changes | Distributed ownership |

The Anthropic spec (verified at
`code.claude.com/docs/en/sub-agents`) explicitly says the
`skills:` field is preload-only:

> "This field controls which skills are preloaded, not which skills
> the subagent can access: without it, the subagent can still discover
> and invoke project, user, and plugin skills through the Skill tool
> during execution."

So we're already paying for preloading that buys nothing the runtime
Skill tool doesn't already offer.

## Goal

Replace per-agent preload lists with **one universal catalog skill**:

1. Create `skills/skills-index/SKILL.md` — a single document that
   lists every CPV skill with "when to use it" guidance.
2. Every CPV agent declares only `skills: [skills-index]` in
   frontmatter.
3. Each agent body gains one prepended sentence reminding it to load
   skills dynamically.
4. Existing skill descriptions update their `Loaded by <agent>`
   claims to the generic
   `Loaded dynamically by CPV agents via skills-index`.
5. Skills themselves stay agent-agnostic — they were already designed
   to be skill-shaped, so most need only a small descriptive tweak.

Result: each agent's preload payload drops from ~5-30 KB of skill
content to ~5 KB of catalog. Skills load on demand only when an
agent's Phase 0 triage actually needs them.

## Out of scope

| # | Item | Reason |
|---|------|--------|
| 1 | Deleting any agent | Use Solution A/D later for that. This TRDD is purely the universal-loader change. |
| 2 | Renaming any skill | Out of scope. |
| 3 | Restructuring skill bodies for agent-agnostic instructions | Best-effort pass; most skills already agent-agnostic. Deferred sweep for any that are still tightly coupled. |
| 4 | Removing the `skills:` frontmatter field from agents entirely | Keep it with `[skills-index]` so the existing `test_all_agents_declare_skills_list` test stays meaningful. |

## Design

### `skills-index` skill

Location: `skills/skills-index/SKILL.md` + `references/skills-catalog.md`.

Frontmatter:

```yaml
---
name: skills-index
description: "Catalog of every CPV skill with when-to-use guidance. Loaded as the ONLY preloaded skill by all CPV agents (TRDD-478d9687) — agents consult this catalog and invoke other skills on demand via the Skill tool. Use when an agent needs to pick which downstream CPV skill to invoke for the current task."
user-invocable: false
allowed-tools: Read
---
```

Body structure:

1. Overview (≤300 chars) — "I am the catalog. Read me first to pick a skill."
2. Table of every skill: name, "When to use" phrase, key inputs.
3. The fully-qualified `Skill({skill: "claude-plugins-validation:<name>"})` invocation pattern for each, so the orphan-detection test sees the skill as "loaded".
4. Cross-cutting routing tips (e.g. "if you're a fixer and the finding count > 40 use batch-fix-protocol AND emit `[BATCH_REQUIRED]`").

Size: ~5 KB SKILL.md + references/skills-catalog.md offload for any
overflow. SKILL.md MUST stay under 5000 chars per the validator's
WARNING ceiling.

### Agent frontmatter change

Before:

```yaml
skills:
  - fix-validation
  - canonical-pipeline
  - plugin-validation-skill
  - marketplace-authoring-contract
  - batch-fix-protocol
```

After:

```yaml
skills:
  - skills-index
```

Identical for all 11 agents. No exceptions in this TRDD.

### Agent body change

Add this exactly-one-line at the top of every agent body (after the
H1 heading):

```text
**Dynamic skill loading:** You MUST load the skills you need at runtime via the `Skill` tool. Use ONLY the skills needed for the current task to save tokens. The pre-loaded `skills-index` skill is your catalog — consult it BEFORE invoking any other skill.
```

Per user direction, **nothing else changes in the body**. The
existing Phase 0 triage / routing tables (already added in v2.92.0
for plugin-fixer and doctor) continue to work — they just now
invoke skills via Skill tool calls that happen to be already
documented in the catalog.

### Skill description update

Each existing skill's `description:` field has its
`Loaded by <agent>` clause swapped to:

```text
Loaded dynamically by CPV agents via skills-index.
```

This:

- Satisfies the validator's `RE_LOADED_BY` regex (still matches).
- No longer triggers the `test_loaded_by_claims_match_actual_agents`
  test (the test only inspects descriptions with `Loaded by <named-agent>`
  patterns; generic phrasing skips).

### Orphan-detection test

`test_every_skill_is_loaded_by_at_least_one_agent` already supports
"loaded via the Skill tool in another skill's body" (added in v2.91.1
Path 4). The `skills-index` SKILL.md body will contain a
fully-qualified `Skill({skill: "claude-plugins-validation:<name>"})`
invocation pattern for every CPV skill, satisfying the test.

The `skills-index` skill itself is loaded by every agent's
frontmatter `skills:` list (Path 1).

## Phases

| # | Phase | Deliverables |
|---|-------|--------------|
| 1 | Catalog | Create `skills/skills-index/SKILL.md` + `references/skills-catalog.md` |
| 2 | Frontmatter sweep | 11 agent files updated: `skills: [...]` → `skills: [skills-index]` + one-line reminder in body |
| 3 | Skill description sweep | 32 existing skills' descriptions update `Loaded by <agent>` → `Loaded dynamically by CPV agents via skills-index` |
| 4 | Tests | Adjust `test_all_agents_declare_skills_list` / add `test_skills_index_universal.py` |
| 5 | Validate + ship | self-scan 0/0/0/0/0, full suite green, publish v2.93.0 |

## Test plan

| # | Test | What it pins |
|---|------|--------------|
| 1 | `test_skills_index_universal.py::test_index_exists` (new) | `skills/skills-index/SKILL.md` is present and well-formed |
| 2 | …`::test_every_agent_declares_only_skills_index` | All 11 agents have `skills: ["skills-index"]` (exactly one entry, exactly that name) |
| 3 | …`::test_every_agent_body_has_dynamic_loading_reminder` | The one-line reminder is present in every agent body |
| 4 | …`::test_index_lists_every_skill` | The SKILL.md body / references contain a fully-qualified Skill call for every CPV skill except `skills-index` itself |
| 5 | `test_consolidation_v211.py::test_every_skill_is_loaded_by_at_least_one_agent` (existing) | Still passes — every skill reachable via skills-index Path 4 scan |
| 6 | `test_consolidation_v211.py::test_loaded_by_claims_match_actual_agents` (existing) | Updated descriptions no longer trigger named-agent checks — test still passes |

## Acceptance criteria

- [ ] `skills/skills-index/SKILL.md` exists and validates
- [ ] All 11 agents have `skills: [skills-index]` only
- [ ] All 11 agent bodies have the one-line dynamic-loading reminder
- [ ] All 32 existing skills' descriptions updated
- [ ] All ~5400 existing tests + new ones pass
- [ ] Self-scan 0/0/0/0/0
- [ ] Ship as v2.93.0

## Risks + mitigations

| # | Risk | Mitigation |
|---|------|------------|
| 1 | A skill silently depends on the calling agent's preloaded peers (e.g. assumes `fix-validation` is already in context) | The catalog explicitly tells the agent to invoke peer skills on demand; the skill bodies that need peers reference them in their own bodies. Most CPV skills are already self-contained — quick audit catches edge cases. |
| 2 | `skills-index` body exceeds the 5000-char SKILL.md ceiling | Offload the per-skill table into `references/skills-catalog.md` with progressive disclosure. |
| 3 | The orphan-detection test breaks because some skill is no longer transitively reachable | The catalog MUST reference every CPV skill via fully-qualified Skill call — the new `test_index_lists_every_skill` test pins this contract. |
| 4 | Existing TRDDs / memory references mention specific `Loaded by X` patterns | Those are historical; the generic phrasing supersedes. Update only the MEMORY.md highlights, not every TRDD. |
| 5 | The on-demand invocation pattern adds tool-call latency | Acceptable — the latency is N×~1 s for N skills used, vs. the current cost of preloading 5+ skills at startup. Net win for typical runs that use 1-2 skills. |

## Approval log

- 2026-08-25T17:25:14+0200 — CLOSED as superseded by the CPV session (board drain; authority delegated by USER 2026-08-25). own frontmatter already recorded superseded-by TRDD-9dd64dbf (batch_ab)
