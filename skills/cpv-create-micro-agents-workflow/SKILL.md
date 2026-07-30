---
name: cpv-create-micro-agents-workflow
description: Build a ONE-FOR-ALL agent — every skill it needs is listed BY NAME, and each one runs in a SEPARATE subagent (a one-skill-agent with minimal context). Two paths - convert ONE existing agent in place, or generate a plugin-wide RLM launcher plus a TypeScript Workflow coordinator that decomposes a task into skill-focused micro-agents and verifies them. Use when you want skill-per-subagent execution instead of one agent doing everything. Trigger with "create a micro-agents workflow" or /cpv-create-micro-agents-workflow.
when_to_use: When you want each skill run as a focused micro-agent with almost no context (better in-distribution recall, cheap turns), either by converting one agent's own skill closure into a ONE-FOR-ALL graph or by generating a plugin-wide workflow coordinator that sequences and verifies the steps.
user-invocable: true
---

# cpv-create-micro-agents-workflow

## Overview

Builds the **ONE-FOR-ALL AGENT** architecture: the agent lists every skill it needs BY NAME
in `skills:` frontmatter — exactly like an ALL-IN-ONE agent — and the ONLY difference is
WHERE a skill runs: each one executes in a **separate subagent** (a one-skill-agent, minimal
context) instead of in the agent itself. Small context ⇒ the pattern is more likely
"in-distribution" (better training-memory recall) and every turn is cheap.

**A skill's content is NEVER copied.** The fork is declared IN PLACE in the shared skill's
own frontmatter, so there is no private per-agent copy to rot. That single-source-of-truth
rule is also the one genuine COST of this architecture — see "Shared-skill safety" below.

Two paths, and they are complementary:

**A. AGENT-SCOPED (in place)** — `convert_agent.py <agent.md> --to one-for-all`. Uses that
agent's own skill closure and routing structure; the choice tree lives in the AGENT's body
(a skill cannot declare its own `skills:` list — that field is agent-only).

**B. PLUGIN-WIDE (RLM workflow)** — `create_micro_agents_workflow.py <plugin>`. A thin
launcher agent plus a Workflow-tool coordinator that plans a task into an ordered skill
sequence and spawns a fresh near-empty agent per step. Only ONE agent is created, because
the per-skill micro-agents are spawned dynamically by the Workflow tool rather than
hand-authored one-per-skill.

## The three frontmatter facts that decide whether path A works at all

1. **`context: fork` is the fork mechanism — NOT `agent:`.** `agent:` only selects WHICH
   subagent type once fork is already set (`Explore` / `Plan` additionally skip CLAUDE.md,
   which is how "minimal context" is actually achieved). **A skill carrying `agent:` alone
   does nothing.**
2. **`background` defaults to `true`, so a forked skill returns NOTHING inline** — its
   result arrives as a notification. A graph that threads one step's output into the next
   needs **`background: false`** (Claude Code **v2.1.218+**). Without it the steps appear to
   run and silently deliver nothing downstream.
3. **`skills:` is NOT valid inside a skill** (agent-only), so a node cannot carry its own
   skill list and the choice tree must live in the agent's body.

## Shared-skill safety (path A)

Adding `context: fork` to a SHARED skill changes how it executes for EVERY agent that lists
it, and there is no private copy to change instead. So `convert_agent.py --to one-for-all`
REPORTS each shared skill it would convert plus how many other agents reach it, and REFUSES
to mutate one without `--force`. Read that report before consenting.

Path B (below) writes two artifacts into the target plugin:

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

**Path A — convert ONE existing agent into a ONE-FOR-ALL agent:**

1. Run `convert_agent.py <agent.md> --to one-for-all --dry-run` FIRST and read the
   shared-skill report: every node it would convert, and how many other agents reach it.
2. Re-run without `--dry-run` (adding `--force` if any node is shared) to write the agent
   and add `context: fork` + `background: false` to each node skill's own frontmatter.
3. Optional `--node-agent Explore` also sets `agent:` on each node (which type to fork as).
   Optional `--skills-root PATH` (repeatable) makes the closure hermetic.
4. The conversion REFUSES rather than break the plugin when a node skill invokes ITSELF (a
   `context: fork` self-invocation is the v2.1.145 infinite-loop antipattern) or has no
   frontmatter block at all.

**Path B — generate the plugin-wide RLM workflow:**

1. Identify the target plugin path.
2. Run `create_micro_agents_workflow.py <plugin-path>`. It builds the skill palette (each
   skill's name + one-line purpose, skipping meta/router skills), renders the workflow `.ts`
   with that palette embedded, and writes the launcher agent + the `.ts`.
3. Options: `--name BASE` overrides the default (the plugin slug) — produces `<base>-micro-agents`
   and `<base>-workflow-launcher`; `--include-all` keeps meta skills in the palette; `--force`
   overwrites existing artifacts.
4. Validate the target plugin afterwards.

Copy this checklist and track your progress:

- [ ] Path chosen (A convert one agent, or B plugin-wide workflow)
- [ ] Path A: shared-skill report read BEFORE consenting with `--force`
- [ ] Path A: each node carries `context: fork` AND `background: false`
- [ ] Path B: `agents/<base>-workflow-launcher.md` created
- [ ] Path B: `workflows/<base>-micro-agents.ts` created (palette embedded)
- [ ] `validate_plugin --strict` re-run on the target

## Output

- Path A: `<plugin>/agents/<source>-one-for-all.md` (references skills by NAME, never copies
  them) plus `context: fork` + `background: false` added IN PLACE to each node skill, and
  the `verification-before-completion` companion skill inside the TARGET plugin's skills
  directory — mandatory on every variant, written from the bundled template when absent and
  NEVER overwritten when present.
- Path B: `<plugin>/agents/<base>-workflow-launcher.md` — the minimal launcher.
- Path B: `<plugin>/workflows/<base>-micro-agents.ts` — the Workflow-tool coordinator.
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
| Path A refuses on a SHARED skill | Read the report; re-run with `--force` only if changing it for every listing agent is intended |
| Path A refuses on a self-invoking skill | Restructure that skill into a helper first — a forked self-invocation is the v2.1.145 antipattern |

## Examples

```bash
# Path A — convert one agent (report first, then consent)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/convert_agent.py" /path/to/plugin/agents/worker.md --to one-for-all --dry-run
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/convert_agent.py" /path/to/plugin/agents/worker.md --to one-for-all --force

# Path B — plugin-wide RLM workflow
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/create_micro_agents_workflow.py" /path/to/plugin
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/create_micro_agents_workflow.py" /path/to/plugin --name mytasks
```

## Resources

- `cpv-create-mono-agent` skill — the ALL-IN-ONE architecture (same list, skills run in the agent)
- `cpv-scaffold-agent` skill — scaffold a single minimal agent instead
- `cpv-plugin-validation-skill` — validate the generated launcher for correctness
