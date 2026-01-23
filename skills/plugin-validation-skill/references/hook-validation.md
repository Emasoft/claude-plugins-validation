# Hook Validation Reference

Complete reference for Claude Code hook configuration and validation.

## Table of Contents

- [1. Hook Configuration File](#1-hook-configuration-file)
- [2. Valid Hook Events](#2-valid-hook-events)
- [3. Matcher Syntax](#3-matcher-syntax)
- [4. Hook Types](#4-hook-types)
- [5. Hook Input/Output Format](#5-hook-inputoutput-format)
- [6. Script Requirements](#6-script-requirements)
- [7. Common Hook Errors](#7-common-hook-errors)
- [8. Validation Checklist](#8-validation-checklist)

---

## 1. Hook Configuration File

### Location

Standard location: `hooks/hooks.json` (auto-loaded by Claude Code)

### Basic Structure

```json
{
  "description": "Optional description of hooks",
  "hooks": {
    "EventName": [
      {
        "matcher": "ToolName|AnotherTool",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/scripts/my-hook.sh",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

### Top-Level Fields

| Field | Required | Description |
|-------|----------|-------------|
| description | No | Human-readable description |
| hooks | Yes | Object mapping event names to hook arrays |

---

## 2. Valid Hook Events

There are **13 valid hook events**:

| Event | Has Matcher | Description |
|-------|-------------|-------------|
| PreToolUse | Yes | Before tool execution (can block/allow/modify) |
| PostToolUse | Yes | After successful tool execution |
| PostToolUseFailure | Yes | After tool execution failure |
| PermissionRequest | Yes | When permission dialog shown |
| UserPromptSubmit | No | When user submits prompt |
| Notification | Yes | When notifications sent |
| Stop | No | When agent attempts to stop |
| SubagentStop | No | When subagent attempts to stop |
| SubagentStart | No | When subagent starts |
| SessionStart | Yes | At session start |
| SessionEnd | No | At session end |
| PreCompact | Yes | Before conversation compaction |
| Setup | Yes | During initial setup |

### Events Without Matchers

These events fire globally and don't support tool-specific matchers:
- UserPromptSubmit
- Stop
- SubagentStop
- SubagentStart
- SessionEnd

### Example for Each Event

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [{"type": "command", "command": "..."}]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "..."}]
      }
    ],
    "Stop": [
      {
        "hooks": [{"type": "command", "command": "..."}]
      }
    ],
    "SessionStart": [
      {
        "hooks": [{"type": "command", "command": "..."}]
      }
    ]
  }
}
```

---

## 3. Matcher Syntax

### Tool Name Matching

Match specific tools by name:

```json
{
  "matcher": "Write"
}
```

### Multiple Tools (OR)

Match any of multiple tools using pipe:

```json
{
  "matcher": "Write|Edit|Read"
}
```

### Regex Patterns

Use regex for complex matching:

```json
{
  "matcher": "^(Write|Edit)$"
}
```

### All Tools (Wildcard)

Match all tools:

```json
{
  "matcher": ".*"
}
```

### Valid Tool Names

Common tools that can be matched:

| Tool | Description |
|------|-------------|
| Read | Read file contents |
| Write | Write/create files |
| Edit | Edit existing files |
| Bash | Execute shell commands |
| Glob | Find files by pattern |
| Grep | Search file contents |
| Task | Launch subagents |
| WebFetch | Fetch web content |
| WebSearch | Search the web |
| AskUserQuestion | Ask user questions |
| NotebookEdit | Edit Jupyter notebooks |
| mcp__* | MCP tool calls |

---

## 4. Hook Types

### Command Type

Executes a shell command:

```json
{
  "type": "command",
  "command": "${CLAUDE_PLUGIN_ROOT}/scripts/my-hook.sh",
  "timeout": 30
}
```

| Field | Required | Description |
|-------|----------|-------------|
| type | Yes | Must be "command" |
| command | Yes | Script path (use ${CLAUDE_PLUGIN_ROOT}) |
| timeout | No | Timeout in seconds (default: 60) |

### Prompt Type

Modifies the system prompt:

```json
{
  "type": "prompt",
  "prompt": "Always follow coding standards when modifying files."
}
```

| Field | Required | Description |
|-------|----------|-------------|
| type | Yes | Must be "prompt" |
| prompt | Yes | Text to add to system prompt |

---

## 5. Hook Input/Output Format

### Input (stdin JSON)

Hook scripts receive JSON data via stdin:

```json
{
  "hook_event": "PreToolUse",
  "tool_name": "Write",
  "tool_input": {
    "file_path": "/path/to/file.txt",
    "content": "file contents..."
  },
  "session_id": "abc123",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

### Reading Input in Scripts

**Bash:**
```bash
#!/bin/bash
INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path')
```

**Python:**
```python
#!/usr/bin/env python3
import json
import sys

data = json.load(sys.stdin)
tool_name = data.get("tool_name")
tool_input = data.get("tool_input", {})
```

### Output Format

Hook scripts can return JSON to stdout:

**Allow action (PreToolUse):**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow"
  }
}
```

**Block action (PreToolUse):**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "File is read-only"
  }
}
```

**Request confirmation:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "ask",
    "permissionDecisionReason": "This action modifies protected files"
  }
}
```

### Exit Codes

| Exit Code | Meaning | Effect |
|-----------|---------|--------|
| 0 | Success | Continue normally, stdout may modify behavior |
| 2 | Blocking error | Stop action, stderr shown to Claude |
| Other | Non-blocking error | Continue, stderr shown in verbose mode |

---

## 6. Script Requirements

### Executable Permission

All hook scripts must be executable:

```bash
chmod +x scripts/*.sh
chmod +x scripts/*.py
```

### Shebang Line

Scripts must have proper shebang:

**Bash:**
```bash
#!/bin/bash
# or
#!/usr/bin/env bash
```

**Python:**
```python
#!/usr/bin/env python3
```

### Path Requirements

- Use `${CLAUDE_PLUGIN_ROOT}` for all plugin-relative paths
- Never hardcode absolute paths
- Scripts should be in plugin's scripts/ directory

### Linting Requirements

**Python scripts should pass:**
```bash
ruff check scripts/*.py
mypy scripts/*.py
```

**Bash scripts should pass:**
```bash
shellcheck scripts/*.sh
```

---

## 7. Common Hook Errors

### Error: Invalid Event Type

**Wrong:**
```json
{
  "hooks": {
    "BeforeToolUse": [...]  // Invalid event name
  }
}
```

**Correct:**
```json
{
  "hooks": {
    "PreToolUse": [...]
  }
}
```

### Error: Script Not Found

**Wrong:**
```json
{
  "command": "/absolute/path/to/script.sh"
}
```

**Correct:**
```json
{
  "command": "${CLAUDE_PLUGIN_ROOT}/scripts/script.sh"
}
```

### Error: Script Not Executable

**Symptom:** Hook doesn't fire or returns permission error

**Fix:**
```bash
chmod +x scripts/my-hook.sh
```

### Error: Matcher on Non-Matcher Event

**Wrong:**
```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "Write",  // Stop doesn't support matchers
        "hooks": [...]
      }
    ]
  }
}
```

**Correct:**
```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [...]  // No matcher field
      }
    ]
  }
}
```

### Error: Missing Type Field

**Wrong:**
```json
{
  "command": "my-script.sh"  // Missing type
}
```

**Correct:**
```json
{
  "type": "command",
  "command": "${CLAUDE_PLUGIN_ROOT}/scripts/my-script.sh"
}
```

### Error: Invalid JSON Output

**Wrong script output:**
```
OK
```

**Correct script output (if blocking):**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Blocked by validation"
  }
}
```

---

## 8. Validation Checklist

### Pre-release Hook Checklist

- [ ] hooks.json is valid JSON
- [ ] All event names are valid (13 allowed)
- [ ] Matchers only used with matcher-supporting events
- [ ] All scripts use `${CLAUDE_PLUGIN_ROOT}` paths
- [ ] All referenced scripts exist
- [ ] All scripts are executable (`chmod +x`)
- [ ] Scripts have proper shebang lines
- [ ] Python scripts pass ruff and mypy
- [ ] Bash scripts pass shellcheck
- [ ] Scripts handle stdin JSON correctly
- [ ] Scripts return valid JSON when needed
- [ ] Timeout values are reasonable

### Validation Command

```bash
uv run python scripts/validate_hook.py /path/to/hooks.json
```

---

## Related References

- [Plugin Structure](plugin-structure.md) - Overall plugin layout
- [Skill Validation](skill-validation.md) - Skill configuration
- [MCP Validation](mcp-validation.md) - MCP server setup
