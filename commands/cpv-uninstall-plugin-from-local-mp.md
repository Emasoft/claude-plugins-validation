---
name: cpv-uninstall-plugin-from-local-mp
description: Uninstall a Claude Code plugin from a local marketplace and clean up settings
argument-hint: "<plugin-name>@<marketplace-name> [--dry-run] [--keep-data]"
agent: plugin-manager
user-invocable: true
---

Uninstall a plugin from a local marketplace. Use `--dry-run` to preview.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --uninstall <plugin-name>@<marketplace-name> [--dry-run]
```

Removes the plugin directory, cleans settings and installed_plugins.json. Use `--keep-data` to preserve `${CLAUDE_PLUGIN_DATA}`.

See the **plugin-management** skill for full details.
After uninstall, run `/reload-plugins` or restart Claude Code.
