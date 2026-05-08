---
name: cpv-create-mcp
description: Register a new MCP server in an existing plugin's .mcp.json (stdio default; supports HTTP transport via --http-url; cross-platform command via Python/Node).
argument-hint: <plugin-path> <server-name> <command>
allowed-tools: Bash(uv:*)
user-invocable: true
---

# /cpv-create-mcp

Register a new MCP server in a plugin's `.mcp.json`. The default
transport is stdio (executable spawned per session). HTTP transport is
also supported via the `--http-url` flag.

The server's `command` MUST be cross-platform — invoke it via `node`,
`python3`, `uv run`, or `npx` so it runs identically on Linux, macOS,
and Windows. A bare `./server.sh` is rejected with a CRITICAL finding.

## Usage

```bash
# Node.js MCP server (stdio)
/cpv-create-mcp /path/to/my-plugin todo "node ${CLAUDE_PLUGIN_ROOT}/servers/todo/index.js"

# Python MCP server (stdio)
/cpv-create-mcp /path/to/my-plugin db "uv run --with sqlite3 python ${CLAUDE_PLUGIN_ROOT}/servers/db/server.py"

# HTTP transport (uncommon — only for remote services)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" /path/to/my-plugin \
  --type mcp --name api --http-url "https://api.example.com/mcp"
```

## Under the hood

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" <plugin-path> \
  --type mcp --name <server-name> --command "<command>"
```

## Behavior

- `.mcp.json` is created if missing, otherwise merged. Identical
  entries (same server name + command) are skipped.
- Server name MUST be unique within the plugin. CPV's
  `validate_mcp` flags collisions across `.mcp.json` AND inline
  `plugin.json:mcpServers` (cross-source shadowing is a silent
  drop on Claude Code's side).
- `workspace` is a reserved server name (Claude Code v2.1.128+) —
  rejected.
- Bundled executables / scripts go in `servers/<server-name>/` per
  the v2.65.0+ convention.

## When to use

- Exposing a database, API, or filesystem as MCP-callable tools.
- Wrapping a long-running daemon (the MCP server stays alive across
  Claude Code calls).
- Adding plugin-specific resources / prompts (MCP servers can ship
  both, not just tools).

## When NOT to use

- For one-off tasks — use a slash command or agent instead. MCP servers
  add startup overhead.
- For tools that need plugin-author secrets at runtime — use
  `userConfig` + `${user_config.KEY}` substitution to inject them
  safely (never hardcode).
