# Hook Configuration — Validation Issues and Fixes

## Table of Contents

- [1. hooks.json Structure Issues](#1-hooksjson-structure-issues)
- [2. Event Type Issues](#2-event-type-issues)
- [3. Matcher Issues](#3-matcher-issues)
- [4. Hook Type Issues](#4-hook-type-issues)
- [5. Command Hook Issues](#5-command-hook-issues)
- [6. Prompt Hook Issues](#6-prompt-hook-issues)
- [7. Agent Hook Issues](#7-agent-hook-issues)
- [8. Timeout Issues](#8-timeout-issues)
- [9. Script Path Issues](#9-script-path-issues)
- [10. Script Linting Issues](#10-script-linting-issues)
- [11. Field Validation Issues](#11-field-validation-issues)
- [12. Informational Notices](#12-informational-notices)
- [13. Runtime-Dep, Invocation & Path-Traversal Issues (TRDD-0028dd34)](#13-runtime-dep--invocation-issues-trdd-0028dd34)

## Checklist

- [ ] Identify the hook event and type (command/http/prompt/agent)
- [ ] Match the finding to one of the 13 sections below
- [ ] Read the hook's script (if command-type) in full before editing
- [ ] Apply fix — preserve effective hook behavior; never silence with `|| true` / `2>/dev/null`
- [ ] For runtime-dep findings (§13): change invocation (PEP 723 / SessionStart venv), never strip imports
- [ ] Re-validate the plugin

---

Comprehensive remediation guide for all issues detected by `validate_hook.py`.

## Valid Hook Events (all 18)

| Event | Supports Matchers | Supports Prompt/Agent Hooks |
|-------|-------------------|-----------------------------|
| `PreToolUse` | Yes | Yes |
| `PostToolUse` | Yes | Yes |
| `PostToolUseFailure` | Yes | Yes |
| `PermissionRequest` | Yes | Yes |
| `Notification` | Yes | No (command only) |
| `PreCompact` | Yes | No (command only) |
| `Setup` | Yes | Yes |
| `SessionStart` | Yes | No (command only) |
| `SessionEnd` | Yes | No (command only) |
| `SubagentStart` | Yes | No (command only) |
| `SubagentStop` | Yes | Yes |
| `ConfigChange` | Yes | No (command only) |
| `UserPromptSubmit` | No (ignored) | Yes |
| `Stop` | No (ignored) | Yes |
| `TeammateIdle` | No (ignored) | No (command only) |
| `TaskCompleted` | No (ignored) | Yes |
| `WorktreeCreate` | No (ignored) | No (command only) |
| `WorktreeRemove` | No (ignored) | No (command only) |
| `InstructionsLoaded` | No (ignored) | No (command only) |

## Timeout Units

**IMPORTANT**: Claude Code hook timeouts are specified in **SECONDS** (verified against https://code.claude.com/docs/en/hooks.md "Common fields" table: "Seconds before canceling").

| Value | Actual Duration |
|-------|----------------|
| `5` | 5 seconds |
| `30` | 30 seconds (default for prompt/http hooks) |
| `60` | 60 seconds (default for agent hooks) |
| `600` | 10 minutes (default for command hooks) |
| `10000` | **≥ 10000s → likely a bug**: the validator warns that a value this large looks like milliseconds — fix by dividing by 1000 |

Prior editions of this document INCORRECTLY claimed hooks used milliseconds — that was a documentation bug. The validator's `validate_hook.py` has always compared against second-unit thresholds (600 default, >10000 warning), and the official hooks.md spec has always said seconds.

## 1. hooks.json Structure Issues

### CRITICAL: Hook file not found

**Error message**: `Hook file not found: {hook_path}`
**Severity**: CRITICAL
**Root cause**: The specified hooks.json file does not exist at the given path.
**Fix**:
1. Verify the path is correct: `ls -la path/to/hooks.json`
2. Create the file if it does not exist:
   ```json
   {
     "hooks": {}
   }
   ```
3. For plugins, the file must be at `<plugin_root>/hooks.json`
4. For project hooks, the file must be at `.claude/hooks.json`
5. For user hooks, the file must be at `~/.claude/hooks.json`

---

### CRITICAL: Invalid JSON syntax

**Error message**: `Invalid JSON: {error_message} at line {line_number}`
**Severity**: CRITICAL
**Root cause**: The file contains malformed JSON that cannot be parsed.
**Fix**:
1. Open the file and check the line number indicated in the error
2. Common issues:
   - Trailing commas after the last item in an array or object
   - Missing commas between items
   - Unquoted keys (JSON requires double-quoted keys)
   - Single quotes instead of double quotes
   - Missing closing braces `}` or brackets `]`
3. Use a JSON validator: `python3 -m json.tool hooks.json`
4. Example of valid JSON:
   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "matcher": "Bash",
           "hooks": [
             {
               "type": "command",
               "command": "echo hello"
             }
           ]
         }
       ]
     }
   }
   ```

---

### CRITICAL: Root must be a JSON object

**Error message**: `Root must be a JSON object`
**Severity**: CRITICAL
**Root cause**: The top-level element of hooks.json is not a JSON object (e.g., it is an array, string, or number).
**Fix**:
1. Ensure the file starts with `{` and ends with `}`
2. The root must be an object containing at least a `"hooks"` key:
   ```json
   {
     "hooks": {}
   }
   ```
3. **Wrong** (array at root):
   ```json
   [{"type": "command", "command": "echo hi"}]
   ```

---

### CRITICAL: Missing required 'hooks' object

**Error message**: `Missing required 'hooks' object`
**Severity**: CRITICAL
**Root cause**: The top-level JSON object does not contain the `"hooks"` key.
**Fix**:
1. Add the `"hooks"` key to the root object:
   ```json
   {
     "description": "My plugin hooks",
     "hooks": {
       "PreToolUse": []
     }
   }
   ```
2. **Wrong** (hooks defined at root level without wrapper):
   ```json
   {
     "PreToolUse": [...]
   }
   ```
3. **Correct**:
   ```json
   {
     "hooks": {
       "PreToolUse": [...]
     }
   }
   ```

---

### CRITICAL: 'hooks' must be an object

**Error message**: `'hooks' must be an object, got {type_name}`
**Severity**: CRITICAL
**Root cause**: The `"hooks"` value is not a JSON object (e.g., it is an array or string).
**Fix**:
1. The `"hooks"` value must be a JSON object mapping event names to arrays:
   ```json
   {
     "hooks": {
       "PreToolUse": [],
       "Stop": []
     }
   }
   ```
2. **Wrong** (hooks as array):
   ```json
   {
     "hooks": [{"type": "command", "command": "echo hi"}]
   }
   ```

---

### MAJOR: 'description' must be a string

**Error message**: `'description' must be a string, got {type_name}`
**Severity**: MAJOR
**Root cause**: The optional `"description"` field at the top level is not a string.
**Fix**:
1. Ensure `description` is a quoted string:
   ```json
   {
     "description": "Hooks for my CI enforcement plugin",
     "hooks": {}
   }
   ```
2. **Wrong**:
   ```json
   {
     "description": 42,
     "hooks": {}
   }
   ```

---

## 2. Event Type Issues

### CRITICAL: Unknown hook event

**Error message**: `Unknown hook event: '{event_name}'. Valid events: [list]`
**Severity**: CRITICAL
**Root cause**: An event name in the `"hooks"` object is not recognized by Claude Code.
**Fix**:
1. Check the event name for typos (names are case-sensitive)
2. Valid event names are (all 26):
   - `PreToolUse`
   - `PostToolUse`
   - `PostToolUseFailure`
   - `PermissionRequest`
   - `UserPromptSubmit`
   - `Notification`
   - `Stop`
   - `StopFailure` (v2.1.78)
   - `SubagentStop`
   - `SubagentStart`
   - `SessionStart`
   - `SessionEnd`
   - `PreCompact`
   - `PostCompact` (v2.1.76)
   - `Setup`
   - `TeammateIdle`
   - `TaskCompleted`
   - `ConfigChange`
   - `WorktreeCreate`
   - `WorktreeRemove`
   - `InstructionsLoaded`
   - `Elicitation` (v2.1.76)
   - `ElicitationResult` (v2.1.76)
   - `CwdChanged` (v2.1.83)
   - `FileChanged` (v2.1.83)
   - `TaskCreated` (v2.1.84)
3. **Wrong**: `"preToolUse"`, `"pre_tool_use"`, `"PreTooluse"`
4. **Correct**: `"PreToolUse"`
5. **New: Fuzzy matching** — the validator now suggests corrections for misspelled events. If you see `did you mean 'PreToolUse'?` in the error message, it detected a close match. Common typos:
   - `preToolUse` → `PreToolUse` (wrong case)
   - `PreTooluse` → `PreToolUse` (wrong capitalization)
   - `PostTooluseFailure` → `PostToolUseFailure` (missing uppercase)

---

### CRITICAL: Event config must be an array

**Error message**: `Event config for '{event_name}' must be an array, got {type_name}`
**Severity**: CRITICAL
**Root cause**: The value for an event key is not an array of matcher blocks.
**Fix**:
1. Each event's value must be an array of matcher block objects:
   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "matcher": "Bash",
           "hooks": [
             { "type": "command", "command": "echo intercepted" }
           ]
         }
       ]
     }
   }
   ```
2. **Wrong** (object instead of array):
   ```json
   {
     "hooks": {
       "PreToolUse": {
         "matcher": "Bash",
         "hooks": [...]
       }
     }
   }
   ```
3. **Wrong** (hook directly without matcher block wrapper):
   ```json
   {
     "hooks": {
       "PreToolUse": { "type": "command", "command": "echo hi" }
     }
   }
   ```

---

## 3. Matcher Issues

### CRITICAL: Matcher block must be an object

**Error message**: `Matcher block must be an object, got {type_name}`
**Severity**: CRITICAL
**Root cause**: An item in the event's array is not a JSON object (e.g., it is a string or number).
**Fix**:
1. Each item in the event array must be a matcher block object with `"hooks"` (required) and `"matcher"` (optional):
   ```json
   [
     {
       "matcher": "Bash",
       "hooks": [
         { "type": "command", "command": "echo hello" }
       ]
     }
   ]
   ```
2. **Wrong** (string instead of object):
   ```json
   ["Bash"]
   ```

---

### CRITICAL: Matcher block missing required 'hooks' array

**Error message**: `Matcher block missing required 'hooks' array`
**Severity**: CRITICAL
**Root cause**: A matcher block object does not contain the `"hooks"` key.
**Fix**:
1. Every matcher block must have a `"hooks"` array:
   ```json
   {
     "matcher": "Bash",
     "hooks": [
       { "type": "command", "command": "echo intercepted" }
     ]
   }
   ```
2. **Wrong** (missing hooks key):
   ```json
   {
     "matcher": "Bash",
     "type": "command",
     "command": "echo hi"
   }
   ```

---

### CRITICAL: 'hooks' (in matcher block) must be an array

**Error message**: `'hooks' must be an array, got {type_name}`
**Severity**: CRITICAL
**Root cause**: The `"hooks"` field inside a matcher block is not an array.
**Fix**:
1. The `"hooks"` value must be an array of hook definition objects:
   ```json
   {
     "matcher": "Bash",
     "hooks": [
       { "type": "command", "command": "echo check" }
     ]
   }
   ```
2. **Wrong** (single object instead of array):
   ```json
   {
     "matcher": "Bash",
     "hooks": { "type": "command", "command": "echo check" }
   }
   ```

---

### MAJOR: Matcher must be a string

**Error message**: `Matcher must be a string, got {type_name}`
**Severity**: MAJOR
**Root cause**: The `"matcher"` field is not a string type (e.g., a number or boolean).
**Fix**:
1. The matcher must be a string (can be a regex pattern):
   ```json
   { "matcher": "Bash|Read|Write" }
   ```
2. **Wrong**:
   ```json
   { "matcher": true }
   { "matcher": 42 }
   { "matcher": ["Bash", "Read"] }
   ```

---

### MAJOR: Invalid regex in matcher

**Error message**: `Invalid regex in matcher '{matcher}': {error}`
**Severity**: MAJOR
**Root cause**: The matcher string contains invalid regular expression syntax.
**Fix**:
1. Matchers are interpreted as regular expressions. Fix the syntax:
   - Unmatched parentheses: `(Bash|Read` -> `(Bash|Read)`
   - Unescaped special chars: `Bash[` -> `Bash\[` or `Bash`
   - Bad quantifier: `Bash+*` -> `Bash+`
2. Test your regex: `python3 -c "import re; re.compile('your_pattern')"`
3. Common valid patterns:
   - Match one tool: `"Bash"`
   - Match multiple tools: `"Bash|Read|Write"`
   - Match MCP tools: `"mcp__.*"`
   - Match all: `".*"` or `""` or omit the field

---

### MINOR: 'hooks' array is empty

**Error message**: `'hooks' array is empty`
**Severity**: MINOR
**Root cause**: A matcher block has an empty hooks array, so no hooks will fire.
**Fix**:
1. Add at least one hook definition, or remove the entire matcher block:
   ```json
   {
     "matcher": "Bash",
     "hooks": [
       { "type": "command", "command": "echo hook fired" }
     ]
   }
   ```
2. If you intend to have no hooks, remove the matcher block entirely.

---

### INFO: Matcher provided for event that ignores matchers

**Error message**: `Matcher '{matcher}' provided for {event_name} (matchers are ignored for this event)`
**Severity**: INFO
**Root cause**: A matcher is specified for an event that does not support matchers. The matcher will be silently ignored.
**Fix**:
1. Events that do NOT support matchers: `UserPromptSubmit`, `Stop`, `TeammateIdle`, `TaskCompleted`, `WorktreeCreate`, `WorktreeRemove`, `InstructionsLoaded`
2. Remove the `"matcher"` field or set it to `""` for clarity:
   ```json
   {
     "hooks": [
       { "type": "command", "command": "echo stopped" }
     ]
   }
   ```
3. This is informational only and will not break functionality.

---

### INFO: Non-standard tool name in matcher

**Error message**: `Matcher '{part}' is not a common tool name (may be custom or MCP tool)`
**Severity**: INFO
**Root cause**: A matcher for `PreToolUse`, `PostToolUse`, or `PermissionRequest` contains a tool name that is not in the common built-in set.
**Fix**:
1. Common built-in tool names: `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Task`, `WebFetch`, `WebSearch`, `NotebookEdit`
2. MCP tool names start with `mcp__` (e.g., `mcp__slack__send_message`)
3. This is informational — custom or MCP tool names are valid. Verify the name matches the tool you intend to intercept.

---

### INFO: Unknown Notification matcher type

**Error message**: `Notification matcher '<part>' is not a common type — known types: auth_success, elicitation_dialog, idle_prompt, permission_prompt`
**Severity**: INFO
**Root cause**: A matcher for a `Notification` event uses a value that is not in the known set of notification types.
**Fix**:
1. Known Notification matcher types:
   - `permission_prompt` — permission request notification
   - `idle_prompt` — idle timeout prompt
   - `auth_success` — authentication success
   - `elicitation_dialog` — user dialog prompt
2. Verify your matcher string matches one of these types, or use `"*"` to match all notifications.
3. Custom notification types may be valid — this is informational only.

---

### INFO: Unknown SessionStart matcher source

**Error message**: `SessionStart matcher '<part>' is not a known source — known values: clear, compact, resume, startup`
**Severity**: INFO
**Root cause**: A matcher for a `SessionStart` event uses a value that is not in the known set of session start sources.
**Fix**:
1. Known SessionStart sources:
   - `startup` — fresh session start
   - `resume` — resuming a previous session
   - `clear` — session cleared
   - `compact` — session compacted
2. Use the correct source name, or omit the matcher to match all SessionStart events.

---

### INFO: Unknown PreCompact matcher trigger

**Error message**: `PreCompact matcher '<part>' is not a known trigger — known values: auto, manual`
**Severity**: INFO
**Root cause**: A matcher for a `PreCompact` event uses a value that is not in the known set of compact triggers.
**Fix**:
1. Known PreCompact triggers:
   - `auto` — automatic compaction
   - `manual` — user-initiated compaction
2. Use the correct trigger name, or omit the matcher to match all PreCompact events.

---

## 4. Hook Type Issues

### CRITICAL: Hook must be an object

**Error message**: `Hook must be an object, got {type_name}`
**Severity**: CRITICAL
**Root cause**: A hook definition inside a `"hooks"` array is not a JSON object.
**Fix**:
1. Each hook must be an object with at least `"type"` and the corresponding field:
   ```json
   { "type": "command", "command": "echo hello" }
   ```
2. **Wrong** (string instead of object):
   ```json
   "echo hello"
   ```

---

### CRITICAL: Hook missing required 'type' field

**Error message**: `Hook missing required 'type' field`
**Severity**: CRITICAL
**Root cause**: A hook definition does not specify its type.
**Fix**:
1. Add the `"type"` field. Valid types are: `"command"`, `"prompt"`, `"agent"`
   ```json
   { "type": "command", "command": "bash script.sh" }
   ```
2. **Wrong**:
   ```json
   { "command": "echo hello" }
   ```

---

### CRITICAL: Invalid hook type

**Error message**: `Invalid hook type: '{hook_type}'. Valid types: ['agent', 'command', 'prompt']`
**Severity**: CRITICAL
**Root cause**: The `"type"` field value is not one of the three valid types.
**Fix**:
1. Use one of: `"command"`, `"prompt"`, `"agent"`
2. **Wrong**: `"type": "shell"`, `"type": "script"`, `"type": "cmd"`
3. **Correct**: `"type": "command"`

---

### CRITICAL: Event only supports command hooks

**Error message**: `Event '{event_name}' only supports type 'command' hooks, not '{hook_type}'. Prompt and agent hooks are not supported for this event.`
**Severity**: CRITICAL
**Root cause**: A `prompt` or `agent` hook type is used with an event that only supports `command` hooks.
**Fix**:
1. The following events only support `type: "command"`:
   - `ConfigChange`
   - `Notification`
   - `PreCompact`
   - `SessionEnd`
   - `SessionStart`
   - `SubagentStart`
   - `TeammateIdle`
   - `WorktreeCreate`
   - `WorktreeRemove`
   - `InstructionsLoaded`
2. Change the hook type to `"command"`:
   ```json
   { "type": "command", "command": "your-script.sh" }
   ```
3. If you need AI-driven logic, have your command script perform the logic and return JSON output.

---

### MAJOR: 'async: true' only supported on command hooks

**Error message**: `'async: true' is only supported on type 'command' hooks, not '{hook_type}'. Prompt and agent hooks cannot run asynchronously.`
**Severity**: MAJOR
**Root cause**: The `"async": true` field is set on a prompt or agent hook, which does not support asynchronous execution.
**Fix**:
1. Remove `"async": true` from prompt/agent hooks:
   ```json
   { "type": "prompt", "prompt": "Check this output" }
   ```
2. Only command hooks support `"async": true`:
   ```json
   { "type": "command", "command": "log-event.sh", "async": true }
   ```

---

## 5. Command Hook Issues

### CRITICAL: Command hook missing required 'command' field

**Error message**: `Command hook missing required 'command' field`
**Severity**: CRITICAL
**Root cause**: A hook with `"type": "command"` does not have a `"command"` field.
**Fix**:
1. Add the `"command"` field:
   ```json
   { "type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/check.sh" }
   ```

---

### CRITICAL: 'command' must be a string

**Error message**: `'command' must be a string, got {type_name}`
**Severity**: CRITICAL
**Root cause**: The `"command"` field is not a string (e.g., it is an array or number).
**Fix**:
1. The command must be a single string (the full shell command):
   ```json
   { "type": "command", "command": "node ${CLAUDE_PLUGIN_ROOT}/scripts/check.mjs" }
   ```
2. **Wrong** (array of arguments):
   ```json
   { "type": "command", "command": ["node", "check.mjs"] }
   ```

---

### CRITICAL: 'command' cannot be empty

**Error message**: `'command' cannot be empty`
**Severity**: CRITICAL
**Root cause**: The `"command"` field is an empty or whitespace-only string.
**Fix**:
1. Provide a valid shell command:
   ```json
   { "type": "command", "command": "echo 'hook executed'" }
   ```

---

### MAJOR: Command uses absolute path

**Error message**: `Command uses absolute path '{path}' — use ${CLAUDE_PLUGIN_ROOT} or ${CLAUDE_PROJECT_DIR} for portability`
**Severity**: MAJOR
**Root cause**: The command starts with a hardcoded absolute path (e.g., `/usr/local/bin/script.sh`), making it non-portable across systems.
**Fix**:
1. Replace absolute paths with environment variables:
   - `${CLAUDE_PLUGIN_ROOT}` — root directory of the plugin (plugin hooks only)
   - `${CLAUDE_PROJECT_DIR}` — current project directory
2. **Wrong**:
   ```json
   { "command": "/home/user/scripts/check.sh" }
   ```
3. **Correct**:
   ```json
   { "command": "${CLAUDE_PLUGIN_ROOT}/scripts/check.sh" }
   ```
4. Or use commands available on `$PATH`:
   ```json
   { "command": "node ${CLAUDE_PLUGIN_ROOT}/scripts/check.mjs" }
   ```

---

### WARNING: Hook command uses package executor for remote package

**Error message**: `Hook command uses {executor} to execute remote package '{pkg_name}' — this downloads and runs code from a registry. Verify the package is trusted and consider pinning a version.`
**Severity**: WARNING
**Root cause**: The command uses a package executor (`npx`, `bunx`, `uvx`, `pipx`, `pnpx`) to download and execute a remote package, which is a supply-chain security risk.
**Fix**:
1. Verify the package is trusted and well-known
2. Pin a specific version to avoid unexpected changes:
   ```json
   { "command": "npx shellcheck@0.9.0 ${CLAUDE_PLUGIN_ROOT}/scripts/hook.sh" }
   ```
3. **Better**: Install the package locally and reference it directly:
   ```json
   { "command": "node ${CLAUDE_PLUGIN_ROOT}/node_modules/.bin/eslint ." }
   ```
4. The detected executors are: `npx`, `bunx`, `uvx`, `pipx`, `pnpx`

---

### MAJOR: CLAUDE_ENV_FILE used in wrong event

**Error message**: `CLAUDE_ENV_FILE is only available in SessionStart and Setup hooks`
**Severity**: MAJOR
**Root cause**: The command references `CLAUDE_ENV_FILE` but the hook is not for a `SessionStart` or `Setup` event.
**Fix**:
1. The `CLAUDE_ENV_FILE` environment variable is only provided to hooks for:
   - `SessionStart`
   - `Setup`
2. Move your environment-writing logic to a `SessionStart` or `Setup` hook:
   ```json
   {
     "hooks": {
       "SessionStart": [
         {
           "hooks": [
             { "type": "command", "command": "echo 'MY_VAR=value' >> $CLAUDE_ENV_FILE" }
           ]
         }
       ]
     }
   }
   ```
3. For other events, use a different mechanism to set environment state.

---

### MAJOR: Script not found (absolute path)

**Error message**: `Script not found: {script_path}`
**Severity**: MAJOR
**Root cause**: The command references a script file that does not exist at the resolved path.
**Fix**:
1. Verify the script exists at the expected location
2. Check the file extension and path are correct
3. If using `${CLAUDE_PLUGIN_ROOT}`, pass `--plugin-root` when validating:
   ```bash
   uv run python scripts/validate_hook.py hooks.json --plugin-root /path/to/plugin
   ```
4. Ensure the script has been committed and is part of the plugin/project

---

### MINOR: Script without interpreter prefix

**Error message**: `Command runs '<script>' without an explicit interpreter — add one (e.g. python3, node, bash) for cross-platform reliability`
**Severity**: MINOR
**Root cause**: The hook command's first token is a script file (`.py`, `.js`, `.ts`, `.sh`, `.rb`, `.pl`) but no interpreter is explicitly specified. Without an interpreter prefix, the script relies on the shebang line or OS file association, which may fail cross-platform.
**Fix**:
1. Add an explicit interpreter before the script:
   ```json
   { "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check.py" }
   ```
   Not:
   ```json
   { "command": "${CLAUDE_PLUGIN_ROOT}/scripts/check.py" }
   ```
2. Common interpreter prefixes:
   | Extension | Interpreter |
   |-----------|-------------|
   | `.py` | `python3` |
   | `.js`, `.mjs` | `node` |
   | `.ts` | `bun` or `npx tsx` |
   | `.sh` | `bash` |
   | `.rb` | `ruby` |
   | `.pl` | `perl` |

---

### MINOR: Command starts with tilde path

**Error message**: `Command starts with '~/' — tilde expansion may not work in hook commands. Use $HOME/ or ${CLAUDE_PLUGIN_ROOT}/ instead.`
**Severity**: MINOR
**Root cause**: The command starts with `~/` (tilde expansion). Tilde expansion is a shell feature that may not work in all execution contexts. Hook commands may be executed without a full shell, causing `~/` to be interpreted literally.
**Fix**:
1. Replace `~/` with `$HOME/`:
   ```json
   { "command": "$HOME/scripts/run.sh" }
   ```
2. Or better, use `${CLAUDE_PLUGIN_ROOT}/` for plugin-relative paths:
   ```json
   { "command": "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" }
   ```

---

### MINOR: Bare 'cd' without chained command

**Error message**: `'cd' alone has no effect — each hook runs in a fresh shell. Combine with your command: 'cd /dir && your-command'`
**Severity**: MINOR
**Root cause**: The command is just `cd <dir>` without any follow-up command. Each hook execution runs in a fresh shell process, so `cd` alone changes the directory of a shell that immediately exits — it has no lasting effect.
**Fix**:
1. Chain `cd` with your actual command using `&&`:
   ```json
   { "command": "cd ${CLAUDE_PLUGIN_ROOT} && python3 scripts/check.py" }
   ```
2. Or use the full path directly:
   ```json
   { "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check.py" }
   ```

---

### MINOR: Command contains backslash paths

**Error message**: `Command contains backslash paths — use forward slashes for cross-platform compatibility`
**Severity**: MINOR
**Root cause**: The command contains backslash path separators (`\`), which are Windows-specific. For cross-platform compatibility, always use forward slashes (`/`), which work on all operating systems including Windows.
**Fix**:
1. Replace all backslashes with forward slashes:
   ```json
   { "command": "${CLAUDE_PLUGIN_ROOT}/scripts/check.py" }
   ```
   Not:
   ```json
   { "command": "${CLAUDE_PLUGIN_ROOT}\\scripts\\check.py" }
   ```
2. Forward slashes work on Windows, macOS, and Linux.

---

### MINOR: Relative path without CLAUDE_PLUGIN_ROOT

**Error message**: `Command uses relative path '<path>' without ${CLAUDE_PLUGIN_ROOT} — hook working directory is not guaranteed. Use ${CLAUDE_PLUGIN_ROOT}/... for reliability.`
**Severity**: MINOR
**Root cause**: The command starts with a relative path like `./scripts/run.sh` but does not use `${CLAUDE_PLUGIN_ROOT}`. The working directory when a hook executes is NOT guaranteed to be the plugin root, so relative paths may fail to resolve.
**Fix**:
1. Replace relative paths with `${CLAUDE_PLUGIN_ROOT}`:
   ```json
   { "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" }
   ```
   Not:
   ```json
   { "command": "bash ./scripts/run.sh" }
   ```
2. `${CLAUDE_PLUGIN_ROOT}` is always set to the plugin's installation directory, regardless of the current working directory.

---

## 6. Prompt Hook Issues

### CRITICAL: Prompt hook missing required 'prompt' field

**Error message**: `Prompt hook missing required 'prompt' field`
**Severity**: CRITICAL
**Root cause**: A hook with `"type": "prompt"` does not have a `"prompt"` field.
**Fix**:
1. Add the `"prompt"` field with instructions for the AI:
   ```json
   {
     "type": "prompt",
     "prompt": "Review the tool arguments and check for security issues. Return JSON with 'decision' field."
   }
   ```

---

### CRITICAL: 'prompt' must be a string

**Error message**: `'prompt' must be a string, got {type_name}`
**Severity**: CRITICAL
**Root cause**: The `"prompt"` field is not a string.
**Fix**:
1. Ensure the prompt is a quoted string:
   ```json
   { "type": "prompt", "prompt": "Analyze this code for security issues." }
   ```

---

### CRITICAL: 'prompt' cannot be empty

**Error message**: `'prompt' cannot be empty`
**Severity**: CRITICAL
**Root cause**: The `"prompt"` field is an empty or whitespace-only string.
**Fix**:
1. Provide meaningful instructions:
   ```json
   { "type": "prompt", "prompt": "Check if the Bash command is safe to run. Output JSON with 'decision': 'allow' or 'deny'." }
   ```

---

### MAJOR: Prompt hook 'model' must be a non-empty string

**Error message**: `Prompt hook 'model' must be a non-empty string`
**Severity**: MAJOR
**Root cause**: The optional `"model"` field is empty or not a string.
**Fix**:
1. Provide a valid model identifier or remove the field:
   ```json
   {
     "type": "prompt",
     "prompt": "Review this for safety",
     "model": "claude-sonnet-4-20250514"
   }
   ```
2. If you do not need a specific model, omit the `"model"` field entirely.

---

### INFO: Prompt hooks may not be effective for certain events

**Error message**: `Prompt hooks for {event_name} may not be as effective as command hooks`
**Severity**: INFO
**Root cause**: A prompt hook is used for an event where command hooks are typically more effective.
**Fix**:
1. Prompt hooks are most useful for: `Stop`, `SubagentStop`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`
2. For other events, consider using a command hook that runs a script instead.
3. This is informational only — prompt hooks will still work for events that support them.

---

### INFO: Prompt doesn't contain $ARGUMENTS placeholder

**Error message**: `Prompt doesn't contain $ARGUMENTS placeholder (input JSON will be appended automatically)`
**Severity**: INFO
**Root cause**: The prompt text does not include the `$ARGUMENTS` placeholder.
**Fix**:
1. Claude Code appends the hook input JSON to the prompt automatically if `$ARGUMENTS` is not present
2. For more control over where input appears, include `$ARGUMENTS`:
   ```json
   {
     "type": "prompt",
     "prompt": "Given this tool call: $ARGUMENTS\n\nCheck for security issues and respond with JSON."
   }
   ```
3. This is informational only — the hook will work without it.

---

## 7. Agent Hook Issues

### CRITICAL: Agent hook missing required 'prompt' field

**Error message**: `Agent hook missing required 'prompt' field`
**Severity**: CRITICAL
**Root cause**: A hook with `"type": "agent"` does not have a `"prompt"` field.
**Fix**:
1. Add the `"prompt"` field:
   ```json
   {
     "type": "agent",
     "prompt": "Review the output and suggest improvements."
   }
   ```

---

### MAJOR: Agent hook 'prompt' must be a non-empty string

**Error message**: `Agent hook 'prompt' must be a non-empty string`
**Severity**: MAJOR
**Root cause**: The `"prompt"` field in an agent hook is empty or not a string.
**Fix**:
1. Provide a non-empty string prompt:
   ```json
   {
     "type": "agent",
     "prompt": "Analyze the code changes and provide feedback."
   }
   ```

---

### MAJOR: Agent hook 'model' must be a non-empty string

**Error message**: `Agent hook 'model' must be a non-empty string`
**Severity**: MAJOR
**Root cause**: The optional `"model"` field in an agent hook is empty or not a string.
**Fix**:
1. Provide a valid model identifier or remove the field:
   ```json
   {
     "type": "agent",
     "prompt": "Review code",
     "model": "claude-sonnet-4-20250514"
   }
   ```

---

### MAJOR: Agent hook 'timeout' must be a number

**Error message**: `Agent hook 'timeout' must be a number (seconds)`
**Severity**: MAJOR
**Root cause**: The `"timeout"` field for an agent hook is not a number.
**Fix**:
1. **Note**: All Claude Code hook timeouts are in **seconds**. Agent-hook default is 60 seconds.
   ```json
   {
     "type": "agent",
     "prompt": "Review code",
     "timeout": 120
   }
   ```

---

### MAJOR: Agent hook 'timeout' must be positive

**Error message**: `Agent hook 'timeout' must be positive`
**Severity**: MAJOR
**Root cause**: The timeout value is zero or negative.
**Fix**:
1. Set a positive timeout value:
   ```json
   { "type": "agent", "prompt": "Review", "timeout": 60 }
   ```

---

### MINOR: Agent hook timeout exceeds 10 minutes

**Error message**: `Agent hook timeout exceeds 10 minutes`
**Severity**: MINOR
**Root cause**: The agent hook timeout is set to more than 600 seconds (10 minutes).
**Fix**:
1. Consider reducing the timeout. Agent hooks over 10 minutes are unusual:
   ```json
   { "type": "agent", "prompt": "Quick review", "timeout": 300 }
   ```
2. If you genuinely need a long timeout, acknowledge the warning — it will not block validation.

---

## 8. Timeout Issues

**ALL hook timeouts are SECONDS.** Default values per official `hooks.md` "Common fields" spec:

| Hook type | Default timeout |
|---|---|
| `command` | 600 seconds (10 minutes) |
| `prompt` | 30 seconds |
| `agent` | 60 seconds |
| `http` | 30 seconds |

The validator warns when a value looks accidentally milliseconds-scaled (> 10000 → probable bug).

### MAJOR: 'timeout' must be a number

**Error message**: `'timeout' must be a number, got {type_name}`
**Severity**: MAJOR
**Root cause**: The `"timeout"` field is not a numeric type (e.g., it is a string or boolean).
**Fix**:
1. Provide a numeric value in **seconds**:
   ```json
   { "type": "command", "command": "echo hi", "timeout": 5 }
   ```
2. **Wrong**:
   ```json
   { "timeout": "5" }
   { "timeout": "5s" }
   ```

---

### MAJOR: 'timeout' must be positive

**Error message**: `'timeout' must be positive`
**Severity**: MAJOR
**Root cause**: The timeout value is zero or negative.
**Fix**:
1. Set a positive timeout in **seconds**:
   ```json
   { "type": "command", "command": "echo hi", "timeout": 5 }
   ```

---

### WARNING: Command / Prompt hook timeout looks like milliseconds

**Error message**: `Command hook timeout is {timeout}s — this looks like milliseconds. Hook timeouts are in SECONDS (default: 600 for command hooks).` (analogous for prompt hooks with default 30)
**Severity**: WARNING
**Root cause**: The timeout value is > 10000 seconds (≈ 2.8 hours) which almost certainly means the author wrote milliseconds. The field IS seconds — if you meant 5 seconds, write `5`, not `5000`.
**Fix**:
1. Divide the value by 1000 if you meant milliseconds:
   ```json
   { "type": "command", "command": "echo hi", "timeout": 5 }
   ```
   not
   ```json
   { "type": "command", "command": "echo hi", "timeout": 5000 }
   ```
2. If you genuinely need > 10000 seconds for a command hook, consider `"async": true` so the hook runs in the background and doesn't block Claude Code:
   ```json
   { "type": "command", "command": "long-task.sh", "async": true }
   ```

---

### WARNING: Command hook timeout exceeds default 600s

**Error message**: `Command hook timeout is {timeout}s — exceeds default 600s`
**Severity**: WARNING
**Root cause**: The timeout value is between 600 and 10000 seconds. The default is 600s (10 min); anything larger blocks Claude Code operation for longer than usual.
**Fix**:
1. Reduce the timeout to ≤ 600 if possible, or mark the hook async so long runs don't block:
   ```json
   { "type": "command", "command": "long-task.sh", "async": true }
   ```
2. Recommended range (critical path): 1–60 seconds. Up to 600 seconds for legitimate long-running setup hooks (e.g. SessionStart dependency install).

---

### WARNING: Prompt hook timeout exceeds 600s

**Error message**: `Prompt hook timeout is {timeout}s — exceeds 600s`
**Severity**: WARNING
**Root cause**: A prompt hook (which invokes an AI model) has a timeout > 600s. This is unusual for AI model calls.
**Fix**:
1. Reduce timeout — prompt hooks should complete in seconds-to-minutes:
   ```json
   { "type": "prompt", "prompt": "Check safety", "timeout": 30 }
   ```
2. If the prompt is complex, simplify it or break it into smaller hooks.

---

### WARNING: HTTP hook timeout exceeds 600s

**Error message**: `HTTP hook timeout is {timeout}s — exceeds 600s`
**Severity**: WARNING
**Root cause**: An HTTP hook has a timeout > 600s. Remote calls should not block this long.
**Fix**:
1. Reduce the timeout:
   ```json
   { "type": "http", "url": "https://...", "timeout": 10 }
   ```
2. For latency-sensitive events (`UserPromptSubmit`, `PreToolUse`, etc.) use timeouts ≤ 5 seconds — see §13.8.

---

## 9. Script Path Issues

### MAJOR: Script not found

**Error message**: `Script not found: {script_path}`
**Severity**: MAJOR
**Root cause**: The script referenced in the command does not exist at the resolved path.
**Fix**:
1. Verify the script file exists: `ls -la path/to/script.sh`
2. If using environment variables, pass `--plugin-root` during validation:
   ```bash
   uv run python scripts/validate_hook.py hooks.json --plugin-root ./my-plugin
   ```
3. Ensure the script is included in your plugin or project
4. Check for typos in the filename and extension

---

### MAJOR: Script not executable

**Error message**: `Script not executable: {script_name}`
**Severity**: MAJOR
**Root cause**: The script file exists but does not have execute permission.
**Fix**:
1. Add execute permission:
   ```bash
   chmod +x path/to/script.sh
   ```
2. For git repositories, ensure the permission is tracked:
   ```bash
   git update-index --chmod=+x path/to/script.sh
   ```
3. Alternatively, invoke the script via its interpreter:
   ```json
   { "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/check.sh" }
   { "command": "python3 ${CLAUDE_PLUGIN_ROOT}/scripts/check.py" }
   { "command": "node ${CLAUDE_PLUGIN_ROOT}/scripts/check.mjs" }
   ```

---

## 10. Script Linting Issues

### Bash Scripts (shellcheck)

#### MAJOR: shellcheck error

**Error message**: `shellcheck SC{code}: {message}`
**Severity**: MAJOR
**Root cause**: shellcheck found an error-level issue in the bash script.
**Fix**:
1. Run shellcheck locally to see full details: `shellcheck path/to/script.sh`
2. Common issues:
   - `SC2086`: Double-quote variables to prevent globbing and word splitting
   - `SC2046`: Quote command substitutions
   - `SC2006`: Use `$(...)` instead of backticks
3. Fix the issue in your script and re-validate

#### MINOR: shellcheck warning

**Error message**: `shellcheck SC{code}: {message}`
**Severity**: MINOR
**Root cause**: shellcheck found a warning-level issue.
**Fix**:
1. Run `shellcheck path/to/script.sh` for details
2. Consider fixing or adding a `# shellcheck disable=SC{code}` directive if intentional

#### MINOR: shellcheck not available

**Error message**: `shellcheck not available locally or via bunx/npx, skipping lint for {script_name}`
**Severity**: MINOR
**Root cause**: shellcheck is not installed.
**Fix**:
1. Install shellcheck: `brew install shellcheck` (macOS) or `apt install shellcheck` (Linux)
2. Or install via npm: `npm install -g shellcheck`

#### MINOR: shellcheck timeout

**Error message**: `shellcheck timeout for {script_name}`
**Severity**: MINOR
**Root cause**: shellcheck took more than 30 seconds to analyze the script.
**Fix**:
1. The script may be very large — consider breaking it into smaller files
2. This does not indicate a problem with the script itself

---

### Python Scripts (ruff + mypy)

#### MAJOR: ruff lint error

**Error message**: `ruff {code}: {message}`
**Severity**: MAJOR
**Root cause**: The Python linter ruff found an issue.
**Fix**:
1. Run ruff locally: `ruff check path/to/script.py`
2. Report issues: `ruff check path/to/script.py` (read-only, no --fix)
3. Common codes: `F401` (unused import), `E501` (line too long), `F841` (unused variable)

#### MAJOR: mypy type error

**Error message**: `mypy: {message}`
**Severity**: MAJOR
**Root cause**: mypy found a type checking error.
**Fix**:
1. Run mypy locally: `mypy --ignore-missing-imports path/to/script.py`
2. Fix type annotations or add `# type: ignore` comments where appropriate

#### MINOR: ruff/mypy not available

**Error message**: `ruff not available locally or via uvx, skipping lint for {script_name}` / `mypy not available locally or via uvx, skipping type check for {script_name}`
**Severity**: MINOR
**Root cause**: The linting tool is not installed.
**Fix**:
1. Install ruff: `pip install ruff` or `uv tool install ruff`
2. Install mypy: `pip install mypy` or `uv tool install mypy`

#### MINOR: ruff/mypy timeout

**Error message**: `ruff timeout for {script_name}` / `mypy timeout for {script_name}`
**Severity**: MINOR
**Root cause**: The linter took too long (30s for ruff, 60s for mypy).
**Fix**: Consider breaking large scripts into smaller modules.

#### MINOR: ruff/mypy error

**Error message**: `ruff error: {error}` / `mypy error: {error}`
**Severity**: MINOR
**Root cause**: An unexpected error occurred while running the linter.
**Fix**: Check that the linter is properly installed and the script is valid Python.

---

### JavaScript/TypeScript Scripts (eslint)

#### MAJOR: eslint error

**Error message**: `eslint {rule}: {message}`
**Severity**: MAJOR
**Root cause**: eslint found a severity-2 (error) issue.
**Fix**:
1. Run eslint locally: `npx eslint path/to/script.mjs`
2. Report issues: `npx eslint path/to/script.mjs` (read-only, no --fix)

#### MINOR: eslint warning

**Error message**: `eslint {rule}: {message}`
**Severity**: MINOR
**Root cause**: eslint found a severity-1 (warning) issue.
**Fix**: Review and fix or configure eslint rules as appropriate.

#### MINOR: eslint not available

**Error message**: `eslint not available locally or via bunx/npx, skipping lint for {script_name}`
**Severity**: MINOR
**Root cause**: eslint is not installed.
**Fix**: Install eslint: `npm install -g eslint` or use `npx eslint`

#### MINOR: eslint timeout/error

**Error message**: `eslint timeout for {script_name}` / `eslint error: {error}`
**Severity**: MINOR
**Root cause**: eslint took too long (30s) or encountered an unexpected error.
**Fix**: Ensure eslint configuration is correct and the script is valid JS/TS.

---

## 11. Field Validation Issues

### MAJOR: 'statusMessage' must be a string

**Error message**: `'statusMessage' must be a string`
**Severity**: MAJOR
**Root cause**: The optional `"statusMessage"` field is not a string type.
**Fix**:
1. Provide a string value:
   ```json
   {
     "type": "command",
     "command": "check.sh",
     "statusMessage": "Running safety check..."
   }
   ```

---

### MAJOR: 'once' must be a boolean

**Error message**: `'once' must be a boolean, got {type_name}`
**Severity**: MAJOR
**Root cause**: The `"once"` field is not a boolean.
**Fix**:
1. Use `true` or `false`:
   ```json
   {
     "type": "command",
     "command": "setup.sh",
     "once": true
   }
   ```
2. **Wrong**: `"once": "true"`, `"once": 1`

---

### INFO: 'once' field detected

**Error message**: `'once' field detected (only works in skill-defined hooks)`
**Severity**: INFO
**Root cause**: The `"once"` field is used outside of a skill's hooks.json.
**Fix**:
1. The `"once"` field only has effect in skill-defined hooks (hooks inside a skill's configuration)
2. In non-skill hooks, this field is ignored
3. If this IS a skill hook, no action needed — this is informational only

---

### WARNING: Unknown hook field

**Error message**: `Unknown hook field '{key}' — not part of the Claude Code hook spec. If used by plugin scripts, consider documenting it.`
**Severity**: WARNING
**Root cause**: A field in the hook definition is not recognized as part of the Claude Code hook specification.
**Fix**:
1. Known hook fields are: `type`, `command`, `prompt`, `model`, `timeout`, `async`, `matcher`, `statusMessage`, `once`, `description`
2. If the field is intentionally consumed by your plugin script, document it in your plugin README
3. If it is a typo, correct it:
   - `"comand"` -> `"command"`
   - `"timeOut"` -> `"timeout"`
   - `"status_message"` -> `"statusMessage"`
4. Unknown fields will not break anything but may indicate a configuration mistake

---

## 12. Informational Notices

### INFO: No hooks configured for event

**Error message**: `No hooks configured for {event_name}`
**Severity**: INFO
**Root cause**: An event is declared but has an empty configuration array.
**Fix**:
1. Either add hooks for the event or remove the event key from your configuration:
   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "matcher": "Bash",
           "hooks": [
             { "type": "command", "command": "echo check" }
           ]
         }
       ]
     }
   }
   ```

---

### INFO: Validating hook N of M

**Error message**: `Validating hook {i} of {count}...`
**Severity**: INFO
**Root cause**: Progress indicator during validation.
**Fix**: No fix needed — this is a progress message.

---

### INFO: Validating N matcher block(s) for event

**Error message**: `Validating {count} matcher block(s) for {event_name}`
**Severity**: INFO
**Root cause**: Progress indicator showing how many matcher blocks exist for an event.
**Fix**: No fix needed — this is a progress message.

---

### INFO: Matcher block N

**Error message**: `Matcher block {i}...`
**Severity**: INFO
**Root cause**: Progress indicator for matcher block validation.
**Fix**: No fix needed — this is a progress message.

---

### PASSED: Valid JSON syntax

**Error message**: `Valid JSON syntax`
**Severity**: PASSED
**Root cause**: The hooks.json file is valid JSON.
**Fix**: No fix needed — this is a success message.

---

### PASSED: Valid top-level structure

**Error message**: `Valid top-level structure`
**Severity**: PASSED
**Root cause**: The top-level structure has the required `"hooks"` object.
**Fix**: No fix needed — this is a success message.

---

### PASSED: Description

**Error message**: `Description: {description_text}...`
**Severity**: PASSED
**Root cause**: A valid description field was found.
**Fix**: No fix needed — this is a success message.

---

### PASSED: Command validated

**Error message**: `Command: {command_text}...`
**Severity**: PASSED
**Root cause**: The command field passed validation.
**Fix**: No fix needed — this is a success message.

---

### PASSED: Prompt validated

**Error message**: `Prompt: {prompt_text}...`
**Severity**: PASSED
**Root cause**: The prompt field passed validation.
**Fix**: No fix needed — this is a success message.

---

### PASSED: Script executable

**Error message**: `Script executable: {script_name}`
**Severity**: PASSED
**Root cause**: The script has execute permissions.
**Fix**: No fix needed — this is a success message.

---

### PASSED: shellcheck OK

**Error message**: `shellcheck: {script_name} OK`
**Severity**: PASSED
**Root cause**: shellcheck found no issues.
**Fix**: No fix needed — this is a success message.

---

### PASSED: ruff check OK

**Error message**: `ruff check: {script_name} OK`
**Severity**: PASSED
**Root cause**: ruff found no issues.
**Fix**: No fix needed — this is a success message.

---

### PASSED: mypy OK

**Error message**: `mypy: {script_name} OK`
**Severity**: PASSED
**Root cause**: mypy found no type errors.
**Fix**: No fix needed — this is a success message.

---

### PASSED: eslint OK

**Error message**: `eslint: {script_name} OK`
**Severity**: PASSED
**Root cause**: eslint found no issues.
**Fix**: No fix needed — this is a success message.

---

### PASSED: All hooks valid for event

**Error message**: `All hooks valid for {event_name}`
**Severity**: PASSED
**Root cause**: All hooks for the given event passed validation.
**Fix**: No fix needed — this is a success message.

---

## Quick Reference: Complete hooks.json Template

```json
{
  "description": "Example plugin hooks",
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/check-bash.sh",
            "timeout": 5000,
            "statusMessage": "Checking Bash command safety..."
          }
        ]
      },
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "prompt",
            "prompt": "Review if this file write is safe. $ARGUMENTS\nRespond with JSON: {\"decision\": \"allow\"} or {\"decision\": \"deny\", \"reason\": \"...\"}",
            "timeout": 10000
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "prompt",
            "prompt": "Before stopping, verify all tasks are complete."
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "echo 'SESSION_TOKEN=abc123' >> $CLAUDE_ENV_FILE",
            "timeout": 5000
          }
        ]
      }
    ]
  }
}
```

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | All checks passed |
| `1` | CRITICAL issues found (hooks will not work) |
| `2` | MAJOR issues found (significant problems) |
| `3` | MINOR issues found (may affect behavior) |

---

## 13. Runtime-Dep & Invocation Issues (TRDD-0028dd34)

**Context:** Perfect Skill Suggester v3.1.0 shipped a `UserPromptSubmit` hook
that crashed on every prompt with `ERROR: pycozo is required` because the
command invoked `python3 pss_hook.py` — and system `python3` had no pycozo.
CPV's legacy hook validator silently approved the plugin. The TRDD-0028dd34
rewrite added a tokenizer, AST-based import detection, PEP 723 metadata
parsing, a cross-cutting runtime-dep reconciliation check, module-scope
`sys.exit` detection, and an `unset VIRTUAL_ENV` antipattern check.

**Critical fix-agent rule:** all of these diagnostics report real runtime
failures. The fix must preserve the hook's effective behavior — usually
that means **changing HOW the script is invoked, not WHAT it does**. Never
"fix" one of these by deleting the hook, muting the warning, or changing
the script's logic to strip third-party imports unless a simpler stdlib
alternative genuinely exists.

### 13.1. MAJOR: Plain interpreter with third-party imports

**Error message**: `Hook invokes {script} (imports: {mods}) via plain interpreter — third-party imports {mods} will fail at runtime unless satisfied via \`uv run --script\` + PEP 723 metadata, \`uv run --with\`, or a ${CLAUDE_PLUGIN_DATA}/.venv/bin/python set up by a SessionStart hook. (Note: do NOT substitute \`uvx\` — ...)`

**Severity**: MAJOR
**Root cause**: The hook runs `python3 script.py` (or `python script.py`, `python3.12 script.py`, etc.) on a script whose imports include packages outside the Python stdlib. At runtime, whatever `python3` resolves to on the user's PATH gets used — typically system Python, which has none of the project's dependencies. The hook will raise `ImportError` on every invocation. This is exactly what killed PSS v3.1.0.

**Preferred fix — PEP 723 inline metadata + `uv run --script`:**

1. Add a PEP 723 metadata block at the top of the script (before any `import` statement, after any shebang):
   ```python
   #!/usr/bin/env python3
   # /// script
   # requires-python = ">=3.10"
   # dependencies = [
   #     "pycozo[embedded]>=0.7.6",
   #     # One entry per third-party import mentioned in the validator message
   # ]
   # ///
   ```
2. Change the hook command from `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/foo.py` to `uv run --quiet --script ${CLAUDE_PLUGIN_ROOT}/scripts/foo.py`. Example:
   ```jsonc
   // BEFORE
   { "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/foo.py\"" }
   // AFTER
   { "type": "command", "command": "uv run --quiet --script \"${CLAUDE_PLUGIN_ROOT}/scripts/foo.py\"" }
   ```
3. Keep `--quiet` so uv's progress output does not leak into hook stdout (which Claude Code treats as hook-returned JSON).
4. Do NOT substitute `uvx` (`uv tool run`). `uvx` runs installable PyPI packages via their `[project.scripts]` entry-points; it cannot target a local `.py` file with PEP 723 metadata — `uvx /path/to/foo.py` attempts to resolve `foo.py` as a PyPI package and fails. There is no `uvx --script` flag.
5. Document uv as a prerequisite in the plugin README (e.g. add a Requirements section stating Python ≥ 3.10 and uv on `PATH`).

**Alternative fix A — `uv run --with`:** If PEP 723 metadata is infeasible (e.g. the script is shared between the hook and a packaged entry-point with its own dep manifest), pass `--with` flags for each third-party dep:
```jsonc
{ "command": "uv run --with pycozo --with httpx \"${CLAUDE_PLUGIN_ROOT}/scripts/foo.py\"" }
```
The fix-agent must supply a `--with pkg[==version]` for every module named in the validator's `imports: {...}` list. Missing entries will re-fire the check (now as the "uv-run-with missing" MAJOR — see §13.3).

**Alternative fix B — `${CLAUDE_PLUGIN_DATA}/.venv/bin/python`:** If the plugin needs a dedicated venv that survives across invocations:

1. Add a SessionStart hook that creates the venv on first session and installs deps:
   ```jsonc
   "SessionStart": [{
     "hooks": [{
       "type": "command",
       "command": "diff -q \"${CLAUDE_PLUGIN_ROOT}/pyproject.toml\" \"${CLAUDE_PLUGIN_DATA}/pyproject.toml\" >/dev/null 2>&1 || (mkdir -p \"${CLAUDE_PLUGIN_DATA}\" && cp \"${CLAUDE_PLUGIN_ROOT}/pyproject.toml\" \"${CLAUDE_PLUGIN_DATA}/\" && uv venv \"${CLAUDE_PLUGIN_DATA}/.venv\" --python 3.10 && VIRTUAL_ENV=\"${CLAUDE_PLUGIN_DATA}/.venv\" uv pip install -r \"${CLAUDE_PLUGIN_ROOT}/pyproject.toml\")"
     }]
   }]
   ```
2. Change the consuming hook to invoke the venv python directly:
   ```jsonc
   { "command": "\"${CLAUDE_PLUGIN_DATA}/.venv/bin/python\" \"${CLAUDE_PLUGIN_ROOT}/scripts/foo.py\"" }
   ```

**Alternative fix C — make the script stdlib-only:** Only if the third-party dep is genuinely optional (e.g. a nice-to-have formatter), refactor the script to remove the import. Never do this by `try: import x except ImportError: x = None` — the validator treats try-except-guarded imports as third-party too (see §13.7 for the right pattern when a dep is truly optional).

**Do NOT:**
- Add `|| true`, `2>/dev/null`, or other error suppression. That hides the failure without fixing it.
- Delete the hook. Find out what it does first.
- Change `python3` to `python` — same problem.
- Set `async: true` to "fire and forget" the ImportError. Claude Code still surfaces hook errors.

---

### 13.2. MAJOR: `uv run --script` with no PEP 723 metadata block

**Error message**: `Hook uses \`uv run --script\` on {script} but the script has no PEP 723 inline metadata block. Add a \`# /// script\` header declaring dependencies: {mods}.`

**Severity**: MAJOR
**Root cause**: The hook command uses `uv run --script` (the correct wrapper) but the referenced script has no `# /// script` block, so uv creates an empty-deps cache venv and the script's imports fail.

**Fix**:

1. Add the metadata block immediately after any shebang and before the first `import`:
   ```python
   #!/usr/bin/env python3
   # /// script
   # requires-python = ">=3.10"
   # dependencies = [
   # ]
   # ///
   import os  # stdlib imports go below
   import pycozo  # third-party
   ```
2. Populate `dependencies` with a PEP 508 requirement spec for each module in the validator's `mods` list. Use the **PyPI project name**, not the import name (`pillow`, not `PIL`; `beautifulsoup4`, not `bs4`). Include a version floor when possible:
   ```
   "pycozo[embedded]>=0.7.6",
   "httpx>=0.27",
   "pyyaml>=6",
   ```
3. If unsure of the project name, run `uv pip show <import-name>` in a venv that already has the package, or check PyPI.
4. Commit the script change alongside the hooks.json change.

**Do NOT** remove `--script` from the command to silence the warning — that falls back to the §13.1 failure mode.

---

### 13.3. MAJOR: PEP 723 block is incomplete

**Error message**: `PEP 723 metadata in {script} is missing declarations for imported third-party modules: {missing}. Add them to the \`dependencies\` list in the \`# /// script\` block.`

**Severity**: MAJOR
**Root cause**: The PEP 723 block exists but the `dependencies` array omits packages the script actually imports. uv's cache venv will not contain them at runtime.

**Fix**:

1. Open the script. Locate the `# /// script` block.
2. For each module name in `{missing}`, append a PEP 508 line inside `dependencies`. Preserve any existing version pins:
   ```diff
    # /// script
    # requires-python = ">=3.10"
    # dependencies = [
    #     "pycozo[embedded]>=0.7.6",
   +#     "httpx>=0.27",
    # ]
    # ///
   ```
3. Keep the map from import name to PyPI name in mind (same table as §13.2).
4. If the script legitimately does NOT use a module at runtime (e.g. an import sits behind `if typing.TYPE_CHECKING:`), move that import inside the TYPE_CHECKING guard so ast detection skips it:
   ```python
   from typing import TYPE_CHECKING
   if TYPE_CHECKING:
       import some_heavy_lib  # type-only import, not a runtime dep
   ```
   The validator walks all imports including those inside `if TYPE_CHECKING` — this is intentional, because runtime-reachable `if` branches also fire. Use `if TYPE_CHECKING` ONLY for true type-only imports.

---

### 13.4. MAJOR: `uv run --with` flags do not cover imports

**Error message**: `\`uv run --with\` flags do not cover imported modules {missing} in {script}. Add them to --with.`

**Severity**: MAJOR
**Root cause**: The command uses `uv run --with pkg foo.py` (explicit-deps mode) but at least one import has no matching `--with` flag.

**Fix**:

1. Append a `--with <pkg>` for each missing module. Example:
   ```jsonc
   // BEFORE
   { "command": "uv run --with pycozo \"${CLAUDE_PLUGIN_ROOT}/scripts/foo.py\"" }
   // AFTER (foo.py also imports httpx)
   { "command": "uv run --with pycozo --with httpx \"${CLAUDE_PLUGIN_ROOT}/scripts/foo.py\"" }
   ```
2. `--with` accepts comma-separated lists too: `--with pycozo,httpx` — use whichever style keeps the command readable.
3. Consider migrating to PEP 723 metadata (§13.1 Preferred Fix) once `--with` grows past 2–3 entries — inline metadata is easier to review and auto-invalidates its cache on changes.

---

### 13.5. MINOR: venv-python invocation without SessionStart setup hook

**Error message**: `Hook invokes {script} via ${CLAUDE_PLUGIN_DATA}/.venv/bin/python but no SessionStart hook was found that creates the venv (expected: a command containing \`uv venv\` or \`pip install\` targeting ${CLAUDE_PLUGIN_DATA}). First-install runs will fail with ImportError for {mods}.`

**Severity**: MINOR
**Root cause**: The hook targets a plugin-scoped venv via direct binary invocation, but no companion SessionStart hook exists to create that venv on first session. The user's first prompt after install will fail because `.venv/bin/python` does not exist yet.

**Fix**:

Add a SessionStart hook whose command meets the detector's heuristic (contains `CLAUDE_PLUGIN_DATA` **and** matches one of `uv venv`, `python -m venv`, or `pip install`):

```jsonc
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "diff -q \"${CLAUDE_PLUGIN_ROOT}/pyproject.toml\" \"${CLAUDE_PLUGIN_DATA}/pyproject.toml\" >/dev/null 2>&1 || (mkdir -p \"${CLAUDE_PLUGIN_DATA}\" && cp \"${CLAUDE_PLUGIN_ROOT}/pyproject.toml\" \"${CLAUDE_PLUGIN_DATA}/\" && uv venv \"${CLAUDE_PLUGIN_DATA}/.venv\" --python 3.10 && VIRTUAL_ENV=\"${CLAUDE_PLUGIN_DATA}/.venv\" uv pip install -r \"${CLAUDE_PLUGIN_ROOT}/pyproject.toml\")"
      }]
    }],
    "UserPromptSubmit": [{
      "hooks": [{
        "type": "command",
        "command": "\"${CLAUDE_PLUGIN_DATA}/.venv/bin/python\" \"${CLAUDE_PLUGIN_ROOT}/scripts/foo.py\""
      }]
    }]
  }
}
```

Key properties:
- The `diff -q` guard skips reinstall when the manifest has not changed, keeping warm-session latency to a few milliseconds.
- On plugin updates that change `pyproject.toml`, the diff fails → venv re-syncs automatically.
- `uv venv` / `uv pip install` is preferred over `python -m venv` / `pip install` for speed, but either matches the detector.

If switching to `uv run --script` (§13.1 Preferred Fix) is feasible instead, do that — it needs no SessionStart bootstrap and has better cache semantics.

---

### 13.6. MAJOR: Script calls `sys.exit` / `raise SystemExit` at module scope

**Error message**: `{script} calls sys.exit()/exit()/raise SystemExit at MODULE scope (line(s): {lines}) — the hook process will be killed at import time if the call path is reached. Move such exits inside a function guarded by \`if __name__ == '__main__':\` or raise ImportError instead.`

**Severity**: MAJOR
**Root cause**: Python's `sys.exit()` raises `SystemExit`. When an `import` statement triggers `SystemExit` at module top level (including inside a top-level `if` block), the SystemExit propagates **past any `try/except ImportError` in the importer** and kills the entire process. For a hook script this means the hook dies silently on every invocation the moment the problematic import path is taken.

This was the second layer of the PSS v3.1.0 failure: `pss_cozodb.py` had `sys.exit("ERROR: pycozo is required...")` inside a top-level `except ImportError:` block, so even though `pss_hook.py` wrapped its import of `pss_cozodb` in `try/except ImportError`, the SystemExit from the nested sys.exit was NOT caught.

**Fix — three patterns (pick by context):**

**Pattern A — CLI script with `main()`:** If the script IS a CLI that's meant to be run directly, move the exit logic into `main()` and guard with `if __name__ == '__main__':`.

   ```diff
   - import sys
   - if not os.environ.get("TOKEN"):
   -     sys.exit("TOKEN env var required")
   - do_work()
   + import sys
   + def main() -> int:
   +     if not os.environ.get("TOKEN"):
   +         print("TOKEN env var required", file=sys.stderr)
   +         return 1
   +     do_work()
   +     return 0
   + if __name__ == "__main__":
   +     sys.exit(main())
   ```

   The exit now only fires when the script is run as `__main__`, not when imported.

**Pattern B — Library module with optional dep:** If the script is imported by other modules and has an optional dependency, raise `ImportError` (which IS caught by `try/except ImportError`) or fall back gracefully. NEVER call `sys.exit` from a library.

   ```diff
   - import sys
   - try:
   -     import pycozo
   - except ImportError:
   -     sys.exit("ERROR: pycozo is required.")
   + try:
   +     import pycozo
   + except ImportError as _e:
   +     # Provide a stub/sentinel or raise ImportError with a clear message —
   +     # NEVER sys.exit, because SystemExit propagates past the caller's
   +     # `except ImportError:` and kills the process.
   +     pycozo = None  # type: ignore[assignment]
   +     _pycozo_import_error = _e
   +
   + def _require_pycozo():
   +     if pycozo is None:
   +         raise ImportError(
   +             "pycozo is required. Install via PEP 723 metadata + uv run --script, "
   +             "or add to the plugin's SessionStart venv-setup hook."
   +         ) from _pycozo_import_error
   ```

**Pattern C — Assertion-style top-level guard:** If the script genuinely must not load under certain conditions (rare), raise a concrete exception type that the caller can handle — not a bare `SystemExit`:

   ```python
   class HookConfigurationError(RuntimeError):
       """Raised at import time when the hook is misconfigured."""
   if some_required_condition is False:
       raise HookConfigurationError("required: foo env var set")
   ```

**Do NOT:**
- Comment out the `sys.exit` to silence the warning — you remove the check but keep the bad design.
- Wrap the `sys.exit` in `if __name__ == '__main__':` AT MODULE TOP LEVEL for a library module — that works but Pattern A is the clearer idiom.
- Change `sys.exit(...)` to `os._exit(...)` — that's strictly worse (skips cleanup, same kill semantics).

---

### 13.7. WARNING: `unset VIRTUAL_ENV` with plain `python3` (conditional)

**Error message**: `Command runs \`unset VIRTUAL_ENV\` and then invokes a plain \`python3\` interpreter. Unsetting VIRTUAL_ENV removes the user's venv but leaves you depending on whatever \`python3\` resolves to on PATH — typically system Python with none of the project's dependencies. ...`

**Severity**: WARNING
**Root cause**: The command tries to isolate from the user's ambient environment by unsetting `VIRTUAL_ENV`, but then falls back to plain `python3`. System Python has none of the plugin's deps — this is the exact PSS v3.1.0 failure mode. The warning fires ONLY when `unset VIRTUAL_ENV` coincides with an `interpreter-python` ScriptRef in the same command AND no safer-python ref (`uv-run-script`, `uv-run-with`, `venv-python`) is present. Legitimate uses with `uv run --script` or direct-invoked venv python do NOT trigger this warning.

**Fix**: apply the same fix as §13.1 (switch to `uv run --script` with PEP 723 metadata). The `unset VIRTUAL_ENV` becomes unnecessary once the invocation changes:

```jsonc
// BEFORE
{ "command": "unset VIRTUAL_ENV; python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/foo.py\"" }
// AFTER
{ "command": "uv run --quiet --script \"${CLAUDE_PLUGIN_ROOT}/scripts/foo.py\"" }
```

If for some reason `unset VIRTUAL_ENV` is still desired after switching to `uv run --script` (e.g. belt-and-suspenders isolation), the warning WILL NOT fire — the check requires BOTH `unset VIRTUAL_ENV` AND plain `python3` AND no safer invocation.

Analogous PYTHONPATH variant: same diagnosis and same fix; a bare `unset PYTHONPATH; python3 foo.py` is flagged for the same reason.

---

### 13.8. WARNING: HTTP hook latency-sensitive event with long timeout

**Error message**: `HTTP hook on '{event_name}' has a {timeout}s timeout — this event blocks user interaction, so every invocation can stall for up to {timeout}s on a slow/failing endpoint. Consider \`async: true\` or a shorter timeout (<= 5s).`

**Severity**: WARNING
**Root cause**: HTTP hooks block the hook thread until the remote responds. For events that sit on the user's critical path (`UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `SessionStart`), a slow or unresponsive endpoint stalls every invocation. A 30s timeout means every bad endpoint hit is a 30s freeze.

**Fix — pick the option that matches intent:**

1. **If the HTTP call is fire-and-forget (analytics, audit logs):** mark the hook async so it does not block:
   ```jsonc
   { "type": "http", "url": "https://analytics.example.com/event", "async": true, "timeout": 30 }
   ```
   Async hooks run in the background and their latency never touches the user.
2. **If the HTTP response matters (allow/deny decision):** cap the timeout at 5 seconds and make sure the endpoint is fast:
   ```jsonc
   { "type": "http", "url": "https://policy.example.com/check", "timeout": 5 }
   ```
   If the endpoint legitimately takes longer, you are in trouble — the user will notice every call. Consider moving the decision out of the critical path.
3. **If the event is not actually latency-sensitive:** you likely want a different event (e.g. `Stop` or `TaskCompleted`) rather than `UserPromptSubmit`.

The warning is advisory — it does not block the hook from running. But if the endpoint ever degrades, the user sees the stall before any error is raised.

---

### 13.9. Edge cases the fix-agent MUST handle correctly

| Scenario | Validator sees | Correct fix |
|---|---|---|
| Script uses only stdlib (`os`, `sys`, `json`) | `plain python3` but no third-party imports → silent PASS | No change needed — the hook is safe as-is. Fix-agent MUST NOT touch it. |
| Script has optional dep behind `try/except ImportError` | AST still detects the import → flagged | Apply §13.6 Pattern B (ImportError-raising helper) AND §13.1 (uv-run-script + PEP 723) |
| Script imports `pss_cozodb` (sibling plugin module) | Not flagged — sibling modules excluded by `plugin_script_dir` heuristic | No action — validator handles this. |
| Script has PEP 723 block AND uses `python3` invocation | Metadata block is ignored by `python3` (it's just a comment). Validator flags interpreter-python + third-party imports. | Change invocation to `uv run --quiet --script`; the existing metadata block is reused. |
| Script imports `PIL` (aka `pillow`) | Validator sees `PIL` in `imports` | PEP 723 entry must use PyPI name: `"pillow>=10"`, not `"PIL"` |
| Hook uses `python3.12 foo.py` (versioned interpreter) | Recognized as `interpreter-python` by the version-suffix regex | Same fix as §13.1 — `uv run --script` supersedes the versioned invocation and uv reads `requires-python` from the PEP 723 block |
| Hook uses compound command `unset VAR; uv run --script foo.py` | `unset VIRTUAL_ENV` detected but coincides with `uv-run-script` ref → NO warning | Fix-agent MUST NOT touch this — it's a legitimate defensive pattern |
| Hook uses `env python3 foo.py` (portable shebang style) | Recognized as interpreter-python after `env` wrapper parsing | Same fix as §13.1 — `env uv run --script foo.py` works too |
| Multiple scripts in one command: `foo.sh && python3 bar.py` | Extractor emits TWO ScriptRefs (foo.sh direct, bar.py interpreter-python) | Fix each ref independently; their diagnoses are unrelated |

---

### 13.10. Cross-reference with rewritten extractor

The extractor now returns `ScriptRef(path, invocation_mode, simple_command, explicit_deps)` for every script. The fix-agent can use `invocation_mode` to know which fix-path applies:

| invocation_mode | §13 subsection that diagnoses it |
|---|---|
| `interpreter-python` + third-party imports | §13.1 |
| `uv-run-script` + no PEP 723 block | §13.2 |
| `uv-run-script` + incomplete PEP 723 | §13.3 |
| `uv-run-with` + missing --with flags | §13.4 |
| `venv-python` + no SessionStart setup | §13.5 |
| any Python mode + module-scope sys.exit | §13.6 |
| (separate: `unset VIRTUAL_ENV` + interpreter-python) | §13.7 |
| (separate: HTTP hook + latency-sensitive event) | §13.8 |

---

### 13.11. WARNING: Path-traversal in hook command

**Error message A** (command-string regex): `Command contains a \`..\` path segment that escapes the plugin/project root — this is a path-traversal pattern that may break plugin isolation or enable cross-plugin interference. ...`

**Error message B** (resolved-path check): `Script path \`{path}\` resolves OUTSIDE the plugin root \`{plugin_root}\` via \`..\` segments — this breaks plugin isolation. ...`

**Severity**: WARNING
**Root cause**: The hook command references a script path containing `..` components that, once resolved, land outside the plugin's declared root directory. Typical instances:
1. `${CLAUDE_PLUGIN_ROOT}/../sibling_plugin/foo.py` — directly reaches into a sibling plugin
2. `${CLAUDE_PROJECT_DIR}/../.secrets/key.json` — escapes the project directory to read sensitive data
3. `${CLAUDE_PLUGIN_DATA}/../../shared/cache` — escapes the plugin's persistent data area
4. `/abs/path/with/../../traversal/foo.py` — absolute path containing traversal segments

**Why this matters**:
- **Plugin isolation**: Each plugin is a self-contained unit. Reaching into another plugin's files bypasses that boundary and creates hidden cross-plugin dependencies that break when the other plugin updates.
- **Security**: Traversal can access credentials, env files, or scripts outside the plugin's declared footprint. A malicious or buggy hook could read secrets that Claude Code's permission model intends to protect.
- **Maintainability**: Path-traversal references are fragile. Any restructure of the repo or plugin install location invalidates them silently.

**Fix — three patterns based on intent:**

1. **Intended cross-plugin access** (rare, should be explicit via plugin-dependencies): if the plugin genuinely needs data from a sibling, declare the sibling as a plugin dependency in `plugin.json`:
   ```json
   {
     "dependencies": {
       "other-plugin": "^1.0.0"
     }
   }
   ```
   Then access the sibling's files via `${CLAUDE_PROJECT_DIR}` + a relative path that does not use `..` — or require the sibling to expose data via an MCP server or a shared file in `${CLAUDE_PLUGIN_DATA}`.

2. **Accidental traversal** (most common): the path was wrong. Rewrite to reference the intended location directly:
   ```jsonc
   // BEFORE (escapes plugin root)
   { "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/../shared/lib.py\"" }
   // AFTER
   { "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/shared/lib.py\"" }
   ```
   Move the referenced file INTO the plugin if it belongs there.

3. **Traversal was for testing** (development-only): delete the hook or guard it behind an env-var check so it doesn't ship in the published plugin.

**Do NOT:**
- "Fix" the warning by adding `set +e` or `|| true` — the warning is not an error that stops the hook, it is advisory. Suppressing it leaves the isolation break in place.
- Rewrite the path with `realpath` / `readlink -f` to pre-resolve traversal — that silences the static check without fixing the semantic issue. The resolved-path check will still fire at validation time.
- Add traversal to a SessionStart setup hook that writes outside `${CLAUDE_PLUGIN_DATA}` — shared mutable state across plugins is a well-known antipattern.

**Legitimate patterns that WILL NOT trigger the warning:**
- `${CLAUDE_PLUGIN_ROOT}/scripts/hooks/foo.py` — deep subdirs, no `..`
- `${CLAUDE_PROJECT_DIR}/src/main/foo.py` — deep subdirs, no `..`
- Paths inside quoted strings with `..` in the FILENAME itself (e.g. `"foo..bar.py"` — regex requires `..` as a path segment, not part of a name)

---

### 13.12. Fix-agent checklist (work each item top-to-bottom)

Copy the checklist below into your fix log at `$MAIN_ROOT/reports/plugin-fixer/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md` (at the main-repo root — first entry of `git worktree list`, never a linked worktree; both `reports/` and `reports_dev/` gitignored) and tick items as they're complete. Do NOT skip the verification step — silently leaving a script broken is worse than not fixing it at all.

```
Runtime-dep fix checklist (per affected hook command)

Phase 1 — identify
- [ ] Read the validator message. Match its phrase against §13.1–§13.8 to
      pick the diagnosis subsection.
- [ ] Open the hook file (`hooks/hooks.json` or `hooks.json`) and locate the
      exact command string the finding refers to. DO NOT guess — line/column
      in the report is authoritative.
- [ ] Open the referenced script. Confirm:
      * which modules it imports (compare against the validator's `imports: {...}`)
      * whether any imports are stdlib-only (if so, the fix is usually `no-op`;
        see §13.9 first row)
      * whether a PEP 723 `# /// script` block already exists
      * whether any `sys.exit` / `raise SystemExit` sits at module scope

Phase 2 — choose fix path
- [ ] If third-party imports exist AND hook uses plain `python3`:
      apply §13.1 (primary: `uv run --script` + PEP 723 block)
- [ ] If hook uses `uv run --script` but PEP 723 is missing / incomplete:
      apply §13.2 or §13.3 (add / extend block)
- [ ] If hook uses `uv run --with`:
      apply §13.4 (add --with <pkg> per missing import)
- [ ] If hook uses `${CLAUDE_PLUGIN_DATA}/.venv/bin/python`:
      apply §13.5 (add SessionStart setup hook)
- [ ] If script has module-scope sys.exit:
      apply §13.6 (Pattern A/B/C as applicable)
- [ ] If `unset VIRTUAL_ENV` + plain python3 fires:
      the fix from §13.1 makes this warning disappear automatically
- [ ] If HTTP hook latency warning fires:
      apply §13.8 (`async: true` OR shorter timeout)

Phase 3 — apply
- [ ] Make the edit to hooks.json (command string) AND/OR the script (PEP 723
      block, sys.exit relocation) as §13.x specifies. Keep all changes minimal.
- [ ] DO NOT substitute `uvx` for `uv run --script` — they are not interchangeable.
- [ ] DO NOT add error suppression (`|| true`, `2>/dev/null`) to mute the issue.
- [ ] DO NOT delete the hook to make the warning go away.
- [ ] Preserve the hook's effective behavior: what it does must match what it
      did before (minus the actual bug).

Phase 4 — verify (REQUIRED)
- [ ] Re-read the edited files to confirm the changes landed as intended.
- [ ] If PEP 723 deps were added, run the script once locally under uv to
      confirm uv can resolve the listed packages:
      `uv run --quiet --script <path/to/script.py> --help` (or equivalent)
- [ ] If a SessionStart venv-setup hook was added, run the command locally once
      to confirm it creates the venv without error.
- [ ] Verify that the test suite for the plugin still passes.
- [ ] Ask the validator to re-run and confirm the diagnostic is gone.

Phase 5 — log
- [ ] Append a Fix-log entry documenting: validator message, subsection used,
      files touched, commands run to verify.
```

PSS-specific examples (what the PSS v3.1.0 → v3.1.1 fix actually did):
- `unset VIRTUAL_ENV; python3 pss_hook.py` → `uv run --quiet --script pss_hook.py`
- Added PEP 723 block to `pss_hook.py` declaring `pycozo[embedded]>=0.7.6`
- Replaced `sys.exit("ERROR: pycozo is required.")` in `pss_cozodb.py` at module scope with a sentinel `Client = None` + `_require_pycozo()` helper that raises `ImportError` on first use (§13.6 Pattern B)
- Added `uv` as a prerequisite in the README

This is the canonical reference fix — mimic its shape.

---
