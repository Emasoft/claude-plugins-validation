---
description: Uninstall a Claude Code plugin and clean up settings
---

Uninstall a plugin using its canonical name:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --uninstall <plugin-name>@<marketplace-name>
```

For a dry run first:
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --uninstall <plugin-name>@<marketplace-name> --dry-run
```

This removes the plugin directory, updates marketplace.json, cleans settings.local.json and installed_plugins.json, and removes cached data.

After successful uninstall, remind the user to run `/reload-plugins`.
