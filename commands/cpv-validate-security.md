---
name: cpv-validate-security
description: Scan a plugin for security vulnerabilities
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep
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
- Checks for private path leaks (e.g., absolute home directory paths in committed files)
- Validates tool permission scopes are not overly broad
- Flags world-writable scripts or missing executable permission guards

## Execution

```bash
uv run python scripts/validate_security.py <plugin-path> --report docs_dev/validate_security_$(date +%Y%m%d).md
```

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-hooks` - Hook-only validation
