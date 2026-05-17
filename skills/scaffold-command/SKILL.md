---
name: scaffold-command
description: Scaffold a new slash command in an existing plugin (creates commands/<name>.md with valid frontmatter that passes validate_plugin out of the box).
when_to_use: When the cpv-main-menu user picks Create → Add slash command to existing plugin, or any flow needs to drop a minimal valid commands/<name>.md into a plugin
user-invocable: false
allowed-tools: Bash(uv:*)
---

# scaffold-command

Add a new slash command to an existing plugin. The scaffold lands at
`<plugin-path>/commands/<command-name>.md` with valid frontmatter so
the plugin still passes `validate_plugin` immediately.

## Usage

Invoke via the underlying script:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" <plugin-path> \
  --type command --name <command-name> --description "<description>" \
  --allowed-tools "<tools>"
```

After scaffolding:
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" <plugin-path>
```

## Behavior

- Existing files are NEVER overwritten.
- `commands/` directory is auto-created.
- The slash command becomes available as `/<plugin-name>:<command-name>`
  once the plugin is reloaded.
- Frontmatter follows the canonical spec: `name`, `description`,
  `argument-hint` (optional), `allowed-tools` (set to a safe minimum),
  `user-invocable: true`.

## When to use

- Exposing a single workflow as a one-keystroke slash command.
- Wrapping a long bash incantation behind a memorable name.
- Providing a quick-launch entry point for an agent (the command body
  just dispatches the agent with the user's args).
