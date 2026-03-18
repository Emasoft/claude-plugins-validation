---
description: Install, update, uninstall, enable, or disable remote plugins from GitHub marketplaces
---

Manage remote plugins from registered marketplaces. Plugin identifiers use canonical form: `<plugin-name>@<marketplace-name>`.

**Remote plugin operations:**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" install <plugin-name>@<marketplace-name> [--scope user|project|local]
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" update <plugin-name>@<marketplace-name>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" uninstall <plugin-name>@<marketplace-name>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" list [--available] [--json]
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" enable <plugin-name>@<marketplace-name>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" disable <plugin-name>@<marketplace-name>
```

**Local plugin uninstall/enable/disable:**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --uninstall <plugin-name>@<marketplace-name>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <plugin-name>@<marketplace-name>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --disable <plugin-name>@<marketplace-name>
```

After changes, remind the user to run `/reload-plugins`.
