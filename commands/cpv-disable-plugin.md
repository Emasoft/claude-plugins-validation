---
description: Disable a Claude Code plugin without removing it
---

Disable a plugin without removing its files:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --disable <plugin-name>@<marketplace-name>
```

This updates `settings.local.json` to set `enabledPlugins[key] = false`. The plugin files remain on disk.

To re-enable later: `/cpv-enable-plugin`

After disabling, remind the user to run `/reload-plugins`.
