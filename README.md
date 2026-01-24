# Claude Plugins Validation

> **Installation:** This plugin is distributed via the [Emasoft Plugins Marketplace](https://github.com/Emasoft/emasoft-plugins).
> See [Installation](#installation) below for instructions.

Comprehensive validation suite for Claude Code plugins, marketplaces, hooks, skills, and MCP servers.

## Overview

This plugin provides:

- **Validation Scripts**: Python scripts for validating all plugin components
- **Expert Agent**: `plugin-validator` agent for interactive validation
- **Documentation Skill**: `plugin-validation-skill` with detailed reference guides

## Installation

### Install from Emasoft Marketplace (Recommended)

```bash
# Step 1: Add the Emasoft marketplace
claude plugin marketplace add https://github.com/Emasoft/emasoft-plugins

# Step 2: Install this plugin
claude plugin install claude-plugins-validation@emasoft-plugins

# Step 3: Verify installation
claude plugin list

# Step 4: Restart Claude Code
```

For detailed installation instructions, troubleshooting, and updates, see the **[Emasoft Plugins Marketplace README](https://github.com/Emasoft/emasoft-plugins#readme)**.

### Local Development (for contributors)

```bash
# Use directly without installation
claude --plugin-dir /path/to/claude-plugins-validation
```

## Usage

### Validate a Plugin

```bash
cd /path/to/claude-plugins-validation
uv run python scripts/validate_plugin.py /path/to/your-plugin --verbose
```

### Validate Specific Components

```bash
# Validate skills
uv run python scripts/validate_skill.py /path/to/skill-dir

# Validate hooks
uv run python scripts/validate_hook.py /path/to/hooks.json

# Validate MCP configuration
uv run python scripts/validate_mcp.py /path/to/plugin

# Validate marketplace
uv run python scripts/validate_marketplace.py /path/to/marketplace
```

### Use the Agent

Ask Claude to use the `plugin-validator` agent:

> "Use the plugin-validator agent to validate my atlas-orchestrator plugin"

### Use the Skill

Reference the skill for guidance:

> "I need help validating my plugin hooks. Can you use the plugin-validation-skill?"

## Exit Codes

All validation scripts return consistent exit codes:

| Code | Meaning | Description |
|------|---------|-------------|
| 0 | Passed | All checks passed |
| 1 | Critical | Plugin unusable - must fix |
| 2 | Major | Some features may fail - should fix |
| 3 | Minor | Warnings only - recommended to fix |

## Validation Coverage

### Plugin Validation (`validate_plugin.py`)

- Plugin manifest (`.claude-plugin/plugin.json`)
- Directory structure
- Component references (commands, agents, skills)
- Hook configurations
- MCP server definitions
- Script linting (Python via ruff, shell via shellcheck)

### Hook Validation (`validate_hook.py`)

- JSON structure
- Valid event types (13 supported)
- Matcher syntax
- Script paths and executability
- Hook type configuration

### Skill Validation (`validate_skill.py`)

- SKILL.md existence
- Frontmatter YAML validity
- Required fields (name, description)
- Claude Code specific fields (context, agent, user-invocable)

### MCP Validation (`validate_mcp.py`)

- `.mcp.json` structure
- Transport types (stdio, http, sse)
- Required fields per transport
- Environment variable syntax
- Path portability

### Marketplace Validation (`validate_marketplace.py`)

- `marketplace.json` structure
- Required fields (name, plugins)
- Plugin entry validation
- Source type configuration
- Local path resolution

## Directory Structure

```
claude-plugins-validation/
├── .claude-plugin/
│   └── plugin.json           # Plugin manifest
├── agents/
│   └── plugin-validator.md   # Expert validation agent
├── skills/
│   └── plugin-validation-skill/
│       ├── SKILL.md          # Main skill file
│       └── references/       # Detailed reference docs
│           ├── plugin-structure.md
│           ├── hook-validation.md
│           ├── skill-validation.md
│           ├── mcp-validation.md
│           └── marketplace-validation.md
├── scripts/
│   ├── validate_plugin.py    # Main plugin validator
│   ├── validate_skill.py     # Skill validator
│   ├── validate_hook.py      # Hook validator
│   ├── validate_mcp.py       # MCP server validator
│   └── validate_marketplace.py # Marketplace validator
└── README.md
```

## Requirements

- Python 3.10+
- uv (Python package manager)

### Multi-Language Linter Support

The plugin-validator agent automatically detects all languages used in a plugin and installs required linters:

| Language | File Extensions | Linters | Install Command |
|----------|-----------------|---------|-----------------|
| Python | `.py` | ruff, mypy | `uv tool install --python 3.12 ruff && uv tool install --python 3.12 mypy` |
| JavaScript | `.js`, `.mjs`, `.cjs` | eslint | `npm install -g eslint` |
| TypeScript | `.ts`, `.tsx` | eslint, typescript | `npm install -g eslint typescript` |
| Rust | `.rs` | clippy, rustfmt | `rustup component add clippy rustfmt` |
| Go | `.go` | staticcheck, golangci-lint | `go install honnef.co/go/tools/cmd/staticcheck@latest` |
| Shell/Bash | `.sh`, `.bash` | shellcheck | `brew install shellcheck` or `uv tool install --python 3.12 shellcheck-py` |
| PowerShell | `.ps1`, `.psm1`, `.psd1` | PSScriptAnalyzer | `pwsh -Command "Install-Module PSScriptAnalyzer -Scope CurrentUser"` |
| Ruby | `.rb` | rubocop | `gem install rubocop` |

The agent will:
1. Scan the plugin for all file extensions
2. Detect which languages are present
3. Check if required linters are installed
4. Auto-install missing linters before validation

### Multi-Language Dependency Verification

The agent verifies dependencies for all languages found in the plugin:

| Language | Dependency Files | Verification |
|----------|------------------|--------------|
| Python | `requirements.txt`, `pyproject.toml`, `setup.py`, `Pipfile` | Scans imports, checks declarations |
| JavaScript/TypeScript | `package.json` | Scans require/import, checks dependencies |
| Rust | `Cargo.toml` | Scans use statements, checks dependencies |
| Go | `go.mod` | Scans imports, checks module requirements |
| Shell/Bash | Shebang lines, `source` statements | Checks command availability |
| PowerShell | `.psd1` manifests, `#Requires` statements | Checks module availability |
| Ruby | `Gemfile` | Scans require statements, checks gems |

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes
4. Run validation on your changes: `uv run python scripts/validate_plugin.py .`
5. Submit a pull request

## License

MIT License - see LICENSE file

## Author

Emasoft (713559+Emasoft@users.noreply.github.com)

## Related Resources

- [Claude Code Documentation](https://code.claude.com/docs/)
- [MCP Protocol Specification](https://modelcontextprotocol.io)
- [OpenSpec Agent Skills](https://github.com/agentskills/agentskills)
