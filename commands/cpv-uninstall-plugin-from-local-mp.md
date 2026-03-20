---
name: cpv-uninstall-plugin-from-local-mp
description: Uninstall a Claude Code plugin from a local marketplace and clean up settings
argument-hint: "<plugin-name>@<marketplace-name> [--dry-run]"
user-invocable: true
---

Uninstall a plugin and clean up all associated settings.

## Usage

```bash
# Dry run first (recommended)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --uninstall <plugin-name>@<marketplace-name> --dry-run

# Actual uninstall
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --uninstall <plugin-name>@<marketplace-name>
```

## What It Does

1. Removes the plugin directory from the marketplace
2. Updates marketplace.json
3. Cleans `enabledPlugins` from settings.local.json and installed_plugins.json
4. Removes cached plugin data

**Note:** Plugin data at `${CLAUDE_PLUGIN_DATA}` is deleted on uninstall. Use `--keep-data` to preserve persistent state.

After successful uninstall, remind the user to run `/reload-plugins` or restart Claude Code.

## Error Handling

| Error | Resolution |
|-------|------------|
| Plugin not found | Check the name with `/cpv-list-plugins` or `/cpv-list-mp-plugins <marketplace>` |
| Permission denied | Check directory permissions |
