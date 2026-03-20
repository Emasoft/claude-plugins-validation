---
name: cpv-install-plugin-from-local-mp
description: Install a Claude Code plugin from a local directory or archive into a local marketplace (no GitHub)
argument-hint: "<source> <marketplace> [--force] [--dry-run]"
agent: plugin-manager
user-invocable: true
---

Install a plugin from a local directory or archive into a local marketplace.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" <source> <marketplace> [--force] [--dry-run]
```

For updates: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --update <source> <marketplace>`

See the **plugin-management** skill for full install workflow, flags, and enable/disable after install.
After install, run `/reload-plugins` or restart Claude Code.
