---
name: add-hook
description: Add a new hook entry to hooks/hooks.json in an existing plugin (idempotent — skips duplicate entries; cross-platform-aware). Use when adding a new event-handler that must run identically on Linux/macOS/Windows. Used dynamically via the-skills-menu (TRDD-478d9687).
when_to_use: When the cpv-main-menu user picks Create → Add hook to existing plugin, or any flow needs to append a new event-handler entry to hooks/hooks.json with cross-platform-safe command
user-invocable: false
---

# add-hook

## Overview

Adds a new hook entry to a plugin's `hooks/hooks.json`. The scaffold is idempotent — re-running with the same event+command is a no-op. The new hook command MUST be cross-platform: prefer Python or Node.js delegation. Bash-only constructs (`set -euo pipefail`, `[[ ]]`, `$(<file)`, process substitution, brace expansion) will trigger `validate_hook` MAJOR findings. Loaded by `cpv-main-menu-agent` via the Create → Add hook menu branch.

## Prerequisites

- `uv` on PATH
- Target plugin path with `.claude-plugin/plugin.json`
- A valid hook event name (see "Valid event names" below)
- A cross-platform command string (delegate to Python/Node, NOT bash-isms)

## Instructions

1. Pick a valid hook event name from the canonical list.
2. Write the command as a Python or Node delegation (the script will reject bash-only constructs).
3. Run the underlying `add_component.py --type hook` invocation.
4. The script creates `hooks/hooks.json` if missing or merges into the existing file.
5. Re-validate via `plugin-validation-skill` to confirm cross-platform compliance.
6. NEVER write runtime state to `${CLAUDE_PLUGIN_ROOT}` (lost on every plugin update). Use `${CLAUDE_PLUGIN_DATA}` instead.

Copy this checklist and track your progress:

- [ ] Event name validated against canonical list
- [ ] Command uses Python/Node delegation (no bash-isms)
- [ ] `add_component.py --type hook` executed
- [ ] `hooks/hooks.json` updated or created
- [ ] `validate_plugin --strict` re-run

## Output

- A new entry in `hooks/hooks.json` keyed by event name.
- Identical entries (same event + same command) are skipped — re-runs are no-ops.
- The file is created if missing; existing entries are preserved.

## Error Handling

| Error | Resolution |
|-------|------------|
| MAJOR: bash-only constructs | Convert command to Python/Node delegation |
| MINOR: POSIX-only tool (jq, sed, awk) | Replace with Python equivalent (json, re.sub, list comprehension) |
| Hook never fires | Check event name spelling; restart Claude Code (plugin hooks pick up on plugin reload) |
| State lost between sessions | Move runtime state writes from `${CLAUDE_PLUGIN_ROOT}` to `${CLAUDE_PLUGIN_DATA}` |

## Examples

```bash
# Cross-platform Python delegation (recommended)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" /path/to/my-plugin \
  --type hook --event PostToolUse \
  --command 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/post_tool.py"'

# Cross-platform Node delegation
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" /path/to/my-plugin \
  --type hook --event SessionStart \
  --command 'node "${CLAUDE_PLUGIN_ROOT}/scripts/init.js"'
```

### Valid event names

PreToolUse, PostToolUse, PostToolUseFailure, PostToolBatch, PermissionRequest, PermissionDenied, UserPromptSubmit, UserPromptExpansion, Notification, Stop, StopFailure, SubagentStop, SubagentStart, SessionStart, SessionEnd, PreCompact, PostCompact, TeammateIdle, TaskCompleted, TaskCreated, ConfigChange, WorktreeCreate, WorktreeRemove, InstructionsLoaded, Elicitation, ElicitationResult, CwdChanged, FileChanged.

### When to use

- Auto-format on file save (PostToolUse on Edit/Write).
- Bootstrap a venv on session start (SessionStart with the diff-and-reinstall pattern).
- Capture telemetry without polluting the conversation (Notification writing to `${CLAUDE_PLUGIN_DATA}/log.jsonl`).

## Resources

- `add-component-to-plugin` skill — multi-purpose scaffold wrapper
- `plugin-validation-skill` — validate hooks.json cross-platform compliance
- `canonical-pipeline` skill — pipeline rules around hook design
- `fix-validation` skill — fix-up recipes for hook errors
