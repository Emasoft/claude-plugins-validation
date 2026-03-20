---
name: cpv-disable-plugin
description: Disable a Claude Code plugin without removing it, with smart name resolution and scope control
argument-hint: "<plugin> [--scope user|local]"
agent: plugin-manager
user-invocable: true
---

Disable a plugin without removing its files. Accepts bare name, `name@marketplace`, or `name@owner/marketplace`.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --disable <plugin> [--scope user|local]
```

`--scope local` disables in this project only, overriding any user-level enable.

See the **plugin-management** skill for full scope rules and name resolution details.
After disabling, run `/reload-plugins` or restart Claude Code. To re-enable: `/cpv-enable-plugin`.
