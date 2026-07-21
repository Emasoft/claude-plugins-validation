# Comprehensive Validation Checklist

Master checklist for validating all Claude Code plugin components. Use this checklist before publishing any plugin or marketplace.

## Table of Contents

- [1. Plugin Manifest Checklist](#1-plugin-manifest-checklist)
- [2. Plugin Structure Checklist](#2-plugin-structure-checklist)
- [3. Hook Configuration Checklist](#3-hook-configuration-checklist)
- [4. Skill Validation Checklist](#4-skill-validation-checklist)
- [5. MCP Server Checklist](#5-mcp-server-checklist)
- [6. Marketplace Checklist](#6-marketplace-checklist)
- [7. Agent Checklist](#7-agent-checklist)
- [8. LSP Server Checklist](#8-lsp-server-checklist)
- [9. Script and Code Quality Checklist](#9-script-and-code-quality-checklist)
- [10. Pre-Release Final Checklist](#10-pre-release-final-checklist)
- [11. Validation Commands](#11-validation-commands)

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
- [ ] `author` field (if present) is a string or object with required "name"
- [ ] `keywords` field (if present) is an array of strings
- [ ] `homepage` field (if present) is a non-empty string
- [ ] `license` field (if present) is a non-empty string
- [ ] Inline `hooks`, `mcpServers`, `lspServers` objects (if present) have valid config

### Manifest Rules

- [ ] `agents` field is array of `.md` **file** paths (NOT directory) — folder paths in `agents` are **rejected by CC** with cryptic `agents: Invalid input` (empirical 2026-04-18). Only `.md` file paths work, both string and array form.
- [ ] `scripts` field is NOT present (invalid field)
- [ ] `templates` field is NOT present (invalid field)
- [ ] `commands` field NOT present if pointing to default `./commands/` (auto-discovered)
- [ ] `agents` field NOT present if pointing to default `./agents/` (auto-discovered)
- [ ] `skills` field NOT present if pointing to default `./skills/` (auto-discovered)
- [ ] `hooks` field NOT present if pointing to default `./hooks/` (auto-discovered)
- [ ] `hooks` field does NOT point to `./hooks/hooks.json` — even though `claude plugin validate` passes silently, this triggers a runtime `Duplicate hooks file detected` error AND **cascades to disable the plugin's MCP servers** (`hook-load-failed` error). Empirical 2026-04-18.
- [ ] `mcpServers` field does NOT point to `./.mcp.json` — redundant (default file is auto-loaded). CPV emits MINOR.
- [ ] No same server name in BOTH `.mcp.json` AND inline `plugin.json:mcpServers` — collisions silently shadow with inline winning. CPV emits MAJOR per duplicate.
- [ ] No same server name in BOTH `.lsp.json` AND inline `plugin.json:lspServers` — same silent-shadow risk. CPV emits MAJOR per duplicate.
- [ ] Component path fields only used for **non-standard** locations

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
- [ ] `plugin.json:hooks` field does NOT point to `./hooks/hooks.json` (the auto-discovered default) — empirical 2026-04-18 confirmed CC's runtime emits `Duplicate hooks file detected` AND cascades `hook-load-failed` which **disables the plugin's MCP servers**. CPV emits MAJOR.
- [ ] Override hook paths point to NON-default files (e.g. `./hooks/extra.json`) — these merge cleanly without cascade

### Event Types

The canonical event list (30 valid events, with the authoritative
per-event "Has Matcher" column) lives in
[Hook Validation §2 Valid Hook Events](hook-validation.md#2-valid-hook-events),
which mirrors the validator's single source of truth
(`cpv_validation_common.py::VALID_HOOK_EVENTS` plus
`validate_hook.py::EVENTS_WITH_MATCHERS` / `EVENTS_WITHOUT_MATCHERS`).
Do NOT re-derive the list here — match-status below is a quick
checklist, not a second source of truth.

Matcher-supporting events:

- [ ] `PreToolUse` (supports matcher)
- [ ] `PostToolUse` (supports matcher)
- [ ] `PostToolUseFailure` (supports matcher)
- [ ] `PermissionRequest` (supports matcher)
- [ ] `PermissionDenied` (supports matcher)
- [ ] `Notification` (supports matcher)
- [ ] `SessionStart` (supports matcher)
- [ ] `SessionEnd` (supports matcher)
- [ ] `SubagentStart` (supports matcher)
- [ ] `SubagentStop` (supports matcher)
- [ ] `PreCompact` (supports matcher)
- [ ] `PostCompact` (supports matcher)
- [ ] `Setup` (supports matcher)
- [ ] `ConfigChange` (supports matcher)
- [ ] `StopFailure` (supports matcher)
- [ ] `InstructionsLoaded` (supports matcher)
- [ ] `Elicitation` (supports matcher)
- [ ] `ElicitationResult` (supports matcher)
- [ ] `FileChanged` (supports matcher)
- [ ] `UserPromptExpansion` (supports matcher)

Events that do NOT support matchers (matcher field is silently ignored):

- [ ] `UserPromptSubmit` (NO matcher)
- [ ] `Stop` (NO matcher)
- [ ] `TeammateIdle` (NO matcher)
- [ ] `TaskCompleted` (NO matcher)
- [ ] `TaskCreated` (NO matcher)
- [ ] `WorktreeCreate` (NO matcher)
- [ ] `WorktreeRemove` (NO matcher)
- [ ] `CwdChanged` (NO matcher)
- [ ] `PostToolBatch` (NO matcher)
- [ ] `MessageDisplay` (NO matcher)

### Matcher Configuration

- [ ] Matchers only used with matcher-supporting events
- [ ] Matcher patterns are valid regex or tool names
- [ ] Tool names correctly spelled (Read, Write, Edit, Bash, etc.)

### Hook Definitions

- [ ] Each hook has `type` field — one of the 5 valid types: "command", "http", "mcp_tool", "prompt", or "agent" (`mcp_tool` added v2.1.118)
- [ ] Command hooks have `command` field
- [ ] Command paths use `${CLAUDE_PLUGIN_ROOT}`
- [ ] Prompt hooks have `prompt` field
- [ ] Optional `timeout` is reasonable (default: 60)
- [ ] Type-restricted events do NOT use `"prompt"`/`"agent"` — see [Hook Validation §"Events That Restrict Hook Types"](hook-validation.md#events-that-restrict-hook-types). Tier 3 (`SessionStart`, `Setup`) accept only `"command"`/`"mcp_tool"`; Tier 2 (`PreCompact`, `PostCompact`, `Notification`, `ConfigChange`, `SessionEnd`, `SubagentStart`, `StopFailure`, `WorktreeCreate`, `WorktreeRemove`, `Elicitation`, `ElicitationResult`, `CwdChanged`, `FileChanged`, `InstructionsLoaded`) also exclude `"prompt"`/`"agent"`
- [ ] Timeout values are in seconds (not milliseconds) — warn if >1000
- [ ] Command hooks: "statusMessage" field (if present) is a string
- [ ] Prompt/Agent hooks: "model" field (if present) is a non-empty string
- [ ] Agent hooks have required "prompt" field
- [ ] Async field only present on command hooks

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
- [ ] `agent` present only alongside `context: fork` (otherwise MAJOR — the field has no effect)
- [ ] `agent` value is a built-in subagent type — `Explore`, `Plan`, `general-purpose`, `statusline-setup`, `Claude Code Guide` — OR a custom agent from `.claude/agents/` (a non-built-in name is INFO, not an error)
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

- [ ] `.mcp.json` at plugin root (auto-discovered) AND/OR
- [ ] `mcpServers` inline in plugin.json AND/OR
- [ ] `mcpServers` references external file (e.g. `"./extras/mcp.json"`)
- [ ] **Sources are loaded ADDITIVELY at runtime** — empirical 2026-04-18. All declared sources contribute their servers.
- [ ] `mcpServers` does NOT point to `./.mcp.json` (redundant with auto-discovery; CPV emits MINOR)

### JSON Structure

- [ ] Configuration is valid JSON
- [ ] `mcpServers` is object with named servers
- [ ] Each server has unique name **across ALL declaration sources** — same name in two sources causes silent inline-wins shadowing. CPV emits MAJOR per duplicate name.

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
- [ ] Transport type is "stdio", "http", or "sse" (sse shows deprecation warning)
- [ ] OAuth config (if present) has required "serverUrl" field
- [ ] Timeout (if present) is a positive number in seconds

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
- [ ] "owner" field present with required "name" sub-field
- [ ] Each plugin has a "source" field (now required)
- [ ] "pip" source type (if used) has "package" field
- [ ] Marketplace name is not reserved (official, anthropic, claude, test, example, demo)
- [ ] SHA/commit values are 40 hex characters

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
| Plugin from remote git | Object | `"source": {"source": "github", "repo": "owner/repo"}` |
| Plugin from git subdirectory | Object | `"source": {"source": "git-subdir", "url": "https://...", "path": "plugins/my-plugin"}` |
| Plugin from npm | Object | `"source": {"source": "npm", "package": "@org/plugin"}` |
| Plugin from URL | Object | `"source": {"source": "url", "url": "https://.../plugin.tar.gz"}` |

**Note:** Inside the source object the discriminator key is `source` (not `type`). Valid per-plugin values: `github`, `url`, `npm`, `git`, `git-subdir`, `directory` (plus a string `./path` for local sources). `settings` (and `file`, `hostPattern`, `pathPattern`) are settings-level-only sources — using one as a per-plugin `source` is a MAJOR validation error.

### **CRITICAL: Git Submodules / Local Plugins**

- [ ] **Local plugins use STRING PATH source, NOT object**
- [ ] If plugin directory exists locally, source MUST be `"./plugin-name"`
- [ ] If source is `{"source": "github", ...}` but plugin exists locally as a git submodule → **CRITICAL ERROR**
- [ ] Use `repository` field at plugin level for documentation only (metadata, not source)

**WRONG (local marketplace with local plugin subdirectories):**
```json
{
  "source": {
    "source": "github",
    "repo": "user/plugin"
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

### GitHub Source Validation (When Using Remote `github` Source)

- [ ] `source.source` is `"github"`
- [ ] `source.repo` is in `owner/repo` format
- [ ] `source.ref` is a valid branch/tag name (if present)
- [ ] `source.sha` is a 40-character hex string (if present)

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

## 7. Agent Checklist

### Agent Frontmatter

- [ ] Agent frontmatter has valid `allowedTools` (from expanded valid tools list)
- [ ] `maxTurns` (if present) is a positive integer
- [ ] `memory` (if present) has valid scope: user, project, or local
- [ ] `isolation` (if present) is "worktree"
- [ ] `background` (if present) is boolean

### Manifest `agents` field — CRITICAL CONSTRAINT (undocumented in Anthropic docs)

- [ ] If `agents` field is present in plugin.json, it contains ONLY `.md` file paths — NEVER folder paths. Empirical 2026-04-18: CC rejects folder paths with cryptic `agents: Invalid input` error in BOTH string and array form. Even the docs' own complete-schema example `"./custom/agents/"` is wrong — it would be rejected.
- [ ] If a plugin author skips `claude plugin validate` and publishes with a folder path, **CC silently drops the agents at runtime** with no error in `--debug` log. CPV pre-empts this with a helpful MAJOR.

---

## 8. LSP Server Checklist

### LSP Configuration

- [ ] `extensionToLanguage` mapping present (critical field)
- [ ] Transport (if present) is "stdio" or "pipe"
- [ ] Numeric fields (startupTimeout, shutdownTimeout, maxRestarts) are numbers
- [ ] `restartOnCrash` (if present) is boolean

### LSP Cross-Source Uniqueness

- [ ] No same server name in BOTH `.lsp.json` (auto-discovered, unwrapped format) AND inline `plugin.json:lspServers` — empirical 2026-04-18 (LSP_WINNER probe) confirmed inline silently wins on collision; the other source's declaration is dropped. CPV emits MAJOR per duplicate.

---

## 9. Script and Code Quality Checklist

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

### Linting Pipeline

- [ ] validate_plugin.py runs repo-wide lint via cpv_lint_engine (read-only, no --fix, no auto-commit)
- [ ] Pre-push hook is a thin wrapper calling scripts/validate_plugin.py (which now owns linting since CPV v2.64.0)
- [ ] Pre-commit hook only checks for sensitive data (no linting)

### General Script Requirements

- [ ] Scripts don't use hardcoded paths
- [ ] Scripts handle errors gracefully
- [ ] Scripts have clear output messages
- [ ] Hook scripts read stdin JSON correctly
- [ ] Hook scripts output valid JSON when required

---

## 10. Pre-Release Final Checklist

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
- [ ] Verify validate_plugin.py passes (it owns repo-wide lint via cpv_lint_engine since v2.64.0)

```bash
uv run python scripts/validate_plugin.py /path/to/plugin --verbose
```

### Version Consistency

- [ ] Version in plugin.json matches CHANGELOG
- [ ] Version in plugin.json matches marketplace entry (if applicable)
- [ ] Git tag matches version (if publishing)

---

## 11. Validation Commands

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
