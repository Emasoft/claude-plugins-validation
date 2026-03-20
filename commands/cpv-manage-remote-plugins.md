---
name: cpv-manage-remote-plugins
description: Install, update, uninstall, enable, or disable remote plugins from GitHub marketplaces
argument-hint: "<action> <plugin>@<marketplace> [--scope user|project|local]"
user-invocable: true
---

Manage remote plugins from registered GitHub marketplaces. Plugin identifiers use canonical form: `<plugin-name>@<marketplace-name>`.

## Remote Plugin Operations (via claude CLI)

```bash
# Install from a registered marketplace
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" install <plugin>@<marketplace> [--scope user|project|local]

# Update to latest version
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" update <plugin>@<marketplace>

# Uninstall
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" uninstall <plugin>@<marketplace>

# List available/installed remote plugins
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" list [--available] [--json]
```

## Enable / Disable (local manage_plugin.py — supports smart name resolution)

```bash
# By bare name (auto-resolves if unique)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <plugin-name>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --disable <plugin-name>

# By full key
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <plugin>@<marketplace>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --disable <plugin>@<marketplace>

# Project-local scope (overrides user level for this project only)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <plugin> --scope local
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --disable <plugin> --scope local
```

## List Marketplace Plugins

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_registry.py" --marketplace <marketplace-name>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_registry.py" --marketplace <owner>/<marketplace-name>
```

After changes, remind the user to run `/reload-plugins` or restart Claude Code.
