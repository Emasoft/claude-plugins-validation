---
name: cpv-validate-enterprise
description: Run enterprise-grade plugin validation
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep
argument-hint: "<plugin-path>"
agent: plugin-validator
user-invocable: true
---

# /cpv-validate-enterprise Command

Validates enterprise compliance requirements for a Claude Code plugin.

## Usage

```
/cpv-validate-enterprise <plugin-path>
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `plugin-path` | Yes | Path to the plugin directory to validate |

## What It Does

- Verifies presence and validity of a LICENSE file
- Checks version fields follow semver format
- Validates plugin name adheres to kebab-case naming conventions
- Checks org-level policy fields in plugin.json
- Reports compliance gaps as CRITICAL, MAJOR, MINOR, or NIT issues

## Execution

```bash
uv run python scripts/validate_enterprise.py <plugin-path> --report docs_dev/validate_enterprise_$(date +%Y%m%d).md
```

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-scoring` - Compute quality score from validation results
