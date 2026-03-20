---
name: cpv-enable-plugin
description: Enable a disabled Claude Code plugin with smart name resolution
argument-hint: "<plugin>"
agent: plugin-manager
user-invocable: true
---

Enable a previously disabled plugin. Accepts bare name, `name@marketplace`, or `name@owner/marketplace`.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <plugin>
```

Writes `enabledPlugins[key] = true` to `~/.claude/settings.json`.

See the **plugin-management** skill for name resolution details.
After enabling, run `/reload-plugins` or restart Claude Code.
