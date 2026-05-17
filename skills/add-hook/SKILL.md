---
name: add-hook
description: Add a new hook entry to hooks/hooks.json in an existing plugin (idempotent — skips duplicate entries; cross-platform-aware so the hook command runs identically on Linux/macOS/Windows).
when_to_use: When the cpv-main-menu user picks Create → Add hook to existing plugin, or any flow needs to append a new event-handler entry to hooks/hooks.json with cross-platform-safe command
user-invocable: false
allowed-tools: Bash(uv:*)
---

# add-hook

Add a new hook entry to a plugin's `hooks/hooks.json`. The scaffold is
idempotent — re-running with the same event+command is a no-op.

The new hook command MUST be cross-platform: Python or Node.js
delegation. Bash-only constructs (`set -euo pipefail`, `[[ ]]`,
`$(<file)`, process substitution, brace expansion) will trigger
validate_hook MAJOR findings. POSIX-only tools (`jq`, `sed`, `awk`,
`shellcheck`) used directly trigger MINOR. The recommended pattern is
to delegate to `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/<your-hook>.py"`.

## Usage

Examples:

```
# Cross-platform Python delegation (recommended)
add-hook /path/to/my-plugin PostToolUse 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/post_tool.py"'

# Cross-platform Node delegation
add-hook /path/to/my-plugin SessionStart 'node "${CLAUDE_PLUGIN_ROOT}/scripts/init.js"'
```

## Under the hood

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" <plugin-path> \
  --type hook --event <event-name> --command "<command>"
```

## Behavior

- `hooks/hooks.json` is created if missing, otherwise merged.
- Identical hook entries (same event + same command) are skipped.
- The command is validated against the cross-platform rules in
  `validate_hook.check_hook_command_cross_platform` before being
  written. Bash-isms are rejected (the agent prompts to convert).
- Hooks must NOT write runtime state to `${CLAUDE_PLUGIN_ROOT}` (state
  lost on every plugin update). Use `${CLAUDE_PLUGIN_DATA}` instead.

## Valid event names

PreToolUse, PostToolUse, PostToolUseFailure, PostToolBatch,
PermissionRequest, PermissionDenied, UserPromptSubmit,
UserPromptExpansion, Notification, Stop, StopFailure, SubagentStop,
SubagentStart, SessionStart, SessionEnd, PreCompact, PostCompact,
TeammateIdle, TaskCompleted, TaskCreated, ConfigChange, WorktreeCreate,
WorktreeRemove, InstructionsLoaded, Elicitation, ElicitationResult,
CwdChanged, FileChanged.

## When to use

- Auto-format on file save (PostToolUse on Edit/Write).
- Bootstrap a venv on session start (SessionStart with the diff-and-reinstall pattern from the persistent-data docs).
- Capture telemetry without polluting the conversation (Notification → write to `${CLAUDE_PLUGIN_DATA}/log.jsonl`).
