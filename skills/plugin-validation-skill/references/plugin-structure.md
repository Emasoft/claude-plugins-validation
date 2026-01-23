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
  "keywords": ["utility", "automation", "development"],
  "agents": [
    "./agents/my-agent.md",
    "./agents/another-agent.md"
  ],
  "skills": "./skills/",
  "hooks": "./hooks/additional-hooks.json"
}
```

| Field | Type | Description |
|-------|------|-------------|
| author | object or string | Author name and email |
| homepage | string | Project homepage URL |
| repository | string | Source repository URL |
| license | string | SPDX license identifier |
| keywords | array | Tags for discovery |
| agents | array | Array of agent .md file paths |
| skills | string | Path to skills directory |
| hooks | string | Path to additional hooks file |

### Fields to Avoid

These fields are NOT valid in plugin.json:

| Invalid Field | Why |
|---------------|-----|
| scripts | Not part of plugin spec |
| templates | Not part of plugin spec |
| hooks (for hooks.json) | hooks/hooks.json auto-loads |

### Agent Field Format

The `agents` field MUST be an array of file paths:

```json
{
  "agents": [
    "./agents/my-agent.md",
    "./agents/another-agent.md"
  ]
}
```

NOT a directory path:
```json
{
  "agents": "./agents/"  // WRONG!
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
| `${CLAUDE_PLUGIN_ROOT}` | Absolute path to plugin directory | All plugin-relative paths |
| `${CLAUDE_PROJECT_DIR}` | Current project root | Accessing project files |

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

### Path Rules

1. **Always use variables** for plugin paths - never hardcode
2. **Relative paths** start with `./` when in manifest
3. **No path traversal** - `../` may not work after installation
4. **Absolute paths break** portability across systems

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

**Wrong:**
```json
{
  "agents": "./agents/"
}
```

**Correct:**
```json
{
  "agents": [
    "./agents/agent-one.md",
    "./agents/agent-two.md"
  ]
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

### Error: Adding hooks.json to Manifest

**Wrong:**
```json
{
  "hooks": "./hooks/hooks.json"
}
```

**Correct:**
- Just place hooks.json in hooks/ directory
- It's auto-loaded by Claude Code
- Only use hooks field for ADDITIONAL hook files

---

## 6. Validation Checklist

### Pre-release Checklist

- [ ] `.claude-plugin/plugin.json` exists
- [ ] plugin.json has name, version, description
- [ ] Plugin name is kebab-case
- [ ] Version follows semver (X.Y.Z)
- [ ] All components at plugin ROOT (not in .claude-plugin/)
- [ ] agents field is array of .md paths (if present)
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
