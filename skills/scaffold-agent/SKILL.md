---
name: scaffold-agent
description: Scaffold a new agent in an existing plugin (creates agents/{NAME}.md with valid frontmatter that passes validate_plugin out of the box). Use when adding a single agent to an existing plugin. Used dynamically via the-skills-menu (TRDD-478d9687).
when_to_use: When the cpv-main-menu user picks Create → Add agent to existing plugin, or any flow needs to drop a minimal valid agents/{NAME}.md into a plugin
user-invocable: false
---

# scaffold-agent

## Overview

Adds a new agent to an existing plugin. The scaffold lands at `<plugin-path>/agents/<agent-name>.md` with valid frontmatter so the plugin still passes `validate_plugin` immediately. Loaded by `cpv-main-menu-agent` via the Create → Add agent menu branch.

## Prerequisites

- `uv` on PATH
- Target plugin path with `.claude-plugin/plugin.json`
- Kebab-case agent name (e.g. `my-agent`)
- A short description and the agent's tool list

## Instructions

1. Pick a kebab-case agent name (`[a-z][a-z0-9-]*`).
2. Write a one-line description that says what the agent does.
3. List the tools the agent needs (e.g. `"Read, Bash, Edit"`).
4. Run `add_component.py --type agent` with the plugin path, name, description, and tools.
5. `agents/` directory is auto-created if missing.
6. After scaffolding, optionally validate the plugin to confirm the new agent passes.

Copy this checklist and track your progress:

- [ ] Agent name kebab-case
- [ ] Description written
- [ ] Tools list defined
- [ ] `add_component.py --type agent` executed
- [ ] `agents/<name>.md` created
- [ ] `validate_plugin --strict` re-run

## Output

- A new file at `<plugin-path>/agents/<agent-name>.md`.
- Frontmatter emits `name`, `description`, and (when `--tools` is given) `tools`. Optional fields like `model`, `maxTurns`, and `skills` are NOT scaffolded — add them by hand if the agent needs them (the minimal stub still passes `validate_plugin`).
- Existing files are NEVER overwritten unless `--force` is passed.

## Error Handling

| Error | Resolution |
|-------|------------|
| Name not kebab-case | Use only `[a-z0-9-]`, starting with a letter |
| File exists | Re-run with `--force` only if you mean to overwrite |
| Target not a plugin | Verify `.claude-plugin/plugin.json` at the path |
| Agent fails to dispatch | Run `validate_agent --strict` on the new file |

## Examples

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" /path/to/plugin \
  --type agent --name my-agent --description "Agent summary" --tools "Read, Bash"
```

After scaffolding, refresh the README:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" /path/to/plugin
```

### When to use

- Creating a domain expert (test-writer, security-auditor, doc-generator).
- Wrapping a complex workflow that needs its own context window.
- Building a multi-step pipeline (each phase as a separate agent).

## Resources

- `add-component-to-plugin` skill — multi-purpose scaffold wrapper
- `refresh-readme` skill — refresh the auto-components block after adding
- `plugin-validation-skill` — validate the scaffold for correctness
