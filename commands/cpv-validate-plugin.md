---
name: cpv-validate-plugin
description: |
  Comprehensive validation for Claude Code plugins. Validates manifest, hooks, agents,
  skills (84+ rules), MCP servers, scripts, and directory structure. Use when auditing
  plugin quality, preparing for marketplace publishing, or CI/CD integration.
allowed-tools: Read, Bash, Glob, Grep
argument-hint: "<plugin_path> [--verbose] [--json] [--marketplace-only]"
user-invocable: true
---

# /cpv-validate-plugin Command

Validates a complete Claude Code plugin directory with all components.

## Usage

```
/cpv-validate-plugin <plugin_path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `plugin_path` | Yes | Path to the plugin directory to validate |

## Options

| Option | Description |
|--------|-------------|
| `--verbose` | Show all checks including passed |
| `--json` | Output results as JSON |
| `--marketplace-only` | Skip plugin.json requirement (for strict=false distribution) |

## What Gets Validated

### 1. Plugin Manifest (`.claude-plugin/plugin.json`)
- Required field: `name`
- Recommended fields: `version`, `description`
- Name format validation (kebab-case, lowercase)
- Version format (semver)
- agents array validation (must be array of .md file paths)

### 2. Hooks Configuration (`hooks/hooks.json`)
- JSON structure validation
- Valid event names (PreToolUse, PostToolUse, Stop, etc.)
- Matcher pattern validation (regex syntax)
- Command/prompt hook validation
- Script existence and executable checks
- Script linting (shellcheck, ruff, mypy)

### 3. Skills (All skills in `skills/` directory)
- Uses comprehensive validator (84+ rules)
- Nixtla strict mode enabled
- Auto-enables 8+1 Pillars for lang-*/convert-* skills
- Validates frontmatter, structure, content quality

### 4. Agents (All `.md` files in `agents/`)
- YAML frontmatter validation
- Required and known fields
- Name format and description quality
- Tool and model validation

### 5. MCP Servers (`.mcp.json` or inline `mcpServers`)
- Transport type validation (stdio, http, sse)
- Required fields per transport type
- Path and environment variable validation
- Security checks (hardcoded credentials)

### 6. Scripts (All scripts in `scripts/`)
- Shebang validation
- Executable permission check
- Python linting (ruff check)
- Python type checking (mypy)
- Bash linting (shellcheck)

### 7. Directory Structure
- Required: `.claude-plugin/` with `plugin.json`
- Optional: `commands/`, `agents/`, `skills/`, `hooks/`, `scripts/`
- README.md and LICENSE presence

## Examples

### Basic Plugin Validation

```
/cpv-validate-plugin ./my-plugin/
```

### Verbose Output

```
/cpv-validate-plugin ./my-plugin/ --verbose
```

### JSON for CI/CD

```
/cpv-validate-plugin ./my-plugin/ --json
```

### Marketplace-Only Plugin

```
/cpv-validate-plugin ./my-plugin/ --marketplace-only
```

## Output

Returns summary with:
- **Exit Code**: 0 (pass), 1 (critical), 2 (major), 3 (minor)
- **Counts**: Issues by severity level
- **Details**: All validation results with file locations

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | CRITICAL issues (plugin won't work) |
| 2 | MAJOR issues (significant problems) |
| 3 | MINOR issues (may affect UX) |

## Execution

```bash
uv run python scripts/validate_plugin.py "$PLUGIN_PATH" $OPTIONS
```

## Related Commands

- `/cpv-validate-skill` - Single skill validation
- `/cpv-validate-hooks` - Hook-only validation
- `/cpv-validate-agents` - Agent-only validation
- `/cpv-validate-mcp` - MCP server validation
