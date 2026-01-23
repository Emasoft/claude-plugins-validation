---
name: plugin-validation-skill
description: Comprehensive validation skill for Claude Code plugins, marketplaces, hooks, skills, and MCP servers. Use this skill when examining, debugging, or validating any Claude Code plugin component.
tags:
  - validation
  - plugins
  - marketplace
  - hooks
  - skills
  - mcp
  - quality-assurance
user-invocable: true
---

# Plugin Validation Skill

This skill teaches how to validate Claude Code plugins and all their components. Use this skill whenever you need to examine, debug, or ensure quality of plugin structures.

## Table of Contents

1. [When to Use This Skill](#when-to-use-this-skill)
2. [Quick Start](#quick-start)
3. [Validation Scripts](#validation-scripts)
4. [Component Reference](#component-reference)
5. [Troubleshooting](#troubleshooting)
6. [Integration Tips](#integration-tips)
7. [Official Documentation Sources](#official-documentation-sources)
8. [How to Use Official Documentation](#how-to-use-official-documentation)
9. [Related Tools](#related-tools)

---

## When to Use This Skill

Use this skill when:

- **Creating a new plugin**: Validate structure before release
- **Debugging plugin issues**: Identify configuration errors
- **Reviewing plugin PRs**: Ensure compliance with specifications
- **Updating existing plugins**: Verify changes don't break compatibility
- **Setting up marketplaces**: Validate marketplace configuration
- **Configuring MCP servers**: Ensure correct server definitions
- **Writing hooks**: Validate hook configurations and scripts
- **Creating skills**: Ensure skill structure and frontmatter are correct

---

## Quick Start

### Validate an Entire Plugin

```bash
cd /path/to/claude-plugins-validation
uv run python scripts/validate_plugin.py /path/to/my-plugin --verbose
```

### Validate Specific Components

```bash
# Validate a skill
uv run python scripts/validate_skill.py /path/to/skill-dir

# Validate hooks
uv run python scripts/validate_hook.py /path/to/hooks.json

# Validate MCP configuration
uv run python scripts/validate_mcp.py /path/to/plugin

# Validate a marketplace
uv run python scripts/validate_marketplace.py /path/to/marketplace
```

### Interpret Exit Codes

| Code | Meaning | Action |
|------|---------|--------|
| 0 | All passed | Ready to use |
| 1 | Critical | Plugin broken - must fix |
| 2 | Major | Features may fail - should fix |
| 3 | Minor | Warnings only - recommended |

---

## Validation Scripts

This plugin includes five validation scripts:

### 1. validate_plugin.py - Main Plugin Validator

**Purpose**: Validates complete plugin structure, manifest, and all components.

**What it checks**:
- Plugin manifest (.claude-plugin/plugin.json)
- Directory structure
- Commands, agents, skills references
- Hooks configuration (calls validate_hook.py)
- MCP servers (calls validate_mcp.py)
- Script linting (ruff for Python, shellcheck for bash)

**Reference**: See [references/plugin-structure.md](references/plugin-structure.md) for:
- Complete plugin directory structure
- plugin.json required and optional fields
- Component placement rules
- Common structure errors

### 2. validate_hook.py - Hook Configuration Validator

**Purpose**: Validates hooks.json and hook script configurations.

**What it checks**:
- JSON structure validity
- Event types (13 valid events)
- Matcher patterns (tool names or regex)
- Script paths and executability
- Hook type configuration (command vs prompt)

**Reference**: See [references/hook-validation.md](references/hook-validation.md) for:
- Valid hook event types
- Matcher syntax and examples
- Hook input/output format
- Script requirements

### 3. validate_skill.py - Skill Structure Validator

**Purpose**: Validates skill directory structure and SKILL.md frontmatter.

**What it checks**:
- SKILL.md existence and structure
- Frontmatter YAML validity
- Required fields (name, description)
- Optional fields validation
- references/ directory

**Reference**: See [references/skill-validation.md](references/skill-validation.md) for:
- Skill directory structure
- Frontmatter field definitions
- Claude Code specific fields
- Best practices

### 4. validate_mcp.py - MCP Server Validator

**Purpose**: Validates MCP server configurations in plugins.

**What it checks**:
- .mcp.json file structure
- Inline mcpServers in plugin.json
- Transport types (stdio, http, sse)
- Required fields per transport
- Environment variable syntax
- Path portability

**Reference**: See [references/mcp-validation.md](references/mcp-validation.md) for:
- MCP configuration formats
- Transport type requirements
- Environment variable usage
- Path handling best practices

### 5. validate_marketplace.py - Marketplace Validator

**Purpose**: Validates marketplace configuration files.

**What it checks**:
- marketplace.json structure
- Required fields (name, plugins)
- Plugin entries validation
- Source type configurations
- Local path resolution

**Reference**: See [references/marketplace-validation.md](references/marketplace-validation.md) for:
- Marketplace structure
- Plugin source types
- Version management
- Distribution best practices

---

## Component Reference

### Plugin Structure Overview

```
my-plugin/
├── .claude-plugin/
│   └── plugin.json          # REQUIRED: Plugin manifest
├── commands/                 # Slash commands
│   └── my-command.md
├── agents/                   # Agent definitions
│   └── my-agent.md
├── skills/                   # Skills (directories)
│   └── my-skill/
│       ├── SKILL.md
│       └── references/
├── hooks/                    # Hook configurations
│   └── hooks.json
├── scripts/                  # Utility scripts
│   └── my-script.sh
├── .mcp.json                 # MCP server definitions
└── README.md
```

### Critical Rules

1. **Components at ROOT**: commands/, agents/, skills/, hooks/ must be at plugin root, NOT inside .claude-plugin/

2. **Path variables**: Always use `${CLAUDE_PLUGIN_ROOT}` for plugin-relative paths

3. **Naming conventions**: Use kebab-case for plugin names, semver for versions

4. **hooks.json auto-loading**: The standard hooks/hooks.json is auto-loaded - don't add it to plugin.json

5. **Agent file format**: The `agents` field in plugin.json must be an array of .md file paths

### Reference Documents

For detailed specifications, read:

| Topic | Reference File |
|-------|----------------|
| Plugin Structure | [references/plugin-structure.md](references/plugin-structure.md) |
| Hook Configuration | [references/hook-validation.md](references/hook-validation.md) |
| Skill Structure | [references/skill-validation.md](references/skill-validation.md) |
| MCP Servers | [references/mcp-validation.md](references/mcp-validation.md) |
| Marketplaces | [references/marketplace-validation.md](references/marketplace-validation.md) |

---

## Troubleshooting

### Plugin Won't Load

1. Check plugin.json is valid JSON: `jq . .claude-plugin/plugin.json`
2. Verify required fields exist: name, version, description
3. Check agents is an array of paths, not a directory
4. Ensure components are at plugin ROOT

### Hooks Not Firing

1. Verify hooks.json syntax: `jq . hooks/hooks.json`
2. Check event type is valid (see reference)
3. Verify matcher matches target tool
4. Ensure scripts are executable: `chmod +x scripts/*.sh`
5. Check script paths use `${CLAUDE_PLUGIN_ROOT}`

### MCP Server Not Starting

1. Check .mcp.json is valid JSON
2. Verify command exists and is executable
3. Check paths use `${CLAUDE_PLUGIN_ROOT}`
4. For stdio: ensure command field exists
5. For http: ensure url field exists
6. Run `claude --debug` to see MCP errors

### Skill Not Found

1. Verify SKILL.md exists in skill directory
2. Check frontmatter has name and description
3. Ensure skill is referenced in plugin.json

### Marketplace Plugin Install Fails

1. Validate marketplace.json: `uv run python scripts/validate_marketplace.py .`
2. Check local paths resolve correctly
3. Verify each plugin has required name field
4. Check source configuration matches type

---

## Integration Tips

### CI/CD Integration

Add validation to your CI pipeline:

```yaml
- name: Validate Plugin
  run: |
    cd /path/to/claude-plugins-validation
    uv run python scripts/validate_plugin.py ${{ github.workspace }} --json > validation.json
    exit_code=$?
    if [ $exit_code -ne 0 ]; then
      cat validation.json
      exit $exit_code
    fi
```

### Pre-commit Hook

Create `.git/hooks/pre-commit`:

```bash
#!/bin/bash
uv run python /path/to/validate_plugin.py . --json
exit $?
```

### VS Code Integration

Add to `.vscode/tasks.json`:

```json
{
  "label": "Validate Plugin",
  "type": "shell",
  "command": "uv run python /path/to/validate_plugin.py ${workspaceFolder} --verbose"
}
```

---

## Official Documentation Sources

**IMPORTANT**: For the most up-to-date specifications, fetch and read these official documentation URLs. Specifications change frequently, so always consult the latest versions before validating plugins.

### Claude Code Fundamentals

| Topic | Official URL |
|-------|--------------|
| Latest Changes | https://code.claude.com/docs/en/changelog.md |
| How Claude Code Works | https://code.claude.com/docs/en/how-claude-code-works.md |
| Extend Claude Code | https://code.claude.com/docs/en/features-overview.md |

### Settings & CLI

| Topic | Official URL |
|-------|--------------|
| Claude Code Settings | https://code.claude.com/docs/en/settings.md |
| CLI Reference | https://code.claude.com/docs/en/cli-reference.md |

### Marketplaces

| Topic | Official URL |
|-------|--------------|
| Discover Plugins | https://code.claude.com/docs/en/discover-plugins.md |
| Create Marketplaces | https://code.claude.com/docs/en/plugin-marketplaces.md |

### Plugins

| Topic | Official URL |
|-------|--------------|
| Create Plugins | https://code.claude.com/docs/en/plugins.md |
| Plugins Reference | https://code.claude.com/docs/en/plugins-reference.md |

### Custom Agents

| Topic | Official URL |
|-------|--------------|
| Create Subagents | https://code.claude.com/docs/en/sub-agents.md |

### Hooks

| Topic | Official URL |
|-------|--------------|
| Hooks Guide | https://code.claude.com/docs/en/hooks-guide.md |
| Hooks Reference | https://code.claude.com/docs/en/hooks.md |

### Skills

| Topic | Official URL |
|-------|--------------|
| Extend with Skills | https://code.claude.com/docs/en/skills.md |

### Other Extensions

| Topic | Official URL |
|-------|--------------|
| Output Styles | https://code.claude.com/docs/en/output-styles.md |
| Status Line Config | https://code.claude.com/docs/en/statusline.md |
| GitHub Actions | https://code.claude.com/docs/en/github-actions.md |

### MCP (Model Context Protocol)

| Topic | Official URL |
|-------|--------------|
| MCP Spec Changelog | https://modelcontextprotocol.io/specification/2025-11-25/changelog.md |
| Connect via MCP | https://code.claude.com/docs/en/mcp.md |
| MCP Server Overview | https://modelcontextprotocol.io/specification/2025-11-25/server/index.md |
| MCP Specification | https://modelcontextprotocol.io/specification/2025-11-25/index.md |
| MCP Schema Reference | https://modelcontextprotocol.io/specification/2025-11-25/schema.md |
| Build MCP Client | https://modelcontextprotocol.io/docs/develop/build-client.md |
| Build MCP Server | https://modelcontextprotocol.io/docs/develop/build-server.md |
| MCP Architecture | https://modelcontextprotocol.io/docs/learn/architecture.md |
| MCP Client Concepts | https://modelcontextprotocol.io/docs/learn/client-concepts.md |
| MCP Server Concepts | https://modelcontextprotocol.io/docs/learn/server-concepts.md |
| MCP SDKs | https://modelcontextprotocol.io/docs/sdk.md |
| MCP Inspector | https://modelcontextprotocol.io/docs/tools/inspector.md |

### MCP Detailed Specifications

| Topic | Official URL |
|-------|--------------|
| Architecture | https://modelcontextprotocol.io/specification/2025-11-25/architecture/index.md |
| Authorization | https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization.md |
| Basic Overview | https://modelcontextprotocol.io/specification/2025-11-25/basic/index.md |
| Security Best Practices | https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices.md |
| Transports | https://modelcontextprotocol.io/specification/2025-11-25/basic/transports.md |
| Tasks | https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks.md |
| Roots | https://modelcontextprotocol.io/specification/2025-11-25/client/roots.md |
| Prompts | https://modelcontextprotocol.io/specification/2025-11-25/server/prompts.md |
| Resources | https://modelcontextprotocol.io/specification/2025-11-25/server/resources.md |
| Tools | https://modelcontextprotocol.io/specification/2025-11-25/server/tools.md |
| Sampling | https://modelcontextprotocol.io/specification/2025-11-25/client/sampling.md |
| Completion | https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/completion.md |
| Logging | https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/logging.md |
| Pagination | https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination.md |
| Versioning | https://modelcontextprotocol.io/specification/versioning.md |

### Troubleshooting

| Topic | Official URL |
|-------|--------------|
| Troubleshooting | https://code.claude.com/docs/en/troubleshooting.md |

---

## How to Use Official Documentation

When validating plugins, always verify against the latest specifications:

1. **Before validation**: Fetch the relevant spec URL to confirm current requirements
2. **When errors occur**: Check the official docs for updated field names or structures
3. **For MCP servers**: The MCP spec version (currently 2025-11-25) defines transport types and fields
4. **For hooks**: Check hooks.md for the current list of valid event types

### Example: Fetching Latest Hook Events

```bash
# Fetch the hooks reference to verify valid event types
curl -s https://code.claude.com/docs/en/hooks.md | grep -A 20 "## Hook Events"
```

### Example: Checking MCP Transport Types

```bash
# Fetch MCP transports spec for current transport types
curl -s https://modelcontextprotocol.io/specification/2025-11-25/basic/transports.md
```

---

## Related Tools

- **shellcheck** - Bash script linting (https://www.shellcheck.net/)
- **ruff** - Python linting and formatting (https://docs.astral.sh/ruff/)
- **mypy** - Python type checking (https://mypy.readthedocs.io/)
- **jq** - JSON validation and querying (https://stedolan.github.io/jq/)
- **skills-ref** - OpenSpec Agent Skills validator (https://github.com/agentskills/agentskills)
