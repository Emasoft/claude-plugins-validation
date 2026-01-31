---
name: cpv-validate-lsp
description: |
  Validate LSP (Language Server Protocol) server configurations in Claude Code.
  Checks language server definitions, initialization options, and workspace settings.
  Use when configuring LSP servers for code intelligence features.
allowed-tools: Read, Bash, Glob, Grep
argument-hint: "<path> [--verbose] [--json]"
user-invocable: true
---

# /cpv-validate-lsp Command

Validates LSP (Language Server Protocol) server configurations.

## Usage

```
/cpv-validate-lsp <path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `path` | Yes | Path to LSP config file or plugin directory |

## Options

| Option | Description |
|--------|-------------|
| `--verbose` | Show all checks including passed |
| `--json` | Output results as JSON |

## What Gets Validated

### 1. Configuration Structure
- Valid JSON/YAML syntax
- Required fields present
- Server command/path validation

### 2. Language Server Fields
Standard LSP server configuration:
- `command` - Server executable path
- `args` - Command-line arguments
- `filetypes` - Associated file types
- `rootPatterns` - Root directory indicators
- `initializationOptions` - Server initialization options
- `settings` - Workspace settings

### 3. Path Validation
- Validates server executable exists
- Checks for absolute vs relative paths
- Environment variable syntax validation

### 4. Common Language Servers
Validates configurations for common LSP servers:
- `pyright` / `pylsp` - Python
- `typescript-language-server` - TypeScript/JavaScript
- `rust-analyzer` - Rust
- `gopls` - Go
- `clangd` - C/C++

## Examples

### Validate LSP Config

```
/cpv-validate-lsp ./.vscode/settings.json
```

### Validate Plugin LSP

```
/cpv-validate-lsp ./my-plugin/
```

### Verbose Output

```
/cpv-validate-lsp ./lsp-config.json --verbose
```

## Configuration Example

```json
{
  "languageServers": {
    "python": {
      "command": "pyright-langserver",
      "args": ["--stdio"],
      "filetypes": ["python"],
      "rootPatterns": ["pyproject.toml", "setup.py"],
      "initializationOptions": {},
      "settings": {
        "python.analysis.typeCheckingMode": "basic"
      }
    }
  }
}
```

## Status

**Note**: LSP validation is a newer feature. The validator currently performs basic
structural validation. Additional semantic validation is planned for future releases.

Currently validated:
- ✅ JSON/YAML structure
- ✅ Required fields presence
- ✅ Path format validation
- ⏳ Server executable verification (planned)
- ⏳ Initialization options schema validation (planned)

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | CRITICAL issues |
| 2 | MAJOR issues |
| 3 | MINOR issues |

## Execution

When LSP validator is available:

```bash
uv run python scripts/validate_lsp.py "$PATH" $OPTIONS
```

Currently falls back to basic JSON/structure validation.

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-mcp` - MCP server validation (similar protocol validation)
- `/cpv-validate-hooks` - Hook validation
- `/cpv-validate-skill` - Skill validation
