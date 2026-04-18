# LSP Configuration — Validation Issues and Fixes

## Table of Contents

- [1. Config File Issues](#1-config-file-issues)
- [2. Server-Level Structure Issues](#2-server-level-structure-issues)
- [3. Unknown Fields](#3-unknown-fields)
- [4. command Field Issues](#4-command-field-issues)
- [5. extensionToLanguage Field Issues](#5-extensiontolanguage-field-issues)
- [6. args Field Issues](#6-args-field-issues)
- [7. filetypes Field Issues](#7-filetypes-field-issues)
- [8. rootPatterns Field Issues](#8-rootpatterns-field-issues)
- [9. initializationOptions and settings Field Issues](#9-initializationoptions-and-settings-field-issues)
- [10. env Field Issues](#10-env-field-issues)
- [11. cwd Field Issues](#11-cwd-field-issues)
- [12. transport Field Issues](#12-transport-field-issues)
- [13. Timeout Field Issues](#13-timeout-field-issues)
- [14. maxRestarts Field Issues](#14-maxrestarts-field-issues)
- [15. restartOnCrash Field Issues](#15-restartoncrash-field-issues)
- [16. Environment Variable Syntax Issues](#16-environment-variable-syntax-issues)
- [17. Path Value Issues](#17-path-value-issues)
- [18. Informational Messages](#18-informational-messages)

## Checklist

- [ ] Identify the LSP config file (`.lsp.json` or inline in `plugin.json`)
- [ ] Match the finding to a numbered section below
- [ ] Verify the language server binary is installed locally (`which <command>`)
- [ ] Apply the fix to the config
- [ ] Re-validate

## Overview

Comprehensive remediation guide for all issues detected by `validate_lsp.py`.

The LSP validator checks Language Server Protocol server configuration files for Claude Code plugins. It validates:

- JSON structure of config files
- Required and recommended fields for each server definition
- Path portability (no hardcoded absolute paths)
- Environment variable syntax
- Field type correctness
- Transport, timeout, and restart settings

**Config file locations checked** (in order): `.lsp.json`, `lsp.json`, `lsp-config.json`, `.vscode/settings.json`

**Known LSP server keys**: `command`, `args`, `filetypes`, `rootPatterns`, `initializationOptions`, `settings`, `env`, `cwd`, `transport`, `extensionToLanguage`, `workspaceFolder`, `startupTimeout`, `shutdownTimeout`, `restartOnCrash`, `maxRestarts`

---

## 1. Config File Issues

### [CRITICAL] Invalid JSON in {file}: {error}
**Source**: `validate_lsp.py` — `validate_lsp_config()`
**What it means**: The LSP configuration file exists but is not valid JSON. It cannot be parsed.
**How to fix**:
1. Validate the JSON:
   ```bash
   python3 -m json.tool path/to/lsp-config.json
   ```
2. Fix the reported error (common issues: trailing commas, missing quotes, mismatched brackets).
3. Use a JSON linter or editor with JSON validation support.

---

### [CRITICAL] '{servers_key}' must be an object in {file}
**Source**: `validate_lsp.py` — `validate_lsp_config()`
**What it means**: The `languageServers`, `lspServers`, or `servers` key exists but its value is not a JSON object (it must be a dictionary mapping server names to configurations).
**How to fix**:
1. Change the structure to an object:
```json
{
  "languageServers": {
    "pyright": {
      "command": "pyright-langserver",
      "args": ["--stdio"]
    }
  }
}
```

---

### [CRITICAL] Server '{server_name}' config must be an object
**Source**: `validate_lsp.py` — `validate_lsp_config()`
**What it means**: A server entry in the `languageServers` object is not a JSON object (it may be a string, array, or null).
**How to fix**:
1. Ensure each server is defined as an object:
```json
{
  "languageServers": {
    "pyright": {
      "command": "pyright-langserver",
      "args": ["--stdio"]
    }
  }
}
```

---

## 2. Server-Level Structure Issues

*(No additional server-level structural messages beyond the config file issues above — individual field issues are listed in their respective sections.)*

---

## 3. Unknown Fields

### [WARNING] Unknown field '{key}' in server {server_name}
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: A field in the server configuration is not a recognized LSP field. It may be a typo, a custom extension, or a deprecated field.
**How to fix**:
1. Check the field name against the known fields: `command`, `args`, `filetypes`, `rootPatterns`, `initializationOptions`, `settings`, `env`, `cwd`, `transport`, `extensionToLanguage`, `workspaceFolder`, `startupTimeout`, `shutdownTimeout`, `restartOnCrash`, `maxRestarts`.
2. Remove unknown fields or correct typos:
   ```json
   // BAD:
   { "comand": "pyright-langserver" }
   // GOOD:
   { "command": "pyright-langserver" }
   ```
3. If the field is intentional (e.g., a server-specific extension), this warning can be noted but the config will still work.

---

## 4. command Field Issues

### [CRITICAL] Server {server_name} missing required 'command' field
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: Every LSP server definition must specify the `command` field — the executable to launch as the language server. Without it the server cannot be started.
**How to fix**:
1. Add the `command` field:
```json
{
  "languageServers": {
    "pyright": {
      "command": "pyright-langserver",
      "args": ["--stdio"]
    }
  }
}
```
2. The command can be:
   - An executable name (found via `$PATH`): `"pyright-langserver"`
   - A path using `${CLAUDE_PLUGIN_ROOT}`: `"${CLAUDE_PLUGIN_ROOT}/bin/my-server"`
   - A runtime invocation: `"npx"` with args `["pyright-langserver", "--stdio"]`

---

### [CRITICAL] Server {server_name} 'command' must be a string
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `command` field exists but is not a string value (e.g., it is an array or number).
**How to fix**:
1. Set `command` to a string:
```json
{ "command": "pyright-langserver" }
```
2. If you need to pass arguments, use the `args` field:
```json
{
  "command": "npx",
  "args": ["pyright-langserver", "--stdio"]
}
```

---

### [MAJOR] Server {server_name} command not executable
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The command path was resolved using `${CLAUDE_PLUGIN_ROOT}` and the file exists, but it does not have execute permissions.
**How to fix**:
1. Make the file executable:
   ```bash
   chmod +x path/to/server-binary
   ```
2. Verify:
   ```bash
   ls -la path/to/server-binary
   # Should show: -rwxr-xr-x
   ```

---

### [INFO] Server {server_name} command '{command}' not found in PATH
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The command string is not an absolute/plugin-relative path and was not found in `$PATH`. This could mean the server is not installed, or the PATH in the CI environment is different.
**How to fix**:
1. Install the language server:
   ```bash
   npm install -g pyright          # for pyright
   pip install python-lsp-server   # for pylsp
   go install golang.org/x/tools/gopls@latest  # for gopls
   ```
2. Or specify the full path using `${CLAUDE_PLUGIN_ROOT}`:
   ```json
   { "command": "${CLAUDE_PLUGIN_ROOT}/node_modules/.bin/pyright-langserver" }
   ```
3. Or document the installation requirement in the plugin README.

---

## 5. extensionToLanguage Field Issues

### [MINOR] Server '{server_name}' missing recommended 'extensionToLanguage' field - maps file extensions to language IDs (e.g., {".go": "go"})
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `extensionToLanguage` field is recommended by the official Claude Code plugin docs to map file extensions to language IDs. It helps Claude know which files this server handles.
**How to fix**:
1. Add `extensionToLanguage` mapping:
```json
{
  "command": "pyright-langserver",
  "extensionToLanguage": {
    ".py": "python",
    ".pyi": "python"
  }
}
```
Common mappings:
- `.py` → `python`
- `.ts`, `.tsx` → `typescript`
- `.js`, `.jsx` → `javascript`
- `.go` → `go`
- `.rs` → `rust`

---

### [CRITICAL] Server '{server_name}' 'extensionToLanguage' must be an object mapping extensions to language IDs
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `extensionToLanguage` field exists but is not a JSON object.
**How to fix**:
1. Set it to a proper key-value object:
```json
{
  "extensionToLanguage": {
    ".py": "python",
    ".pyi": "python"
  }
}
```

---

### [MINOR] Server '{server_name}' extension '{ext}' should start with '.'
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: An extension key in `extensionToLanguage` does not start with a dot.
**How to fix**:
1. Add the leading dot:
```json
{
  "extensionToLanguage": {
    ".py": "python"   // Correct
    // NOT: "py": "python"
  }
}
```

---

### [MAJOR] Server '{server_name}' language for '{ext}' must be a string
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: A value in `extensionToLanguage` is not a string (e.g., it is a number or array).
**How to fix**:
1. Ensure all language ID values are strings:
```json
{
  "extensionToLanguage": {
    ".py": "python",   // string - correct
    ".ts": "typescript"  // string - correct
    // NOT: ".ts": 123  // number - wrong
  }
}
```

---

## 6. args Field Issues

### [MAJOR] Server {server_name} 'args' must be an array
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `args` field exists but is not a JSON array.
**How to fix**:
1. Change `args` to an array of strings:
```json
{
  "command": "pyright-langserver",
  "args": ["--stdio"]
}
```

---

### [MAJOR] Server {server_name} args[{i}] must be a string
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: One of the elements in the `args` array is not a string.
**How to fix**:
1. Ensure all args are strings. Convert numbers to strings:
```json
{
  "args": ["--stdio", "--max-memory", "4096"]
  // NOT: ["--stdio", 4096]  // number - wrong
}
```

---

## 7. filetypes Field Issues

### [MAJOR] Server {server_name} 'filetypes' must be an array
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `filetypes` field exists but is not a JSON array.
**How to fix**:
1. Change `filetypes` to an array of strings:
```json
{
  "filetypes": ["python", "python3"]
}
```

---

### [MINOR] Server {server_name} has empty filetypes array
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `filetypes` field is an empty array. Add file type identifiers so Claude knows which file types this server handles.
**How to fix**:
1. Add the relevant file type identifiers:
```json
{
  "filetypes": ["python"]
}
```

---

### [MAJOR] Server {server_name} filetype must be a string
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: One of the elements in the `filetypes` array is not a string.
**How to fix**:
1. Ensure all filetypes are strings:
```json
{
  "filetypes": ["python", "python3"]
  // NOT: [42, "python"]
}
```

---

## 8. rootPatterns Field Issues

### [MAJOR] Server {server_name} 'rootPatterns' must be an array
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `rootPatterns` field exists but is not a JSON array.
**How to fix**:
1. Change `rootPatterns` to an array of strings:
```json
{
  "rootPatterns": ["pyproject.toml", "setup.py", ".git"]
}
```

---

### [MAJOR] Server {server_name} rootPattern must be a string
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: One of the elements in `rootPatterns` is not a string.
**How to fix**:
1. Ensure all root patterns are strings:
```json
{
  "rootPatterns": ["pyproject.toml", "setup.py"]
  // NOT: [{"file": "pyproject.toml"}]  // object - wrong
}
```

---

## 9. initializationOptions and settings Field Issues

### [MAJOR] Server {server_name} 'initializationOptions' must be an object
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `initializationOptions` field exists but is not a JSON object.
**How to fix**:
1. Set `initializationOptions` to a JSON object:
```json
{
  "initializationOptions": {
    "pythonPath": "${CLAUDE_PLUGIN_ROOT}/.venv/bin/python"
  }
}
```

---

### [MAJOR] Server {server_name} 'settings' must be an object
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `settings` field exists but is not a JSON object.
**How to fix**:
1. Set `settings` to a JSON object:
```json
{
  "settings": {
    "python.analysis.typeCheckingMode": "basic"
  }
}
```

---

## 10. env Field Issues

### [MAJOR] Server {server_name} 'env' must be an object
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `env` field exists but is not a JSON object.
**How to fix**:
1. Set `env` to an object mapping environment variable names to string values:
```json
{
  "env": {
    "PYTHONPATH": "${CLAUDE_PLUGIN_ROOT}/src",
    "NODE_ENV": "production"
  }
}
```

---

### [MAJOR] Server {server_name} env[{key}] must be a string
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: One of the values in the `env` object is not a string. All environment variable values must be strings.
**How to fix**:
1. Convert numeric or boolean values to strings:
```json
{
  "env": {
    "PORT": "8080",       // string - correct
    "DEBUG": "true"       // string - correct
    // NOT: "PORT": 8080  // number - wrong
  }
}
```

---

## 11. cwd Field Issues

### [MAJOR] Server {server_name} 'cwd' must be a string
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `cwd` field exists but is not a string.
**How to fix**:
1. Set `cwd` to a string path:
```json
{
  "cwd": "${CLAUDE_PLUGIN_ROOT}"
}
```

---

## 12. transport Field Issues

### [MAJOR] Server '{server_name}' 'transport' must be 'stdio' or 'socket', got '{value}'
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `transport` field contains an unrecognized value. Only `stdio` and `socket` are valid.
**How to fix**:
1. Use one of the valid transport types:
```json
{
  "transport": "stdio"    // Most common - server uses stdin/stdout
  // or:
  "transport": "socket"   // Server uses a network socket
}
```
Note: Most language servers use `stdio`. Only use `socket` if the server documentation explicitly requires it.

---

## 13. Timeout Field Issues

### [MAJOR] Server '{server_name}' '{timeout_field}' must be a number (milliseconds)
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `startupTimeout` or `shutdownTimeout` field exists but is not a number. Timeout values must be integers or floats representing milliseconds.
**How to fix**:
1. Set the timeout to a number (in milliseconds):
```json
{
  "startupTimeout": 10000,    // 10 seconds
  "shutdownTimeout": 5000     // 5 seconds
  // NOT: "startupTimeout": "10s"  // string - wrong
}
```

---

### [MAJOR] Server '{server_name}' '{timeout_field}' must be positive
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `startupTimeout` or `shutdownTimeout` field is zero or negative. Timeout values must be greater than zero.
**How to fix**:
1. Set a positive timeout value:
```json
{
  "startupTimeout": 10000,   // 10 seconds - positive
  "shutdownTimeout": 5000    // 5 seconds - positive
  // NOT: "startupTimeout": 0   // zero - wrong
  // NOT: "startupTimeout": -1  // negative - wrong
}
```

---

## 14. maxRestarts Field Issues

### [MAJOR] Server '{server_name}' 'maxRestarts' must be an integer
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `maxRestarts` field exists but is not an integer value.
**How to fix**:
1. Set `maxRestarts` to a non-negative integer:
```json
{
  "maxRestarts": 3,    // Restart up to 3 times
  "maxRestarts": 0     // Never auto-restart
  // NOT: "maxRestarts": 3.5  // float - wrong
  // NOT: "maxRestarts": "3"  // string - wrong
}
```

---

### [MAJOR] Server '{server_name}' 'maxRestarts' must be non-negative
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `maxRestarts` field is a negative integer. It must be zero (no restarts) or positive.
**How to fix**:
1. Use 0 (disabled) or a positive integer:
```json
{
  "maxRestarts": 3     // Allow up to 3 restarts
  // NOT: "maxRestarts": -1  // negative - wrong
}
```

---

## 15. restartOnCrash Field Issues

### [MAJOR] Server '{server_name}' 'restartOnCrash' must be a boolean
**Source**: `validate_lsp.py` — `validate_lsp_server()`
**What it means**: The `restartOnCrash` field exists but is not a JSON boolean value.
**How to fix**:
1. Use JSON boolean literals (lowercase, no quotes):
```json
{
  "restartOnCrash": true
  // or:
  "restartOnCrash": false
  // NOT: "restartOnCrash": "true"  // string - wrong
  // NOT: "restartOnCrash": 1       // number - wrong
}
```

---

## 16. Environment Variable Syntax Issues

### [MAJOR] Malformed env var syntax (unclosed braces) in {context}
**Source**: `validate_lsp.py` — `validate_env_var_syntax()`
**What it means**: A string value contains `${` but the number of opening `${` and closing `}` do not match. This means an environment variable reference is not properly closed.
**How to fix**:
1. Ensure every `${` has a matching `}`:
```json
{
  "command": "${CLAUDE_PLUGIN_ROOT}/bin/server"
  // NOT: "${CLAUDE_PLUGIN_ROOT/bin/server"   // missing closing }
}
```
2. Count `${` and `}` manually if needed.

---

### [INFO] Env var ${var_name} has no default value in {context}
**Source**: `validate_lsp.py` — `validate_env_var_syntax()`
**What it means**: A string uses `${VAR_NAME}` (without a `:-default`) and `VAR_NAME` is not one of the built-in plugin variables (`CLAUDE_PLUGIN_ROOT`, `CLAUDE_PROJECT_DIR`). If `VAR_NAME` is unset at runtime, the expansion will be empty, potentially causing issues.
**How to fix**:
1. Add a default value using `:-`:
```json
{
  "command": "${MY_SERVER_PATH:-/usr/local/bin/my-server}"
}
```
2. Or document that the environment variable must be set before running the plugin.
3. Or use `${CLAUDE_PLUGIN_ROOT}` for paths relative to the plugin.

---

## 17. Path Value Issues

### [MAJOR] Absolute path found in {context}: {value} - use ${CLAUDE_PLUGIN_ROOT} for portability
**Source**: `validate_lsp.py` — `validate_path_value()`
**What it means**: A path value (in `command`, `cwd`, args, or env values) contains a hardcoded absolute path like `/usr/local/bin/server` or `C:\tools\server.exe`. Absolute paths break portability — the plugin will only work on one specific machine.
**How to fix**:
1. Replace absolute paths with `${CLAUDE_PLUGIN_ROOT}`-relative paths:
```json
{
  "command": "${CLAUDE_PLUGIN_ROOT}/bin/my-server"
  // NOT: "/Users/username/projects/plugin/bin/my-server"
}
```
2. Or use a command name that is found via `$PATH` (no path prefix):
```json
{
  "command": "pyright-langserver"
}
```

---

### [INFO] Referenced file may not exist: {value}
**Source**: `validate_lsp.py` — `validate_path_value()`
**What it means**: A path using `${CLAUDE_PLUGIN_ROOT}` was resolved against the current plugin root, and the resulting file path does not exist on disk. This is informational — the file may be installed later or may only exist on target systems.
**How to fix**:
1. Verify the file exists at the expected location within the plugin:
   ```bash
   ls path/to/plugin/bin/my-server
   ```
2. If the file should be bundled with the plugin, add it to the plugin directory.
3. If it is installed by a setup script, document this requirement.

---

## 18. Informational Messages

### [INFO] LSP config file not found: {file}
**Source**: `validate_lsp.py` — `validate_lsp_config()`
**What it means**: A specific LSP config file path was checked but does not exist. Informational only.
**How to fix**: No fix required if the file is intentionally absent. To create an LSP config, create one of: `.lsp.json`, `lsp.json`, `lsp-config.json`.

---

### [INFO] No language server definitions found in {file}
**Source**: `validate_lsp.py` — `validate_lsp_config()`
**What it means**: The config file is valid JSON but does not contain any of the recognized server keys (`languageServers`, `lspServers`, `servers`). The validator has nothing to check.
**How to fix**:
1. Add a `languageServers` key:
```json
{
  "languageServers": {
    "pyright": {
      "command": "pyright-langserver",
      "args": ["--stdio"]
    }
  }
}
```

---

### [INFO] No LSP servers defined in {file}
**Source**: `validate_lsp.py` — `validate_lsp_config()`
**What it means**: The `languageServers` key exists but its object is empty `{}`.
**How to fix**:
1. Add server definitions or remove the empty `languageServers` key.

---

### [INFO] Found {N} LSP server(s) in {file}
**Source**: `validate_lsp.py` — `validate_lsp_config()`
**What it means**: Informational. N servers were found and will be validated.
**How to fix**: No action required.

---

### [MINOR] `lspServers` redundancy nudge (override = default `.lsp.json`)

**Source**: `validate_lsp.py` — `validate_plugin_lsp()` (added v2.23.1, mirrors MCP)
**Error message**: `Field 'lspServers' = '<path>' resolves to the auto-discovered '.lsp.json' default at plugin root. This is redundant ...`
**What it means**: When `plugin.json` has `"lspServers": "./.lsp.json"`, the override is pointing at the file that's already auto-discovered. CC silently accepts and loads the file once, but the declaration is redundant and confusing. Analogous to the MCP equivalent (mcp-fixes §12a). Unlike the hooks equivalent (which CASCADES to disable MCP), this LSP redundancy does not cause runtime failures.

**Important side effect**: When this nudge fires, CPV will ALSO emit a MAJOR cross-source duplicate for every server declared in `.lsp.json` (because the same file is "loaded" from two source labels). Removing the redundant field fixes both findings at once.

**How to fix**:
1. Remove the `lspServers` field entirely (the default file will still load):
   ```diff
   {
     "name": "my-plugin",
   - "lspServers": "./.lsp.json"
   }
   ```
2. Or, if you actually need an additional LSP config file, point at a non-default path:
   ```json
   { "lspServers": "./extras/lsp.json" }
   ```

---

### [INFO] No LSP configuration files found
**Source**: `validate_lsp.py` — `validate_plugin_lsp()`
**What it means**: None of the standard LSP config locations (`.lsp.json`, `lsp.json`, `lsp-config.json`, `.vscode/settings.json`) exist in the plugin directory. This is informational — LSP config is optional.
**How to fix**: No fix required if the plugin does not use language servers. To add LSP support, create `lsp.json` in the plugin root.

---

### [MAJOR] LSP server '{name}' is declared in {source1} and {source2} — server names must be unique across all LSP sources

**Source**: `validate_lsp.py` — `validate_plugin_lsp()`
**Empirical evidence**: TRDD-20260418, test plugin `cpv-lsp-coexist-test`. CC's debug log showed `Loaded 3 LSP server(s) from plugin: cpv-lsp-coexist-test` (4 declarations across .lsp.json + plugin.json:lspServers, deduplicated to 3 unique names). The LSP_WINNER probe further established that **inline `plugin.json:lspServers` WINS** when names collide; the losing source's declaration is silently dropped at runtime with no warning from CC.

**What it means**: A plugin has the same LSP server name (e.g., `pyright-lsp`) declared in more than one source — most commonly both `.lsp.json` (auto-discovered at plugin root) AND inline `lspServers` in `plugin.json`. CC silently picks one (inline wins) and discards the other. The plugin author may not realize one configuration is being ignored.

Source labels in the message:
| Label | Meaning |
|---|---|
| `.lsp.json` | The auto-discovered config at plugin root (unwrapped: top-level keys are server names) |
| `plugin.json:lspServers` | Inline `lspServers: {...}` dict in `plugin.json` |
| `lsp.json`, `lsp-config.json`, `.vscode/settings.json` | Other auto-checked LSP config locations |

**How to fix**:
1. Identify the duplicated server name(s) from the error message.
2. Decide which source should "own" the server. Default preference: **inline `plugin.json:lspServers`** (single source of truth alongside the rest of the manifest).
3. Remove the duplicate entry from the OTHER source(s).
4. Re-validate.

**Example — duplicate**:
`.lsp.json`:
```json
{
  "pyright-lsp": {
    "command": "pyright-langserver",
    "args": ["--stdio"],
    "extensionToLanguage": {".py": "python"}
  }
}
```
`plugin.json`:
```json
{
  "name": "my-plugin",
  "lspServers": {
    "pyright-lsp": {
      "command": "pyright-langserver",
      "args": ["--stdio", "--watch"],
      "extensionToLanguage": {".py": "python"}
    },
    "rust-lsp": { "command": "rust-analyzer", "extensionToLanguage": {".rs": "rust"} }
  }
}
```
CPV emits: `LSP server 'pyright-lsp' is declared in .lsp.json and plugin.json:lspServers — server names must be unique across all LSP sources (when collisions occur, the inline plugin.json:lspServers entry WINS per empirical test; the other source is silently dropped)`.

**Example — fixed**: delete `.lsp.json` (since `pyright-lsp` was its only entry); the inline definition in `plugin.json` already covers it. Or, if you wanted `.lsp.json` to win, remove `pyright-lsp` from `plugin.json:lspServers`.

**Why this is MAJOR**: silent shadowing means one of your two configurations is being ignored and you may not realize it. Explicitly choose which source owns each server.
