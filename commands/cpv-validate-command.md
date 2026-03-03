---
name: cpv-validate-command
description: |
  Command file validation for Claude Code plugins. Validates command .md files
  for correct YAML frontmatter, required fields, and argument hints.
allowed-tools: Read, Bash(uv:*,python:*), Glob, Grep
argument-hint: "<plugin-path>"
agent: plugin-validator
user-invocable: true
---

# /cpv-validate-command Command

Validates all command `.md` files in a Claude Code plugin's `commands/` directory.

## Usage

```
/cpv-validate-command <plugin-path>
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `plugin-path` | Yes | Path to the plugin directory whose commands/ folder to validate |

## What It Does

- Parses YAML frontmatter in every `commands/*.md` file
- Checks for required fields: `name`, `description`, `allowed-tools`, `argument-hint`
- Validates `agent` and `user-invocable` field types and values
- Verifies command name matches the filename (kebab-case consistency)
- Reports missing or malformed fields as CRITICAL, MAJOR, or MINOR issues

## Execution

```bash
uv run python scripts/validate_command.py <plugin-path>
```

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-encoding` - File encoding validation
