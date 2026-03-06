---
name: cpv-validate-documentation
description: Check plugin documentation completeness
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep
argument-hint: "<plugin-path>"
agent: plugin-validator
user-invocable: true
---

# /cpv-validate-documentation Command

Validates documentation quality for a Claude Code plugin directory.

## Usage

```
/cpv-validate-documentation <plugin-path>
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `plugin-path` | Yes | Path to the plugin directory to validate |

## What It Does

- Checks README.md exists and contains required sections (Usage, Installation)
- Validates CHANGELOG.md format and version entry consistency
- Verifies inline docs in agent and skill files meet quality standards
- Confirms plugin description in docs matches `plugin.json` manifest
- Detects missing or empty docstrings in script files

## Execution

```bash
uv run python scripts/validate_documentation.py <plugin-path> --report docs_dev/validate_documentation_$(date +%Y%m%d).md
```

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-xref` - Cross-reference validation
- `/cpv-fix-validation` — Fix issues from a validation report
- `/cpv-semantic-validation` — Deep semantic analysis (uses opus)
