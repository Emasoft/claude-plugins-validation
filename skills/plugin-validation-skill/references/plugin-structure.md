# Plugin Structure Reference

Complete reference for Claude Code plugin directory structure and manifest configuration.

## Table of Contents

- [1. Directory Structure](#1-directory-structure)
- [2. Plugin Manifest (plugin.json)](#2-plugin-manifest-pluginjson)
- [3. Component Placement Rules](#3-component-placement-rules)
- [4. Path Variables](#4-path-variables)
- [5. Common Structure Errors](#5-common-structure-errors)
- [6. Validation Checklist](#6-validation-checklist)

---

## 1. Directory Structure

### Standard Plugin Layout

```
my-plugin/
├── .claude-plugin/           # Metadata directory
│   └── plugin.json          # REQUIRED: Plugin manifest
├── commands/                 # Slash commands (at ROOT!)
│   ├── my-command.md
│   └── another-command.md
├── agents/                   # Agent definitions (at ROOT!)
│   ├── my-agent.md
│   └── specialist-agent.md
├── skills/                   # Skills directories (at ROOT!)
│   ├── skill-one/
│   │   ├── SKILL.md
│   │   └── references/
│   └── skill-two/
│       └── SKILL.md
├── hooks/                    # Hook configurations (at ROOT!)
│   └── hooks.json           # Auto-loaded by Claude Code
├── scripts/                  # Utility and hook scripts
│   ├── pre-tool-check.sh
│   ├── post-tool-log.py
│   └── utils.sh
├── schemas/                  # JSON schemas (optional)
│   └── config-schema.json
├── docs/                     # Documentation (optional)
│   └── usage.md
├── .mcp.json                 # MCP server definitions (optional)
├── .lsp.json                 # LSP server configurations (optional)
├── settings.json             # Default settings applied when enabled (optional, agent settings only)
├── README.md                 # Plugin documentation
└── LICENSE                   # License file
```

### Critical Placement Rules

| Component | Correct Location | Wrong Location |
|-----------|------------------|----------------|
| commands/ | Plugin ROOT | .claude-plugin/commands/ |
| agents/ | Plugin ROOT | .claude-plugin/agents/ |
| skills/ | Plugin ROOT | .claude-plugin/skills/ |
| hooks/ | Plugin ROOT | .claude-plugin/hooks/ |
| plugin.json | .claude-plugin/plugin.json | Root plugin.json |

---

## 2. Plugin Manifest (plugin.json)

### Location

`.claude-plugin/plugin.json` (inside the .claude-plugin directory)

### Required Fields

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "Brief description of what this plugin does"
}
```

| Field | Type | Requirements |
|-------|------|--------------|
| name | string | Kebab-case, lowercase, no spaces |
| version | string | Semver format: X.Y.Z |
| description | string | Clear, concise explanation |

### Optional Fields

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "My plugin description",
  "author": {
    "name": "Author Name",
    "email": "author@example.com"
  },
  "homepage": "https://github.com/author/my-plugin",
  "repository": "https://github.com/author/my-plugin",
  "license": "MIT",
  "keywords": ["utility", "automation", "development"]
}
```

> **Note**: Do NOT declare `commands`, `agents`, `skills`, or `hooks` when they use default
> paths (`./commands/`, `./agents/`, `./skills/`, `./hooks/`). Claude Code auto-discovers
> these standard directories. Only declare them when pointing to a non-standard location.

| Field | Type | Description |
|-------|------|-------------|
| author | object or string | Author name and email |
| homepage | string | Project homepage URL |
| repository | string | Source repository URL |
| license | string | SPDX license identifier |
| keywords | array | Tags for discovery |
| agents | string or array | One `.md` file path, or an array of `.md` file paths (never a folder) |
| skills | string or array | Path(s) to extra skill directories (added to default `skills/`) |
| hooks | string or array or object | Path(s) to additional hooks file, or inline hook config |

### Fields to Avoid

These fields are NOT valid or are redundant in plugin.json:

| Field | Why |
|-------|-----|
| scripts | Not part of plugin spec |
| templates | Not part of plugin spec |
| `"commands": "./commands/"` | Redundant — auto-discovered |
| `"agents": "./agents/"` | Redundant — auto-discovered |
| `"skills": "./skills/"` | Redundant — auto-discovered |
| `"hooks": "./hooks/"` | Redundant — auto-discovered |

**Auto-discovery rule**: Claude Code automatically finds `commands/`, `agents/`, `skills/`,
and `hooks/` at the plugin root. Only declare these fields when pointing to a **non-standard**
path (e.g., `"commands": "./src/my-commands/"`).

### Agent Field Format (non-standard paths only)

If agents are in the standard `agents/` directory, do NOT declare them — they are auto-discovered.

Only use the `agents` field when pointing to a non-standard location. Its value must point to `.md` **file** paths — either an array of paths (recommended) or a single path string. It must NEVER be a directory/folder path: Claude Code's manifest validator rejects any folder path here with the cryptic error `agents: Invalid input` (and at runtime silently drops the agents).

```json
{
  "agents": [
    "./custom-agents/my-agent.md",
    "./custom-agents/another-agent.md"
  ]
}
```

A single `.md` file as a string is also valid:
```json
{
  "agents": "./custom-agents/my-agent.md"
}
```

NOT a directory path:
```json
{
  "agents": "./custom-agents/"  // WRONG — folder path; list specific .md files instead
}
```

---

## 3. Component Placement Rules

### Commands

- Location: `commands/` at plugin root
- Format: Markdown files (.md)
- Naming: kebab-case (my-command.md)
- Frontmatter: Optional but recommended

```markdown
---
name: my-command
description: What this command does
---

# My Command

Instructions for the command...
```

### Agents

- Location: `agents/` at plugin root
- Format: Markdown files (.md)
- Must be listed in plugin.json agents array
- Frontmatter: Required

```markdown
---
name: my-agent
description: What this agent specializes in
tools:
  - Read
  - Write
  - Bash
---

# My Agent

You are an agent that...
```

### Skills

- Location: `skills/` at plugin root
- Each skill is a directory
- Must contain SKILL.md
- May contain references/ subdirectory

```
skills/
└── my-skill/
    ├── SKILL.md           # Required
    ├── README.md          # Optional
    └── references/        # Optional
        └── topic.md
```

### Hooks

- Location: `hooks/` at plugin root
- Standard file: `hooks/hooks.json` (auto-loaded)
- Additional hooks via plugin.json hooks field

---

## 4. Path Variables

### Available Variables

| Variable | Expands To | Use Case |
|----------|------------|----------|
| `${CLAUDE_PLUGIN_ROOT}` | Absolute path to plugin directory | All plugin-relative paths (changes on update) |
| `${CLAUDE_PLUGIN_DATA}` | Persistent data directory (~/.claude/plugins/data/{id}/) | Dependencies, caches, state that survives updates (v2.1.78) |
| `${CLAUDE_PROJECT_DIR}` | Current project root | Accessing project files |
| `${CLAUDE_SKILL_DIR}` | Absolute path to skill's own directory | Skill self-references in SKILL.md |
| `${CLAUDE_ENV_FILE}` | Path to env file (SessionStart/Setup only) | Persisting environment variables |
| `${CLAUDE_CODE_REMOTE}` | `"true"` in remote environments | Detecting remote vs local execution |

### Usage in hooks.json

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/scripts/check.sh"
          }
        ]
      }
    ]
  }
}
```

### Usage in .mcp.json

```json
{
  "mcpServers": {
    "my-server": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/my-server",
      "args": ["--config", "${CLAUDE_PLUGIN_ROOT}/config.json"],
      "env": {
        "DATA_DIR": "${CLAUDE_PLUGIN_ROOT}/data"
      }
    }
  }
}
```

### Persistent Data with ${CLAUDE_PLUGIN_DATA}

Use `${CLAUDE_PLUGIN_DATA}` for state that should survive plugin updates (dependencies, caches, generated code). The directory resolves to `~/.claude/plugins/data/{id}/` and is created automatically on first reference.

**Recommended pattern** — install dependencies on first run and re-install when manifest changes:

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "diff -q \"${CLAUDE_PLUGIN_ROOT}/package.json\" \"${CLAUDE_PLUGIN_DATA}/package.json\" >/dev/null 2>&1 || (cd \"${CLAUDE_PLUGIN_DATA}\" && cp \"${CLAUDE_PLUGIN_ROOT}/package.json\" . && npm install) || rm -f \"${CLAUDE_PLUGIN_DATA}/package.json\""
      }]
    }]
  }
}
```

**Use NODE_PATH** in MCP servers to reference persisted dependencies:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": ["${CLAUDE_PLUGIN_ROOT}/server.js"],
      "env": { "NODE_PATH": "${CLAUDE_PLUGIN_DATA}/node_modules" }
    }
  }
}
```

The data directory is deleted on `plugin uninstall` from last scope (unless `--keep-data` is passed).

### Path Rules

1. **Always use variables** for plugin paths - never hardcode
2. **Use `${CLAUDE_PLUGIN_ROOT}`** for scripts, binaries, config bundled with the plugin (changes on update)
3. **Use `${CLAUDE_PLUGIN_DATA}`** for installed dependencies, caches, state (persists across updates)
4. **Relative paths** start with `./` when in manifest
5. **No path traversal** - `../` may not work after installation
6. **Absolute paths break** portability across systems

---

## 5. Common Structure Errors

### Error: Components Inside .claude-plugin/

**Wrong:**
```
my-plugin/
├── .claude-plugin/
│   ├── plugin.json
│   ├── commands/        # WRONG!
│   └── agents/          # WRONG!
```

**Correct:**
```
my-plugin/
├── .claude-plugin/
│   └── plugin.json
├── commands/            # At ROOT
└── agents/              # At ROOT
```

### Error: plugin.json at Root

**Wrong:**
```
my-plugin/
├── plugin.json          # WRONG location!
└── commands/
```

**Correct:**
```
my-plugin/
├── .claude-plugin/
│   └── plugin.json      # Correct location
└── commands/
```

### Error: Agents as Directory Path

**Wrong** (default path — redundant, remove entirely):
```json
{
  "agents": "./agents/"
}
```

**Correct** (non-standard path — use array of file paths):
```json
{
  "agents": [
    "./custom-agents/agent-one.md",
    "./custom-agents/agent-two.md"
  ]
}
```

**Best** (default `agents/` directory — don't declare at all):
```json
{
  "name": "my-plugin",
  "version": "1.0.0"
}
```

### Error: Hardcoded Paths in Hooks

**Wrong:**
```json
{
  "command": "/Users/me/plugins/my-plugin/scripts/check.sh"
}
```

**Correct:**
```json
{
  "command": "${CLAUDE_PLUGIN_ROOT}/scripts/check.sh"
}
```

### Error: Declaring Auto-Discovered Default Paths

**Wrong:**
```json
{
  "commands": "./commands/",
  "agents": "./agents/",
  "skills": "./skills/",
  "hooks": "./hooks/"
}
```

**Correct:**
- Remove ALL of the above from plugin.json
- Claude Code auto-discovers `commands/`, `agents/`, `skills/`, `hooks/` at plugin root
- Only declare these fields when pointing to a **non-standard** path

---

## 6. Validation Checklist

### Pre-release Checklist

- [ ] `.claude-plugin/plugin.json` exists
- [ ] plugin.json has name, version, description
- [ ] Plugin name is kebab-case
- [ ] Version follows semver (X.Y.Z)
- [ ] All components at plugin ROOT (not in .claude-plugin/)
- [ ] agents field is array of .md paths (if present)
- [ ] No redundant default-path declarations in manifest (commands/, agents/, skills/, hooks/)
- [ ] All referenced files exist
- [ ] Scripts are executable (`chmod +x`)
- [ ] All paths use `${CLAUDE_PLUGIN_ROOT}`
- [ ] README.md exists with usage instructions
- [ ] LICENSE file present

### Validation Command

```bash
uv run python scripts/validate_plugin.py /path/to/my-plugin --verbose
```

---

## Related References

- [Hook Validation](hook-validation.md) - Hook configuration details
- [Skill Validation](skill-validation.md) - Skill structure details
- [MCP Validation](mcp-validation.md) - MCP server configuration
- [Marketplace Validation](marketplace-validation.md) - Marketplace structure
