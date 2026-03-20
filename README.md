# Claude Plugins Validation

<!--BADGES-START-->
<!--BADGES-END-->

> **Installation:** This plugin is distributed via the [Emasoft Plugins Marketplace](https://github.com/Emasoft/emasoft-plugins).
> See [Installation](#installation) below for instructions.

Comprehensive validation and management suite for Claude Code plugins, marketplaces, hooks, skills, and MCP servers.

## Overview

This plugin provides:

- **Validation Scripts**: 17 Python validators with 190+ rules for all plugin components
- **Management Scripts**: Plugin lifecycle management — install, uninstall, update, enable, disable, search, doctor, marketplace operations
- **Expert Agents**: `plugin-validator`, `skill-validation-agent`, `plugin-fixer`, `semantic-validator` (validation) + `plugin-manager` (management)
- **Skills**: 8 skills covering validation, management, publishing, scaffolding, and marketplace operations
- **Slash Commands**: 37 commands for validating, managing, fixing, and publishing plugins

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
| `/cpv-fix-validation` | Auto-fix issues from a validation report |
| `/cpv-semantic-validation` | Deep AI-driven semantic analysis (opus, explicit opt-in) |

### Plugin Management Commands (v2.0.0)

| Command | Description |
|---------|-------------|
| `/cpv-install-plugin-from-local-mp` | Install a plugin from local directory or archive into a local marketplace |
| `/cpv-uninstall-plugin-from-local-mp` | Uninstall a plugin from a local marketplace and clean up settings |
| `/cpv-update-plugin` | Update an installed plugin from a new source |
| `/cpv-enable-plugin` | Enable a plugin (smart name resolution, `--scope user\|local`) |
| `/cpv-disable-plugin` | Disable a plugin without removing it (smart name resolution, `--scope user\|local`) |
| `/cpv-list-plugins` | List all installed plugins with version, status, components |
| `/cpv-list-mp-plugins` | List all plugins in a marketplace with version and enabled status |
| `/cpv-search-plugins` | Search plugins by component type or text |
| `/cpv-doctor` | Health-check all plugins, settings, and marketplaces |
| `/cpv-manage-marketplaces` | Add, remove, list, or update GitHub marketplaces |
| `/cpv-manage-remote-plugins` | Install/update/uninstall remote plugins from GitHub |
| `/cpv-validate-github-plugin` | Validate a GitHub plugin without installing (use `--audit` for security scan) |
| `/cpv-validate-github-marketplace` | Validate a GitHub marketplace without registering (use `--audit` for security scan) |
| `/cpv-bump-version` | Bump plugin version (patch/minor/major) |
| `/cpv-version` | Show management tools version |

### Plugin/Marketplace Creation & Publishing Commands (v2.1.0+)

| Command | Description |
|---------|-------------|
| `/cpv-create-local-plugin` | Scaffold a new plugin repo locally (no GitHub) |
| `/cpv-create-local-marketplace` | Scaffold a new marketplace hub locally (no GitHub) |
| `/cpv-publish-a-plugin-as-github-repo` | End-to-end: validate, standardize, create GitHub repo, push, configure CI/CD |
| `/cpv-create-a-github-marketplace` | Create a GitHub marketplace with full CI/CD automation |
| `/cpv-publish-a-plugin-to-a-github-marketplace` | Register a plugin in a marketplace with validation and owner verification |
| `/cpv-standardize` | Audit and fix a plugin or marketplace repo (auto-detects type) |

## Utility Scripts

| Script | Description |
|--------|-------------|
| `lint_files.py` | Read-only file linting for 15 languages |
| `setup_git_hooks.py` | Install/remove git hooks for plugin validation |
| `setup_plugin_pipeline.py` | Setup and validate plugin development pipeline |
| `update_marketplace_metadata.py` | Update marketplace.json when plugin files change |
| `setup_marketplace_automation.py` | Automates GitHub marketplace CI/CD pipeline setup |
| `cpv_token_cost.py` | Token cost reporter — parses agent transcripts for accurate cost breakdown |
| `smart_exec.py` | Intelligent tool executor with cross-platform detection |

### Management Scripts (v2.0.0)

| Script | Description |
|--------|-------------|
| `manage_plugin.py` | Plugin lifecycle: install, uninstall, update, enable, disable |
| `manage_registry.py` | List and search installed plugins by component type or text |
| `manage_doctor.py` | Health-check all plugins, settings, and marketplaces |
| `manage_marketplace.py` | Marketplace management: add, remove, list, update registrations |
| `manage_remote.py` | Remote plugin operations via claude CLI delegation |
| `manage_github_validate.py` | Validate GitHub repos/marketplaces without installing |
| `bump_version.py` | Semantic version bumping (patch/minor/major/set) |
| `cpv_management_common.py` | Shared management infrastructure (JSONC, safe I/O, archives) |

### Creation & Standardization Scripts (v2.1.0)

| Script | Description |
|--------|-------------|
| `generate_plugin_repo.py` | Scaffold a complete plugin repo with all standard files |
| `generate_marketplace_repo.py` | Scaffold a marketplace hub repo (pointers to external plugin repos) |
| `standardize_plugin.py` | Audit and fix existing plugin repo to match CPV standards |
| `standardize_marketplace.py` | Audit and fix existing marketplace repo to match standards |

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

### Install a Plugin Locally

```bash
# Install from directory
uv run scripts/manage_plugin.py ./my-plugin/ my-marketplace

# Install from archive
uv run scripts/manage_plugin.py plugin.tar.gz my-marketplace

# List installed plugins
uv run scripts/manage_registry.py --list

# Health check
uv run scripts/manage_doctor.py --verbose
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
- Environment variable validation (`${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}`, `${CLAUDE_PROJECT_DIR}`, `${CLAUDE_SKILL_DIR}`, `${CLAUDE_ENV_FILE}`, `${CLAUDE_CODE_REMOTE}`)
- Deep path and URL validation in .md files (76+ files scanned, SSRF-safe URL checker)
- Shebang verification for scripts
- Cross-platform script portability

### Hook Validation (`validate_hook.py`)

- JSON structure and schema
- Valid event types (23 supported, including StopFailure, Elicitation, ElicitationResult, PostCompact) with fuzzy matching suggestions
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
│   ├── plugin-fixer.md              # Automated remediation agent
│   └── semantic-validator.md        # Deep AI-driven quality analysis agent
├── commands/
│   ├── cpv-create-a-github-marketplace.md
│   ├── cpv-create-local-marketplace.md
│   ├── cpv-create-local-plugin.md
│   ├── cpv-fix-validation.md
│   ├── cpv-publish-a-plugin-as-github-repo.md
│   ├── cpv-publish-a-plugin-to-a-github-marketplace.md
│   ├── cpv-semantic-validation.md
│   ├── cpv-standardize.md
│   ├── cpv-validate-*.md (14 validators)
│   └── ... (38 commands total)
├── git-hooks/
│   ├── pre-commit                   # Pre-commit validation hook
│   └── pre-push                     # Pre-push validation hook
├── scripts/
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
│   ├── setup_git_hooks.py           # Git hooks setup script
│   ├── setup_marketplace_automation.py  # Marketplace automation setup
│   ├── setup_plugin_pipeline.py     # Plugin pipeline setup
│   └── update_marketplace_metadata.py   # Marketplace metadata updater
├── skills/
│   ├── fix-validation/              # Automated fix guides
│   │   ├── SKILL.md
│   │   └── references/              # Per-validator fix guides
│   ├── plugin-validation-skill/
│   │   ├── SKILL.md                 # Main validation skill
│   │   └── references/              # Detailed reference docs
│   ├── publish-to-marketplace/
│   │   ├── SKILL.md                 # Publish plugin to marketplace
│   │   └── references/
│   │       └── publish-pipeline-guide.md
│   ├── semantic-validation-skill/
│   │   └── SKILL.md                 # Deep semantic analysis skill
│   ├── setup-github-marketplace/
│   │   ├── SKILL.md                 # Marketplace setup skill
│   │   └── references/
│   ├── setup-plugin-repo/
│   │   ├── SKILL.md                 # Plugin repo scaffolding skill
│   │   └── references/
│   │       ├── plugin-binary-builds.md
│   │       ├── plugin-hooks-and-scripts.md
│   │       ├── plugin-repo-templates.md
│   │       └── plugin-workflows.md
│   └── skill-validation-skill/
│       ├── SKILL.md                 # Skill validation skill
│       └── references/
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
