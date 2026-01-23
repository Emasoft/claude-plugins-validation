# MCP Server Validation Reference

Complete reference for MCP (Model Context Protocol) server configuration in Claude Code plugins.

## Table of Contents

- [1. MCP Configuration Locations](#1-mcp-configuration-locations)
- [2. Server Definition Fields](#2-server-definition-fields)
- [3. Transport Types](#3-transport-types)
- [4. Environment Variables](#4-environment-variables)
- [5. Path Handling](#5-path-handling)
- [6. Complete Configuration Examples](#6-complete-configuration-examples)
- [7. Common MCP Errors](#7-common-mcp-errors)
- [8. Validation Checklist](#8-validation-checklist)

---

## 1. MCP Configuration Locations

### Option 1: Separate .mcp.json File (Recommended)

Place `.mcp.json` at plugin root:

```
my-plugin/
├── .claude-plugin/
│   └── plugin.json
├── .mcp.json              # MCP configuration here
└── ...
```

```json
{
  "mcpServers": {
    "server-name": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/my-server",
      "args": ["--config", "${CLAUDE_PLUGIN_ROOT}/config.json"]
    }
  }
}
```

### Option 2: Inline in plugin.json

Add `mcpServers` directly in plugin manifest:

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "Plugin with MCP server",
  "mcpServers": {
    "server-name": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/my-server"
    }
  }
}
```

### Option 3: Reference Path in plugin.json

Point to external MCP config file:

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "Plugin with MCP server",
  "mcpServers": "./mcp-config.json"
}
```

---

## 2. Server Definition Fields

### All Available Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| command | string | Yes (stdio) | Executable path or command name |
| args | array | No | Command-line arguments |
| env | object | No | Environment variables for server |
| cwd | string | No | Working directory |
| type | string | No | Transport type (default: "stdio") |
| url | string | Yes (http/sse) | Server URL for HTTP transport |
| headers | object | No | HTTP headers for authentication |
| timeout | number | No | Connection timeout in seconds |

### Minimal stdio Server

```json
{
  "mcpServers": {
    "my-server": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/my-server"
    }
  }
}
```

### Full stdio Server

```json
{
  "mcpServers": {
    "my-server": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/my-server",
      "args": ["--port", "8080", "--config", "${CLAUDE_PLUGIN_ROOT}/config.json"],
      "env": {
        "API_KEY": "${API_KEY}",
        "LOG_LEVEL": "info",
        "DATA_DIR": "${CLAUDE_PLUGIN_ROOT}/data"
      },
      "cwd": "${CLAUDE_PLUGIN_ROOT}",
      "timeout": 30
    }
  }
}
```

### HTTP Server

```json
{
  "mcpServers": {
    "remote-server": {
      "type": "http",
      "url": "https://api.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${API_TOKEN}"
      }
    }
  }
}
```

---

## 3. Transport Types

### stdio (Default)

Standard input/output communication with local process.

| Required | Optional |
|----------|----------|
| command | args, env, cwd, timeout |

```json
{
  "my-server": {
    "command": "node",
    "args": ["${CLAUDE_PLUGIN_ROOT}/server.js"]
  }
}
```

### http

HTTP protocol for remote servers.

| Required | Optional |
|----------|----------|
| url | headers, timeout |

```json
{
  "remote-server": {
    "type": "http",
    "url": "https://api.example.com/mcp",
    "headers": {
      "X-API-Key": "${API_KEY}"
    }
  }
}
```

### sse (Server-Sent Events)

**DEPRECATED** - Use http instead.

```json
{
  "legacy-server": {
    "type": "sse",
    "url": "https://api.example.com/sse"
  }
}
```

### Transport Selection

| Use Case | Transport |
|----------|-----------|
| Local executable | stdio |
| Remote API | http |
| Legacy systems | sse (deprecated) |

---

## 4. Environment Variables

### Expansion Syntax

| Syntax | Description |
|--------|-------------|
| `${VAR}` | Expands to environment variable value |
| `${VAR:-default}` | Uses `default` if variable not set |

### Plugin-Specific Variables

| Variable | Description |
|----------|-------------|
| `${CLAUDE_PLUGIN_ROOT}` | Absolute path to plugin directory |
| `${CLAUDE_PROJECT_DIR}` | Project root directory |

### Where Variables Expand

Environment variables are expanded in:
- `command` - Server executable path
- `args` - Command-line arguments
- `env` - Environment variables for server
- `url` - HTTP server URL
- `headers` - HTTP authentication headers
- `cwd` - Working directory

### Example with Variables

```json
{
  "mcpServers": {
    "api-server": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/api-server",
      "args": [
        "--config", "${CLAUDE_PLUGIN_ROOT}/config.json",
        "--port", "${SERVER_PORT:-3000}"
      ],
      "env": {
        "API_KEY": "${API_KEY}",
        "DB_URL": "${DB_URL:-localhost:5432}",
        "PLUGIN_DIR": "${CLAUDE_PLUGIN_ROOT}",
        "PROJECT_DIR": "${CLAUDE_PROJECT_DIR}"
      }
    }
  }
}
```

### Required vs Optional Variables

- **Required**: If variable is not set and has no default, config parsing fails
- **With default**: Use `${VAR:-default}` for optional variables

---

## 5. Path Handling

### Critical Rules

1. **Always use `${CLAUDE_PLUGIN_ROOT}`** for plugin-relative paths
2. **Never use absolute paths** - breaks portability
3. **Relative paths** start with `./` when referencing plugin files
4. **Path traversal** (`../`) may not work after installation

### Correct Path Usage

```json
{
  "mcpServers": {
    "my-server": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/my-server",
      "args": ["--config", "${CLAUDE_PLUGIN_ROOT}/config/settings.json"],
      "cwd": "${CLAUDE_PLUGIN_ROOT}"
    }
  }
}
```

### Incorrect Path Usage (Will Break)

```json
{
  "mcpServers": {
    "my-server": {
      "command": "/absolute/path/to/server",
      "args": ["--config", "../config/settings.json"]
    }
  }
}
```

### NPM Package Servers

For npm-based MCP servers:

```json
{
  "mcpServers": {
    "npm-server": {
      "command": "npx",
      "args": ["@company/mcp-server", "--plugin-mode"],
      "cwd": "${CLAUDE_PLUGIN_ROOT}"
    }
  }
}
```

---

## 6. Complete Configuration Examples

### Database MCP Server

```json
{
  "mcpServers": {
    "database": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/db-server",
      "args": [
        "--config", "${CLAUDE_PLUGIN_ROOT}/config/db-config.json",
        "--verbose"
      ],
      "env": {
        "DB_HOST": "${DB_HOST:-localhost}",
        "DB_PORT": "${DB_PORT:-5432}",
        "DB_USER": "${DB_USER}",
        "DB_PASSWORD": "${DB_PASSWORD}",
        "PLUGIN_ROOT": "${CLAUDE_PLUGIN_ROOT}"
      },
      "cwd": "${CLAUDE_PLUGIN_ROOT}"
    }
  }
}
```

### Multiple Servers

```json
{
  "mcpServers": {
    "local-db": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/db-server",
      "args": ["--config", "${CLAUDE_PLUGIN_ROOT}/config.json"]
    },
    "remote-api": {
      "type": "http",
      "url": "https://api.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${API_TOKEN}"
      }
    },
    "cache": {
      "command": "npx",
      "args": ["@org/cache-mcp-server"],
      "env": {
        "CACHE_URL": "${CACHE_URL:-redis://localhost:6379}"
      }
    }
  }
}
```

### Full Plugin with MCP

**plugin.json:**
```json
{
  "name": "database-tools",
  "version": "1.0.0",
  "description": "Database management tools via MCP",
  "mcpServers": "./.mcp.json"
}
```

**.mcp.json:**
```json
{
  "mcpServers": {
    "database": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/db-server",
      "args": ["--config", "${CLAUDE_PLUGIN_ROOT}/config/db.json"],
      "env": {
        "DB_HOST": "${DB_HOST:-localhost}",
        "DB_PORT": "${DB_PORT:-5432}"
      },
      "cwd": "${CLAUDE_PLUGIN_ROOT}"
    }
  }
}
```

---

## 7. Common MCP Errors

### Error: Server Not Starting

**Cause:** Command doesn't exist or not executable

**Fix:**
1. Verify command path: `ls -la ${CLAUDE_PLUGIN_ROOT}/servers/`
2. Check permissions: `chmod +x servers/my-server`
3. Use `${CLAUDE_PLUGIN_ROOT}` for paths

### Error: Tools Not Appearing

**Cause:** Server doesn't implement MCP protocol correctly

**Fix:**
1. Test server independently: `./servers/my-server --help`
2. Check server logs via `claude --debug`
3. Verify server implements MCP tool registration

### Error: Path Errors

**Cause:** Absolute paths used

**Wrong:**
```json
{"command": "/Users/me/plugins/my-plugin/server"}
```

**Correct:**
```json
{"command": "${CLAUDE_PLUGIN_ROOT}/server"}
```

### Error: Config Parse Failure

**Cause:** Required environment variable not set

**Fix:** Add default value:
```json
{"env": {"API_KEY": "${API_KEY:-default_key}"}}
```

### Error: Missing Command Field

**Cause:** stdio server without command

**Wrong:**
```json
{
  "my-server": {
    "args": ["--config", "config.json"]
  }
}
```

**Correct:**
```json
{
  "my-server": {
    "command": "${CLAUDE_PLUGIN_ROOT}/server",
    "args": ["--config", "${CLAUDE_PLUGIN_ROOT}/config.json"]
  }
}
```

### Error: Missing URL for HTTP

**Cause:** HTTP/SSE server without url field

**Wrong:**
```json
{
  "remote-server": {
    "type": "http",
    "headers": {"Authorization": "Bearer token"}
  }
}
```

**Correct:**
```json
{
  "remote-server": {
    "type": "http",
    "url": "https://api.example.com/mcp",
    "headers": {"Authorization": "Bearer token"}
  }
}
```

### Error: Invalid Environment Variable Syntax

**Wrong:**
```json
{"env": {"KEY": "$API_KEY"}}
```

**Correct:**
```json
{"env": {"KEY": "${API_KEY}"}}
```

---

## 8. Validation Checklist

### Pre-release MCP Checklist

- [ ] .mcp.json is valid JSON (or mcpServers in plugin.json)
- [ ] Each server has unique name
- [ ] stdio servers have `command` field
- [ ] http/sse servers have `url` field
- [ ] All paths use `${CLAUDE_PLUGIN_ROOT}`
- [ ] No absolute paths
- [ ] Required env vars have defaults or are documented
- [ ] Env var syntax is `${VAR}` or `${VAR:-default}`
- [ ] Server executables exist and are executable
- [ ] Server implements MCP protocol correctly
- [ ] sse transport migrated to http if possible

### Validation Command

```bash
uv run python scripts/validate_mcp.py /path/to/plugin
```

### Debugging

```bash
# See MCP server initialization and errors
claude --debug
```

Debug output shows:
- MCP server initialization status
- Connection errors
- Configuration parsing issues

---

## Related References

- [Plugin Structure](plugin-structure.md) - Overall plugin layout
- [Hook Validation](hook-validation.md) - Hook configuration
- [Skill Validation](skill-validation.md) - Skill structure
- [Marketplace Validation](marketplace-validation.md) - Distribution
