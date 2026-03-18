---
description: List all locally installed Claude Code plugins with version, status, and components
---

List all locally installed plugins:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_registry.py" --list
```

Each plugin shows name, version, enabled/disabled status, and detected components (commands, agents, skills, rules, hooks, MCP, LSP, output-styles).

If no plugins are installed, suggest using `/cpv-install-plugin` or `claude plugin install` to get started.
