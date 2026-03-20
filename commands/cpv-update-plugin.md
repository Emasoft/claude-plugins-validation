---
name: cpv-update-plugin
description: Update an installed Claude Code plugin from a new source
agent: plugin-manager
user-invocable: true
---

Update a plugin by uninstalling the old version and reinstalling from a new source:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --update <source> <marketplace>
```

The source can be a directory or archive. The marketplace must match where the plugin is currently installed.

For a dry run:
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --update <source> <marketplace> --dry-run
```

After successful update, remind the user to run `/reload-plugins`.

See the **plugin-management** skill for full details.
