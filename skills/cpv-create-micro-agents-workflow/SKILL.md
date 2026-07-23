---
name: cpv-create-micro-agents-workflow
description: EXPERIMENTAL RLM (Recursive Language Model) generator — build a minimal launcher agent plus a TypeScript Workflow coordinator that decomposes any task into skill-focused micro-agents (near-empty context each), runs and verifies them. Use when you want skill-per-agent, low-context execution instead of one big prefilled agent. Trigger with "create a micro-agents workflow" or /cpv-create-micro-agents-workflow.
when_to_use: When you want each skill run as a focused micro-agent with almost no context (better in-distribution recall, cheap turns) and a coordinator that sequences and verifies them, rather than a single agent that carries every skill at once.
user-invocable: true
---

# cpv-create-micro-agents-workflow

## Overview

Generates the **RLM (Recursive Language Model)** architecture into a target plugin — the
opposite of `cpv-create-mono-agent`. Instead of prefilling one huge agent, each skill is run
as a FOCUSED micro-agent with almost no context (just that skill and one clear input), and a
coordinator sequences them. Small context ⇒ the pattern is more likely "in-distribution"
(better training-memory recall) and every turn is cheap. Only ONE agent is created — a thin
launcher — because the per-skill micro-agents are spawned dynamically by the Workflow tool,
not hand-authored one-per-skill.

Two artifacts are written into the target plugin:

1. **`agents/<base>-workflow-launcher.md`** — a MINIMAL launcher agent whose only job is: for
   any task, call the **Workflow** tool with `args` = the task, and relay the result. It does
   no work itself.
2. **`workflows/<base>-micro-agents.ts`** — a Workflow-tool coordinator, templated with the
   plugin's skill palette (read at generation time). It plans the task into an ordered skill
   sequence, runs each step via a fresh near-empty agent (`agent(...)`), threads output into
   the next step's input, and verifies each step. `workflows/` is a validator `known_dirs`
   entry, so the `.ts` does not trip a structural finding (and is still security-scanned).

## Prerequisites

- `uv` on PATH
- Target plugin path with `.claude-plugin/plugin.json` and a `skills/` directory
- At runtime, the launcher agent needs the **Workflow** tool available in the session

## Instructions

1. Identify the target plugin path.
2. Run `create_micro_agents_workflow.py <plugin-path>`. It builds the skill palette (each
   skill's name + one-line purpose, skipping meta/router skills), renders the workflow `.ts`
   with that palette embedded, and writes the launcher agent + the `.ts`.
3. Options: `--name BASE` overrides the default (the plugin slug) — produces `<base>-micro-agents`
   and `<base>-workflow-launcher`; `--include-all` keeps meta skills in the palette; `--force`
   overwrites existing artifacts.
4. Validate the target plugin afterwards.

Copy this checklist and track your progress:

- [ ] Target plugin path identified
- [ ] `create_micro_agents_workflow.py <plugin>` run
- [ ] `agents/<base>-workflow-launcher.md` created
- [ ] `workflows/<base>-micro-agents.ts` created (palette embedded)
- [ ] `validate_plugin --strict` re-run on the target

## Output

- `<plugin>/agents/<base>-workflow-launcher.md` — the minimal launcher.
- `<plugin>/workflows/<base>-micro-agents.ts` — the Workflow-tool coordinator.
- Existing files are NEVER overwritten unless `--force` is passed.

## How the workflow runs (runtime)

The launcher hands the task to the Workflow tool as `args`. The `.ts` then: (Plan) asks a
planner agent to decompose the task into an ordered list of `{skill, input}` steps using only
the embedded palette; (Execute) runs each step via `agent("Invoke the skill \"X\" …")` with a
per-step verify agent, threading each output into the next input. The final result is returned
to the launcher, which relays it.

## Caveats

- The launcher requires the **Workflow** tool at runtime; it is omitted from a `tools:` list
  (the agent inherits all session tools) so it never fails validation on an unknown tool.
- Decomposition quality depends on the planner agent; treat this as an experimental scaffold to
  iterate on, not a finished pipeline.

## Error Handling

| Error | Resolution |
|-------|------------|
| Name not kebab-case | `--name` must be `[a-z0-9-]`, starting with a letter |
| File exists | Re-run with `--force` to overwrite the launcher and/or workflow |
| Target not a plugin | Verify `.claude-plugin/plugin.json` at the path |
| Empty palette | The target has no non-meta skills — nothing to sequence |
| Workflow tool unavailable | The launcher needs the Workflow tool in the session at runtime |

## Examples

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/create_micro_agents_workflow.py" /path/to/plugin
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/create_micro_agents_workflow.py" /path/to/plugin --name mytasks
```

## Resources

- `cpv-create-mono-agent` skill — the opposite (prefill-everything) architecture
- `cpv-scaffold-agent` skill — scaffold a single minimal agent instead
- `cpv-plugin-validation-skill` — validate the generated launcher for correctness
