---
name: cpv-enable-plugin
description: Enable a disabled Claude Code plugin with smart name resolution and scope control
argument-hint: "<plugin> [--scope user|local]"
agent: plugin-manager
user-invocable: true
---

Enable a previously disabled plugin. Accepts bare name, `name@marketplace`, or `name@owner/marketplace`.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <plugin> [--scope user|local]
```

`--scope local` enables in this project only and auto-disables at user level (per-project opt-in).

See the **plugin-management** skill for full scope rules, cascading behavior, and name resolution details.
After enabling, run `/reload-plugins` or restart Claude Code.
