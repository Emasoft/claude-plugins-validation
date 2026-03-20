---
name: cpv-disable-plugin
description: Disable a Claude Code plugin without removing it, with smart name resolution
argument-hint: "<plugin>"
agent: plugin-manager
user-invocable: true
---

Disable a plugin without removing its files. Accepts bare name, `name@marketplace`, or `name@owner/marketplace`.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --disable <plugin>
```

Writes `enabledPlugins[key] = false` to `~/.claude/settings.json`.

See the **plugin-management** skill for name resolution details.
After disabling, run `/reload-plugins` or restart Claude Code. To re-enable: `/cpv-enable-plugin`.
