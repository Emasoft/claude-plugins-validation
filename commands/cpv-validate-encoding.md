---
name: cpv-validate-encoding
description: Validate file encoding in plugin files
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep
argument-hint: "<plugin-path>"
agent: plugin-validator
user-invocable: true
---

# /cpv-validate-encoding Command

Validates file encoding compliance for all files in a Claude Code plugin.

## Usage

```
/cpv-validate-encoding <plugin-path>
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `plugin-path` | Yes | Path to the plugin directory to validate |

## What It Does

- Scans all text files for UTF-8 encoding compliance
- Detects and flags BOM (Byte Order Mark) markers
- Identifies binary files incorrectly placed among text sources
- Reports encoding violations with file paths and byte offsets
- Flags files with mixed or unexpected line endings (CRLF vs LF)

## Execution

```bash
uv run python scripts/validate_encoding.py <plugin-path> --report docs_dev/validate_encoding_$(date +%Y%m%d).md
```

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-command` - Command file validation
