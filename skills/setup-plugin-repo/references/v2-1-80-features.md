# Claude Code v2.1.80+ Plugin Features

## Table of Contents

- [Monitor tool](#monitor-tool)
- [userConfig (plugin.json)](#userconfig-pluginjson)
- [channels (plugin.json)](#channels-pluginjson)
- [CLAUDE_PLUGIN_OPTION_<KEY> env vars](#claude_plugin_option_key-env-vars)
- [Inline marketplace (settings.json)](#inline-marketplace-settingsjson)
- [managed-settings.d/ drop-in directory](#managed-settingsd-drop-in-directory)
- [Plugin skill `name` field (v2.1.98)](#plugin-skill-name-field-v2198)

Features added in Claude Code v2.1.80 through v2.1.98 that CPV validators already accept.

## Checklist

- [ ] Using Monitors? — declare in `monitors/monitors.json`
- [ ] Using userConfig? — every entry has `title` + `type` (5-type whitelist)
- [ ] Using channels? — `server` matches an `mcpServers` key
- [ ] CLAUDE_PLUGIN_OPTION_* env vars referenced where needed
- [ ] Inline marketplace source type valid (8 allowed)
- [ ] Skills with explicit `name:` frontmatter

## Monitor tool

v2.1.98 added the `Monitor` tool. It runs a shell command in the background and streams each stdout line to Claude. Permissions are the same as `Bash` — anything forbidden for Bash is also forbidden for Monitor.

Declare it in an agent's `tools:` list:

```markdown
---
name: ci-monitor
description: Watch CI logs in real time. Use when the user wants live build feedback.
model: sonnet
tools:
  - Monitor
  - Read
  - Bash
---
```

Validator: `scripts/cpv_validation_common.py:287`.

## userConfig (plugin.json)

User-configurable values prompted at plugin enable time. Keys must be valid identifiers (`^[a-zA-Z_][a-zA-Z0-9_]*$`). Claude Code's runtime Zod schema enforces stricter rules than the public docs suggest:

- **`title`** (string) — **REQUIRED**. Missing title fails install with `userConfig.<key>.title: Invalid input: expected string, received undefined`
- **`type`** (string) — **REQUIRED**, must be exactly one of `"string" | "number" | "boolean" | "directory" | "file"`. Missing/invalid type fails install with `userConfig.<key>.type: Invalid option: expected one of "string"|"number"|"boolean"|"directory"|"file"`. Note: `"integer"`, `"array"`, `"object"` are NOT accepted by the runtime.
- **`description`** (string) — optional but recommended
- **`sensitive`** (boolean) — optional; set `true` for secrets so they are routed to the system keychain instead of `CLAUDE_PLUGIN_OPTION_*` expansion
- **`default`** — optional; when present must match the declared `type`

Authoring rule: when scaffolding a new userConfig entry, ALWAYS include both `title` and `type`. Inferring `type` from the field name:

| Field-name pattern | Recommended `type` |
|---|---|
| `*_interval`, `*_seconds`, `*_timeout`, `*_threshold`, `*_count`, `*_days`, `*_port`, `max_*`, `min_*` | `number` |
| `enable_*`, `disable_*`, `use_*`, `is_*`, `has_*`, `*_flag` | `boolean` |
| `*_dir`, `workspace_dir`, `output_dir` (ABSOLUTE path expected) | `directory` |
| `*_file`, `config_file`, `credentials_file` (ABSOLUTE path expected) | `file` |
| path/slug/token/URL/everything else | `string` |

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "userConfig": {
    "WORKSPACE_DIR": {
      "title": "Workspace directory",
      "description": "Absolute path to the user's workspace directory",
      "type": "directory"
    },
    "OPENAI_API_KEY": {
      "title": "OpenAI API key",
      "description": "OpenAI API key used by the assistant",
      "type": "string",
      "sensitive": true
    },
    "POLL_INTERVAL": {
      "title": "Poll interval (seconds)",
      "description": "How often to poll upstream",
      "type": "number",
      "default": 900
    },
    "ENABLE_CACHE": {
      "title": "Enable cache",
      "type": "boolean",
      "default": true
    }
  }
}
```

Access values in hooks/MCP/LSP via `${user_config.WORKSPACE_DIR}`, and (non-sensitive only) as `${CLAUDE_PLUGIN_OPTION_WORKSPACE_DIR}`.

Validator: `scripts/validate_plugin.py` — `validate_manifest` enforces the 5-type whitelist and required-`title`/`type` fields (CPV v2.22.4+).

## channels (plugin.json)

Channel declarations for message injection. Each channel has a required `server` field that MUST match an entry in `mcpServers`.

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "mcpServers": {
    "my-bridge": {
      "command": "node",
      "args": ["${CLAUDE_PLUGIN_ROOT}/mcp/bridge.js"]
    }
  },
  "channels": [
    {
      "server": "my-bridge",
      "name": "inbox"
    }
  ]
}
```

Validator: `scripts/validate_plugin.py:303-327`.

## CLAUDE_PLUGIN_OPTION_<KEY> env vars

Every key under `userConfig` is auto-exported as `CLAUDE_PLUGIN_OPTION_<KEY>`. Sensitive values are omitted. CPV's env var whitelist matches them by pattern, so repositories created through `setup-plugin-repo` do not need to list them manually anywhere.

Use in SKILL.md bodies, hook scripts, and MCP args:

```jsonc
// in plugin.json
{
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": [
        "${CLAUDE_PLUGIN_ROOT}/mcp/server.js",
        "--workspace=${CLAUDE_PLUGIN_OPTION_WORKSPACE_DIR}"
      ]
    }
  }
}
```

```markdown
<!-- SKILL.md body -->
Default workspace: `${CLAUDE_PLUGIN_OPTION_WORKSPACE_DIR}`.
```

Validator: `scripts/cpv_validation_common.py:335-346`.

## Inline marketplace (settings.json)

v2.1.80 lets teams declare small marketplaces inline in `settings.json`. Each plugin still needs a real source (`github`, `npm`, etc.) and must appear in `enabledPlugins`. This complements — not replaces — full `marketplace.json` marketplaces.

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

Put this in `settings.json`, NOT in `marketplace.json` — the two are distinct. See also: [setup-github-marketplace skill](../../setup-github-marketplace/SKILL.md) for the repo-based alternative.

## managed-settings.d/ drop-in directory

Teams can drop independent settings fragments into `managed-settings.d/*.json`. They are merged alphabetically on top of `managed-settings.json`, so later fragments override earlier ones.

Platform paths:

- macOS: `/Library/Application Support/ClaudeCode/managed-settings.d/`
- Linux: `/etc/claude-code/managed-settings.d/`
- Windows: `C:\ProgramData\ClaudeCode\managed-settings.d\`

Example fragment `/etc/claude-code/managed-settings.d/30-telemetry.json`:

```json
{
  "telemetry": {
    "enabled": true,
    "endpoint": "https://telemetry.corp.local/v1/ingest"
  }
}
```

## Plugin skill `name` field (v2.1.98)

When a plugin declares `"skills": ["./"]` pointing at the plugin root, the skill's invocation name comes from the SKILL.md frontmatter `name:` field. If `name:` is missing, Claude Code falls back to the directory basename — which silently breaks after any rename.

Always set `name:` explicitly in v2.1.98+:

```markdown
---
name: my-plugin-main
description: >
  Canonical entry point for my-plugin. Use when the user asks for
  my-plugin's primary workflow.
---
```

This guarantees stable invocation regardless of directory layout.
