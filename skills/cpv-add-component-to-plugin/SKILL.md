---
name: cpv-add-component-to-plugin
description: Add a new component (skill / agent / command / hook / mcp) to an existing plugin. Use when scaffolding a single component into a plugin without re-running the generator. Used dynamically via cpv-the-skills-menu (TRDD-478d9687).
when_to_use: When the cpv-main-menu user picks Manage → Add component, or any flow needs to scaffold a single component (skill/agent/command/hook/mcp) into an existing plugin
user-invocable: false
---

# cpv-add-component-to-plugin

## Overview

Adds a new component (skill / agent / command / hook / mcp) to an existing plugin without re-running the generator or hand-editing scaffolds. Each component lands as a minimal but valid stub with frontmatter that passes `validate_plugin` / `validate_skill` out of the box. Loaded dynamically via cpv-the-skills-menu, reached via the Manage → Add component menu branch.

## Prerequisites

- `uv` on PATH
- Target plugin path with a valid `.claude-plugin/plugin.json`
- Component type known: `skill`, `agent`, `command`, `hook`, `mcp`

## Instructions

1. Choose a component type (skill, agent, command, hook, mcp).
2. Run the relevant invocation from "Examples" with the target plugin path + name + description.
3. For `hook` / `mcp`, verify the JSON file already exists or will be created (the script merges entries idempotently).
4. After scaffolding, refresh the README via the `cpv-refresh-readme` skill so the `<!-- BEGIN AUTO-COMPONENTS -->` block picks up the new component.
5. Re-validate the parent plugin via `cpv-plugin-validation-skill` to confirm no drift was introduced.

Copy this checklist and track your progress:

- [ ] Component type chosen
- [ ] `add_component.py` invoked with all required args
- [ ] README refreshed via `refresh_readme.py`
- [ ] `validate_plugin --strict` re-run
- [ ] Exit 0 confirmed

## Output

- A new file or merged JSON entry under the plugin's canonical component directory:
  - `skill` → `skills/<name>/SKILL.md`
  - `agent` → `agents/<name>.md`
  - `command` → `commands/<name>.md`
  - `hook` → `hooks/hooks.json` (merged)
  - `mcp` → `.mcp.json` (merged)
- Idempotent: re-runs with the same args are no-ops.

## Error Handling

| Error | Resolution |
|-------|------------|
| `existing file would be overwritten` | Re-run with `--force` only if you genuinely intend to clobber |
| `unknown --type` | Use one of: skill, agent, command, hook, mcp |
| `target is not a plugin` | Confirm `.claude-plugin/plugin.json` exists at the path |
| `hook event invalid` | See the canonical event list in CC docs (PreToolUse, PostToolUse, etc.) |

## Examples

```bash
# Add a skill
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" /path/to/plugin \
  --type skill --name my-skill --description "What it does"

# Add an agent
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" /path/to/plugin \
  --type agent --name my-agent --description "Agent summary" --tools "Read, Bash"

# Add a slash command
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" /path/to/plugin \
  --type command --name my-command --description "What the command does" \
  --allowed-tools "Bash(uv:*)"

# Append a hook entry to hooks/hooks.json (idempotent — skips dup)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" /path/to/plugin \
  --type hook --event PreToolUse --command "echo before-tool"

# Register an MCP server in .mcp.json (stdio default; --http-url for HTTP)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" /path/to/plugin \
  --type mcp --name my-server --command "node /path/to/server.js"
```

After adding, refresh the README:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" /path/to/plugin
```

## Resources

- `cpv-scaffold-skill` skill — focused single-purpose skill wrapper
- `cpv-scaffold-agent` skill — focused agent scaffold wrapper
- `cpv-scaffold-command` skill — focused command scaffold wrapper
- `cpv-add-hook` skill — focused hook merge wrapper
- `cpv-register-mcp` skill — focused MCP registration wrapper
- `cpv-refresh-readme` skill — refresh the auto-components README block
