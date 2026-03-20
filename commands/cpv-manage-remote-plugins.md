---
name: cpv-manage-remote-plugins
description: Install, update, uninstall, enable, or disable remote plugins from GitHub marketplaces
argument-hint: "<action> <plugin>@<marketplace>"
agent: plugin-manager
user-invocable: true
---

Manage remote plugins from registered GitHub marketplaces.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" install <plugin>@<marketplace>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" update <plugin>@<marketplace>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" uninstall <plugin>@<marketplace>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_remote.py" list [--available] [--json]
```

For enable/disable with smart name resolution, use `/cpv-enable-plugin` and `/cpv-disable-plugin`.

See the **plugin-management** skill for full details on all operations.
After changes, run `/reload-plugins` or restart Claude Code.
