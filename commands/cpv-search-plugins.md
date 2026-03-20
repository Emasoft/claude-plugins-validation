---
name: cpv-search-plugins
description: Search installed plugins by component type (commands, agents, skills, rules, hooks, mcp, lsp) or text query
agent: plugin-manager
user-invocable: true
---

Search installed plugins:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_registry.py" --search <query>
```

The query can be:
- A component type: `commands`, `agents`, `skills`, `rules`, `hooks`, `mcp`, `lsp`, `output-styles`
- A text string: matches plugin names, descriptions, and component types

Present matching plugins with their details. If no matches found, suggest broadening the search or using `/cpv-list-plugins` to see all plugins.

See the **plugin-management** skill for all list/search operations.
