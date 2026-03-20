---
name: cpv-enable-plugin
description: Enable a disabled Claude Code plugin at user or local scope
argument-hint: "<plugin-name>@<marketplace-name> [--scope user|local]"
user-invocable: true
---

Enable a previously disabled plugin.

## Usage

```bash
# Enable in settings.local.json (default — per-machine, not committed to git)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <plugin-name>@<marketplace-name>

# Enable in settings.json (user-level — shared across machines if committed)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --enable <plugin-name>@<marketplace-name> --scope user
```

## What It Does

Sets `enabledPlugins["<plugin-name>@<marketplace-name>"] = true` in the target settings file.

| Scope | File | Use case |
|-------|------|----------|
| `local` (default) | `~/.claude/settings.local.json` | Per-machine, not committed to git |
| `user` | `~/.claude/settings.json` | Shared across machines if settings.json is synced |

After enabling, remind the user to run `/reload-plugins` or restart Claude Code.
