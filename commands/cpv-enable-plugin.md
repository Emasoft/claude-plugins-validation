---
description: Enable a disabled Claude Code plugin
---

Enable a previously disabled plugin:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <plugin-name>@<marketplace-name>
```

This updates `settings.local.json` to set `enabledPlugins[key] = true` without modifying files.

After enabling, remind the user to run `/reload-plugins`.
