---
name: cpv-enable-plugin
description: Enable a disabled Claude Code plugin with smart name resolution and scope control
argument-hint: "<plugin> [--scope user|local]"
user-invocable: true
---

Enable a previously disabled plugin.

## Usage

```bash
# By plugin name (auto-resolves marketplace if unique)
/cpv-enable-plugin claude-plugins-validation

# By name@marketplace (explicit)
/cpv-enable-plugin claude-plugins-validation@emasoft-plugins

# By name@owner/marketplace (disambiguate same-name marketplaces)
/cpv-enable-plugin claude-plugins-validation@Emasoft/emasoft-plugins

# Enable only in this project (project-level override)
/cpv-enable-plugin claude-plugins-validation --scope local
```

## What It Does

Sets `enabledPlugins["<plugin>@<marketplace>"] = true` in the target settings file.

**Plugin name resolution:**
1. `plugin-name` — searches all settings files + on-disk marketplaces. If unique match, uses it. If ambiguous, lists matches and asks.
2. `plugin-name@marketplace` — uses as-is.
3. `plugin-name@owner/marketplace` — strips owner prefix, uses `plugin-name@marketplace`.

**Checks:** Verifies the plugin is installed (in enabledPlugins or on disk) before enabling. Exits with error if not found.

## Scopes

| Scope | File | Effect |
|-------|------|--------|
| `user` (default) | `~/.claude/settings.json` | Plugin enabled globally for all projects |
| `local` | `.claude/settings.local.json` (project) | Plugin enabled for this project only |

### Local scope cascading

When `--scope local` is used to **enable** a plugin:
- The plugin is enabled in the project's `.claude/settings.local.json`
- It is **automatically disabled** at user level (`~/.claude/settings.json`)
- This means the plugin must now be explicitly enabled per-project

This is useful when you want a plugin active in specific projects only.

After enabling, run `/reload-plugins` or restart Claude Code.
