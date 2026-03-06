# Claude Plugins Validation

> **Installation:** This plugin is distributed via the [Emasoft Plugins Marketplace](https://github.com/Emasoft/emasoft-plugins).
> See [Installation](#installation) below for instructions.

Comprehensive validation suite for Claude Code plugins, marketplaces, hooks, skills, and MCP servers.

## Overview

This plugin provides:

- **Validation Scripts**: Python scripts for validating all plugin components (190+ rules)
- **Expert Agents**: `plugin-validator` agent for interactive validation and remediation, `skill-validation-agent` for specialized skill validation
- **Skills**: `plugin-validation-skill`, `install-plugin`, `setup-github-marketplace`, `skill-validation-skill`
- **Slash Commands**: 17 commands for validating, installing, and managing plugins
- **Local Plugin Installer**: `claude-plugin-install.py` for installing plugins without a GitHub marketplace

## Installation (Production)

Install from the Emasoft marketplace. Use `--scope user` to make this plugin available globally to all Claude Code instances.

```bash
# Add Emasoft marketplace (first time only)
claude plugin marketplace add emasoft-plugins --url https://github.com/Emasoft/emasoft-plugins

# Install plugin (--scope user = available globally to all Claude Code instances, recommended)
claude plugin install claude-plugins-validation@emasoft-plugins --scope user

# RESTART Claude Code after installing (required!)
```

Utility plugins are installed once with `--scope user` and become available to all Claude Code instances.

This is a utility plugin — it provides validation commands and skills. No `--agent` flag needed; just start Claude Code normally and the validation commands will be available.

## Development Only (--plugin-dir)

`--plugin-dir` loads a plugin directly from a local directory without marketplace installation. Use only during plugin development.

```bash
claude --plugin-dir ./OUTPUT_SKILLS/claude-plugins-validation
```

## Usage

### Slash Commands

| Command | Description |
|---------|-------------|
| `/cpv-validate-plugin` | Comprehensive plugin validation (manifest, hooks, skills, MCP, scripts) |
| `/cpv-validate-hooks` | Validate hook configurations (hooks.json) |
| `/cpv-validate-skill` | Validate skill directories (SKILL.md, frontmatter, references) |
| `/cpv-validate-mcp` | Validate MCP server configurations |
| `/cpv-validate-marketplace` | Validate marketplace configurations |
| `/cpv-validate-agents` | Validate agent definition files |
| `/cpv-validate-lsp` | Validate LSP server configurations |
| `/cpv-validate-command` | Validate command definition files (frontmatter, structure) |
| `/cpv-validate-documentation` | Validate documentation quality (README sections, links, images) |
| `/cpv-validate-encoding` | Validate file encoding (UTF-8, BOM, line endings, control chars) |
| `/cpv-validate-enterprise` | Validate enterprise compliance (author, license, SPDX, tags, context) |
| `/cpv-validate-rules` | Validate rule files (.md files in rules/ directory) |
| `/cpv-validate-scoring` | Compute plugin quality score (weighted category scoring) |
| `/cpv-validate-security` | Security validation (injection, path traversal, secrets, permissions) |
| `/cpv-validate-xref` | Cross-reference validation (agent refs, version sync, hook scripts) |
| `/cpv-install-plugin` | Install, update, enable/disable, and manage plugins locally |
| `/cpv-setup-github-marketplace` | Set up a GitHub marketplace with CI/CD |

## Utility Scripts

| Script | Description |
|--------|-------------|
| `bump_version.py` | Bump semantic version across all plugin files |
| `check_version_consistency.py` | Verify version consistency across files |
| `lint_files.py` | Read-only file linting for 15 languages |
| `setup_git_hooks.py` | Install/remove git hooks for plugin validation |
| `setup_plugin_pipeline.py` | Setup and validate plugin development pipeline |
| `update_marketplace_metadata.py` | Update marketplace.json when plugin files change |
| `setup_marketplace_automation.py` | Automates GitHub marketplace CI/CD pipeline setup |
| `cpv_token_cost.py` | Token cost reporter — parses agent transcripts for accurate cost breakdown |
| `smart_exec.py` | Intelligent tool executor with cross-platform detection |

## Common Options

All validation scripts share the following flags:

| Option | Description |
|--------|-------------|
| `--verbose, -v` | Show all results including passed checks |
| `--json` | Output results as JSON |
| `--strict` | Strict mode (NIT issues also block validation) |
| `--report PATH` | Save full output to file, print compact summary to stdout |
| `path` | Plugin root path (defaults to parent of scripts/) |

### Validate a Plugin

```bash
cd /path/to/claude-plugins-validation
uv run python scripts/validate_plugin.py /path/to/your-plugin --verbose
```

### Strict Mode

Use `--strict` to treat NIT-level issues as blocking (exit code 4):

```bash
uv run python scripts/validate_plugin.py /path/to/your-plugin --strict
```

In strict mode, nitpick issues that would normally be informational cause a non-zero exit code. This is useful for CI pipelines that enforce best practices.

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

# Cross-reference validation (agent refs, version sync, subagent types)
uv run python scripts/validate_xref.py /path/to/plugin
```

### Install a Plugin Locally (No Marketplace)

```bash
# Install from archive or directory
uv run python scripts/claude-plugin-install.py ./my-plugin.tar.gz
uv run python scripts/claude-plugin-install.py ./my-plugin-dir/

# Validate, list, uninstall, health check
uv run python scripts/claude-plugin-install.py --validate ./my-plugin-dir/
uv run python scripts/claude-plugin-install.py --list
uv run python scripts/claude-plugin-install.py --uninstall my-plugin@local-my-plugin
uv run python scripts/claude-plugin-install.py --update ./new-version.tar.gz my-marketplace
uv run python scripts/claude-plugin-install.py --enable my-plugin@my-marketplace
uv run python scripts/claude-plugin-install.py --disable my-plugin@my-marketplace
uv run python scripts/claude-plugin-install.py --doctor
```

### Use the Agent

Ask Claude to use one of the validation agents:

> "Use the plugin-validator agent to validate my atlas-orchestrator plugin"
> "Use the skill-validation-agent to validate my custom skill"

### Use the Skill

Reference the skill for guidance:

> "I need help validating my plugin hooks. Can you use the plugin-validation-skill?"

## Exit Codes

All validation scripts return consistent exit codes:

| Code | Meaning | Description |
|------|---------|-------------|
| 0 | Passed | All checks passed |
| 1 | Critical | Plugin unusable — must fix |
| 2 | Major | Some features may fail — should fix |
| 3 | Minor | Minor issues — recommended to fix |
| 4 | NIT | Nitpick issues found — only returned in `--strict` mode |

### Severity Levels

Validation results use the following severity levels:

| Level | Blocks Validation | Description |
|-------|-------------------|-------------|
| CRITICAL | Always | Plugin is unusable |
| MAJOR | Always | Significant problems, some features may fail |
| MINOR | Always | Small issues, recommended to fix |
| NIT | Only in `--strict` | Style or best-practice nitpicks |
| WARNING | Never | Informational warnings, never block validation |
| INFO | Never | Informational notes |
| PASSED | Never | Check passed successfully |

## Validation Coverage

### Plugin Validation (`validate_plugin.py`)

- Plugin manifest (`.claude-plugin/plugin.json`)
- Directory structure and known directory whitelist
- Component references (commands, agents, skills)
- Hook configurations (via comprehensive hook validator)
- MCP server definitions
- Script linting (Python via ruff/mypy, shell via shellcheck)
- Content presence check (no empty plugins)
- Settings.json validation (recognized keys)
- Shebang verification for scripts
- Cross-platform script portability

### Hook Validation (`validate_hook.py`)

- JSON structure and schema
- Valid event types (19 supported, including InstructionsLoaded) with fuzzy matching suggestions
- Matcher syntax and value validation (Notification types, SessionStart, PreCompact)
- Script paths and executability
- Hook type configuration
- Bash command portability (interpreter detection, tilde expansion, bare `cd`, Windows paths, relative paths)

### Skill Validation (`validate_skill.py` / `validate_skill_comprehensive.py`)

- SKILL.md existence and frontmatter YAML validity
- Required fields (name, description, allowed-tools)
- Required sections (Overview, Prerequisites, Instructions, Output, Error Handling, Examples, Resources)
- Claude Code specific fields (context, agent, user-invocable)
- Nixtla Quality Standards strict mode
- AgentSkills OpenSpec compliance
- Token budget analysis (line count, word count)
- Reference file validation

### MCP Validation (`validate_mcp.py`)

- `.mcp.json` structure
- Transport types (stdio, http, sse)
- Required fields per transport
- OAuth configuration (clientId, callbackPort, authServerMetadataUrl)
- Environment variable syntax
- Path portability

### Marketplace Validation (`validate_marketplace.py`)

- `marketplace.json` structure
- Required fields (name, plugins)
- Plugin entry validation
- Source type configuration (github, url, npm, pip, git-subdir)
- Local path resolution

### Cross-Reference Validation (`validate_xref.py`)

- Agent Task() call references
- Subagent_type matching against available agents
- Version synchronization across plugin.json, README.md, marketplace.json
- Command agent references
- Skill references
- Hook script references

### Additional Validators

| Script | Purpose |
|--------|---------|
| `validate_agent.py` | Agent definition files |
| `validate_command.py` | Command definition files |
| `validate_documentation.py` | Documentation quality |
| `validate_encoding.py` | File encoding |
| `validate_enterprise.py` | Enterprise compliance |
| `validate_lsp.py` | LSP server configurations |
| `validate_rules.py` | Rule files validation |
| `validate_scoring.py` | Scoring system |
| `validate_security.py` | Security checks |
| `validate_marketplace_pipeline.py` | CI/CD pipeline validation |

## Directory Structure

```
claude-plugins-validation/
├── .claude-plugin/
│   └── plugin.json                  # Plugin manifest
├── .github/workflows/
│   ├── ci.yml                       # CI pipeline
│   ├── notify-marketplace.yml       # Marketplace notification
│   ├── release.yml                  # Release automation
│   └── validate.yml                 # Validation workflow
├── agents/
│   ├── plugin-validator.md          # Expert validation agent
│   ├── skill-validation-agent.md    # Skill validation agent
│   └── references/                  # Fix guides per validator
│       ├── code-quality-fixes.md
│       ├── documentation-fixes.md
│       ├── encoding-fixes.md
│       ├── enterprise-fixes.md
│       ├── hook-fixes.md
│       ├── lsp-fixes.md
│       ├── marketplace-fixes.md
│       ├── mcp-fixes.md
│       ├── plugin-structure-fixes.md
│       ├── plugin-validator-detailed-procedures.md
│       ├── rules-fixes.md
│       ├── scoring-fixes.md
│       ├── security-fixes.md
│       ├── skill-fixes.md
│       ├── skill-semantic-validation.md
│       └── xref-fixes.md
├── commands/
│   ├── cpv-install-plugin.md        # Local plugin install/update/enable/disable
│   ├── cpv-setup-github-marketplace.md # Marketplace setup command
│   ├── cpv-validate-agents.md       # Agent validation command
│   ├── cpv-validate-command.md      # Command validation command
│   ├── cpv-validate-documentation.md # Documentation quality command
│   ├── cpv-validate-encoding.md     # File encoding validation command
│   ├── cpv-validate-enterprise.md   # Enterprise compliance command
│   ├── cpv-validate-hooks.md        # Hook validation command
│   ├── cpv-validate-lsp.md          # LSP validation command
│   ├── cpv-validate-marketplace.md  # Marketplace validation command
│   ├── cpv-validate-mcp.md          # MCP validation command
│   ├── cpv-validate-plugin.md       # Plugin validation command
│   ├── cpv-validate-rules.md        # Rules validation command
│   ├── cpv-validate-scoring.md      # Quality scoring command
│   ├── cpv-validate-security.md     # Security validation command
│   ├── cpv-validate-skill.md        # Skill validation command
│   └── cpv-validate-xref.md        # Cross-reference validation command
├── git-hooks/
│   ├── pre-commit                   # Pre-commit validation hook
│   └── pre-push                     # Pre-push validation hook
├── scripts/
│   ├── claude-plugin-install.py     # Local plugin installer (standalone)
│   ├── cpv_token_cost.py            # Token cost reporter (hook + CLI)
│   ├── cpv_validation_common.py     # Shared validation utilities
│   ├── gitignore_filter.py          # Gitignore pattern filter
│   ├── lint_files.py                # Multi-language file linter
│   ├── smart_exec.py                # Smart script executor
│   ├── validate_plugin.py           # Main plugin validator
│   ├── validate_skill.py            # Skill validator
│   ├── validate_skill_comprehensive.py  # Comprehensive skill validator (190+ rules)
│   ├── validate_hook.py             # Hook validator
│   ├── validate_mcp.py              # MCP server validator
│   ├── validate_marketplace.py      # Marketplace validator
│   ├── validate_marketplace_pipeline.py # Pipeline validator
│   ├── validate_agent.py            # Agent validator
│   ├── validate_command.py          # Command validator
│   ├── validate_documentation.py    # Documentation validator
│   ├── validate_encoding.py         # Encoding validator
│   ├── validate_enterprise.py       # Enterprise compliance validator
│   ├── validate_lsp.py              # LSP validator
│   ├── validate_rules.py            # Rules validator
│   ├── validate_scoring.py          # Scoring validator
│   ├── validate_security.py         # Security validator
│   ├── validate_xref.py             # Cross-reference validator
│   ├── bump_version.py              # Version bumping utility
│   ├── check_version_consistency.py # Version consistency checker
│   ├── setup_git_hooks.py           # Git hooks setup script
│   ├── setup_marketplace_automation.py  # Marketplace automation setup
│   ├── setup_plugin_pipeline.py     # Plugin pipeline setup
│   └── update_marketplace_metadata.py   # Marketplace metadata updater
├── skills/
│   ├── install-plugin/
│   │   └── SKILL.md                 # Local plugin installation skill
│   ├── plugin-validation-skill/
│   │   ├── SKILL.md                 # Main validation skill
│   │   └── references/              # Detailed reference docs
│   │       ├── plugin-structure.md
│   │       ├── hook-validation.md
│   │       ├── skill-validation.md
│   │       ├── mcp-validation.md
│   │       ├── marketplace-validation.md
│   │       ├── pipeline-validation.md
│   │       ├── validation-checklist.md
│   │       ├── validation-procedures.md
│   │       ├── official-docs-urls.md
│   │       ├── troubleshooting-python-scripts.md
│   │       └── pre-push-hook.py
│   ├── setup-github-marketplace/
│   │   ├── SKILL.md                 # Marketplace setup skill
│   │   └── references/
│   │       ├── marketplace-structure.md
│   │       ├── workflow-templates.md
│   │       ├── script-templates.md
│   │       ├── readme-template.md
│   │       ├── troubleshooting.md
│   │       └── pat-setup.md
│   └── skill-validation-skill/
│       ├── SKILL.md                 # Skill validation skill
│       └── references/
│           ├── frontmatter-schema.md
│           ├── pillars-coverage.md
│           ├── scoring-system.md
│           └── validation-rules.md
├── templates/
│   ├── README-marketplace.md
│   ├── github-workflows/
│   │   ├── notify-marketplace.yml
│   │   ├── update-submodules.yml
│   │   └── validate-marketplace.yml
│   └── scripts/
│       └── sync_marketplace_versions.py
├── tests/
│   ├── conftest.py
│   ├── test_cpv_validation_common.py
│   ├── test_extended_linting.py
│   ├── test_gitignore_filter.py
│   ├── test_new_validation_checks.py
│   ├── test_toc_embedding.py
│   ├── test_validate_agent.py
│   ├── test_validate_command.py
│   ├── test_validate_documentation.py
│   ├── test_validate_encoding.py
│   ├── test_validate_enterprise.py
│   ├── test_validate_hook.py
│   ├── test_validate_lsp.py
│   ├── test_validate_marketplace_pipeline.py
│   ├── test_validate_marketplace.py
│   ├── test_validate_mcp.py
│   ├── test_validate_plugin.py
│   ├── test_validate_rules.py
│   ├── test_validate_scoring.py
│   ├── test_validate_security.py
│   ├── test_validate_skill_comprehensive.py
│   ├── test_validate_skill.py
│   ├── test_validate_xref.py
│   ├── test_validator_early_exit.py
│   └── fixtures/                    # Test fixtures
│       ├── valid_plugin/
│       └── invalid_plugin/
├── CHANGELOG.md
├── LICENSE
├── README.md
├── cliff.toml
├── pyproject.toml
├── pytest.ini
├── requirements.txt
└── uv.lock
```

## Requirements

- Python 3.12+
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

## Self-Validation

The plugin validates itself. Run:

```bash
uv run python scripts/validate_plugin.py . --verbose --report docs_dev/self-validation.md
```

## Troubleshooting

### Plugin Not Loading

If the plugin does not load when starting Claude Code:

1. **Verify installation**: Run `claude plugin list` to see installed plugins
2. **Check the manifest**: Ensure `.claude-plugin/plugin.json` exists and is valid JSON
3. **Run validation**: `uv run python scripts/validate_plugin.py /path/to/plugin --verbose`
4. **Enable debug mode**: Launch Claude with `claude --debug` to see plugin loading details
5. **Check for conflicts**: Ensure no other plugin has the same name

### Validation Scripts Not Running

If validation scripts fail to execute:

1. **Ensure uv is installed**: Run `uv --version` to verify installation
2. **Verify Python version**: Requires Python 3.12 or higher. Check with `python3 --version`
3. **Check file permissions**: Scripts must be executable. Run `chmod +x scripts/*.py`
4. **Initialize environment**: Run `uv sync` in the plugin directory to install dependencies

### Import Errors

If you see `ModuleNotFoundError` or import errors:

```bash
# Install dependencies
cd /path/to/claude-plugins-validation
uv sync

# Activate the virtual environment
source .venv/bin/activate

# Verify installation
uv run python -c "import pathlib; print('OK')"
```

### Validation Fails but Plugin Works

Exit code 3 (minor issues) means the plugin will function but does not follow all best practices:

- **Warnings vs Errors**: Minor issues are recommendations, not blockers
- **Best practices**: Missing README.md, optional fields not set, documentation gaps
- **Fix or ignore**: Address warnings when time permits, but they will not prevent plugin usage

If validation returns exit code 1 (critical) or 2 (major) but the plugin appears to work:

1. The issue may only manifest in specific scenarios
2. Some features may be silently broken
3. Future Claude Code updates may enforce stricter validation
4. Recommend fixing issues to ensure long-term compatibility

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
