# Claude Code v2.1.80+ Plugin Features

CPV validators already accept these features. Use them when they apply — CPV will not flag a well-formed plugin for including any of them.

## Table of Contents

- [Monitor tool](#monitor-tool)
- [userConfig (plugin.json)](#userconfig-pluginjson)
- [channels (plugin.json)](#channels-pluginjson)
- [CLAUDE_PLUGIN_OPTION_<KEY> env vars](#claude_plugin_option_key-env-vars)
- [Inline marketplace (settings.json)](#inline-marketplace-settingsjson)
- [managed-settings.d/ drop-in directory](#managed-settingsd-drop-in-directory)
- [Plugin skill `name` field (v2.1.98)](#plugin-skill-name-field-v2198)

## Checklist

- [ ] Using Monitors? — declare in `monitors/monitors.json`
- [ ] Using userConfig? — every entry has `title` AND `type` (5-type whitelist)
- [ ] Using channels? — each `server` matches an `mcpServers` key
- [ ] CLAUDE_PLUGIN_OPTION_* env vars used where needed
- [ ] Inline marketplace source type is valid (8 allowed)
- [ ] Skills with explicit `name:` frontmatter for path-based invocation

## Monitor tool

New in v2.1.98. Runs a shell command in the background and feeds each stdout line to Claude as it is produced. Shares Bash permission rules — anything forbidden for Bash is forbidden for Monitor.

Declare in an agent's `tools` list exactly like any other tool:

```markdown
---
name: log-watcher
description: Tail log files and react to errors. Use when monitoring an app under test.
model: sonnet
tools:
  - Monitor
  - Read
  - Bash
---
```

Validator: `scripts/cpv_validation_common.py` — `Monitor` is a member of the `VALID_TOOLS` set.

## userConfig (plugin.json)

User-configurable values prompted at plugin enable time. Keys must be valid identifiers (`^[a-zA-Z_][a-zA-Z0-9_]*$`). Per the v2.1.121 spec, every entry **requires** three sub-fields — `type`, `title`, and `description` (CPV emits a MAJOR for each missing one). `type` must be one of `string`, `number`, `boolean`, `directory`, `file`. Optional sub-fields: `sensitive` (mark secrets), `required`, `default`, `multiple` (string only), `min`/`max` (number only).

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "userConfig": {
    "API_TOKEN": {
      "type": "string",
      "title": "API token",
      "description": "API token used to authenticate with the upstream service",
      "sensitive": true
    },
    "REGION": {
      "type": "string",
      "title": "Deployment region",
      "description": "Deployment region (e.g. us-east-1)"
    }
  }
}
```

Access values in hooks/MCP/LSP string substitutions via `${user_config.KEY}`. Each key is also exported as `CLAUDE_PLUGIN_OPTION_<KEY>` (see below).

Validator: `scripts/validate_plugin.py` — `validate_user_config_structure()`.

## channels (plugin.json)

Channel declarations for message injection. Each entry needs a `server` field that MUST match a key under `mcpServers`.

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": ["${CLAUDE_PLUGIN_ROOT}/server/index.js"]
    }
  },
  "channels": [
    {
      "server": "my-server",
      "name": "alerts"
    }
  ]
}
```

Validator: `scripts/validate_plugin.py` — `validate_channels_structure()`.

## CLAUDE_PLUGIN_OPTION_<KEY> env vars

For every key in `userConfig`, Claude Code auto-exports an env var named `CLAUDE_PLUGIN_OPTION_<KEY>` (uppercase, starts with a letter). Non-sensitive values can be interpolated into skill body text, hook scripts, and MCP config as `${CLAUDE_PLUGIN_OPTION_REGION}`.

The pattern is enforced by CPV: `^CLAUDE_PLUGIN_OPTION_[A-Z][A-Z0-9_]*$` — the env var whitelist (`PLUGIN_ENV_VAR_PATTERNS` in `scripts/cpv_validation_common.py`) matches any name fitting that shape, so no manual whitelisting is needed.

Example usage inside a SKILL.md:

```markdown
Use `${CLAUDE_PLUGIN_OPTION_REGION}` as the default region when the user does not supply one.
```

## Inline marketplace (settings.json)

v2.1.80 lets teams declare small marketplaces inline in `settings.json` (NOT in a `marketplace.json` file). Each listed plugin still needs a real source (`github`, `npm`, etc.) and must be enabled in `enabledPlugins`.

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

Notes:
- `source: "settings"` is for the MARKETPLACE entry itself, not individual plugins.
- Per-plugin source must be a real fetchable source — a relative path (`./path`), `github`, `url`, `git-subdir`, or `npm`. `settings` is a marketplace-level-only source type and is rejected if used at the per-plugin level.

## managed-settings.d/ drop-in directory

Teams can drop independent settings fragments into `managed-settings.d/*.json`. They are merged alphabetically on top of `managed-settings.json`, so fragments override the base file.

Platform paths (see Claude Code spec for full list):

- macOS: `/Library/Application Support/ClaudeCode/managed-settings.d/`
- Linux: `/etc/claude-code/managed-settings.d/`
- Windows: `C:\ProgramData\ClaudeCode\managed-settings.d\`

Example fragment `/etc/claude-code/managed-settings.d/10-proxy.json`:

```json
{
  "env": {
    "HTTPS_PROXY": "http://proxy.corp.local:8080"
  }
}
```

## Plugin skill `name` field (v2.1.98)

When a plugin declares `"skills": ["./"]` pointing at the plugin root, the skill's invocation name comes from the `name:` field in SKILL.md frontmatter. If `name:` is missing, Claude Code falls back to the directory basename — which is fragile if the directory is ever renamed.

**Recommendation (v2.1.98+):** always set `name:` explicitly in every SKILL.md, even when the directory name already matches.

```markdown
---
name: my-plugin-main
description: >
  Primary skill for my-plugin. Use when the user asks for my-plugin's
  canonical workflow.
---
```
