# Comprehensive Validation Checklist

Master checklist for validating all Claude Code plugin components. Use this checklist before publishing any plugin or marketplace.

## Table of Contents

- [1. Plugin Manifest Checklist](#1-plugin-manifest-checklist)
- [2. Plugin Structure Checklist](#2-plugin-structure-checklist)
- [3. Hook Configuration Checklist](#3-hook-configuration-checklist)
- [4. Skill Validation Checklist](#4-skill-validation-checklist)
- [5. MCP Server Checklist](#5-mcp-server-checklist)
- [6. Marketplace Checklist](#6-marketplace-checklist)
- [7. Script and Code Quality Checklist](#7-script-and-code-quality-checklist)
- [8. Pre-Release Final Checklist](#8-pre-release-final-checklist)
- [9. Validation Commands](#9-validation-commands)

---

## 1. Plugin Manifest Checklist

### Required Fields (.claude-plugin/plugin.json)

- [ ] `.claude-plugin/plugin.json` file exists
- [ ] File contains valid JSON (no syntax errors)
- [ ] `name` field present and is kebab-case (lowercase, hyphenated)
- [ ] `version` field present and follows semver (X.Y.Z)
- [ ] `description` field present with clear explanation

### Optional Fields (If Present)

- [ ] `author` is string or object with `name`, `email`
- [ ] `homepage` is valid URL
- [ ] `repository` is valid URL
- [ ] `license` is valid SPDX identifier
- [ ] `keywords` is array of strings

### Manifest Rules

- [ ] `agents` field is array of `.md` file paths (NOT directory)
- [ ] `scripts` field is NOT present (invalid field)
- [ ] `templates` field is NOT present (invalid field)
- [ ] `hooks` field NOT pointing to `./hooks/hooks.json` (auto-loaded)
- [ ] `hooks` field only used for ADDITIONAL hook files

### Manifest Validation Command

```bash
jq . .claude-plugin/plugin.json && echo "✓ Valid JSON"
```

---

## 2. Plugin Structure Checklist

### Directory Layout

- [ ] `.claude-plugin/` directory exists at plugin root
- [ ] `plugin.json` is INSIDE `.claude-plugin/` (not at root)
- [ ] `commands/` directory at ROOT (not in .claude-plugin/)
- [ ] `agents/` directory at ROOT (not in .claude-plugin/)
- [ ] `skills/` directory at ROOT (not in .claude-plugin/)
- [ ] `hooks/` directory at ROOT (not in .claude-plugin/)

### Component Files

- [ ] All referenced command .md files exist
- [ ] All referenced agent .md files exist
- [ ] All referenced skill directories contain SKILL.md
- [ ] README.md exists at plugin root
- [ ] LICENSE file present

### Path Variables

- [ ] All script paths use `${CLAUDE_PLUGIN_ROOT}`
- [ ] No hardcoded absolute paths anywhere
- [ ] No path traversal (`../`) in configurations
- [ ] Relative paths start with `./`

---

## 3. Hook Configuration Checklist

### hooks.json Structure

- [ ] `hooks/hooks.json` is valid JSON
- [ ] Top-level has `hooks` object
- [ ] Optional `description` field is string

### Event Types

Only these 13 event types are valid:

- [ ] `PreToolUse` (supports matcher)
- [ ] `PostToolUse` (supports matcher)
- [ ] `PostToolUseFailure` (supports matcher)
- [ ] `PermissionRequest` (supports matcher)
- [ ] `UserPromptSubmit` (NO matcher)
- [ ] `Notification` (supports matcher)
- [ ] `Stop` (NO matcher)
- [ ] `SubagentStop` (NO matcher)
- [ ] `SubagentStart` (NO matcher)
- [ ] `SessionStart` (supports matcher)
- [ ] `SessionEnd` (NO matcher)
- [ ] `PreCompact` (supports matcher)
- [ ] `Setup` (supports matcher)

### Matcher Configuration

- [ ] Matchers only used with matcher-supporting events
- [ ] Matcher patterns are valid regex or tool names
- [ ] Tool names correctly spelled (Read, Write, Edit, Bash, etc.)

### Hook Definitions

- [ ] Each hook has `type` field ("command" or "prompt")
- [ ] Command hooks have `command` field
- [ ] Command paths use `${CLAUDE_PLUGIN_ROOT}`
- [ ] Prompt hooks have `prompt` field
- [ ] Optional `timeout` is reasonable (default: 60)

### Hook Scripts

- [ ] All referenced scripts exist
- [ ] All scripts are executable (`chmod +x`)
- [ ] Scripts have proper shebang (`#!/bin/bash` or `#!/usr/bin/env python3`)
- [ ] Scripts handle stdin JSON input correctly
- [ ] Scripts return valid JSON when needed
- [ ] Exit codes are correct (0=success, 2=blocking error)

### Hook Validation Command

```bash
jq . hooks/hooks.json && echo "✓ Valid JSON"
```

---

## 4. Skill Validation Checklist

### Skill Directory Structure

- [ ] Each skill is a directory (not a file)
- [ ] SKILL.md exists in skill directory
- [ ] references/ subdirectory properly organized (if present)

### SKILL.md Frontmatter

- [ ] Frontmatter has opening `---` delimiter
- [ ] Frontmatter has closing `---` delimiter
- [ ] Frontmatter is valid YAML
- [ ] `name` field present (required)
- [ ] `description` field present (required)

### Optional Frontmatter Fields

- [ ] `tags` is array of strings (if present)
- [ ] `user-invocable` is boolean (if present)
- [ ] `aliases` is array of strings (if present)
- [ ] `version` follows semver (if present)

### Claude Code Specific Fields

- [ ] `context` value is `fork` if present (only valid value)
- [ ] `agent` value is valid if present:
  - `api-coordinator`
  - `test-engineer`
  - `deploy-agent`
  - `debug-specialist`
  - `code-reviewer`
- [ ] `user-invocable` is `true` or `false` (if present)

### Skill Content

- [ ] Content has clear structure with headings
- [ ] Examples are included
- [ ] No broken internal links
- [ ] References link to existing files

### Skill Validation Command

```bash
# Validate with this plugin's script
uv run python scripts/validate_skill.py /path/to/skill

# Or with OpenSpec validator (ignores Claude Code fields)
skills-ref validate /path/to/skill
```

---

## 5. MCP Server Checklist

### Configuration Location

- [ ] `.mcp.json` at plugin root OR
- [ ] `mcpServers` inline in plugin.json OR
- [ ] `mcpServers` references external file

### JSON Structure

- [ ] Configuration is valid JSON
- [ ] `mcpServers` is object with named servers
- [ ] Each server has unique name

### stdio Transport (Default)

- [ ] `command` field present (required for stdio)
- [ ] Command path uses `${CLAUDE_PLUGIN_ROOT}`
- [ ] Command executable exists and is runnable
- [ ] `args` is array of strings (if present)
- [ ] `env` uses `${VAR}` syntax (if present)
- [ ] `cwd` uses `${CLAUDE_PLUGIN_ROOT}` (if present)

### http Transport

- [ ] `type` field is `"http"`
- [ ] `url` field present (required for http)
- [ ] URL is valid HTTPS URL
- [ ] `headers` uses `${VAR}` for secrets (if present)

### sse Transport (Deprecated)

- [ ] `type` field is `"sse"`
- [ ] `url` field present (required for sse)
- [ ] Consider migrating to http transport

### Environment Variables

- [ ] All env vars use `${VAR}` syntax (not `$VAR`)
- [ ] Optional vars have defaults: `${VAR:-default}`
- [ ] Required vars are documented

### Path Handling

- [ ] No absolute paths
- [ ] No path traversal (`../`)
- [ ] Plugin paths use `${CLAUDE_PLUGIN_ROOT}`
- [ ] Project paths use `${CLAUDE_PROJECT_DIR}`

### MCP Validation Command

```bash
uv run python scripts/validate_mcp.py /path/to/plugin
```

---

## 6. Marketplace Checklist

### marketplace.json Structure

- [ ] `marketplace.json` or `.claude-plugin/marketplace.json` exists
- [ ] File is valid JSON
- [ ] `name` field present (required)
- [ ] `plugins` field present and is array (required)

### Marketplace Metadata

- [ ] Name is kebab-case
- [ ] `version` follows semver (if present)
- [ ] `description` explains the marketplace (if present)

### Plugin Entries

- [ ] Each plugin has `name` field (required)
- [ ] Plugin names are kebab-case
- [ ] Plugin names are unique (no duplicates)
- [ ] `version` follows semver (if present)
- [ ] `description` is clear (if present)

### Source Configuration

**CRITICAL: Choose correct format based on scenario**

| Scenario | Format | Example |
|----------|--------|---------|
| Plugin as local subdirectory | String path | `"source": "./my-plugin"` |
| Plugin as git submodule | String path | `"source": "./my-plugin"` |
| Plugin from remote git | Object | `"source": {"type": "git", "repository": "..."}` |
| Plugin from npm | Object | `"source": {"type": "npm", "package": "..."}` |
| Plugin from URL | Object | `"source": {"type": "url", "url": "..."}` |

### **CRITICAL: Git Submodules / Local Plugins**

- [ ] **Local plugins use STRING PATH source, NOT git object**
- [ ] If plugin directory exists locally, source MUST be `"./plugin-name"`
- [ ] If source is `{"type": "git", ...}` but plugin exists locally → **CRITICAL ERROR**
- [ ] Use `repository` field at plugin level for documentation only

**WRONG (local marketplace with local plugin subdirectories):**
```json
{
  "source": {
    "type": "git",
    "repository": "https://github.com/user/plugin"
  }
}
```

**CORRECT (for local marketplace with plugin subdirectories):**
```json
{
  "source": "./plugin-name",
  "repository": "https://github.com/user/plugin"
}
```

### Git Source Validation (When Using Remote Git)

- [ ] `type` is `"git"`
- [ ] `repository` is valid git URL
- [ ] `branch` is valid branch name (if present)
- [ ] `tag` is valid tag (if present)

### Local Source Validation

- [ ] Path resolves relative to marketplace.json
- [ ] Plugin directory exists
- [ ] Plugin contains valid plugin.json

### GitHub Deployment (For Public Marketplaces)

- [ ] Main README.md exists at marketplace root
- [ ] README has Installation section with 4 steps:
  1. Add marketplace command
  2. Install plugin command
  3. Verify installation command
  4. Restart reminder
- [ ] README has Update section
- [ ] README has Uninstall section
- [ ] README has Troubleshooting section
- [ ] Each plugin subfolder has README.md
- [ ] No placeholder content ([TODO], [INSERT], etc.)

### Marketplace Validation Command

```bash
uv run python scripts/validate_marketplace.py /path/to/marketplace --verbose
```

---

## 7. Script and Code Quality Checklist

### Python Scripts

- [ ] All Python scripts pass ruff linting
- [ ] All Python scripts pass mypy type checking
- [ ] Proper shebang: `#!/usr/bin/env python3`
- [ ] Executable permission set

```bash
ruff check scripts/*.py
mypy scripts/*.py
```

### Bash Scripts

- [ ] All Bash scripts pass shellcheck
- [ ] Proper shebang: `#!/bin/bash` or `#!/usr/bin/env bash`
- [ ] Executable permission set

```bash
shellcheck scripts/*.sh
chmod +x scripts/*.sh
```

### General Script Requirements

- [ ] Scripts don't use hardcoded paths
- [ ] Scripts handle errors gracefully
- [ ] Scripts have clear output messages
- [ ] Hook scripts read stdin JSON correctly
- [ ] Hook scripts output valid JSON when required

---

## 8. Pre-Release Final Checklist

### Documentation

- [ ] README.md complete and up-to-date
- [ ] All features documented
- [ ] Installation instructions correct
- [ ] Usage examples provided
- [ ] Troubleshooting section exists

### Testing

- [ ] Plugin loads without errors: `claude --plugin-dir /path/to/plugin`
- [ ] All hooks fire correctly
- [ ] All commands work
- [ ] All skills accessible
- [ ] MCP servers start and respond

### Validation Scripts

- [ ] All validation scripts pass with exit code 0

```bash
uv run python scripts/validate_plugin.py /path/to/plugin --verbose
```

### Version Consistency

- [ ] Version in plugin.json matches CHANGELOG
- [ ] Version in plugin.json matches marketplace entry (if applicable)
- [ ] Git tag matches version (if publishing)

---

## 9. Validation Commands

### Quick Reference

```bash
# Validate entire plugin
uv run python scripts/validate_plugin.py /path/to/plugin --verbose

# Validate hooks only
uv run python scripts/validate_hook.py /path/to/hooks.json

# Validate skills only
uv run python scripts/validate_skill.py /path/to/skill

# Validate MCP only
uv run python scripts/validate_mcp.py /path/to/plugin

# Validate marketplace
uv run python scripts/validate_marketplace.py /path/to/marketplace --verbose

# OpenSpec skill validation
skills-ref validate /path/to/skill
```

### Exit Code Reference

| Code | Severity | Meaning |
|------|----------|---------|
| 0 | None | All checks passed |
| 1 | Critical | Plugin unusable - must fix immediately |
| 2 | Major | Some features may fail - should fix |
| 3 | Minor | Warnings only - recommended to fix |

### JSON Output

For CI/CD integration, use `--json` flag:

```bash
uv run python scripts/validate_plugin.py /path/to/plugin --json > results.json
```

---

## Related References

- [Plugin Structure](plugin-structure.md) - Complete plugin layout
- [Hook Validation](hook-validation.md) - Hook configuration details
- [Skill Validation](skill-validation.md) - Skill structure details
- [MCP Validation](mcp-validation.md) - MCP server configuration
- [Marketplace Validation](marketplace-validation.md) - Marketplace setup
