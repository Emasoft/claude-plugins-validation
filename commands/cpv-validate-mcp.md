---
name: cpv-validate-mcp
description: |
  Validate MCP (Model Context Protocol) server configurations in Claude Code plugins.
  Checks .mcp.json files and inline mcpServers definitions. Validates transport types,
  required fields, paths, environment variables, and security. Use when configuring
  MCP servers or debugging connection issues.
allowed-tools: Read, Bash, Glob, Grep
argument-hint: "<path> [--verbose] [--json]"
user-invocable: true
---

# /cpv-validate-mcp Command

Validates MCP server configurations in Claude Code plugins.

## Usage

```
/cpv-validate-mcp <path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `path` | Yes | Path to .mcp.json file or plugin directory |

## Options

| Option | Description |
|--------|-------------|
| `--verbose` | Show all checks including passed |
| `--json` | Output results as JSON |

## What Gets Validated

### 1. JSON Structure
- Valid JSON syntax
- `mcpServers` object present
- Server names are valid identifiers

### 2. Transport Types
Valid transport types:
- `stdio` (default) - Local process communication
- `http` - HTTP/REST transport
- `sse` - Server-Sent Events (deprecated, prefer http)

### 3. Required Fields by Transport

**stdio servers:**
- `command` (required) - Executable path or command
- `args` (optional) - Command-line arguments array
- `env` (optional) - Environment variables object
- `cwd` (optional) - Working directory

**http/sse servers:**
- `url` (required) - Server URL (http:// or https://)
- `headers` (optional) - HTTP headers for authentication

### 4. Path Validation
- Warns about absolute paths (use `${CLAUDE_PLUGIN_ROOT}`)
- Validates environment variable syntax
- Checks script existence and executability

### 5. Environment Variables
Supported variables:
- `${CLAUDE_PLUGIN_ROOT}` - Plugin root directory
- `${CLAUDE_PROJECT_DIR}` - Project root directory
- `${VAR_NAME}` - Custom environment variables
- `${VAR_NAME:-default}` - Variables with defaults

### 6. Security Checks
- Warns about hardcoded credentials in headers
- Checks for API keys in `Authorization`, `X-API-Key` headers
- Recommends environment variables for secrets

### 7. Deprecation Warnings
- SSE transport is deprecated (migrate to http)
- Warns about ignored fields per transport type

## Examples

### Validate Plugin MCP Config

```
/cpv-validate-mcp ./my-plugin/
```

### Validate Specific .mcp.json

```
/cpv-validate-mcp ./my-plugin/.mcp.json
```

### Verbose Output

```
/cpv-validate-mcp ./my-plugin/ --verbose
```

### JSON Output

```
/cpv-validate-mcp ./my-plugin/.mcp.json --json
```

## Output Example

```
============================================================
MCP Configuration Validation Report
============================================================

Summary:
  CRITICAL: 0
  MAJOR:    1
  MINOR:    1

Details:
  [MAJOR] Server 'my-server' has hardcoded credential in headers[Authorization] - use environment variables
  [MINOR] Server 'old-server' uses deprecated 'sse' transport - consider migrating to 'http'

------------------------------------------------------------
✗ Issues found
```

## Configuration Examples

### stdio Server (Local MCP)

```json
{
  "mcpServers": {
    "my-local-mcp": {
      "command": "${CLAUDE_PLUGIN_ROOT}/mcp/server.py",
      "args": ["--port", "3000"],
      "env": {
        "API_KEY": "${MY_API_KEY:-default_key}"
      }
    }
  }
}
```

### HTTP Server (Remote MCP)

```json
{
  "mcpServers": {
    "remote-mcp": {
      "type": "http",
      "url": "https://api.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_TOKEN}"
      }
    }
  }
}
```

### npx-based MCP

```json
{
  "mcpServers": {
    "npx-mcp": {
      "command": "npx",
      "args": ["-y", "@example/mcp-server"]
    }
  }
}
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | CRITICAL issues (MCP will not work) |
| 2 | MAJOR issues (significant problems) |
| 3 | MINOR issues (may affect behavior) |

## Execution

```bash
uv run python scripts/validate_mcp.py "$PATH" $OPTIONS
```

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-hooks` - Hook validation
- `/cpv-validate-skill` - Skill validation
- `/cpv-validate-agents` - Agent validation
