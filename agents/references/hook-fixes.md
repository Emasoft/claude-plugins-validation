# Hook Configuration — Validation Issues and Fixes

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

**IMPORTANT**: Claude Code hook timeouts are specified in **MILLISECONDS**, not seconds.

| Value | Actual Duration |
|-------|----------------|
| `100` | 100ms (0.1 seconds) |
| `5000` | 5 seconds |
| `30000` | 30 seconds |
| `60000` | 1 minute |
| `600000` | 10 minutes |

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

---

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
2. Valid event names are (all 19):
   - `PreToolUse`
   - `PostToolUse`
   - `PostToolUseFailure`
   - `PermissionRequest`
   - `UserPromptSubmit`
   - `Notification`
   - `Stop`
   - `SubagentStop`
   - `SubagentStart`
   - `SessionStart`
   - `SessionEnd`
   - `PreCompact`
   - `Setup`
   - `TeammateIdle`
   - `TaskCompleted`
   - `ConfigChange`
   - `WorktreeCreate`
   - `WorktreeRemove`
   - `InstructionsLoaded`
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
1. **Note**: Agent hook timeouts are in **seconds** (unlike command/prompt hooks which use milliseconds). Default is 60 seconds.
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

### MAJOR: 'timeout' must be a number (command/prompt hooks)

**Error message**: `'timeout' must be a number, got {type_name}`
**Severity**: MAJOR
**Root cause**: The `"timeout"` field is not a numeric type (e.g., it is a string or boolean).
**Fix**:
1. Provide a numeric value in **milliseconds**:
   ```json
   { "type": "command", "command": "echo hi", "timeout": 5000 }
   ```
2. **Wrong**:
   ```json
   { "timeout": "5000" }
   { "timeout": "5s" }
   ```

---

### MAJOR: 'timeout' must be positive (command/prompt hooks)

**Error message**: `'timeout' must be positive`
**Severity**: MAJOR
**Root cause**: The timeout value is zero or negative.
**Fix**:
1. Set a positive timeout in milliseconds:
   ```json
   { "type": "command", "command": "echo hi", "timeout": 5000 }
   ```

---

### WARNING: Command hook timeout unusually long

**Error message**: `Command hook timeout is {timeout}ms ({seconds}s) — unusually long`
**Severity**: WARNING
**Root cause**: The command hook timeout exceeds 600,000ms (10 minutes).
**Fix**:
1. Consider whether the command genuinely needs more than 10 minutes
2. Long-running hooks block Claude Code's operation
3. Recommended range: 1,000ms to 60,000ms (1 second to 1 minute)
4. For long tasks, consider using `"async": true` so the hook runs in the background:
   ```json
   { "type": "command", "command": "long-task.sh", "async": true }
   ```

---

### WARNING: Command hook timeout very short

**Error message**: `Command hook timeout is {timeout}ms — very short, may cause premature timeouts`
**Severity**: WARNING
**Root cause**: The command hook timeout is less than 100ms, which may not give the command enough time to execute.
**Fix**:
1. Increase the timeout to at least 1000ms (1 second):
   ```json
   { "type": "command", "command": "quick-check.sh", "timeout": 5000 }
   ```
2. Even simple commands need time for process startup

---

### WARNING: Prompt hook timeout unusually long

**Error message**: `Prompt hook timeout is {timeout}ms ({seconds}s) — unusually long`
**Severity**: WARNING
**Root cause**: The prompt hook timeout exceeds 600,000ms (10 minutes).
**Fix**:
1. Prompt hooks invoke an AI model — 10+ minutes is excessive
2. Recommended range: 5,000ms to 60,000ms (5 seconds to 1 minute)
3. Simplify the prompt if processing takes too long

---

### WARNING: Prompt hook timeout very short

**Error message**: `Prompt hook timeout is {timeout}ms — very short, may cause premature timeouts`
**Severity**: WARNING
**Root cause**: The prompt hook timeout is less than 100ms, which is far too short for AI model inference.
**Fix**:
1. Increase to at least 5000ms (5 seconds):
   ```json
   { "type": "prompt", "prompt": "Check safety", "timeout": 10000 }
   ```
2. AI model calls typically need several seconds

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
