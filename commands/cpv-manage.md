---
name: cpv-manage
description: Interactive plugin management — install, update, enable, disable, search, health-check
agent: plugin-manager
argument-hint: "[action] [plugin_name]"
user-invocable: true
---

Manage Claude Code plugins: install, update, enable, disable, search, list, health-check, marketplace operations.

If an action and plugin name are provided as $ARGUMENTS, execute that action.
Otherwise, ask the user what they want to do.
