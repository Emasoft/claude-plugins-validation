# Frontmatter Schema Reference

This document defines the complete schema for SKILL.md frontmatter validation.

## Table of Contents

- [1. Required Fields](#1-required-fields)
- [2. Optional Fields (Claude Code)](#2-optional-fields-claude-code)
- [3. Enterprise Fields](#3-enterprise-fields)
- [4. Field Validation Details](#4-field-validation-details)
- [5. Field Whitelist Modes](#5-field-whitelist-modes)
- [6. Examples](#6-examples)

## Checklist

- [ ] Required fields present (`name`, `description`)
- [ ] Optional fields valid (when present)
- [ ] Enterprise fields use correct types
- [ ] Field whitelist mode honored (no unknown fields in strict mode)
- [ ] Frontmatter parses as valid YAML

---

## 1. Required Fields

These fields are always required in every skill.

### name

| Property | Value |
|----------|-------|
| **Type** | string |
| **Required** | Yes |
| **Max Length** | 64 characters |
| **Format** | kebab-case (lowercase letters, numbers, hyphens) |
| **Constraints** | No leading/trailing hyphens, no consecutive hyphens |
| **OpenSpec** | Must match directory name |

**Example**:
```yaml
name: pdf-processing
```

### description

| Property | Value |
|----------|-------|
| **Type** | string |
| **Required** | Yes |
| **Min Length** | 20 characters (recommended) |
| **Max Length** | 200 tokens (MAJOR; bpe estimate) — TRDD-021250b5; was 1024 chars hard / <200 chars recommended |
| **Quality** | Must describe WHAT and WHEN to use |

**Example**:
```yaml
description: |
  Extracts text and tables from PDF files, fills forms, and merges PDFs.
  Use when working with PDF documents or when user mentions PDFs or forms.
  Trigger with /pdf or when processing document files.
```

---

## 2. Optional Fields (Claude Code)

These fields are supported by Claude Code but not required.

### allowed-tools

| Property | Value |
|----------|-------|
| **Type** | string (CSV) or array |
| **Required** | No (but recommended) |
| **Format** | Comma-separated tool names |
| **Strict Mode** | Must be CSV string (not array), no unscoped Bash |

**Valid Tools**:
- Read, Write, Edit, Bash, Glob, Grep
- WebFetch, WebSearch, Task, TodoWrite
- NotebookEdit, AskUserQuestion, Skill
- MCP tools: `mcp__server__tool`

**Scoped Bash Examples**:
```yaml
# Unscoped (forbidden in strict mode)
allowed-tools: Bash

# Scoped (allowed in strict mode)
allowed-tools: Bash(git:*), Bash(npm:*), Read, Edit
```

### context

| Property | Value |
|----------|-------|
| **Type** | string |
| **Required** | No |
| **Valid Values** | `fork` only |
| **Purpose** | Run skill in forked agent context |

**Example**:
```yaml
context: fork
```

### agent

| Property | Value |
|----------|-------|
| **Type** | string |
| **Required** | No (requires `context: fork`) |
| **Built-in Values** | Explore, Plan, general-purpose |
| **Custom Values** | Any agent defined in .claude/agents/ |

**Example**:
```yaml
context: fork
agent: code-reviewer
```

### user-invocable

| Property | Value |
|----------|-------|
| **Type** | boolean |
| **Required** | No |
| **Default** | false |
| **Purpose** | Allow user to invoke skill via `/skill-name` |

**Example**:
```yaml
user-invocable: true
```

### disable-model-invocation

| Property | Value |
|----------|-------|
| **Type** | boolean |
| **Required** | No |
| **Default** | false |
| **Purpose** | Prevent model from auto-selecting this skill |

**Example**:
```yaml
disable-model-invocation: true
```

### model

| Property | Value |
|----------|-------|
| **Type** | string |
| **Required** | No |
| **Purpose** | Override model for skill execution |

**Example**:
```yaml
model: claude-3-opus
```

### argument-hint

| Property | Value |
|----------|-------|
| **Type** | string |
| **Required** | No |
| **Purpose** | Hint text for skill arguments |

**Example**:
```yaml
argument-hint: "<file_path> [--format json|yaml]"
```

### hooks

| Property | Value |
|----------|-------|
| **Type** | object |
| **Required** | No |
| **Purpose** | Define hooks for skill lifecycle |

**Example**:
```yaml
hooks:
  pre-execution: scripts/validate-input.sh
  post-execution: scripts/format-output.sh
```

---

## 3. Enterprise Fields

Additional fields required for marketplace/enterprise deployment.

### version

| Property | Value |
|----------|-------|
| **Type** | string |
| **Required** | Enterprise only |
| **Format** | Semantic versioning (X.Y.Z) |

**Example**:
```yaml
version: "1.2.0"
```

### author

| Property | Value |
|----------|-------|
| **Type** | string |
| **Required** | Enterprise only |

**Example**:
```yaml
author: "Example Organization"
```

### license

| Property | Value |
|----------|-------|
| **Type** | string |
| **Required** | Enterprise only |
| **Format** | SPDX identifier or custom |

**Example**:
```yaml
license: MIT
```

### metadata

| Property | Value |
|----------|-------|
| **Type** | object |
| **Required** | No |
| **Purpose** | Custom client-specific properties |

**Example**:
```yaml
metadata:
  author: example-org
  version: "1.0"
  category: document-processing
```

### compatibility

| Property | Value |
|----------|-------|
| **Type** | string |
| **Required** | No |
| **Max Length** | 500 characters |
| **Purpose** | Document environment requirements |

**Example**:
```yaml
compatibility: Requires git, docker, jq. Internet access needed.
```

---

## 4. Field Validation Details

### Name Validation Algorithm

```python
def validate_name(name: str, directory_name: str) -> list[str]:
    errors = []

    # Normalize Unicode
    name = unicodedata.normalize("NFKC", name.strip())

    # Type check
    if not isinstance(name, str):
        errors.append("name must be a string")
        return errors

    # Non-empty check
    if not name:
        errors.append("name cannot be empty")
        return errors

    # Length check
    if len(name) > 64:
        errors.append(f"name exceeds 64 characters ({len(name)})")

    # Lowercase check
    if name != name.lower():
        errors.append("name must be lowercase")

    # Character whitelist
    if not all(c.isalnum() or c == "-" for c in name):
        errors.append("name must use only letters, numbers, hyphens")

    # Hyphen rules
    if name.startswith("-") or name.endswith("-"):
        errors.append("name cannot start or end with hyphen")

    if "--" in name:
        errors.append("name cannot contain consecutive hyphens")

    # Reserved words
    if "anthropic" in name.lower() or "claude" in name.lower():
        errors.append("name contains reserved word")

    # Directory match (OpenSpec strict)
    if name != directory_name:
        errors.append(f"name must match directory name ({directory_name})")

    return errors
```

### Description Quality Checks

| Check | Strict Mode | Standard Mode |
|-------|-------------|---------------|
| Non-empty | CRITICAL | CRITICAL |
| Max 200 tokens (TRDD-021250b5; was 1024 chars) | MAJOR | MAJOR |
| "Use when..." phrase | MAJOR | INFO |
| "Trigger with..." phrase | MINOR | INFO |
| No first person | MAJOR | INFO |
| No second person | MAJOR | INFO |

---

## 5. Field Whitelist Modes

### OpenSpec Strict Mode (`--openspec`)

Only these 6 fields are allowed:

```yaml
name: required
description: required
license: optional
allowed-tools: optional
metadata: optional
compatibility: optional
```

All other fields cause MAJOR errors.

### Claude Code Standard Mode

All fields from OpenSpec plus:

```yaml
argument-hint: optional
disable-model-invocation: optional (boolean)
user-invocable: optional (boolean)
model: optional
context: optional (must be "fork")
agent: optional (requires context: fork)
hooks: optional (object)
```

Unknown fields cause INFO warnings.

### Enterprise Mode

All fields from Claude Code plus:

```yaml
version: required (semver)
author: required
license: required
```

---

## 6. Examples

### Minimal Valid Skill (OpenSpec)

```yaml
---
name: my-skill
description: A simple skill that does something useful.
---

# My Skill

Instructions go here.
```

### Claude Code Skill with Forked Agent

```yaml
---
name: code-review-skill
description: |
  Reviews code for quality and best practices.
  Use when user asks for code review or mentions PR review.
  Trigger with /review or when reviewing pull requests.
allowed-tools: Read, Edit, Bash(git:*), Grep
context: fork
agent: code-reviewer
user-invocable: true
argument-hint: "<file_or_directory> [--strict]"
---

# Code Review Skill

## Overview
...
```

### Enterprise Skill with Full Metadata

```yaml
---
name: enterprise-pdf-processor
description: |
  Enterprise-grade PDF processing with audit logging.
  Use when processing sensitive PDF documents.
  Trigger with /pdf-secure or for compliance workflows.
version: "2.1.0"
author: "Example Corp"
license: "Proprietary"
allowed-tools: Read, Write, Bash(poppler:*), Bash(ghostscript:*)
metadata:
  category: document-processing
  compliance: SOC2
  audit-logging: enabled
compatibility: Requires poppler-utils, ghostscript. No internet access needed.
---

# Enterprise PDF Processor

## Overview
...
```

### Strict Mode Compliant Skill (Nixtla)

```yaml
---
name: strict-compliant-skill
description: |
  Processes data files according to enterprise standards.
  Use when transforming CSV, JSON, or XML data files.
  Trigger with /transform-data or when data conversion is needed.
allowed-tools: Read, Write, Bash(jq:*), Bash(xq:*)
version: "1.0.0"
author: "Data Team"
license: MIT
---

# Strict Compliant Skill

## Overview

This skill transforms data between CSV, JSON, and XML formats.

## Prerequisites

- jq installed for JSON processing
- xq installed for XML processing (pip install yq)

## Instructions

1. Identify the input file format
2. Determine the target format
3. Run the appropriate transformation command
4. Validate the output

## Output

- Transformed data file in target format
- Validation report (if enabled)

## Error Handling

- Invalid input format: Exit with error, suggest correct format
- Missing dependencies: Exit with install instructions
- Malformed data: Exit with line number and error details

## Examples

### Convert CSV to JSON

```bash
# Input: data.csv
# Output: data.json
cat data.csv | jq -R -s 'split("\n") | map(split(","))'
```

## Resources

- [jq Manual](https://stedolan.github.io/jq/manual/)
- [yq Documentation](https://mikefarah.gitbook.io/yq/)
```
