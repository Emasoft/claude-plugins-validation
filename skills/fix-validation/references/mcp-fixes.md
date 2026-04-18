# MCP Server Configuration — Validation Issues and Fixes

Comprehensive remediation guide for all issues detected by `validate_mcp.py`.

## Table of Contents

- [1. Configuration File Issues](#1-configuration-file-issues)
- [2. Server Definition Issues](#2-server-definition-issues)
- [3. Transport Type Issues](#3-transport-type-issues)
- [4. stdio Transport Issues](#4-stdio-transport-issues)
- [5. HTTP/SSE Transport Issues](#5-httpsse-transport-issues)
- [6. Environment Variable Issues](#6-environment-variable-issues)
- [7. Path Issues](#7-path-issues)
- [8. Args / Env / Cwd Field Issues](#8-args--env--cwd-field-issues)
- [9. Headers Issues](#9-headers-issues)
- [10. Timeout Issues](#10-timeout-issues)
- [11. OAuth Issues](#11-oauth-issues)
- [12. Plugin Manifest Issues](#12-plugin-manifest-issues)
- [13. Cross-Source Duplicate Server Names](#13-cross-source-duplicate-server-names)

---

## Checklist

- [ ] Identify which MCP config (.mcp.json or inline) and server the finding references
- [ ] Match to a numbered section below
- [ ] Verify the server command is executable and installed
- [ ] Apply the fix (env vars, args, transport, etc.)
- [ ] Re-validate

## 1. Configuration File Issues

### CRITICAL: Invalid JSON in configuration file

**Error message**: `Invalid JSON in {rel_path}: {parse_error}`
**Severity**: CRITICAL
**Root cause**: The `.mcp.json` file contains malformed JSON — trailing commas, missing quotes, unescaped characters, or encoding issues.
**Fix**:
1. Run `python3 -m json.tool .mcp.json` to see the exact parse error location.
2. Open the file and fix the syntax at the reported line/column.
3. Common mistakes:
   - Trailing comma after the last property in an object or array.
   - Single quotes instead of double quotes.
   - Unescaped backslashes in Windows paths (use `\\` or forward slashes).
4. Example of valid JSON:
```json
{
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": ["server.js"]
    }
  }
}
```

### CRITICAL: mcpServers must be an object

**Error message**: `'mcpServers' must be an object in {rel_path}`
**Severity**: CRITICAL
**Root cause**: The `mcpServers` key exists but its value is not a JSON object (e.g., it is an array, a string, a number, or null).
**Fix**:
1. Ensure `mcpServers` is a JSON object mapping server names to server config objects.
2. Wrong:
```json
{
  "mcpServers": ["my-server"]
}
```
3. Correct:
```json
{
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": ["server.js"]
    }
  }
}
```

### INFO: MCP config file not found

**Error message**: `MCP config file not found: {rel_path}`
**Severity**: INFO
**Root cause**: The expected `.mcp.json` file does not exist at the given path. This is informational — not every plugin requires an MCP config.
**Fix**:
1. If this plugin should define MCP servers, create a `.mcp.json` file in the plugin root:
```json
{
  "mcpServers": {}
}
```
2. If the plugin does not need MCP servers, this message can be safely ignored.

### INFO: No mcpServers field in config

**Error message**: `No 'mcpServers' field in {rel_path}`
**Severity**: INFO
**Root cause**: The `.mcp.json` file is valid JSON but does not contain a `mcpServers` key.
**Fix**:
1. Add the `mcpServers` key if this file is intended to define MCP servers:
```json
{
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": ["server.js"]
    }
  }
}
```
2. If `mcpServers` is intentionally absent, this can be ignored.

### INFO: No MCP servers defined

**Error message**: `No MCP servers defined in {rel_path}`
**Severity**: INFO
**Root cause**: The `mcpServers` object is present but empty (`{}`).
**Fix**:
1. Add at least one server definition, or remove the file if MCP is not needed:
```json
{
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": ["index.js"]
    }
  }
}
```

### INFO: Found N MCP server(s)

**Error message**: `Found {N} MCP server(s) in {rel_path}`
**Severity**: INFO
**Root cause**: Informational — reports how many servers were discovered. No action needed.

---

## 2. Server Definition Issues

### CRITICAL: Server config must be an object

**Error message**: `Server '{server_name}' config must be an object`
**Severity**: CRITICAL
**Root cause**: The value for a server key in `mcpServers` is not a JSON object. It may be a string, array, number, boolean, or null.
**Fix**:
1. Each server entry must be a JSON object with at least a `command` (for stdio) or `url` (for http/sse).
2. Wrong:
```json
{
  "mcpServers": {
    "my-server": "node server.js"
  }
}
```
3. Correct:
```json
{
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": ["server.js"]
    }
  }
}
```

### MAJOR: Duplicate server name

**Error message**: `Duplicate server name: {server_name}`
**Severity**: MAJOR
**Root cause**: Two or more server entries in `mcpServers` share the same key. JSON parsers keep only the last occurrence, so earlier definitions are silently lost.
**Fix**:
1. Rename duplicate servers to have unique keys:
```json
{
  "mcpServers": {
    "my-server-a": { "command": "node", "args": ["a.js"] },
    "my-server-b": { "command": "node", "args": ["b.js"] }
  }
}
```

### MINOR: Server name format

**Error message**: `Server name '{server_name}' should be alphanumeric with hyphens/underscores`
**Severity**: MINOR
**Root cause**: The server name does not match the expected pattern `^[a-zA-Z][a-zA-Z0-9_-]*$`. It may start with a digit, contain spaces, or include special characters.
**Fix**:
1. Rename the server to start with a letter and contain only alphanumeric characters, hyphens, or underscores:
   - Wrong: `"123server"`, `"my server"`, `"my.server"`
   - Correct: `"my-server"`, `"myServer"`, `"my_server_v2"`

### WARNING: Unknown field in server config

**Error message**: `Unknown field '{key}' in server {server_name}`
**Severity**: WARNING
**Root cause**: The server configuration contains a field not in the known set: `command`, `args`, `env`, `cwd`, `type`, `url`, `headers`, `timeout`, `oauth`.
**Fix**:
1. Check for typos in the field name (e.g., `arguments` instead of `args`, `environment` instead of `env`).
2. Remove the unknown field if it is not needed, or verify it is a valid extension field for your MCP client.
3. Known fields reference:
```json
{
  "command": "node",
  "args": ["server.js"],
  "env": { "KEY": "value" },
  "cwd": "${CLAUDE_PLUGIN_ROOT}",
  "type": "stdio",
  "url": "https://example.com/mcp",
  "headers": { "Authorization": "${API_KEY}" },
  "timeout": 30,
  "oauth": { "clientId": "abc123", "callbackPort": 3000 }
}
```

---

## 3. Transport Type Issues

### MAJOR: Invalid transport type

**Error message**: `Invalid transport type '{transport}' for server {server_name}`
**Severity**: MAJOR
**Root cause**: The `type` field contains a value other than `stdio`, `sse`, or `http`.
**Fix**:
1. Set `type` to one of the three valid values:
   - `"stdio"` — local process with stdin/stdout communication (default if `type` is omitted).
   - `"http"` — streamable HTTP transport (recommended for remote servers).
   - `"sse"` — Server-Sent Events transport (deprecated, prefer `http`).
2. Example:
```json
{
  "my-server": {
    "type": "http",
    "url": "https://example.com/mcp"
  }
}
```

### MINOR: SSE transport deprecation (in transport detection)

**Error message**: `Server {server_name} uses 'sse' transport which is deprecated. Consider migrating to 'http' (streamable-http) transport instead.`
**Severity**: MINOR
**Root cause**: The server uses the `sse` transport type, which is deprecated in favor of the `http` (streamable-http) transport.
**Fix**:
1. Change `"type": "sse"` to `"type": "http"`.
2. Verify the remote MCP server supports streamable-http. If it only supports SSE, keep `sse` until the server is upgraded.
3. Before:
```json
{
  "my-server": {
    "type": "sse",
    "url": "https://example.com/mcp/sse"
  }
}
```
4. After:
```json
{
  "my-server": {
    "type": "http",
    "url": "https://example.com/mcp"
  }
}
```

---

## 4. stdio Transport Issues

### CRITICAL: Missing required command field

**Error message**: `Server {server_name} missing required 'command' field`
**Severity**: CRITICAL
**Root cause**: A server with `type: "stdio"` (or no `type` specified, which defaults to stdio) does not have a `command` field. The `command` field tells Claude Code which executable to launch.
**Fix**:
1. Add the `command` field specifying the executable:
```json
{
  "my-server": {
    "command": "node",
    "args": ["${CLAUDE_PLUGIN_ROOT}/server.js"]
  }
}
```
2. Common commands: `node`, `python3`, `npx`, `uvx`, `bunx`, or a path to a binary.

### MAJOR: Command not executable

**Error message**: `Server {server_name} command not executable: {resolved_path}`
**Severity**: MAJOR
**Root cause**: The command file exists on disk but does not have the executable permission bit set.
**Fix**:
1. Grant execute permission:
```bash
chmod +x /path/to/your/command
```
2. If the command is a script, ensure it has a proper shebang line:
```bash
#!/usr/bin/env node
```
or
```bash
#!/usr/bin/env python3
```

### INFO: Command not found in PATH

**Error message**: `Server {server_name} command '{command}' not found (may be resolved at runtime)`
**Severity**: INFO
**Root cause**: The command is not currently available in `$PATH`. It may be installed later, be available in a different environment, or use environment variable substitution at runtime.
**Fix**:
1. Verify the command will be available at runtime. Install it if needed:
   - `npm install -g <package>` for Node.js tools.
   - `pip install <package>` for Python tools.
   - `brew install <package>` on macOS.
2. If the command is bundled with the plugin, use `${CLAUDE_PLUGIN_ROOT}` to reference it:
```json
{
  "command": "${CLAUDE_PLUGIN_ROOT}/bin/my-server"
}
```

### WARNING: Package executor running remote package

**Error message**: `Server {server_name} uses {command} to execute remote package '{pkg_name}' — this downloads and runs code from a registry. Verify the package is trusted and consider pinning a version.`
**Severity**: WARNING
**Root cause**: The command is `npx`, `bunx`, `uvx`, `pipx`, or `pnpx`, and the first argument is a package name (not a local path). This means the server will download and execute code from a public registry every time it starts.
**Fix**:
1. Pin the package to an exact version to prevent supply-chain attacks:
```json
{
  "my-server": {
    "command": "npx",
    "args": ["@modelcontextprotocol/server-filesystem@1.2.3", "/path"]
  }
}
```
2. Alternatively, install the package locally and reference it directly:
```json
{
  "my-server": {
    "command": "node",
    "args": ["${CLAUDE_PLUGIN_ROOT}/node_modules/.bin/mcp-server"]
  }
}
```
3. If you trust the package and want automatic updates, you can leave it as is — but audit the package first.

### INFO: URL present but transport is stdio

**Error message**: `Server {server_name} has 'url' but transport is stdio - url will be ignored`
**Severity**: INFO
**Root cause**: The server has `type: "stdio"` (or defaults to it) but also includes a `url` field. The `url` is only used for `http` or `sse` transports and will be silently ignored.
**Fix**:
1. If you intended a remote server, set the correct transport type:
```json
{
  "my-server": {
    "type": "http",
    "url": "https://example.com/mcp"
  }
}
```
2. If you intended a local stdio server, remove the `url` field:
```json
{
  "my-server": {
    "command": "node",
    "args": ["server.js"]
  }
}
```

---

## 5. HTTP/SSE Transport Issues

### CRITICAL: Missing url for HTTP/SSE server

**Error message**: `Server {server_name} (type={transport}) missing 'url'`
**Severity**: CRITICAL
**Root cause**: A server with `type: "http"` or `type: "sse"` does not have a `url` field. The `url` is required for the client to know where to connect.
**Fix**:
1. Add the `url` field:
```json
{
  "my-server": {
    "type": "http",
    "url": "https://example.com/mcp"
  }
}
```
2. For local development servers:
```json
{
  "my-server": {
    "type": "http",
    "url": "http://localhost:3000/mcp"
  }
}
```

### MAJOR: URL must use http(s) scheme

**Error message**: `Server {server_name} url should be http(s):// : {url}`
**Severity**: MAJOR
**Root cause**: The `url` value does not start with `http://` or `https://` and is not an environment variable reference (`${...}`). It may use an unsupported scheme like `ws://` or `ftp://`, or be a bare hostname.
**Fix**:
1. Prefix the URL with the correct scheme:
```json
{
  "url": "https://example.com/mcp"
}
```
2. If the URL comes from an environment variable:
```json
{
  "url": "${MCP_SERVER_URL}"
}
```

### MAJOR: Unencrypted HTTP for remote server

**Error message**: `Server {server_name} uses unencrypted HTTP for remote server — use HTTPS to protect data in transit.`
**Severity**: MAJOR
**Root cause**: The `url` starts with `http://` and points to a non-localhost address. Data (including tool results and conversation content) will be transmitted in cleartext.
**Fix**:
1. Switch to HTTPS:
```json
{
  "url": "https://example.com/mcp"
}
```
2. If the server does not support HTTPS, set up a TLS-terminating reverse proxy or VPN tunnel.
3. `http://` is acceptable only for localhost addresses (`localhost`, `127.0.0.1`, `[::1]`, `0.0.0.0`).

### WARNING: Remote URL security

**Error message**: `Server {server_name} connects to remote URL '{url}' — remote MCP servers can access tool results and conversation data. Ensure the server is trusted and uses HTTPS.`
**Severity**: WARNING
**Root cause**: The server URL points to a non-localhost address. Remote MCP servers receive tool calls, their arguments, and results, which may contain sensitive project data.
**Fix**:
1. Verify you trust the remote server operator and their data handling practices.
2. Ensure the URL uses HTTPS (not plain HTTP).
3. Consider adding authentication headers:
```json
{
  "my-server": {
    "type": "http",
    "url": "https://example.com/mcp",
    "headers": {
      "Authorization": "Bearer ${MCP_API_KEY}"
    }
  }
}
```

### MINOR: SSE transport deprecated (in http/sse validation block)

**Error message**: `Server {server_name} uses deprecated 'sse' transport - consider migrating to 'http'`
**Severity**: MINOR
**Root cause**: Duplicate deprecation warning emitted in the http/sse validation branch. Same as the transport-type-level SSE deprecation.
**Fix**: Same as [Transport Type Issues > MINOR: SSE transport deprecation](#minor-sse-transport-deprecation-in-transport-detection).

### INFO: Command present but transport is HTTP/SSE

**Error message**: `Server {server_name} has 'command' but transport is {transport} - command will be ignored`
**Severity**: INFO
**Root cause**: The server has `type: "http"` or `type: "sse"` but also includes a `command` field. HTTP/SSE servers connect to a URL, so `command` is not used.
**Fix**:
1. Remove the `command` (and `args`) field if the server is remote:
```json
{
  "my-server": {
    "type": "http",
    "url": "https://example.com/mcp"
  }
}
```
2. If you intended a local stdio server, remove `type` and `url`, and keep `command`:
```json
{
  "my-server": {
    "command": "node",
    "args": ["server.js"]
  }
}
```

---

## 6. Environment Variable Issues

### MAJOR: Malformed env var syntax (unclosed braces)

**Error message**: `Malformed env var syntax (unclosed braces) in {context}`
**Severity**: MAJOR
**Root cause**: The value contains `${` but the number of opening `${` sequences does not match the number of closing `}` braces. This will cause runtime substitution to fail.
**Fix**:
1. Ensure every `${` has a matching `}`:
   - Wrong: `"${MY_VAR"`, `"${MY_VAR}/${OTHER"`
   - Correct: `"${MY_VAR}"`, `"${MY_VAR}/${OTHER}"`
2. If you need a literal `${`, escape it or restructure the value. In most MCP runtimes, `${` always triggers variable substitution.

### INFO: Env var without default value

**Error message**: `Env var ${VAR_NAME} has no default value in {context} - config will fail if not set`
**Severity**: INFO
**Root cause**: An environment variable reference like `${API_KEY}` is used without a default fallback. If the variable is not set in the user's environment, the server will fail to start or receive an empty string.
**Fix**:
1. Add a default value using the `${VAR:-default}` syntax:
```json
{
  "env": {
    "PORT": "${MCP_PORT:-3000}"
  }
}
```
2. Or document in your plugin's README that the variable must be set before use.
3. Note: `CLAUDE_PLUGIN_ROOT` and `CLAUDE_PROJECT_DIR` are automatically set by Claude Code and do not need defaults.

---

## 7. Path Issues

### MAJOR: Absolute path found

**Error message**: `Absolute path found in {context}: {value} - use ${CLAUDE_PLUGIN_ROOT} for portability`
**Severity**: MAJOR
**Root cause**: A field contains a hardcoded absolute path like `/usr/local/bin/server` or `C:\tools\server.exe`. Absolute paths are not portable across machines or installations.
**Fix**:
1. Replace absolute paths with `${CLAUDE_PLUGIN_ROOT}`-relative paths:
   - Wrong: `"/home/user/plugins/my-plugin/server.js"`
   - Correct: `"${CLAUDE_PLUGIN_ROOT}/server.js"`
2. For system binaries, use just the command name and let PATH resolution find it:
   - Wrong: `"/usr/local/bin/node"`
   - Correct: `"node"`
3. Example:
```json
{
  "my-server": {
    "command": "${CLAUDE_PLUGIN_ROOT}/bin/my-server",
    "cwd": "${CLAUDE_PLUGIN_ROOT}"
  }
}
```

### MINOR: Path should use CLAUDE_PLUGIN_ROOT

**Error message**: `Path in {context} should use ${CLAUDE_PLUGIN_ROOT}: {value}`
**Severity**: MINOR
**Root cause**: A value contains a relative path (with `/` or `\`) but does not use `${CLAUDE_PLUGIN_ROOT}`. In a plugin context, relative paths resolve against the current working directory, which may not be the plugin root.
**Fix**:
1. Prefix the path with `${CLAUDE_PLUGIN_ROOT}/`:
   - Wrong: `"scripts/start.sh"`
   - Correct: `"${CLAUDE_PLUGIN_ROOT}/scripts/start.sh"`
2. For command args that are not plugin-relative paths, this warning can be reviewed and ignored if appropriate.

### INFO: Referenced file may not exist

**Error message**: `Referenced file may not exist: {value} (resolved: {resolved_path})`
**Severity**: INFO
**Root cause**: The path uses `${CLAUDE_PLUGIN_ROOT}` and the validator resolved it against the actual plugin directory, but the target file was not found on disk.
**Fix**:
1. Verify the file path is correct and the file exists:
```bash
ls -la /path/to/plugin/root/expected/file
```
2. Common causes:
   - Typo in the file name or path.
   - The file has not been built/compiled yet (run `npm run build` or equivalent).
   - The file is created at runtime (this warning can be ignored).
3. Ensure the file is included in your plugin's distribution package.

---

## 8. Args / Env / Cwd Field Issues

### MAJOR: args must be an array

**Error message**: `Server {server_name} 'args' must be an array`
**Severity**: MAJOR
**Root cause**: The `args` field is present but is not a JSON array. It may be a string, object, or other type.
**Fix**:
1. Wrap arguments in a JSON array:
   - Wrong: `"args": "--port 3000"`
   - Correct: `"args": ["--port", "3000"]`
2. Each argument must be a separate string element:
```json
{
  "command": "node",
  "args": ["server.js", "--port", "3000", "--verbose"]
}
```

### MAJOR: args element must be a string

**Error message**: `Server {server_name} args[{i}] must be a string`
**Severity**: MAJOR
**Root cause**: An element in the `args` array is not a string. JSON numbers, booleans, objects, or null values are not valid.
**Fix**:
1. Convert all array elements to strings:
   - Wrong: `"args": ["server.js", 3000, true]`
   - Correct: `"args": ["server.js", "3000", "--verbose"]`

### MAJOR: env must be an object

**Error message**: `Server {server_name} 'env' must be an object`
**Severity**: MAJOR
**Root cause**: The `env` field is present but is not a JSON object. It may be an array, string, or other type.
**Fix**:
1. Use a JSON object mapping variable names to values:
   - Wrong: `"env": ["KEY=value"]`
   - Correct: `"env": { "KEY": "value" }`
2. Example:
```json
{
  "env": {
    "NODE_ENV": "production",
    "API_KEY": "${MY_API_KEY}",
    "PORT": "3000"
  }
}
```

### MAJOR: env key must be a string

**Error message**: `Server {server_name} env key must be a string`
**Severity**: MAJOR
**Root cause**: An environment variable key in the `env` object is not a string. In practice, JSON object keys are always strings, so this typically cannot occur with valid JSON. It may indicate a programmatic construction error.
**Fix**:
1. Ensure all keys are strings (they should be by default in JSON):
```json
{
  "env": {
    "MY_VAR": "value"
  }
}
```

### MAJOR: env value must be a string

**Error message**: `Server {server_name} env[{key}] must be a string`
**Severity**: MAJOR
**Root cause**: An environment variable value is not a string. Environment variables are always strings; numbers, booleans, and objects are not valid.
**Fix**:
1. Convert all values to strings:
   - Wrong: `"env": { "PORT": 3000, "DEBUG": true }`
   - Correct: `"env": { "PORT": "3000", "DEBUG": "true" }`
2. Use env var references for secrets:
```json
{
  "env": {
    "API_KEY": "${MY_SECRET_API_KEY}"
  }
}
```

### MAJOR: cwd must be a string

**Error message**: `Server {server_name} 'cwd' must be a string`
**Severity**: MAJOR
**Root cause**: The `cwd` field is present but is not a string.
**Fix**:
1. Set `cwd` to a string path:
```json
{
  "cwd": "${CLAUDE_PLUGIN_ROOT}"
}
```
2. The `cwd` value also goes through path validation, so use `${CLAUDE_PLUGIN_ROOT}` for portability.

---

## 9. Headers Issues

### MAJOR: headers must be an object

**Error message**: `Server {server_name} 'headers' must be an object`
**Severity**: MAJOR
**Root cause**: The `headers` field is present but is not a JSON object.
**Fix**:
1. Use a JSON object mapping header names to string values:
```json
{
  "headers": {
    "Authorization": "Bearer ${API_TOKEN}",
    "X-Custom-Header": "value"
  }
}
```

### MAJOR: Header value must be a string

**Error message**: `Server {server_name} headers[{key}] must be a string`
**Severity**: MAJOR
**Root cause**: A header value is not a string. HTTP header values must be strings.
**Fix**:
1. Convert the value to a string:
   - Wrong: `"headers": { "X-Timeout": 30 }`
   - Correct: `"headers": { "X-Timeout": "30" }`

### MAJOR: Hardcoded credential in headers

**Error message**: `Server {server_name} has hardcoded credential in headers[{key}] - use environment variables`
**Severity**: MAJOR
**Root cause**: A header with a sensitive name (`Authorization`, `X-Api-Key`, or `Api-Key`) contains a literal value instead of an environment variable reference. Hardcoded credentials will be committed to version control and shared with anyone who installs the plugin.
**Fix**:
1. Replace the literal value with an environment variable reference:
   - Wrong:
```json
{
  "headers": {
    "Authorization": "Bearer sk-abc123secret"
  }
}
```
   - Correct:
```json
{
  "headers": {
    "Authorization": "Bearer ${MCP_API_KEY}"
  }
}
```
2. Users set the variable in their environment or Claude Code settings before using the plugin.

---

## 10. Timeout Issues

### MAJOR: Timeout must be a number

**Error message**: `Server {server_name} 'timeout' must be a number, got {type_name}`
**Severity**: MAJOR
**Root cause**: The `timeout` field is present but is not a numeric type (`int` or `float`). It may be a string, boolean, or object.
**Fix**:
1. Set `timeout` to a number (without quotes):
   - Wrong: `"timeout": "30"`
   - Correct: `"timeout": 30`

### MAJOR: Timeout must be positive

**Error message**: `Server {server_name} 'timeout' must be positive`
**Severity**: MAJOR
**Root cause**: The `timeout` value is zero or negative, which is not a meaningful timeout duration.
**Fix**:
1. Set a positive timeout value (in seconds):
```json
{
  "timeout": 30
}
```
2. Choose a timeout appropriate for the server's expected response time. Typical values range from 10 to 120 seconds.

---

## 11. OAuth Issues

### MAJOR: OAuth must be an object

**Error message**: `Server {server_name} 'oauth' must be an object, got {type_name}`
**Severity**: MAJOR
**Root cause**: The `oauth` field is present but is not a JSON object.
**Fix**:
1. Set `oauth` to an object with the appropriate fields:
```json
{
  "my-server": {
    "type": "http",
    "url": "https://example.com/mcp",
    "oauth": {
      "clientId": "your-client-id",
      "callbackPort": 3000
    }
  }
}
```

### MAJOR: oauth.clientId must be a string

**Error message**: `Server {server_name} 'oauth.clientId' must be a string`
**Severity**: MAJOR
**Root cause**: The `clientId` field inside the `oauth` object is present but is not a string.
**Fix**:
1. Set `clientId` to a string:
   - Wrong: `"clientId": 12345`
   - Correct: `"clientId": "12345"`

### MAJOR: oauth.callbackPort must be an integer

**Error message**: `Server {server_name} 'oauth.callbackPort' must be an integer`
**Severity**: MAJOR
**Root cause**: The `callbackPort` field inside the `oauth` object is present but is not an integer. It may be a string or float.
**Fix**:
1. Set `callbackPort` to an integer (without quotes, no decimals):
   - Wrong: `"callbackPort": "3000"` or `"callbackPort": 3000.5`
   - Correct: `"callbackPort": 3000`

---

## 12. Plugin Manifest Issues

These issues are reported by `validate_plugin_mcp()` when checking `.claude-plugin/plugin.json`.

### MAJOR: Referenced MCP config not found

**Error message**: `Referenced MCP config not found: {mcp_servers_path}`
**Severity**: MAJOR
**Root cause**: The `mcpServers` field in `plugin.json` is a string (path reference) pointing to an external config file, but that file does not exist at the resolved location.
**Fix**:
1. Verify the path in `plugin.json`:
```json
{
  "mcpServers": "./.mcp.json"
}
```
2. Ensure the referenced file exists relative to the plugin root:
```bash
ls -la /path/to/plugin/.mcp.json
```
3. Common issues:
   - Typo in the filename (e.g., `mcp.json` vs `.mcp.json`).
   - The file is in a subdirectory but the path is wrong.
   - The file was not committed to the repository.

### MAJOR: mcpServers must be a string or object in plugin.json

**Error message**: `mcpServers must be a string (path) or object`
**Severity**: MAJOR
**Root cause**: The `mcpServers` field in `plugin.json` is neither a string (path to external config) nor an object (inline server definitions). It may be an array, number, boolean, or null.
**Fix**:
1. Use a string to reference an external config:
```json
{
  "mcpServers": "./.mcp.json"
}
```
2. Or use an inline object:
```json
{
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": ["${CLAUDE_PLUGIN_ROOT}/server.js"]
    }
  }
}
```

### CRITICAL: Inline server config must be an object (plugin.json)

**Error message**: `Server '{server_name}' config must be an object`
**Severity**: CRITICAL
**Root cause**: An inline server definition inside `plugin.json`'s `mcpServers` object is not a JSON object.
**Fix**: Same as [Server Definition Issues > CRITICAL: Server config must be an object](#critical-server-config-must-be-an-object).

### INFO: Found inline mcpServers in plugin.json

**Error message**: `Found inline mcpServers in plugin.json ({N} server(s))`
**Severity**: INFO
**Root cause**: Informational — reports that inline MCP server definitions were found in `plugin.json`. No action needed.

---

## 12a. Redundant `mcpServers` pointing at default `.mcp.json`

### MINOR: `mcpServers` redundancy nudge

**Error message**: `Field 'mcpServers' points to './.mcp.json' which Claude Code auto-discovers at the plugin root. This is redundant ...`
**Severity**: MINOR
**File**: `.claude-plugin/plugin.json`
**Root cause**: When `plugin.json` has `"mcpServers": "./.mcp.json"`, the override is pointing at the file that's already auto-discovered. Empirically (test `cpv-mcp-default-path-test`, 2026-04-18) CC silently accepts this and loads the file once at runtime — but the declaration is redundant and confusing. Unlike the analogous `hooks` case (which CASCADES to disable MCP), this MCP redundancy does not cause runtime failures. CPV emits MINOR as a defensive nudge.

**Important side effect**: When this nudge fires, CPV will ALSO emit a MAJOR cross-source duplicate for every server declared in `.mcp.json` (see §13). That's because the same file is "loaded" from two source labels in CPV's accounting (auto-discovery + override path). Removing the redundant field fixes both findings at once.

**Fix**:
1. Remove the `mcpServers` field entirely (the default file will still load automatically):
   ```diff
   {
     "name": "my-plugin",
   - "mcpServers": "./.mcp.json"
   }
   ```
2. Or, if you actually need an additional MCP config file, point at a non-default path:
   ```json
   {
     "mcpServers": "./extras/mcp.json"
   }
   ```

---

## 13. Cross-Source Duplicate Server Names

A plugin can declare MCP servers in multiple sources simultaneously — `.mcp.json` at plugin root, inline `mcpServers: {...}` in `plugin.json`, and a path-string `mcpServers: "./path/to/file.json"` in `plugin.json` may all coexist. **However, every server name must be unique across ALL sources.** The same server name (the key in `mcpServers`) declared in two sources is a configuration conflict.

### MAJOR: MCP server declared in multiple sources

**Error message**: `MCP server '{name}' is declared in {source1} and {source2} — server names must be unique across all MCP sources`
**Severity**: MAJOR
**Root cause**: The same server name (e.g. `database-tools`) appears in more than one MCP declaration source within the same plugin. Claude Code does not specify which source wins for runtime resolution; in practice this either causes the server to fail to load, or one declaration is silently shadowed, leaving the user with a "phantom" config that does nothing.

**How sources are identified in the message:**
| Label | Meaning |
|---|---|
| `.mcp.json` | The auto-discovered config at plugin root |
| `plugin.json:mcpServers` | An inline `mcpServers: {...}` dict in `plugin.json` |
| `plugin.json:mcpServers -> ./<path>` | A path-string `mcpServers: "./<path>"` in `plugin.json` pointing to an external config file |

**Fix**:
1. Identify the duplicated server name(s) from the error message.
2. Decide which source should "own" the server. Default preference (when the user has no specific reason to split):
   - **Inline `plugin.json:mcpServers`** — single source of truth alongside the rest of the manifest. Easiest to maintain.
   - **`.mcp.json`** — pick this if you want the MCP config in a standalone file that can be reused outside the plugin context.
   - **External file via path-string** — pick this only if you have a specific reason (e.g. generated config, shared between multiple plugins).
3. Remove the duplicate entry from the OTHER source(s). Do NOT just rename — rename only if the user actually wants two different servers.
4. Re-validate.

**Example — duplicate**:
`.mcp.json`:
```json
{
  "mcpServers": {
    "database-tools": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/db-server"
    }
  }
}
```
`plugin.json`:
```json
{
  "name": "my-plugin",
  "mcpServers": {
    "database-tools": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/db-server"
    },
    "api-tools": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/api-server"
    }
  }
}
```
CPV emits: `MCP server 'database-tools' is declared in .mcp.json and plugin.json:mcpServers — server names must be unique across all MCP sources`.

**Example — fixed (consolidated into plugin.json)**:
Delete `.mcp.json` entirely (since `database-tools` was its only entry). Keep `plugin.json` as-is. `database-tools` and `api-tools` are now both declared in one place and there is no conflict.

**Example — fixed (kept in .mcp.json, removed from plugin.json)**:
`plugin.json`:
```json
{
  "name": "my-plugin",
  "mcpServers": {
    "api-tools": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/api-server"
    }
  }
}
```
`.mcp.json` keeps `database-tools`. Each server name now lives in exactly one source.

**Verification**:
After fixing, re-run `validate_mcp.py` (or `cpv-validate-plugin <path>`) and confirm the MAJOR is gone.

**Why this is MAJOR (not CRITICAL)**: the plugin will load, but the server may behave unpredictably at runtime. Fix before publishing.

---

## Quick Reference: Severity Levels

| Severity | Exit Code | Meaning |
|----------|-----------|---------|
| CRITICAL | 1 | Configuration is broken and will not work at all |
| MAJOR | 2 | Configuration has serious issues that will likely cause failures |
| MINOR | 3 | Configuration works but has quality/portability issues |
| WARNING | — | Security or best-practice concern that should be reviewed |
| INFO | — | Informational message, no action required |

---

## Minimal Valid Configurations

### stdio server (local process)
```json
{
  "mcpServers": {
    "my-server": {
      "command": "node",
      "args": ["${CLAUDE_PLUGIN_ROOT}/server.js"]
    }
  }
}
```

### http server (remote)
```json
{
  "mcpServers": {
    "my-server": {
      "type": "http",
      "url": "https://example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_API_KEY}"
      }
    }
  }
}
```

### http server (localhost, no auth)
```json
{
  "mcpServers": {
    "my-server": {
      "type": "http",
      "url": "http://localhost:3000/mcp"
    }
  }
}
```

### Full-featured stdio server
```json
{
  "mcpServers": {
    "my-server": {
      "command": "${CLAUDE_PLUGIN_ROOT}/bin/server",
      "args": ["--port", "3000", "--config", "${CLAUDE_PLUGIN_ROOT}/config.json"],
      "env": {
        "NODE_ENV": "production",
        "API_KEY": "${MY_API_KEY:-default_key}"
      },
      "cwd": "${CLAUDE_PLUGIN_ROOT}",
      "timeout": 30
    }
  }
}
```

### OAuth-enabled http server
```json
{
  "mcpServers": {
    "my-server": {
      "type": "http",
      "url": "https://example.com/mcp",
      "oauth": {
        "clientId": "my-app-client-id",
        "callbackPort": 8080
      }
    }
  }
}
```
