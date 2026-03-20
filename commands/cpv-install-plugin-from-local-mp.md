---
name: cpv-install-plugin-from-local-mp
description: Install a Claude Code plugin from a local directory or archive into a local marketplace (no GitHub)
argument-hint: "<source> <marketplace> [--force] [--dry-run] [--scope user|project|local]"
user-invocable: true
---

Install a plugin using the CPV management CLI.

## Local Install

Provide a source (directory or archive) and a marketplace name:
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" <source> <marketplace> [--force] [--dry-run]
```

## Remote Install

From a registered GitHub marketplace:
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" install <plugin>@<marketplace> [--scope user|project|local]
```

## Update

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --update <source> <marketplace> [--force]
```

## Workflow

1. Determine if this is a local install, remote install, or update
2. For local installs, validate first: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/validate_plugin.py" <source>`
3. Run the install command
4. After install, enable the plugin if needed:
   - User-level: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <name>@<marketplace>`
   - Project-local: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <name>@<marketplace> --scope local`
5. Remind user to run `/reload-plugins` or restart Claude Code

## Error Handling

| Error | Resolution |
|-------|------------|
| Source not found | Check the path exists |
| Missing plugin.json | Ensure `.claude-plugin/plugin.json` is present; try `/cpv-validate-plugin` first |
| Install fails | Use `--force` to override validation errors, or fix issues first |
