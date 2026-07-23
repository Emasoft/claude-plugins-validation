---
name: cpv-create-mono-agent
description: EXPERIMENTAL prefill-everything generator — build one MONO-agent whose body inlines ALL of a plugin's non-meta skills, so it is ready from turn 1 with every skill already in its cached context (one big cache-creation, then cheap cache-reads, no dynamic skill loading that would break the prompt cache). Use when you want a single always-loaded mega-agent. Trigger with "create a mono-agent" or /cpv-create-mono-agent.
when_to_use: When you want to trade a single large cache-creation for turn-1 skill readiness — an agent that never dynamically loads a skill (so it never breaks the prompt cache) because every skill is already inlined in its body.
user-invocable: true
---

# cpv-create-mono-agent

## Overview

Generates a **mono-agent** into a target plugin: one agent (`agents/<slug>-mono-agent.md`)
whose body is the plugin's entire non-meta skill set concatenated together. This is the
**prefill-everything** cache optimization — the whole skill set enters the agent's cached
context prefix ONCE (a single cache-creation cost, then ~1/10-price cache-reads), so the
agent is ready from turn 1, never needs to dynamically load a skill (which would break the
prompt cache each time), and is nudged to actually USE its skills. It is the opposite of
`cpv-create-micro-agents-workflow` (which shrinks context instead of prefilling it).

This depends on agents having **no body-length limit** — CPV removed the agent body cap
precisely to make this legal (only skills carry a size limit).

## Prerequisites

- `uv` on PATH
- Target plugin path with `.claude-plugin/plugin.json` and a `skills/` directory

## Instructions

1. Identify the target plugin path.
2. Run `create_mono_agent.py <plugin-path>`. It enumerates every `skills/*/SKILL.md`, drops
   meta/router skills (`the-skills-menu`, `main-menu`, `semantic-validation`, and the two
   creator skills), strips each skill's YAML frontmatter, demotes each skill's `#` heading to
   `##` (so the agent keeps a single H1), and concatenates them under `## Skill: <name>`
   sections inside one agent body.
3. The result lands at `agents/<slug>-mono-agent.md` with valid frontmatter, **no `model:`
   pin** (CA-04), and no `tools:` field (so it inherits all tools).
4. Options: `--include-all` inlines LITERALLY every skill (including meta); `--name NAME`
   overrides the default `<slug>-mono-agent`; `--force` overwrites an existing agent.
5. Validate the target plugin afterwards to confirm the generated agent passes.

Copy this checklist and track your progress:

- [ ] Target plugin path identified
- [ ] `create_mono_agent.py <plugin>` run
- [ ] `agents/<slug>-mono-agent.md` created
- [ ] Included/excluded skill counts reviewed
- [ ] `validate_plugin --strict` re-run on the target

## Output

- A new file `<plugin>/agents/<slug>-mono-agent.md` whose body inlines every non-meta skill.
- Existing files are NEVER overwritten unless `--force` is passed.

## Caveats

- The body is **huge by design** — that is the point, and it is only legal because agents
  have no body-length cap.
- The mono body inherits the UNION of all skills' text; if run on a security-teaching plugin
  (or on CPV itself), that union may include rule-pattern strings the target's own security
  scan flags. Always validate the result and review any findings.

## Error Handling

| Error | Resolution |
|-------|------------|
| Name not kebab-case | `--name` must be `[a-z0-9-]`, starting with a letter |
| File exists | Re-run with `--force` only if you mean to overwrite |
| Target not a plugin | Verify `.claude-plugin/plugin.json` at the path |
| No skills found | The target has no `skills/`, or every skill is meta — nothing to inline |
| Result flags a security finding | A skill's own text tripped the target's scan — review and validate |

## Examples

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/create_mono_agent.py" /path/to/plugin
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/create_mono_agent.py" /path/to/plugin --include-all --name my-mega-agent
```

## Resources

- `cpv-create-micro-agents-workflow` skill — the opposite (RLM) architecture
- `cpv-scaffold-agent` skill — scaffold a single minimal agent instead
- `cpv-plugin-validation-skill` — validate the generated agent for correctness
