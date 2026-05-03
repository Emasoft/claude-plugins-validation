---
name: cpv-add-component
description: Add a new component (skill / agent / command / hook / mcp) to an existing plugin
allowed-tools: Bash(uv:*)
user-invocable: true
---

# /cpv-add-component

Add a new component to an existing plugin without re-running the
generator or hand-editing scaffolds. Each component lands as a minimal
but valid stub with frontmatter that passes validate_plugin /
validate_skill out of the box.

## Usage

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

## Behavior

- Existing files are NEVER overwritten unless `--force` is passed.
- For `--type hook` / `--type mcp`, JSON files are merged: identical
  entries are skipped (re-runs are safe).
- Component directories (`skills/`, `agents/`, `commands/`, `hooks/`)
  are auto-created as needed.
- Frontmatter follows the canonical Claude Code spec — runs cleanly
  through validate_plugin, validate_skill, and the publish pipeline.

## Tip: refresh the README after adding

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" /path/to/plugin
```

The README's `<!-- BEGIN AUTO-COMPONENTS -->` block picks up the new
component automatically.
