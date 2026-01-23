# Claude Plugins Validation

Comprehensive validation suite for Claude Code plugins, marketplaces, hooks, skills, and MCP servers.

## Overview

This plugin provides:

- **Validation Scripts**: Python scripts for validating all plugin components
- **Expert Agent**: `plugin-validator` agent for interactive validation
- **Documentation Skill**: `plugin-validation-skill` with detailed reference guides

## Installation

### Via Claude Code CLI

```bash
# Add this marketplace (if published)
claude plugin marketplace add https://github.com/Emasoft/claude-plugins-validation

# Install the plugin
claude plugin install claude-plugins-validation@marketplace-name
```

### Local Development

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

Optional for full linting:
- ruff (Python linting)
- mypy (Python type checking)
- shellcheck (Bash script linting)

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
