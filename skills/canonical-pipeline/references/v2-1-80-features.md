# Claude Code v2.1.80+ Plugin Features

## Table of Contents

- [Monitor tool](#monitor-tool)
- [userConfig (plugin.json)](#userconfig-pluginjson)
- [channels (plugin.json)](#channels-pluginjson)
- [CLAUDE_PLUGIN_OPTION_<KEY> env vars](#claude_plugin_option_key-env-vars)
- [Inline marketplace (settings.json)](#inline-marketplace-settingsjson)
- [managed-settings.d/ drop-in directory](#managed-settingsd-drop-in-directory)
- [Plugin skill `name` field (v2.1.98)](#plugin-skill-name-field-v2198)

Features added in Claude Code v2.1.80 through v2.1.98 that CPV's validators already accept.

## Monitor tool

v2.1.98 background command runner. Feeds each stdout line to Claude. Respects Bash permission rules — anything Bash cannot run, Monitor cannot run either. Declare in an agent's `tools:` list.

```markdown
---
name: build-watcher
description: Watch the webpack build and surface errors. Use when iterating on the frontend.
model: sonnet
tools:
  - Monitor
  - Read
  - Bash
---
```

Validator: `scripts/cpv_validation_common.py:287`.

## userConfig (plugin.json)

User-configurable values prompted at plugin enable time. Keys must be valid identifiers. Each entry should declare `description` (MINOR if missing). Set `sensitive: true` for secrets to keep them out of `CLAUDE_PLUGIN_OPTION_*` expansion.

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "userConfig": {
    "API_ENDPOINT": {
      "description": "Base URL for the upstream service"
    },
    "API_TOKEN": {
      "description": "Bearer token — provided by the user on enable",
      "sensitive": true
    }
  }
}
```

Values are readable from hook/MCP/LSP configs via `${user_config.API_ENDPOINT}`, and (non-sensitive only) as `CLAUDE_PLUGIN_OPTION_API_ENDPOINT`.

Validator: `scripts/validate_plugin.py:283-302`.

## channels (plugin.json)

Channel declarations for message injection. Each entry's `server` field MUST match a key in `mcpServers`.

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "mcpServers": {
    "notifications": {
      "command": "node",
      "args": ["${CLAUDE_PLUGIN_ROOT}/mcp/notifications.js"]
    }
  },
  "channels": [
    {
      "server": "notifications",
      "name": "alerts"
    }
  ]
}
```

Validator: `scripts/validate_plugin.py:303-327` — cross-checks `channels[].server` against `mcpServers` keys and raises MAJOR on mismatch.

## CLAUDE_PLUGIN_OPTION_<KEY> env vars

For every key declared under `userConfig`, Claude Code auto-exports an env var named `CLAUDE_PLUGIN_OPTION_<KEY>`. The pattern `^CLAUDE_PLUGIN_OPTION_[A-Z][A-Z0-9_]*$` is in CPV's whitelist, so you never need to register new env vars manually.

Use them in pipeline-aware places:

```jsonc
// hook config expression
{
  "type": "command",
  "command": "curl -H \"Authorization: Bearer ${CLAUDE_PLUGIN_OPTION_API_TOKEN}\" ${CLAUDE_PLUGIN_OPTION_API_ENDPOINT}/events"
}
```

```markdown
<!-- SKILL.md body -->
Default region: `${CLAUDE_PLUGIN_OPTION_REGION}`.
```

Sensitive keys are omitted from the env var expansion for safety.

Validator: `scripts/cpv_validation_common.py:335-346`.

## Inline marketplace (settings.json)

v2.1.80 supports declaring small marketplaces inline in `settings.json` under `extraKnownMarketplaces`. Plugins still need a real source (`github`, `npm`, etc.) and must appear in `enabledPlugins`.

```jsonc
{
  "extraKnownMarketplaces": {
    "team-internal": {
      "source": { "source": "settings" },
      "plugins": [
        {
          "name": "my-plugin",
          "source": { "source": "github", "repo": "acme/my-plugin" }
        }
      ]
    }
  },
  "enabledPlugins": {
    "my-plugin@team-internal": true
  }
}
```

This is for `settings.json` — `marketplace.json` files still use their normal format.

## managed-settings.d/ drop-in directory

Teams can drop independent settings fragments into `managed-settings.d/*.json`, merged alphabetically on top of `managed-settings.json`.

Platform paths:

- macOS: `/Library/Application Support/ClaudeCode/managed-settings.d/`
- Linux: `/etc/claude-code/managed-settings.d/`
- Windows: `C:\ProgramData\ClaudeCode\managed-settings.d\`

Example fragment `/etc/claude-code/managed-settings.d/20-proxy.json`:

```json
{
  "env": {
    "HTTPS_PROXY": "http://proxy.corp.local:8080"
  }
}
```

## Plugin skill `name` field (v2.1.98)

When a plugin declares `"skills": ["./"]` pointing at the plugin root, the skill's invocation name comes from the `name:` field in SKILL.md frontmatter. If missing, Claude Code falls back to the directory basename — fragile when renaming.

Always set `name:` explicitly in v2.1.98+:

```markdown
---
name: my-plugin-main
description: >
  Canonical entry point for my-plugin. Use when the user asks for
  my-plugin's default workflow.
---
```
