---
name: cpv-validate-lsp
description: Validate LSP server config in a plugin
allowed-tools: Read, Bash, Glob, Grep, Task, AskUserQuestion
argument-hint: "<path_or_plugin_name> [--verbose] [--json]"
agent: plugin-validator
user-invocable: true
---

# /cpv-validate-lsp Command

Validates LSP (Language Server Protocol) server configurations.

## Privacy Check (REQUIRED)

Before running validation, ensure private path detection is configured:

1. **Auto-detect username**: `python3 -c "import getpass; print(getpass.getuser())"`
2. **If auto-detection fails**, ask the user for their system username
3. **Pass to script**: `CLAUDE_PRIVATE_USERNAMES="username" uv run python scripts/...`

## Usage

```
/cpv-validate-lsp <path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `path_or_plugin_name` | Yes | Path to LSP config file, plugin directory, OR just the plugin name for auto-discovery |

### Auto-Discovery

If you provide just a name (e.g., `my-plugin`), the agent will search for LSP config in:
1. Plugin directory (`./my-plugin/`)
2. VS Code settings (`./.vscode/settings.json`)
3. OUTPUT_SKILLS plugins (`./OUTPUT_SKILLS/my-plugin/`)

If multiple matches are found, you'll be asked to choose.

### Typo Tolerance

Names are normalized before searching:
- Converted to lowercase: `My-Plugin` → `my-plugin`
- Underscores become hyphens: `my_plugin` → `my-plugin`

If no exact match is found, fuzzy matching is used (e.g., `valdiate-lsp` → `validate-lsp`).
**Fuzzy matches always require your confirmation before proceeding.**

## Options

| Option | Description |
|--------|-------------|
| `--strict` | Treat NIT issues as blocking (exit 4) |
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

## Output & Exit Codes

Uses standard CPV severity levels and exit codes. With `--report`, saves full output to file and prints only a compact summary. See `/cpv-validate-plugin` for details.

## Execution

When LSP validator is available:

```bash
uv run python scripts/validate_lsp.py "$PATH" $OPTIONS --report docs_dev/validate_lsp_$(date +%Y%m%d).md
```

Currently falls back to basic JSON/structure validation.

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-mcp` - MCP server validation (similar protocol validation)
- `/cpv-validate-hooks` - Hook validation
- `/cpv-validate-skill` - Skill validation
