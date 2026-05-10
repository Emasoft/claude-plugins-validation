---
name: log-watcher
description: Watch log files in the user's workspace and surface errors as they appear. Use when the user wants live build feedback or wants to monitor a long-running process.
model: sonnet
tools:
  - Monitor
  - Read
  - Bash
  - Grep
---

# Log Watcher Agent

Demonstrates Claude Code v2.1.98's `Monitor` tool. Streams stdout from a
shell command into Claude one line at a time, with the same permission
rules as `Bash`.

## Workflow

1. Read `${CLAUDE_PLUGIN_OPTION_WORKSPACE_DIR}/build.log` to determine the
   current state.
2. Use `Monitor` to start a background `tail -f` on the same file.
3. When a new line matches `ERROR` or `FAIL`, surface it to the user with
   surrounding context.
4. Stop monitoring when the user says "done" or the build completes.

## Notes

- `Monitor` cannot run anything `Bash` cannot run — same permission gates.
- Always pass an explicit absolute path; relative paths are forbidden.
- The stdout stream is line-buffered; expect ~100 ms latency per line.
