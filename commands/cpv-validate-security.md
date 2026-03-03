---
name: cpv-validate-security
description: |
  Security validation for Claude Code plugins. Scans for hardcoded secrets,
  unsafe shell patterns, overly broad tool permissions, and private path leaks
  that could expose sensitive data.
allowed-tools: Read, Bash(uv:*,python:*), Glob, Grep
argument-hint: "<plugin-path>"
agent: plugin-validator
user-invocable: true
---

# /cpv-validate-security Command

Validates security posture of a Claude Code plugin directory.

## Usage

```
/cpv-validate-security <plugin-path>
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `plugin-path` | Yes | Path to the plugin directory to validate |

## What It Does

- Scans for hardcoded secrets, tokens, and API keys in all files
- Detects unsafe shell patterns (e.g., unquoted variables, `eval`, `rm -rf`)
- Checks for private path leaks (e.g., `/Users/<username>/` in committed files)
- Validates tool permission scopes are not overly broad
- Flags world-writable scripts or missing executable permission guards

## Execution

```bash
uv run python scripts/validate_security.py <plugin-path>
```

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-hooks` - Hook-only validation
