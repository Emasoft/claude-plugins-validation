# Claude Plugins Validation

<!--BADGES-START-->
![Version](https://img.shields.io/badge/version-2.3.2-blue)
![Tests](https://img.shields.io/badge/tests-1549%20passed-brightgreen)
![Validation](https://img.shields.io/badge/validation-0%20issues-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)
<!--BADGES-END-->

> **Installation:** This plugin is distributed via the [Emasoft Plugins Marketplace](https://github.com/Emasoft/emasoft-plugins).
> See [Installation](#installation) below for instructions.

Comprehensive validation and management suite for Claude Code plugins, marketplaces, hooks, skills, and MCP servers.

## Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Part 1: Validation](#part-1-validation)
  - [Validation Commands](#validation-commands)
  - [How to Validate](#how-to-validate)
  - [Common Validation Options](#common-validation-options)
  - [Exit Codes](#exit-codes)
  - [Validation Coverage](#validation-coverage)
- [Part 2: Plugin & Marketplace Management](#part-2-plugin--marketplace-management)
  - [Regular Operations](#regular-operations)
    - [Install / Uninstall / Update](#install--uninstall--update)
    - [Enable / Disable](#enable--disable)
    - [List / Search / Marketplace](#list--search--marketplace)
    - [Create / Publish](#create--publish)
  - [Fix, Doctor & Diagnostics](#fix-doctor--diagnostics)
    - [Doctor](#doctor)
    - [Fix Validation Issues](#fix-validation-issues)
- [Scripts Reference](#scripts-reference)
- [Agents](#agents)
- [Requirements](#requirements)
- [Troubleshooting](#troubleshooting)

## Overview

| Category | Count | Description |
|----------|-------|-------------|
| Validation scripts | 17 | Python validators with 190+ rules for all plugin components |
| Management scripts | 12 | Plugin lifecycle, marketplace ops, doctor, scaffolding |
| Agents | 6 | Validation, fixing, management, semantic analysis |
| Skills | 11 | Validation, management, publishing, scaffolding |
| Commands | 38 | Slash commands for all operations |
| Tests | 1549 | Full coverage across all modules |

## Installation

Install from the Emasoft marketplace:

```bash
# Add Emasoft marketplace (first time only)
claude plugin marketplace add emasoft-plugins --url https://github.com/Emasoft/emasoft-plugins

# Install plugin (--scope user = available globally, recommended)
claude plugin install claude-plugins-validation@emasoft-plugins --scope user

# RESTART Claude Code after installing (required!)
```

For development only: `claude --plugin-dir ./claude-plugins-validation`

---

## Part 1: Validation

### Validation Commands

| Command | Description |
|---------|-------------|
| `/cpv-validate-plugin` | Comprehensive plugin validation (manifest, hooks, skills, MCP, scripts) |
| `/cpv-validate-hooks` | Validate hook configurations (hooks.json) |
| `/cpv-validate-skill` | Validate skill directories (SKILL.md, frontmatter, references) |
| `/cpv-validate-mcp` | Validate MCP server configurations |
| `/cpv-validate-marketplace` | Validate marketplace configurations |
| `/cpv-validate-agents` | Validate agent definition files |
| `/cpv-validate-lsp` | Validate LSP server configurations |
| `/cpv-validate-command` | Validate command definition files |
| `/cpv-validate-documentation` | Validate documentation quality (README sections, links, images) |
| `/cpv-validate-encoding` | Validate file encoding (UTF-8, BOM, line endings, control chars) |
| `/cpv-validate-enterprise` | Validate enterprise compliance (author, license, SPDX, tags) |
| `/cpv-validate-rules` | Validate rule files (.md files in rules/) |
| `/cpv-validate-scoring` | Compute plugin quality score (weighted categories) |
| `/cpv-validate-security` | Security validation (injection, path traversal, secrets, permissions) |
| `/cpv-validate-xref` | Cross-reference validation (agent refs, version sync, hook scripts) |
| `/cpv-semantic-validation` | Deep AI-driven semantic analysis (opus, explicit opt-in) |
| `/cpv-validate-github-plugin` | Validate a GitHub plugin without installing (`--audit` for security scan) |
| `/cpv-validate-github-marketplace` | Validate a GitHub marketplace without registering (`--audit` for security scan) |

### How to Validate

```bash
# Full plugin validation
uv run python scripts/validate_plugin.py /path/to/plugin --verbose

# Strict mode (NIT issues also block)
uv run python scripts/validate_plugin.py /path/to/plugin --strict

# Validate specific components
uv run python scripts/validate_skill.py /path/to/skill-dir
uv run python scripts/validate_hook.py /path/to/hooks.json
uv run python scripts/validate_mcp.py /path/to/plugin
uv run python scripts/validate_marketplace.py /path/to/marketplace

# Validate a GitHub repo without installing
uv run scripts/manage_github_validate.py --plugin owner/repo
uv run scripts/manage_github_validate.py --marketplace owner/repo --audit
```

### Common Validation Options

| Option | Description |
|--------|-------------|
| `--verbose, -v` | Show all results including passed checks |
| `--json` | Output results as JSON |
| `--strict` | NIT issues also block validation |
| `--report PATH` | Save full output to file, print compact summary |
| `path` | Plugin root path (defaults to parent of scripts/) |

### Exit Codes

| Code | Level | Description |
|------|-------|-------------|
| 0 | Passed | All checks passed |
| 1 | CRITICAL | Plugin unusable — must fix |
| 2 | MAJOR | Significant problems — should fix |
| 3 | MINOR | Small issues — recommended to fix |
| 4 | NIT | Nitpicks — only in `--strict` mode |

Severity levels: CRITICAL, MAJOR, MINOR always block. NIT blocks only in strict mode. WARNING and INFO are informational.

### Validation Coverage

| Validator | What it checks |
|-----------|---------------|
| `validate_plugin.py` | Manifest, directory structure, components, hooks, MCP, scripts, encoding, env vars, paths, URLs, shebangs |
| `validate_hook.py` | JSON schema, 23 event types, matcher syntax, script paths, bash portability |
| `validate_skill.py` | SKILL.md frontmatter, required sections, Nixtla standards, token budget, references |
| `validate_mcp.py` | Transport types, required fields, OAuth config, env vars, path portability |
| `validate_marketplace.py` | Structure, plugin entries, source types, local path resolution |
| `validate_xref.py` | Agent Task() refs, subagent types, version sync, command refs, skill refs |
| `validate_agent.py` | Agent definition files |
| `validate_command.py` | Command definition files |
| `validate_documentation.py` | README sections, links, images |
| `validate_encoding.py` | UTF-8, BOM, line endings, control chars |
| `validate_enterprise.py` | Author, license, SPDX, tags, context |
| `validate_lsp.py` | LSP server configurations |
| `validate_rules.py` | Rule files (.md in rules/) |
| `validate_scoring.py` | Weighted quality scoring |
| `validate_security.py` | Injection, path traversal, secrets, permissions |

---

## Part 2: Plugin & Marketplace Management

### Regular Operations

#### Install / Uninstall / Update

| Command | Description |
|---------|-------------|
| `/cpv-install-plugin-from-local-mp` | Install from local directory or archive into a local marketplace |
| `/cpv-uninstall-plugin-from-local-mp` | Uninstall from a local marketplace |
| `/cpv-update-plugin` | Update from a new source |
| `/cpv-manage-remote-plugins` | Install/update/uninstall from GitHub marketplaces |

#### Enable / Disable

| Command | Description |
|---------|-------------|
| `/cpv-enable-plugin` | Enable a plugin (`--scope user\|local`, smart name resolution) |
| `/cpv-disable-plugin` | Disable without removing (`--scope user\|local`, smart name resolution) |

Smart name resolution accepts: `plugin-name`, `name@marketplace`, `name@owner/marketplace`.

Scope: `--scope user` (default, `~/.claude/settings.json`) or `--scope local` (`<project>/.claude/settings.local.json`).

#### List / Search / Marketplace

| Command | Description |
|---------|-------------|
| `/cpv-list-plugins` | List all installed plugins with version, status, components |
| `/cpv-list-mp-plugins` | List all plugins in a marketplace with enabled status |
| `/cpv-search-plugins` | Search by component type or text |
| `/cpv-manage-marketplaces` | Add, remove, list, or update GitHub marketplaces |
| `/cpv-bump-version` | Bump plugin version (patch/minor/major) |
| `/cpv-version` | Show management tools version |

#### Create / Publish

| Command | Description |
|---------|-------------|
| `/cpv-create-local-plugin` | Scaffold a new plugin repo locally |
| `/cpv-create-local-marketplace` | Scaffold a new marketplace hub locally |
| `/cpv-publish-a-plugin-as-github-repo` | End-to-end: validate, standardize, create GitHub repo, push, CI/CD |
| `/cpv-create-a-github-marketplace` | Create a GitHub marketplace with full CI/CD |
| `/cpv-publish-a-plugin-to-a-github-marketplace` | Register a plugin in a marketplace |
| `/cpv-standardize` | Audit and fix a repo to match standards (auto-detects type) |

### Fix, Doctor & Diagnostics

| Command | Description |
|---------|-------------|
| `/cpv-fix-validation` | Auto-fix issues from a validation report |
| `/cpv-doctor` | Health-check all plugins, settings, marketplaces (`--fix` to auto-repair) |

#### Doctor

```bash
# Diagnose
uv run scripts/manage_doctor.py --verbose

# Diagnose and auto-fix orphaned entries
uv run scripts/manage_doctor.py --fix
```

Checks: CLI auth, settings integrity, marketplace registrations, plugin validation, orphaned entries.

`--fix` auto-removes: orphaned marketplace registrations, stale `enabledPlugins` entries, stale `~/.claude/settings.local.json` entries.

#### Fix Validation Issues

After running validation, use the fixer to auto-remediate:

```bash
# Get a validation report
uv run python scripts/validate_plugin.py /path/to/plugin --report /tmp/report.md

# Ask the plugin-fixer agent to fix it
> "Use the plugin-fixer agent to fix the issues in /tmp/report.md"
```

---

## Scripts Reference

### Validation Scripts

| Script | Purpose |
|--------|---------|
| `validate_plugin.py` | Main plugin validator (190+ rules) |
| `validate_skill.py` | Skill validator |
| `validate_skill_comprehensive.py` | Comprehensive skill validator |
| `validate_hook.py` | Hook validator |
| `validate_mcp.py` | MCP server validator |
| `validate_marketplace.py` | Marketplace validator |
| `validate_xref.py` | Cross-reference validator |
| `validate_agent.py` | Agent validator |
| `validate_command.py` | Command validator |
| `validate_documentation.py` | Documentation validator |
| `validate_encoding.py` | Encoding validator |
| `validate_enterprise.py` | Enterprise compliance validator |
| `validate_lsp.py` | LSP validator |
| `validate_rules.py` | Rules validator |
| `validate_scoring.py` | Scoring validator |
| `validate_security.py` | Security validator |
| `validate_marketplace_pipeline.py` | Pipeline validator |

### Management Scripts

| Script | Purpose |
|--------|---------|
| `manage_plugin.py` | Install, uninstall, update, enable, disable plugins |
| `manage_registry.py` | List, search, marketplace listing |
| `manage_doctor.py` | Health-check and auto-fix (`--fix`) |
| `manage_marketplace.py` | Marketplace registration (add/remove/list/update) |
| `manage_remote.py` | Remote plugin operations via claude CLI |
| `manage_github_validate.py` | Validate GitHub repos without installing |
| `bump_version.py` | Semantic version bumping |
| `cpv_management_common.py` | Shared infrastructure (JSONC, safe I/O, archives) |

### Creation & Standardization Scripts

| Script | Purpose |
|--------|---------|
| `generate_plugin_repo.py` | Scaffold a complete plugin repo |
| `generate_marketplace_repo.py` | Scaffold a marketplace hub repo |
| `standardize_plugin.py` | Audit and fix plugin repo to match standards |
| `standardize_marketplace.py` | Audit and fix marketplace repo |

### Utility Scripts

| Script | Purpose |
|--------|---------|
| `lint_files.py` | Read-only file linting for 15 languages |
| `cpv_token_cost.py` | Token cost reporter |
| `smart_exec.py` | Cross-platform script executor |

## Agents

| Agent | Purpose |
|-------|---------|
| `plugin-validator` | Expert validation agent |
| `skill-validation-agent` | Skill validation agent |
| `plugin-fixer` | Automated remediation agent |
| `semantic-validator` | Deep AI-driven quality analysis |
| `plugin-manager` | Plugin lifecycle management |
| `plugin-creator` | Plugin/marketplace scaffolding and publishing |

## Requirements

- Python 3.12+
- uv (Python package manager)

## Self-Validation

```bash
uv run python scripts/validate_plugin.py . --verbose
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Plugin not loading | Run `claude plugin list`, check `.claude-plugin/plugin.json`, run validation |
| Scripts fail | Ensure `uv --version` works, Python 3.12+, run `uv sync` |
| Import errors | Run `uv sync` then `source .venv/bin/activate` |
| Minor issues (exit 3) | Recommendations, not blockers — fix when time permits |
| Critical issues (exit 1-2) | Must fix — some features may be silently broken |

## License

MIT License - see LICENSE file

## Author

Emasoft (713559+Emasoft@users.noreply.github.com)
