---
name: cpv-list-plugins
description: List all locally installed Claude Code plugins with version, status, and components
user-invocable: true
---

List all locally installed plugins:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_registry.py" --list
```

Each plugin shows name, version, enabled/disabled status, and detected components (commands, agents, skills, rules, hooks, MCP, LSP, output-styles).

If no plugins are installed, suggest using `/cpv-manage` to install plugins from a marketplace or GitHub.

See the **plugin-management** skill for all list/search operations.
