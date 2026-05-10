---
name: v2-1-80-demo-skill
description: Demonstrates v2.1.80-plus features end to end - userConfig substitutions, CLAUDE_PLUGIN_OPTION env vars, and the v2.1.98 plugin-skill name field. Use when the user wants to see every Claude Code v2.1.80-plus feature exercised in one place. Trigger with phrases like "show v2.1.80 features", "demo userConfig", or "exercise the demo plugin".
---

# v2.1.80+ Demo Skill

This skill exists purely as a fixture for CPV. It exercises every feature
documented in the create-plugin skill's v2.1.80 reference file.

## Overview

The skill cross-references three plugin-level features defined in
`plugin.json`:

- `userConfig` keys (read via `${user_config.WORKSPACE_DIR}` in
  hook/MCP/LSP configs).
- The auto-exported `CLAUDE_PLUGIN_OPTION_*` env vars (non-sensitive
  keys only - `API_TOKEN` is `sensitive: true` so it is excluded).
- The `channels` array, whose `server` field cross-references the
  `notifications` MCP server defined in the same manifest.

## Prerequisites

- Plugin must be installed and enabled in Claude Code v2.1.98 or later.
- The five `userConfig` values must have been provided at plugin enable
  time (Claude Code prompts for them automatically).
- The `notifications` MCP server's runtime must be reachable (`node
  ${CLAUDE_PLUGIN_ROOT}/mcp/notifications.js` in this fixture - the
  fixture does not ship the runtime, only the manifest reference).

## Instructions

1. Confirm the user's workspace via
   `${CLAUDE_PLUGIN_OPTION_WORKSPACE_DIR}`.
2. Read `${CLAUDE_PLUGIN_OPTION_CONFIG_FILE}` if the user supplied a
   config file path.
3. Spawn the log-watcher agent (defined in `agents/log-watcher.md`)
   to demonstrate the `Monitor` tool.
4. The agent will surface log lines on the `alerts` channel registered
   against the `notifications` MCP server.

## Output

The skill returns a short summary listing:

- Which userConfig values were resolved.
- Whether the cache flag (`ENABLE_CACHE`) is enabled.
- The poll interval (`POLL_INTERVAL`, in seconds).
- Whether the log-watcher agent successfully started a `Monitor`.

## Error Handling

- If `WORKSPACE_DIR` resolves to a non-existent path, abort with a
  message naming the missing directory.
- If `CONFIG_FILE` is set but unreadable, surface the OS error to the
  user and offer to skip the read.
- If the `notifications` MCP server fails to start, the `alerts`
  channel will be unavailable - report this and continue without
  channel emission.
- Sensitive userConfig keys (`API_TOKEN`) are NOT available as
  `CLAUDE_PLUGIN_OPTION_API_TOKEN`. Use `${user_config.API_TOKEN}` from
  hook/MCP/LSP configs instead.

## Examples

### Example 1: Basic invocation

```
User: run the v2.1.80 demo skill
Skill resolves WORKSPACE_DIR=${CLAUDE_PROJECT_DIR}
Skill resolves CONFIG_FILE=${CLAUDE_PROJECT_DIR}/config.json
Skill spawns log-watcher agent on ${CLAUDE_PROJECT_DIR}/build.log
Skill confirms POLL_INTERVAL=900 and ENABLE_CACHE=true
```

### Example 2: Skipping the optional config file

```
User: run the demo without a config file
CONFIG_FILE is not provided - skill skips step 2 and proceeds.
```

## Resources

- `agents/log-watcher.md` - the agent that uses the `Monitor` tool.
- `.claude-plugin/plugin.json` - the manifest declaring `userConfig`,
  `mcpServers`, and `channels`.
- The CPV repo's create-plugin skill ships a v2-1-80-features.md
  reference that documents every feature exercised here, with
  validator pointers.
