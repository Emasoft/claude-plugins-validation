---
name: register-mcp
description: Register a new MCP server in an existing plugin's .mcp.json (stdio default; supports HTTP transport via --http-url; cross-platform command via Python/Node). Use when adding a new MCP server entry. Used dynamically via the-skills-menu (TRDD-478d9687).
when_to_use: When the cpv-main-menu user picks Create → Add MCP server to existing plugin, or any flow needs to register a new MCP server entry in .mcp.json
user-invocable: false
---

# register-mcp

## Overview

Registers a new MCP server in a plugin's `.mcp.json`. The default transport is stdio (executable spawned per session). HTTP transport is also supported via the `--http-url` flag. The server's `command` MUST be cross-platform — invoke it via `node`, `python3`, `uv run`, or `npx` so it runs identically on Linux, macOS, and Windows. A bare relative shell-script command (e.g. `./run.sh`) is a portability footgun: `validate_mcp` flags a relative file path that omits `${CLAUDE_PLUGIN_ROOT}` as a MINOR finding, and a `.sh` entry point will not run on Windows at all — always wrap it in a cross-platform interpreter. Loaded by `cpv-main-menu-agent` via the Create → Add MCP server menu branch.

## Prerequisites

- `uv` on PATH
- Target plugin path with `.claude-plugin/plugin.json`
- A cross-platform command string (Python, Node, or `uv run` delegation)
- Optional bundled executables under `servers/<server-name>/` (v2.65.0+ convention)

## Instructions

1. Choose a unique server name within the plugin. Check `validate_mcp` for cross-source collisions (`.mcp.json` + inline `plugin.json::mcpServers` shadowing is a silent drop).
2. Reject the reserved server name `workspace` (Claude Code v2.1.128+).
3. Write the command as a cross-platform invocation — never a bare shell script path.
4. Bundled executables should live in `servers/<server-name>/` per the canonical convention.
5. Run `add_component.py --type mcp` with the plugin path and server details.
6. Re-validate via `plugin-validation-skill` and `validate_mcp --strict`.

Copy this checklist and track your progress:

- [ ] Server name unique (not in `.mcp.json` OR inline `mcpServers`)
- [ ] Server name is not `workspace` (reserved)
- [ ] Command is cross-platform (Python/Node/uv-run)
- [ ] Bundled scripts in `servers/<name>/`
- [ ] `add_component.py --type mcp` executed
- [ ] `validate_mcp --strict` exit 0

## Output

- A new entry in `.mcp.json` keyed by server name.
- Identical entries (same name + same command) are skipped — re-runs are no-ops.
- The file is created if missing; existing entries are preserved.

## Error Handling

| Error | Resolution |
|-------|------------|
| MAJOR: server name collision across sources | Remove the duplicate from one source — prefer `.mcp.json` |
| MINOR: relative command path missing `${CLAUDE_PLUGIN_ROOT}` (bare shell script) | Rewrite as a cross-platform `node` / `python3` / `uv run` invocation |
| MAJOR: reserved name `workspace` | Pick a different server name |
| Server fails to start at runtime | Test the command manually; check executable permissions on bundled scripts |

## Examples

```bash
# Node.js MCP server (stdio)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" /path/to/my-plugin \
  --type mcp --name todo \
  --command 'node "${CLAUDE_PLUGIN_ROOT}/servers/todo/index.js"'

# Python MCP server (stdio) — `--with mcp` injects the MCP SDK from PyPI into
# the ephemeral env. Do NOT pass `--with sqlite3`: sqlite3 ships in the Python
# stdlib and has no PyPI distribution, so `--with sqlite3` fails to resolve.
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" /path/to/my-plugin \
  --type mcp --name db \
  --command 'uv run --with mcp python "${CLAUDE_PLUGIN_ROOT}/servers/db/server.py"'

# HTTP transport (uncommon — only for remote services)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" /path/to/my-plugin \
  --type mcp --name api --http-url "https://api.example.com/mcp"
```

### When to use

- Exposing a database, API, or filesystem as MCP-callable tools.
- Wrapping a long-running daemon (the MCP server stays alive across Claude Code calls).
- Adding plugin-specific resources / prompts (MCP servers can ship both, not just tools).

### When NOT to use

- For one-off tasks — use a slash command or agent instead. MCP servers add startup overhead.
- For tools that need plugin-author secrets at runtime — use `userConfig` and substitute `${user_config.KEY}` in the MCP server's `env` block (or in its exec-form `args` array) to inject them safely (never hardcode).
  Since Claude Code **v2.1.207**, `${user_config.*}` is **rejected in a `headersHelper` shell-form command** (shell-injection fix: an option value interpolated into a shell string is attacker-controlled shell input). A `headersHelper` script must read the secret itself — from the server's `env` block or a config file — not receive it interpolated into its command line.

## Resources

- `add-component-to-plugin` skill — multi-purpose scaffold wrapper
- `plugin-validation-skill` — validate MCP config cross-source consistency
- `canonical-pipeline` skill — MCP bundling convention (`servers/` folder)
