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
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep, Write, Task
---

# Plugin Validation Skill

Validates Claude Code plugins and all their components for quality and compliance.

## Overview

This skill provides comprehensive validation for Claude Code plugin components:
- Plugin manifest (`plugin.json`) structure and fields
- Hook configurations (`hooks.json`) and script validation
- Skill frontmatter and content quality (190+ rules)
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
CLAUDE_PRIVATE_USERNAMES="your_username" uv run python scripts/validate_plugin.py /path/to/plugin --report docs_dev/validate_plugin_YYYYMMDD.md
```

### Step 1-5: Validation

1. Navigate to the claude-plugins-validation directory
2. Run the validator: `uv run python scripts/validate_plugin.py /path/to/plugin --report docs_dev/validate_plugin_YYYYMMDD.md`
3. Review output by severity (CRITICAL > MAJOR > MINOR)
4. Fix issues in priority order
5. Re-run validation until exit code 0

### Validation Checklist

Copy this checklist and track your progress:

- [ ] Navigate to the claude-plugins-validation directory
- [ ] Run the main validator: `uv run python scripts/validate_plugin.py /path/to/plugin --report docs_dev/validate_plugin_YYYYMMDD.md`
- [ ] Always use `--report docs_dev/validate_<name>_YYYYMMDD.md` flag — saves full output to file, prints only compact summary
- [ ] Present the summary table and issue list to the user
- [ ] Always show the saved report file path at the end of the output
- [ ] Fix all CRITICAL issues first (plugin won't work)
- [ ] Fix MAJOR issues next (features may fail)
- [ ] Address MINOR issues for polish
- [ ] Re-run validation until exit code 0

## Output

The validators return:
- **Exit Code**: 0 (pass), 1 (CRITICAL), 2 (MAJOR), 3 (MINOR), 4 (NIT, --strict only). WARNING never blocks.
- **Summary**: Issue counts by severity level
- **Details**: Each issue with file location and fix suggestion
- **Grade**: A-F letter grade for skill validation
- **Report File**: Full output saved to `docs_dev/validate_<plugin-name>_<date>.md`

**IMPORTANT**: Always use the `--report` flag when running validation scripts. This saves full output to a timestamped file and prints only a compact summary to stdout. Provide the report file path to the user — never read the report file yourself.

## Error Handling

- **Non-zero exit code from validator**: Report the severity level and list all failing checks. Do NOT proceed with publishing until MAJOR/CRITICAL are resolved.
- **Missing dependencies** (ruff, mypy, shellcheck): Install with `uv pip install ruff mypy` or `brew install shellcheck`. Report which tools are missing.
- **Permission errors**: Ensure scripts are executable (`chmod +x scripts/*.py`).
- **Invalid JSON/YAML**: Show the parse error with file path and line number. Fix syntax before re-validating.
- **Timeout on large plugins**: Increase subprocess timeout or validate components individually.

## Examples

### Example 1: Validate a Plugin

```bash
cd /path/to/claude-plugins-validation
uv run python scripts/validate_plugin.py /path/to/my-plugin --verbose --report docs_dev/validate_plugin_YYYYMMDD.md
```

### Example 2: Validate a Skill Only

```bash
uv run python scripts/validate_skill_comprehensive.py /path/to/skill-dir --strict --report docs_dev/validate_skill_YYYYMMDD.md
```

### Example 3: CI/CD Integration

```bash
uv run python scripts/validate_plugin.py ./my-plugin --json > validation-results.json
```

## Resources

- [Validation Checklist](references/validation-checklist.md) - Master checklist for pre-release
> **validation-checklist.md** — Table of Contents:
> - 1. Plugin Manifest Checklist
> - 2. Plugin Structure Checklist
> - 3. Hook Configuration Checklist
> - 4. Skill Validation Checklist
> - 5. MCP Server Checklist
> - 6. Marketplace Checklist
> - 7. Agent Checklist
> - 8. LSP Server Checklist
> - 9. Script and Code Quality Checklist
> - 10. Pre-Release Final Checklist
> - 11. Validation Commands
> - Related References
- [Plugin Structure](references/plugin-structure.md) - Required plugin directory layout
> **plugin-structure.md** — Table of Contents:
> - 1. Directory Structure
> - 2. Plugin Manifest (plugin.json)
> - 3. Component Placement Rules
> - 4. Path Variables
> - 5. Common Structure Errors
> - 6. Validation Checklist
> - Related References
- [Hook Validation](references/hook-validation.md) - Hook configuration reference
> **hook-validation.md** — Table of Contents:
> - 1. Hook Configuration File
> - 2. Valid Hook Events
> - 3. Matcher Syntax
> - 4. Hook Types
> - 5. Hook Input/Output Format
> - 6. Script Requirements
> - 7. Common Hook Errors
> - 8. Validation Checklist
> - Related References
- [Troubleshooting](references/troubleshooting-python-scripts.md) - Common issues and fixes
  - 1. Bash Arithmetic Exit Codes
  - 2. Unused Variable Warnings - Pyright/ruff
  - 3. Missing Python Dependencies - ModuleNotFoundError
  - 4. Git Hook Not Running
  - 5. Plugin JSON Missing Required Fields
  - 6. Ruff Linting - Unused Variable Error
  - 7. Marketplace Plugin Source Format
  - 8. Version Consistency Between Plugins and Marketplace
  - 9. Git Tag Already Exists Error
  - 10. subprocess.run Output Truncation
  - 11. Best Practices Summary
  - 12. Quick Diagnostic Commands

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

Before publishing any plugin, use the **Master Validation Checklist** (see Resources section above) which covers:
- Plugin manifest and structure checks
- Hook configuration validation
- Skill frontmatter requirements
- MCP server configuration
- Marketplace configuration (including CRITICAL git submodules check)
- Script and code quality

### Validate an Entire Plugin

```bash
cd /path/to/claude-plugins-validation
uv run python scripts/validate_plugin.py /path/to/my-plugin --verbose --report docs_dev/validate_plugin_YYYYMMDD.md
```

### Validate Specific Components

```bash
# Validate a skill
uv run python scripts/validate_skill.py /path/to/skill-dir --report docs_dev/validate_skill_YYYYMMDD.md

# Validate hooks
uv run python scripts/validate_hook.py /path/to/hooks.json --report docs_dev/validate_hook_YYYYMMDD.md

# Validate MCP configuration
uv run python scripts/validate_mcp.py /path/to/plugin --report docs_dev/validate_mcp_YYYYMMDD.md

# Validate a marketplace
uv run python scripts/validate_marketplace.py /path/to/marketplace --report docs_dev/validate_marketplace_YYYYMMDD.md
```

### Interpret Exit Codes

| Code | Meaning | Action |
|------|---------|--------|
| 0 | All passed | Ready to use |
| 1 | Critical | Plugin broken - must fix |
| 2 | Major | Features may fail - should fix |
| 3 | Minor | Warnings only - recommended |
| 4 | Nit | Style issues - only with --strict |

> **Note**: WARNING severity never blocks (exit code 0). NITs only block with `--strict`.

---

## Validation Scripts

This plugin includes five key validation scripts:

### 1. validate_plugin.py - Main Plugin Validator

**Purpose**: Validates complete plugin structure, manifest, and all components.

**What it checks**:
- Plugin manifest (.claude-plugin/plugin.json)
- Directory structure
- Commands, agents, skills references
- Hooks configuration (calls validate_hook.py)
- MCP servers (calls validate_mcp.py)
- Script linting (ruff for Python, shellcheck for bash)
- Plugin-shipped settings.json validation
- Script shebang presence (cross-platform reliability)
- Content presence check (manifest without content)

**Reference**: [references/plugin-structure.md](references/plugin-structure.md)

### 2. validate_hook.py - Hook Configuration Validator

**Purpose**: Validates hooks.json and hook script configurations.

**What it checks**:
- JSON structure validity
- Event types (19 valid events)
- Matcher patterns (tool names or regex)
- Script paths and executability
- Hook type configuration (command, prompt, agent)
- Fuzzy "did you mean?" for misspelled event names
- Notification/SessionStart/PreCompact matcher validation
- Bash command portability (interpreter, tilde paths, bare cd, backslashes)
- Relative path portability (${CLAUDE_PLUGIN_ROOT} usage)

**Reference**: see `hook-validation.md` in Resources above

### 3. validate_skill.py - Skill Structure Validator

**Purpose**: Validates skill directory structure and SKILL.md frontmatter.

**What it checks**:
- SKILL.md existence and structure
- Frontmatter YAML validity
- Required fields (name, description)
- Optional fields validation
- references/ directory

**Reference**: [references/skill-validation.md](references/skill-validation.md)
> **skill-validation.md** — Table of Contents:
> - 1. Skill Directory Structure
> - 2. SKILL.md File Format
> - 3. Frontmatter Fields
> - 4. Claude Code Specific Fields
> - 5. Content Best Practices
> - 6. References Directory
> - 7. Common Skill Errors
> - 8. Validation Checklist
> - Related References

### 4. validate_mcp.py - MCP Server Validator

**Purpose**: Validates MCP server configurations in plugins.

**What it checks**:
- .mcp.json file structure
- Inline mcpServers in plugin.json
- Transport types (stdio, http, sse)
- Required fields per transport
- Environment variable syntax
- Path portability

**Reference**: [references/mcp-validation.md](references/mcp-validation.md)
> **mcp-validation.md** — Table of Contents:
> - 1. MCP Configuration Locations
> - 2. Server Definition Fields
> - 3. Transport Types
> - 3a. OAuth Support
> - 4. Environment Variables
> - 5. Path Handling
> - 6. Complete Configuration Examples
> - 7. Common MCP Errors
> - 8. Validation Checklist
> - Related References

### 5. validate_marketplace.py - Marketplace Validator

**Purpose**: Validates marketplace configuration files.

**What it checks**:
- marketplace.json structure
- Required fields (name, plugins)
- Plugin entries validation
- Source type configurations
- Local path resolution

**Reference**: [references/marketplace-validation.md](references/marketplace-validation.md)
> **marketplace-validation.md** — Table of Contents:
> - 1. Marketplace Overview
> - 2. marketplace.json Structure
> - 3. Plugin Entry Configuration
> - 4. Source Types
> - 5. Local Development Marketplace
> - 6. GitHub Deployment Validation
> - 7. Git Submodule Validation
> - 8. Common Marketplace Errors
> - 9. Validation Checklist
> - Related References

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

1. **Components at ROOT**: commands/, agents/, skills/, hooks/, scripts/ must be at plugin root, NOT inside .claude-plugin/

2. **Path variables**: Always use `${CLAUDE_PLUGIN_ROOT}` for plugin-relative paths

3. **Naming conventions**: Use kebab-case for plugin names, semver for versions

4. **hooks.json auto-loading**: The standard hooks/hooks.json is auto-loaded - don't add it to plugin.json

5. **Agent file format**: The `agents` field in plugin.json must be an array of .md file paths

For detailed specifications, see the [Resources](#resources) section above.

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
6. Use `${CLAUDE_PLUGIN_ROOT}/...` instead of relative `./` paths

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

See the **Troubleshooting** reference in the Resources section above for common Python script issues and fixes.

### Marketplace Plugin Install Fails

1. Validate marketplace.json: `uv run python scripts/validate_marketplace.py . --report docs_dev/validate_marketplace_YYYYMMDD.md`
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

Install the pre-push hook to block pushing broken plugins:

```bash
# Pre-push hook - BLOCKS pushing broken plugins (CRITICAL!)
cp skills/plugin-validation-skill/references/pre-push-hook.py .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

> **Note**: Pre-commit hooks should be configured manually for your project's needs.

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
  "command": "uv run python /path/to/validate_plugin.py ${workspaceFolder} --verbose --report docs_dev/validate_plugin_YYYYMMDD.md"
}
```

For official documentation URLs, see `references/official-docs-urls.md`.
