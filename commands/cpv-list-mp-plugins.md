---
name: cpv-list-mp-plugins
description: List all plugins available from a marketplace with version and enabled status
argument-hint: "<marketplace-name|owner/marketplace-name>"
agent: plugin-manager
user-invocable: true
---

List all plugins in a marketplace with name, version, user-level and project-local enabled status.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_registry.py" --marketplace <name|owner/name>
```

See the **plugin-management** skill for all list/search operations.
