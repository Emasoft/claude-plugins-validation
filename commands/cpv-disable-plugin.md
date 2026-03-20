---
name: cpv-disable-plugin
description: Disable a Claude Code plugin without removing it, at user or local scope
argument-hint: "<plugin-name>@<marketplace-name> [--scope user|local]"
user-invocable: true
---

Disable a plugin without removing its files.

## Usage

```bash
# Disable in settings.local.json (default — per-machine, not committed to git)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --disable <plugin-name>@<marketplace-name>

# Disable in settings.json (user-level — shared across machines if committed)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --disable <plugin-name>@<marketplace-name> --scope user
```

## What It Does

Sets `enabledPlugins["<plugin-name>@<marketplace-name>"] = false` in the target settings file. The plugin files remain on disk.

| Scope | File | Use case |
|-------|------|----------|
| `local` (default) | `~/.claude/settings.local.json` | Per-machine, not committed to git |
| `user` | `~/.claude/settings.json` | Shared across machines if settings.json is synced |

To re-enable later: `/cpv-enable-plugin`

After disabling, remind the user to run `/reload-plugins` or restart Claude Code.
