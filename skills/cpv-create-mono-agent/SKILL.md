---
name: cpv-create-mono-agent
description: Build an ALL-IN-ONE agent — one agent that PRELOADS every skill it needs BY NAME in its `skills:` frontmatter and routes to each one from its own body, so it is ready from turn 1 with the whole set in its cached prefix. Works plugin-wide (every non-meta skill) or scoped to ONE existing agent's skill closure. Use when you want a single always-ready agent. Trigger with "create an all-in-one agent" or /cpv-create-mono-agent.
when_to_use: When you want one agent that holds its whole skill set from turn 1 (no dynamic loading), either for a whole plugin or converted from one existing agent. Skills are referenced by NAME and never copied, so each stays independently editable.
user-invocable: true
---

# cpv-create-mono-agent

## Overview

Generates an **ALL-IN-ONE AGENT**: its `skills:` frontmatter lists every skill it needs
BY NAME, and its body is the routing layer that says WHEN to reach for each one. That
frontmatter list IS the preload — Claude Code injects each named skill's FULL content
into every invocation's cached prefix — so the agent is ready from turn 1 and never has
to fetch a skill mid-task.

**A skill's content is NEVER copied into the agent.** Not concatenated, not duplicated,
not embedded. The reason is single-source-of-truth: a skill must stay INDEPENDENT so it
can be shared by many agents and edited, fixed, or updated ONCE. An inlined copy is a
second source that silently rots the moment the original changes, and with N agents
inlining it there are N stale copies and no signal that any drifted. Referencing costs
nothing, because the preload already puts the content in context.

**SUPERSEDED — do not expect the old behaviour.** Until TRDD-XUNZQ70I this generator
CONCATENATED every skill body into one giant agent. That construction is now forbidden
and there is exactly ONE version of the mechanism — no inlining path behind a flag.

## The three architectures (canonical vocabulary)

| Architecture | `skills:` lists | Skills execute in | Build it with |
|---|---|---|---|
| **ALL-IN-ONE** | every skill it needs | the same agent | this skill |
| **ONE-FOR-ALL** | every skill it needs | a separate subagent per skill | `cpv-create-micro-agents-workflow` |
| **PLUGIN-OMNI** | the plugin's `the-skills-menu` + the companion | resolved at runtime from the menu | `convert_agent.py --to plugin-omni` |

ALL-IN-ONE and ONE-FOR-ALL differ in exactly ONE thing: WHERE a skill runs.

## Prerequisites

- `uv` on PATH
- Target plugin path with `.claude-plugin/plugin.json` and a `skills/` directory
- For the agent-scoped path: the source agent `.md` file

## Instructions

Pick the scope first — they use different scripts:

**A. PLUGIN-WIDE** (every non-meta skill the plugin ships):

1. Run `create_mono_agent.py <plugin-path>`.
2. It lists every `skills/*/SKILL.md` by name, skipping meta/router skills
   (`the-skills-menu`, `main-menu`, `semantic-validation`, and the two creator skills)
   and any skill that CANNOT be preloaded, appends the mandatory
   `verification-before-completion` companion, and writes
   `agents/<slug>-all-in-one.md`.
3. Options: `--include-all` lists literally every skill (including meta);
   `--name NAME` overrides the default `<slug>-all-in-one`; `--force` overwrites.

**B. AGENT-SCOPED** (one existing agent's own skill closure and routing structure):

1. Run `convert_agent.py <agent.md> --to all-in-one`.
2. It resolves that agent's skill CLOSURE (frontmatter preloads + body `Skill()`
   invocations + their transitive refs) through the closure SSOT, lists every REACHABLE
   skill, and derives the routing branches from the SOURCE agent's own headings —
   emitting a flat "choose by intent" table where the source gives no ordering.
3. Options: `--out DIR`, `--name NAME`, `--skills-root PATH` (repeatable, makes the
   closure hermetic), `--force`, `--dry-run`, `--json`.

Both paths: no `model:` pin (CA-04), the `Skill` tool gate is left OPEN, and an existing
output is never overwritten without `--force`.

Copy this checklist and track your progress:

- [ ] Scope chosen (plugin-wide vs one agent) and target path identified
- [ ] Generator run (`create_mono_agent.py` or `convert_agent.py --to all-in-one`)
- [ ] `skills:` list reviewed — every entry is a skill you want paid for on EVERY turn
- [ ] Excluded-skill report read (each exclusion names its reason)
- [ ] `verification-before-completion` present under `skills/`
- [ ] `validate_plugin --strict` re-run on the target

## Output

- A new agent whose `skills:` frontmatter names every skill it needs and whose body
  routes to them.
- The `verification-before-completion` companion skill inside the TARGET plugin's skills
  directory, written from the bundled template when absent and NEVER overwritten when
  present (you may have adapted it). CPV does not ship that skill itself — it is written
  into whatever plugin you are converting.
- Existing files are NEVER overwritten unless `--force` is passed.

## Why the companion skill is mandatory

Every generated variant carries `verification-before-completion`. Its Iron Law — no
completion claim without fresh verification evidence — is exactly the failure mode a
multi-skill agent is most prone to, because a step REPORTING success is not evidence
that it happened. It also interacts with validation: an unresolvable preload is a MAJOR,
which is why the generator ships the skill rather than merely naming it.

## Caveats

- A preload is paid for on EVERY turn of the agent. Listing a skill the body never routes
  to is dead weight, and the validator says so.
- A skill that sets `disable-model-invocation: true` (or a bundled user-only `verify` /
  `code-review`) CANNOT be preloaded at all — Claude Code drops such a preload silently
  and only logs it to the debug log. Those skills are excluded and reported.

## Error Handling

| Error | Resolution |
|-------|------------|
| Name not kebab-case | `--name` must be `[a-z0-9-]`, starting with a letter |
| File exists | Re-run with `--force` only if you mean to overwrite |
| Target not a plugin | Verify `.claude-plugin/plugin.json` at the path |
| No preloadable skills | The target has no usable skill — an agent listing only the companion is an empty shell |
| A skill resolves nowhere | Fix the name or ship the skill; it is excluded, never listed |
| Companion template not found | Ship `design/specs/verification-before-completion.template.md` or add the skill by hand |

## Examples

```bash
# Plugin-wide
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/create_mono_agent.py" /path/to/plugin
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/create_mono_agent.py" /path/to/plugin --include-all --name my-mega-agent

# Scoped to one agent's closure
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/convert_agent.py" /path/to/plugin/agents/worker.md --to all-in-one
```

## Resources

- `cpv-create-micro-agents-workflow` skill — the ONE-FOR-ALL architecture (same list, one subagent per skill)
- `cpv-scaffold-agent` skill — scaffold a single minimal agent instead
- `cpv-plugin-validation-skill` — validate the generated agent for correctness
