---
name: cpv-disable-plugin
description: Disable a Claude Code plugin without removing it, with smart name resolution and scope control
argument-hint: "<plugin> [--scope user|local]"
agent: plugin-manager
user-invocable: true
---

Disable a plugin without removing its files. Accepts bare name, `name@marketplace`, or `name@owner/marketplace`.

```bash
# Disable globally (user-level, default)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --disable <plugin>

# Disable only in this project
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --disable <plugin> --scope local
```

`--scope local` writes to `<project>/.claude/settings.local.json`.

See the **plugin-management** skill for name resolution and scope details.
After disabling, run `/reload-plugins` or restart Claude Code. To re-enable: `/cpv-enable-plugin`.
