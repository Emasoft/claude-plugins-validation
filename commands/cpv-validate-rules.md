---
name: cpv-validate-rules
description: |
  Rules file validation for Claude Code plugins. Validates all .md rule files
  in the rules/ directory for correct frontmatter, required structure sections,
  and valid glob patterns.
allowed-tools: Read, Bash(uv:*,python:*), Glob, Grep
argument-hint: "<plugin-path>"
agent: plugin-validator
user-invocable: true
---

# /cpv-validate-rules Command

Validates all rule files in the `rules/` directory of a Claude Code plugin.

## Usage

```
/cpv-validate-rules <plugin-path>
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `plugin-path` | Yes | Path to the plugin directory to validate |

## What It Does

- Validates YAML frontmatter presence and required fields in each rule file
- Checks rule file structure for required sections (title, description, body)
- Verifies glob patterns in `globs:` frontmatter field are syntactically valid
- Detects duplicate rule names across all files in the `rules/` directory
- Confirms rule files follow kebab-case naming convention

## Execution

```bash
uv run python scripts/validate_rules.py <plugin-path>
```

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-documentation` - Documentation quality validation
