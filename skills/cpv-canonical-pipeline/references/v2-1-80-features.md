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

## Checklist

- [ ] Using Monitors? — declare in `monitors/monitors.json` with name+command+description
- [ ] Using userConfig? — every entry has `title` AND `type` (one of string|number|boolean|directory|file)
- [ ] Using channels? — each has `server` matching an `mcpServers` key
- [ ] Using CLAUDE_PLUGIN_OPTION_* env vars? — pattern-matched by CPV whitelist
- [ ] Inline marketplace in settings.json? — source type is one of the 8 allowed
- [ ] Skills with explicit `name:` frontmatter? — v2.1.98+ required for path-based skills

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

Validator: `Monitor` is in the valid-agent-tools allowlist in
`scripts/cpv_validation_common.py` (so an agent may declare it under
`tools:` without tripping the unknown-tool check).

## userConfig (plugin.json)

User-configurable values prompted at plugin enable time. Keys must be valid identifiers. The runtime Zod schema enforces stricter rules than the public docs suggest:

- **`title`** (string) — **REQUIRED**. Missing title fails install with `userConfig.<key>.title: Invalid input: expected string, received undefined`
- **`type`** (string) — **REQUIRED**, must be exactly one of `"string" | "number" | "boolean" | "directory" | "file"`. Missing/invalid type fails install with `userConfig.<key>.type: Invalid option: expected one of "string"|"number"|"boolean"|"directory"|"file"`. Note: `"integer"`, `"array"`, `"object"` are NOT accepted by the runtime.
- **`description`** (string) — optional but recommended (MINOR if missing)
- **`sensitive`** (boolean) — optional; set `true` for secrets to route them to the system keychain (not `CLAUDE_PLUGIN_OPTION_*` expansion)
- **`default`** — optional; when present must match the declared `type`

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "userConfig": {
    "API_ENDPOINT": {
      "title": "API endpoint URL",
      "description": "Base URL for the upstream service",
      "type": "string"
    },
    "API_TOKEN": {
      "title": "API authentication token",
      "description": "Bearer token — provided by the user on enable",
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
    },
    "WORKSPACE_DIR": {
      "title": "Workspace directory",
      "type": "directory"
    },
    "CONFIG_FILE": {
      "title": "Config file path",
      "type": "file"
    }
  }
}
```

Values are readable from MCP/LSP server configs via `${user_config.API_ENDPOINT}` (including an exec-form `args` array and an MCP server's `env` block), and (non-sensitive only) as `CLAUDE_PLUGIN_OPTION_API_ENDPOINT`.

Since Claude Code **v2.1.207**, `${user_config.*}` is **rejected in a shell-form command** — a hook `command`, a monitor `command`, and an MCP `headersHelper` command (shell-injection fix: an option value interpolated into a shell string is attacker-controlled shell input). Do not emit that shape. Instead: a hook uses exec form (pass the value in the `args` array) or reads `$CLAUDE_PLUGIN_OPTION_<KEY>` inside the script; a monitor or a `headersHelper` reads the value inside the script (config file, or the server's `env` block).

Plugin option values (`pluginConfigs`) are also no longer read from project-level `.claude/settings.json` as of v2.1.207 — only user, `--settings`, and managed settings are honored.

Validator: `scripts/validate_plugin.py` — `validate_user_config_structure` enforces the 5-type whitelist (`USER_CONFIG_TYPE_ENUM`) and the required-`title`/`type` fields.

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

Validator: `validate_channels_structure` in `scripts/validate_plugin.py` — cross-checks `channels[].server` against `mcpServers` keys and raises MAJOR on mismatch.

## CLAUDE_PLUGIN_OPTION_<KEY> env vars

For every key declared under `userConfig`, Claude Code auto-exports an env var named `CLAUDE_PLUGIN_OPTION_<KEY>`. The pattern `^CLAUDE_PLUGIN_OPTION_[A-Z][A-Z0-9_]*$` is in CPV's whitelist, so you never need to register new env vars manually.

Use them in pipeline-aware places:

```jsonc
// hook config expression — the script reads BOTH values from its own environment
{
  "type": "command",
  "command": "${CLAUDE_PLUGIN_ROOT}/scripts/post-event.sh"
}
```

```markdown
<!-- SKILL.md body -->
Default region: `${CLAUDE_PLUGIN_OPTION_REGION}`.
```

Note what the hook example does NOT do: it never interpolates an option into the
command string. Interpolating a SECRET (`...Bearer ${CLAUDE_PLUGIN_OPTION_API_TOKEN}...`)
is wrong twice over, and CPV flags it. First, it does not even work — sensitive keys are
omitted from the env-var expansion for safety (they live in the keychain), so the header
would expand to a bearer prefix with nothing after it. Second, a value interpolated into a
shell-form command lands in the process's argv, where any `ps` on the machine can read it —
that is a token leak, and it is what CPV's `TOKEN_STEAL` rule is for. Let the script read
what it needs from its own environment (non-sensitive keys) or from the keychain (sensitive
ones); a value read inside a process is never exposed on a command line.

Validator: the `PLUGIN_ENV_VAR_PATTERNS` allowlist in
`scripts/cpv_validation_common.py` matches `^CLAUDE_PLUGIN_OPTION_[A-Z][A-Z0-9_]*$`.

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
