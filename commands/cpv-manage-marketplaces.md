---
name: cpv-manage-marketplaces
description: Add, remove, list, or update GitHub plugin marketplaces
agent: plugin-manager
user-invocable: true
---

Manage marketplace registrations.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" add <owner/repo>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" remove <name>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" list [--json]
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" update [name]
```

Accepts any GitHub URL format for `add`. After changes, run `/reload-plugins`.

See the **plugin-management** skill for full marketplace operations.
