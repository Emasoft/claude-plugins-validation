---
name: cpv-validate-xref
description: Cross-reference validate plugin components
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep
argument-hint: "<plugin-path>"
agent: plugin-validator
user-invocable: true
---

# /cpv-validate-xref Command

Validates all internal cross-references within a Claude Code plugin directory.

## Usage

```
/cpv-validate-xref <plugin-path>
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `plugin-path` | Yes | Path to the plugin directory to validate |

## What It Does

- Checks agent `Task()` call references resolve to existing agent files
- Validates `subagent_type` fields match declared agent names
- Verifies version consistency across `plugin.json`, README, and CHANGELOG
- Confirms command frontmatter `agent:` fields reference existing agent files
- Validates skill refs and hook script refs point to existing files

## Execution

```bash
uv run python scripts/validate_xref.py <plugin-path>
```

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-agents` - Agent-only validation
- `/cpv-validate-hooks` - Hook-only validation
