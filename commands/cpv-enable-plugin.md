---
name: cpv-enable-plugin
description: Enable a disabled Claude Code plugin with smart name resolution and scope control
argument-hint: "<plugin> [--scope user|local]"
agent: plugin-manager
user-invocable: true
---

Enable a previously disabled plugin. Accepts bare name, `name@marketplace`, or `name@owner/marketplace`.

```bash
# Enable globally (user-level, default)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <plugin>

# Enable only in this project
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <plugin> --scope local
```

`--scope local` writes to `<project>/.claude/settings.local.json` and **removes** the key from user-level settings (so user `True` doesn't override the local setting).

See the **plugin-management** skill for name resolution details.
After enabling, run `/reload-plugins` or restart Claude Code.
