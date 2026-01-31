---
name: plugin-validation-skill
description: |
  Validate Claude Code plugins, hooks, skills, MCP. Use when checking plugin quality. Trigger with /cpv-validate-plugin.
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

Validates Claude Code plugins and all their components for quality and compliance.

## Overview

This skill provides comprehensive validation for Claude Code plugin components:
- Plugin manifest (`plugin.json`) structure and fields
- Hook configurations (`hooks.json`) and script validation
- Skill frontmatter and content quality (84+ rules)
- MCP server configurations (`.mcp.json`)
- Marketplace configurations and git submodules
- Agent definitions and system prompts

## Prerequisites

- Python 3.12+ with `pyyaml` installed
- `uv` package manager for running validation scripts
- Plugin directory with valid structure (`.claude-plugin/plugin.json`)

## Instructions

### Step 0: Privacy Check (IMPORTANT)

Before validating, ensure private path detection is configured. The validator auto-detects your system username to prevent accidental leaks of private home paths in published plugins.

**If auto-detection fails**, provide your username via environment variable:

```bash
# Set your username for private path detection
export CLAUDE_PRIVATE_USERNAMES="your_username"
```

Or pass it inline when running the validator:

```bash
CLAUDE_PRIVATE_USERNAMES="your_username" uv run python scripts/validate_plugin.py /path/to/plugin
```

### Step 1-5: Validation

1. Navigate to the claude-plugins-validation directory
2. Run the validator: `uv run python scripts/validate_plugin.py /path/to/plugin`
3. Review output by severity (CRITICAL > MAJOR > MINOR)
4. Fix issues in priority order
5. Re-run validation until exit code 0

### Validation Checklist

Copy this checklist and track your progress:

- [ ] Navigate to the claude-plugins-validation directory
- [ ] Run the main validator: `uv run python scripts/validate_plugin.py /path/to/plugin`
- [ ] Fix all CRITICAL issues first (plugin won't work)
- [ ] Fix MAJOR issues next (features may fail)
- [ ] Address MINOR issues for polish
- [ ] Re-run validation until exit code 0

## Output

The validators return:
- **Exit Code**: 0 (pass), 1 (critical), 2 (major), 3 (minor)
- **Summary**: Issue counts by severity level
- **Details**: Each issue with file location and fix suggestion
- **Grade**: A-F letter grade for skill validation

## Error Handling

If validation fails:
1. Check the exit code to determine severity
2. Review CRITICAL issues first (plugin won't work)
3. Address MAJOR issues next (features may fail)
4. Fix MINOR issues for polish (warnings)
5. See [Troubleshooting](#troubleshooting) for common fixes

## Examples

### Example 1: Validate a Plugin

```bash
cd /path/to/claude-plugins-validation
uv run python scripts/validate_plugin.py /path/to/my-plugin --verbose
```

### Example 2: Validate a Skill Only

```bash
uv run python scripts/validate_skill_comprehensive.py /path/to/skill-dir --strict
```

### Example 3: CI/CD Integration

```bash
uv run python scripts/validate_plugin.py ./my-plugin --json > validation-results.json
```

## Resources

- [Validation Checklist](references/validation-checklist.md) - Master checklist for pre-release
- [Plugin Structure](references/plugin-structure.md) - Required plugin directory layout
- [Hook Validation](references/hook-validation.md) - Hook configuration reference
- [Troubleshooting](references/troubleshooting-python-scripts.md) - Common issues and fixes

---

## Table of Contents

1. [When to Use This Skill](#when-to-use-this-skill)
2. [Quick Start](#quick-start)
3. [Validation Scripts](#validation-scripts)
4. [Component Reference](#component-reference)
5. [Troubleshooting](#troubleshooting)
6. [Integration Tips](#integration-tips)
7. [Official Documentation](#official-documentation)
8. [Related Tools](#related-tools)

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

### Pre-Release Checklist

Before publishing any plugin, use the **[Master Validation Checklist](references/validation-checklist.md)** which covers:
- Plugin manifest and structure checks
- Hook configuration validation
- Skill frontmatter requirements
- MCP server configuration
- Marketplace configuration (including CRITICAL git submodules check)
- Script and code quality

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
| **Master Checklist** | [references/validation-checklist.md](references/validation-checklist.md) |
| Plugin Structure | [references/plugin-structure.md](references/plugin-structure.md) |
| Hook Configuration | [references/hook-validation.md](references/hook-validation.md) |
| Skill Structure | [references/skill-validation.md](references/skill-validation.md) |
| MCP Servers | [references/mcp-validation.md](references/mcp-validation.md) |
| Marketplaces | [references/marketplace-validation.md](references/marketplace-validation.md) |
| **Pipeline Validation** | [references/pipeline-validation.md](references/pipeline-validation.md) |
| **Pre-Push Hook** | [references/pre-push-hook.py](references/pre-push-hook.py) |

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

### Python Validation Scripts Issues

See **[references/troubleshooting-python-scripts.md](references/troubleshooting-python-scripts.md)** for common issues with:
- Bash arithmetic exit codes
- Unused variable warnings (Pyright/ruff)
- Missing Python dependencies
- Git hook execution problems
- JSON parsing errors
- Path resolution issues
- Subprocess timeouts
- Version string parsing

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

### Git Hooks Installation

Install all git hooks (pre-commit, pre-push, post-commit) at once:

```bash
python scripts/setup-hooks.py
```

Or install manually:

```bash
# Pre-commit hook - validates staged changes
cp scripts/pre-commit-hook.py .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit

# Pre-push hook - BLOCKS pushing broken plugins (CRITICAL!)
cp scripts/pre-push-hook.py .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

### Pre-Push Hook Behavior

The pre-push hook (`references/pre-push-hook.py`) runs comprehensive validation before every `git push`:

| Severity | Action | Example Issues |
|----------|--------|----------------|
| CRITICAL | **Push blocked** | Missing plugin.json, invalid JSON syntax |
| MAJOR | **Push blocked** | Invalid semver, missing required fields |
| MINOR | Warning only | Missing description, unknown hook event |

**To bypass (NOT RECOMMENDED)**: `git push --no-verify`

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

## Official Documentation

For the complete list of official documentation URLs (Claude Code, MCP, Hooks, Skills, etc.), see **[references/official-docs-urls.md](references/official-docs-urls.md)**.

---

## Related Tools

- **shellcheck** - Bash script linting (https://www.shellcheck.net/)
- **ruff** - Python linting and formatting (https://docs.astral.sh/ruff/)
- **mypy** - Python type checking (https://mypy.readthedocs.io/)
- **jq** - JSON validation and querying (https://stedolan.github.io/jq/)
- **skills-ref** - OpenSpec Agent Skills validator (https://github.com/agentskills/agentskills)
