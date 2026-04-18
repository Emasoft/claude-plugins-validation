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

**Override sources** (additive, NOT replace — verified empirically 2026-04-18):
- `hooks/hooks.json` (default file, ALWAYS auto-loaded)
- `plugin.json` `hooks` field — string path to extra file, array of paths, or inline object
- The default file and any override sources are MERGED at runtime

### CRITICAL: never point `hooks` override at the default file

If `plugin.json` has `"hooks": "./hooks/hooks.json"` (or `"hooks/hooks.json"`), this:
- ✅ Passes `claude plugin validate` silently (CC's validator does not catch it)
- ❌ Triggers runtime ERROR `Duplicate hooks file detected: ./hooks/hooks.json resolves to already-loaded file`
- ❌ **CASCADES to disable the plugin's MCP servers** with `error type: hook-load-failed` (debug log: `Plugin not available for MCP: <plugin>@inline - error type: hook-load-failed`)
- ✅ Hook itself still fires once (CC dedupes), but MCP integration is silently broken

CPV emits MAJOR for this case. Fix: remove the `hooks` field entirely (default file loads automatically), or point at a NON-default path like `./hooks/extra.json`. Empirical evidence: `cpv-hooks-doublefire-test` and `cpv-hooks-nondefault-test` (the latter confirms cascade is SPECIFIC to default-path collision).

### Basic Structure

The top-level `hooks` key must be an object whose keys are event names and values are arrays of hook objects:

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
| hooks | Yes | Object mapping event names to arrays of hook objects |
| disableAllHooks | No | Boolean — temporarily disable all hooks (default: false) |

### JSON Structure Rules

- `hooks` value must be an object (not an array)
- Each key of `hooks` must be a valid event name (string)
- Each event value must be an array of hook group objects
- Each hook group object may have a `matcher` field (string) and must have a `hooks` array
- Each element of `hooks` array is a hook definition with a `type` field

---

## 2. Valid Hook Events

There are **27 valid hook events**:

| Event | Has Matcher | Description |
|-------|-------------|-------------|
| PreToolUse | Yes | Before tool execution (can block/allow/modify) |
| PostToolUse | Yes | After successful tool execution |
| PostToolUseFailure | Yes | After failed tool execution |
| PermissionRequest | Yes | When permission dialog shown |
| Notification | Yes | When notifications sent (command-only event) |
| SessionStart | Yes | At session start |
| SessionEnd | No | At session end |
| SubagentStart | Yes | When subagent starts |
| SubagentStop | Yes | When subagent finishes. Matcher: agent name |
| UserPromptSubmit | No | When user submits prompt |
| Stop | No | When agent attempts to stop |
| PreCompact | Yes | Before conversation compaction (command-only event) |
| Setup | Yes | During initial setup |
| TeammateIdle | No | When a teammate session goes idle |
| TaskCompleted | No | When a task is completed |
| ConfigChange | Yes | When configuration changes |
| WorktreeCreate | No | When a git worktree is created |
| WorktreeRemove | No | When a git worktree is removed |
| InstructionsLoaded | Yes | When CLAUDE.md or .claude/rules/*.md files are loaded (v2.1.69+). Matchers: session_start, nested_traversal, path_glob_match, include, compact |
| PostCompact | Yes | After conversation compaction completes (v2.1.76, command-only). Matchers: manual, auto |
| Elicitation | Yes | When MCP server requests structured input (v2.1.76, command-only). Matcher: MCP server name |
| ElicitationResult | Yes | When user responds to MCP elicitation (v2.1.76, command-only). Matcher: MCP server name |
| StopFailure | Yes | When turn ends due to API error (v2.1.78). Matchers: rate_limit, authentication_failed, billing_error, invalid_request, server_error, max_output_tokens, unknown |
| CwdChanged | No | When working directory changes — e.g. direnv (v2.1.83, command-only) |
| FileChanged | Yes | When watched files change (v2.1.83, command-only). Matcher: filename/basename pattern |
| TaskCreated | No | When a task is created via TaskCreate tool (v2.1.84, command-only) |
| PermissionDenied | Yes | When auto mode classifier denies a tool call (v2.1.89). Matcher: tool name. Output: `{retry: true}` to allow model retry |

### Events With Matchers

These events support tool-specific or context-specific matchers:

- PreToolUse, PostToolUse, PostToolUseFailure, PermissionRequest, PermissionDenied (tool name)
- Notification (notification_type)
- PreCompact, PostCompact (manual, auto)
- SessionStart (startup, resume, clear, compact)
- SessionEnd (clear, resume, logout, prompt_input_exit, other)
- SubagentStart, SubagentStop (agent name)
- ConfigChange (user_settings, project_settings, local_settings, policy_settings, skills)
- StopFailure (rate_limit, authentication_failed, billing_error, invalid_request, server_error, max_output_tokens, unknown)
- InstructionsLoaded (session_start, nested_traversal, path_glob_match, include, compact)
- Elicitation, ElicitationResult (MCP server name)
- FileChanged (filename/basename pattern)
- Setup (legacy)

### Events Without Matchers

These events fire globally and do not support matchers:

- UserPromptSubmit
- Stop
- TeammateIdle
- TaskCompleted
- TaskCreated
- WorktreeCreate
- WorktreeRemove
- CwdChanged

### Command-Only Events

These events only support hook type `"command"` or `"http"` — `"prompt"` and `"agent"` types are not valid for them:

- PreCompact
- PostCompact
- Notification
- ConfigChange
- SessionStart
- SessionEnd
- SubagentStart
- TeammateIdle
- WorktreeCreate
- WorktreeRemove
- Elicitation
- ElicitationResult
- CwdChanged
- FileChanged
- TaskCreated
- InstructionsLoaded

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
    ],
    "TeammateIdle": [
      {
        "hooks": [{"type": "command", "command": "..."}]
      }
    ],
    "WorktreeCreate": [
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

### Common Fields (all hook types)

| Field | Required | Description |
|-------|----------|-------------|
| type | Yes | Hook type: "command", "http", "prompt", or "agent" |
| matcher | No | Tool name or regex to filter which invocations trigger this hook |
| description | No | Human-readable description of what this hook does |
| if | No | Conditional execution using permission rule syntax (v2.1.85). Hook only fires when the condition matches. |

### Command Type

Executes a shell command:

```json
{
  "type": "command",
  "command": "${CLAUDE_PLUGIN_ROOT}/scripts/my-hook.sh",
  "timeout": 30,
  "statusMessage": "Validating file...",
  "async": false
}
```

| Field | Required | Description |
|-------|----------|-------------|
| type | Yes | Must be "command" |
| command | Yes | Script path (use ${CLAUDE_PLUGIN_ROOT}) |
| timeout | No | Timeout in **seconds** (default: 600). Values over 1000 indicate confusion with milliseconds. |
| statusMessage | No | Message shown in UI to indicate progress while the hook runs |
| async | No | If true, the hook runs asynchronously (fire-and-forget). Default: false |
| once | No | If true, runs only once per session (skill/agent hooks only) |

### HTTP Type

POSTs hook input as JSON to a URL (v2.1.63+):

```json
{
  "type": "http",
  "url": "http://localhost:8080/hooks/endpoint",
  "headers": { "Authorization": "Bearer $MY_TOKEN" },
  "allowedEnvVars": ["MY_TOKEN"],
  "timeout": 30,
  "statusMessage": "Checking policy..."
}
```

| Field | Required | Description |
|-------|----------|-------------|
| type | Yes | Must be "http" |
| url | Yes | URL for POST request (http:// or https://) |
| headers | No | Key-value pairs, supports `$VAR_NAME` interpolation |
| allowedEnvVars | No | List of env var names allowed for header interpolation |
| timeout | No | Timeout in seconds (default: 30) |
| statusMessage | No | Message shown in UI during hook execution |
| once | No | If true, runs only once per session (skill/agent hooks only) |

### Prompt Type

Evaluates a prompt with an LLM. Use `$ARGUMENTS` placeholder for hook input JSON:

```json
{
  "type": "prompt",
  "prompt": "Evaluate if Claude should stop: $ARGUMENTS. Check if all tasks are complete.",
  "model": "claude-3-5-haiku-20241022",
  "timeout": 30
}
```

| Field | Required | Description |
|-------|----------|-------------|
| type | Yes | Must be "prompt" |
| prompt | Yes | Prompt text with `$ARGUMENTS` placeholder for hook input JSON |
| model | No | Model to use (defaults to fast model) |
| timeout | No | Timeout in seconds (default: 30) |
| statusMessage | No | Message shown in UI during hook execution |
| once | No | If true, runs only once per session (skill/agent hooks only) |

Note: Prompt type hooks are not valid for command-only events.

### Agent Type

Runs an inline agent (subagent) as a hook:

```json
{
  "type": "agent",
  "prompt": "Review the file that was just written and flag any security issues.",
  "model": "claude-opus-4-6",
  "timeout": 60
}
```

| Field | Required | Description |
|-------|----------|-------------|
| type | Yes | Must be "agent" |
| prompt | Yes | Prompt text with `$ARGUMENTS` placeholder for hook input JSON |
| model | No | Model ID to use for the agent. Defaults to current session model. |
| timeout | No | Timeout in **seconds** (default: 60). Values over 1000 indicate confusion with milliseconds. |
| statusMessage | No | Message shown in UI during hook execution |
| once | No | If true, runs only once per session (skill/agent hooks only) |

Note: Agent type hooks are not valid for command-only events.

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

### Error: Timeout Value in Milliseconds

**Wrong:**
```json
{
  "type": "command",
  "command": "...",
  "timeout": 30000  // This is 30000 seconds — likely meant 30 seconds
}
```

**Correct:**
```json
{
  "type": "command",
  "command": "...",
  "timeout": 30  // 30 seconds
}
```

Timeout is in **seconds**. A value over 1000 is almost certainly a mistake (would mean over 16 minutes).

### Error: Non-Command Type on Command-Only Event

**Wrong:**
```json
{
  "hooks": {
    "PreCompact": [
      {
        "hooks": [{"type": "prompt", "prompt": "..."}]  // PreCompact only allows type "command"
      }
    ]
  }
}
```

**Correct:**
```json
{
  "hooks": {
    "PreCompact": [
      {
        "hooks": [{"type": "command", "command": "..."}]
      }
    ]
  }
}
```

---

## 8. Validation Checklist

### Pre-release Hook Checklist

- [ ] hooks.json is valid JSON
- [ ] Top-level `hooks` key is an object (not an array)
- [ ] All event names are valid (18 allowed)
- [ ] Matchers only used with matcher-supporting events
- [ ] Command-only events (PreCompact, Notification) use only type "command"
- [ ] All scripts use `${CLAUDE_PLUGIN_ROOT}` paths
- [ ] All referenced scripts exist
- [ ] All scripts are executable (`chmod +x`)
- [ ] Scripts have proper shebang lines
- [ ] Python scripts pass ruff and mypy
- [ ] Bash scripts pass shellcheck
- [ ] Scripts handle stdin JSON correctly
- [ ] Scripts return valid JSON when needed
- [ ] Timeout values are in seconds and reasonable (not over 1000)
- [ ] `statusMessage` used where helpful for long-running command hooks
- [ ] `async: true` only used where fire-and-forget behavior is intended

### Validation Command

```bash
uv run python scripts/validate_hook.py /path/to/hooks.json
```

---

## Related References

- [Plugin Structure](plugin-structure.md) - Overall plugin layout
- [Skill Validation](skill-validation.md) - Skill configuration
- [MCP Validation](mcp-validation.md) - MCP server setup
