---
name: cpv-scaffold-command
description: Scaffold a new slash command in an existing plugin (creates commands/{NAME}.md with valid frontmatter that passes validate_plugin out of the box). Use when adding a single slash command to an existing plugin. Used dynamically via cpv-the-skills-menu (TRDD-478d9687).
when_to_use: When the cpv-main-menu user picks Create → Add slash command to existing plugin, or any flow needs to drop a minimal valid commands/{NAME}.md into a plugin
user-invocable: false
---

# cpv-scaffold-command

## Overview

Adds a new slash command to an existing plugin. The scaffold lands at `<plugin-path>/commands/<command-name>.md` with valid frontmatter so the plugin still passes `validate_plugin` immediately. Loaded dynamically via cpv-the-skills-menu, reached via the Create → Add slash command menu branch.

## Prerequisites

- `uv` on PATH
- Target plugin path with `.claude-plugin/plugin.json`
- Kebab-case command name (e.g. `my-command`)
- Optional `--allowed-tools` value (the scaffolder accepts no argument-hint input — add `argument-hint` to the frontmatter by hand afterwards if needed)

## Instructions

1. Pick a kebab-case command name (`[a-z][a-z0-9-]*`).
2. Write a one-line description that says what the command does.
3. Choose the allowed tools (minimal set — scope with `Bash(uv:*)` not bare `Bash`).
4. Run `add_component.py --type command` with the plugin path, name, description, and allowed-tools.
5. `commands/` directory is auto-created if missing.
6. After scaffolding, the slash command becomes `/<plugin-name>:<command-name>` after plugin reload.

Copy this checklist and track your progress:

- [ ] Command name kebab-case
- [ ] Description written
- [ ] Allowed-tools scoped (no bare `Bash`)
- [ ] `add_component.py --type command` executed
- [ ] `commands/<name>.md` created
- [ ] `validate_plugin --strict` re-run

## Output

- A new file at `<plugin-path>/commands/<command-name>.md`.
- Frontmatter emits exactly: `name`, `description`, `allowed-tools`, `user-invocable: true`. `allowed-tools` defaults to bare `Bash` when `--allowed-tools` is omitted — always pass a scoped value (e.g. `Bash(uv:*)`). `add_component.py` does NOT emit `argument-hint`; add it by hand afterwards if the command takes arguments.
- Existing files are NEVER overwritten unless `--force` is passed.

## Error Handling

| Error | Resolution |
|-------|------------|
| Name not kebab-case | Use only `[a-z0-9-]`, starting with a letter |
| File exists | Re-run with `--force` only if you mean to overwrite |
| Target not a plugin | Verify `.claude-plugin/plugin.json` at the path |
| Want least-privilege tools | `validate_plugin` does NOT flag bare `Bash`, but scoping it (`Bash(git:*)`, `Bash(uv:*)`) is best practice — narrow the grant to only what the command runs |
| Command doesn't appear after reload | Run `/reload-plugins` or restart Claude Code |

## Examples

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" /path/to/plugin \
  --type command --name my-command --description "What the command does" \
  --allowed-tools "Bash(uv:*)"
```

After scaffolding, refresh the README:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" /path/to/plugin
```

### When to use

- Exposing a single workflow as a one-keystroke slash command.
- Wrapping a long bash incantation behind a memorable name.
- Providing a quick-launch entry point for an agent (the command body just dispatches the agent with the user's args).

## Resources

- `cpv-add-component-to-plugin` skill — multi-purpose scaffold wrapper
- `cpv-refresh-readme` skill — refresh the auto-components block after adding
- `cpv-plugin-validation-skill` — validate the scaffold for correctness
