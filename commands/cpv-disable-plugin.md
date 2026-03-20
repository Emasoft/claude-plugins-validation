---
name: cpv-disable-plugin
description: Disable a Claude Code plugin without removing it, with smart name resolution and scope control
argument-hint: "<plugin> [--scope user|local]"
user-invocable: true
---

Disable a plugin without removing its files.

## Usage

```bash
# By plugin name (auto-resolves marketplace if unique)
/cpv-disable-plugin claude-plugins-validation

# By name@marketplace (explicit)
/cpv-disable-plugin claude-plugins-validation@emasoft-plugins

# By name@owner/marketplace (disambiguate same-name marketplaces)
/cpv-disable-plugin claude-plugins-validation@Emasoft/emasoft-plugins

# Disable only in this project (even if enabled at user level)
/cpv-disable-plugin claude-plugins-validation --scope local
```

## What It Does

Sets `enabledPlugins["<plugin>@<marketplace>"] = false` in the target settings file. The plugin files remain on disk.

**Plugin name resolution:**
1. `plugin-name` — searches all settings files + on-disk marketplaces. If unique match, uses it. If ambiguous, lists matches and asks.
2. `plugin-name@marketplace` — uses as-is.
3. `plugin-name@owner/marketplace` — strips owner prefix, uses `plugin-name@marketplace`.

**Checks:** Verifies the plugin is installed before disabling. Exits with error if not found.

## Scopes

| Scope | File | Effect |
|-------|------|--------|
| `user` (default) | `~/.claude/settings.json` | Plugin disabled globally for all projects |
| `local` | `.claude/settings.local.json` (project) | Plugin disabled for this project only |

### Local scope override

When `--scope local` is used to **disable** a plugin:
- The plugin is set to `false` in the project's `.claude/settings.local.json`
- This **overrides** any user-level enable — the plugin will NOT load in this project
- Other projects remain unaffected

To re-enable later: `/cpv-enable-plugin`

After disabling, run `/reload-plugins` or restart Claude Code.
