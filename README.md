# Claude Plugins Validation (CPV)

<!--BADGES-START-->
![Version](https://img.shields.io/badge/version-2.4.0-blue)
![Tests](https://img.shields.io/badge/tests-1549%20passed-brightgreen)
![Validation](https://img.shields.io/badge/validation-0%20issues-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)
<!--BADGES-END-->

**Check if your Claude Code plugin is correct, secure, and ready to publish -- without spending a single token.**

CPV is a suite of Python validation scripts that analyze Claude Code plugins locally on your machine. Every check runs offline, produces instant results, and costs nothing. No AI calls, no API keys, no cloud services.

---

## Table of Contents

- [What Does CPV Do?](#what-does-cpv-do)
- [Quick Start](#quick-start)
  - [Option A: Run Directly (No Install)](#option-a-run-directly-no-install)
  - [Option B: Install as a Plugin](#option-b-install-as-a-plugin)
  - [Option C: Developer Mode](#option-c-developer-mode)
- [Validation Commands](#validation-commands)
  - [What Each Validator Checks](#what-each-validator-checks)
  - [Remote Validation](#remote-validation)
- [How to Read the Results](#how-to-read-the-results)
  - [Severity Levels](#severity-levels)
  - [Exit Codes](#exit-codes)
  - [Common Options](#common-options)
- [Plugin Management Commands](#plugin-management-commands)
  - [Install, Update, Remove](#install-update-remove)
  - [Enable and Disable](#enable-and-disable)
  - [Browse and Search](#browse-and-search)
  - [Create and Publish](#create-and-publish)
  - [Fix and Repair](#fix-and-repair)
- [For Developers](#for-developers)
  - [Project Overview](#project-overview)
  - [Scripts Reference](#scripts-reference)
  - [Agents](#agents)
- [Requirements](#requirements)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## What Does CPV Do?

When you build a Claude Code plugin, there are many things that can go wrong: a typo in the manifest, a hook pointing to a missing script, a skill description that won't trigger properly, or a security issue hiding in a shell command.

CPV catches these problems **before** your users do. It runs **17 specialized validators** that check over **190 rules** covering every part of a plugin:

| Area | Examples of what CPV catches |
|------|------------------------------|
| **Structure** | Missing `plugin.json`, wrong directory layout, missing required files |
| **Hooks** | Invalid event names, broken script paths, unsafe shell commands |
| **Skills** | Poor descriptions that won't trigger, missing required sections, files too large |
| **Security** | Hardcoded secrets, path traversal, command injection, prompt injection patterns |
| **Compatibility** | Windows/macOS/Linux path issues, encoding problems, broken cross-references |
| **Quality** | Missing documentation, no license, inconsistent versions, dead references |

**Important:** All checks run locally as pure Python. There are no API calls, no tokens consumed, and no data sent anywhere. The only exception is the optional `/cpv-semantic-validation` command, which uses Claude (opus) for deep AI analysis -- but this must be explicitly requested and is never run automatically.

---

## Quick Start

### Option A: Run Directly (No Install)

The fastest way to validate a plugin. Requires only [uv](https://docs.astral.sh/uv/) (the Python package manager):

```bash
# Validate a plugin (runs all 17 validators)
uvx --from git+https://github.com/Emasoft/claude-plugins-validation cpv-validate /path/to/your-plugin

# Validate with detailed output
uvx --from git+https://github.com/Emasoft/claude-plugins-validation cpv-validate /path/to/your-plugin --verbose

# Save a report to a file
uvx --from git+https://github.com/Emasoft/claude-plugins-validation cpv-validate /path/to/your-plugin --report report.md

# Run a specific validator (e.g., security only)
uvx --from git+https://github.com/Emasoft/claude-plugins-validation cpv-validate-security /path/to/your-plugin
```

This downloads and runs CPV on the fly. Nothing is installed permanently on your system.

### Option B: Install as a Plugin

If you use Claude Code regularly, install CPV as a plugin to get slash commands like `/cpv-validate-plugin`:

```bash
# Add the Emasoft marketplace (first time only)
claude plugin marketplace add emasoft-plugins --url https://github.com/Emasoft/emasoft-plugins

# Install CPV (--scope user makes it available in all projects)
claude plugin install claude-plugins-validation@emasoft-plugins --scope user

# Restart Claude Code after installing
```

Once installed, type `/cpv-validate-plugin` in Claude Code to validate any plugin interactively.

### Option C: Developer Mode

For contributing to CPV or debugging:

```bash
claude --plugin-dir ./claude-plugins-validation
```

---

## Validation Commands

### What Each Validator Checks

Every validator can be run as a **slash command** inside Claude Code (e.g., `/cpv-validate-plugin`) or as a **CLI command** via uvx (e.g., `cpv-validate`).

| Slash Command | CLI Command | What It Checks |
|---------------|-------------|----------------|
| `/cpv-validate-plugin` | `cpv-validate` | **Full plugin validation.** Runs all 17 sub-validators below in sequence. This is the recommended starting point -- it checks everything. |
| `/cpv-validate-skill` | `cpv-validate-skill` | **Skill quality.** Checks SKILL.md files for proper frontmatter, required sections (Overview, Instructions, Examples, etc.), triggering phrases, description length, token budget, and reference file integrity. Uses 190+ rules including Nixtla strict-mode compliance. |
| `/cpv-validate-hooks` | `cpv-validate-hooks` | **Hook configuration.** Validates `hooks.json` against 23 known event types and 4 hook types (command, http, prompt, agent). Checks matcher syntax, script paths, bash portability, and environment variable safety. |
| `/cpv-validate-agents` | `cpv-validate-agents` | **Agent definitions.** Validates agent `.md` files for correct frontmatter fields (name, tools, model, etc.), consistent naming, valid tool references, and markdown structure. |
| `/cpv-validate-command` | `cpv-validate-command` | **Command definitions.** Checks command `.md` files for required frontmatter (allowed-tools, description), valid tool names, argument syntax, and naming conventions. |
| `/cpv-validate-security` | `cpv-validate-security` | **Security vulnerabilities.** Scans for command injection, path traversal, hardcoded secrets, prompt injection patterns, data exfiltration attempts, supply chain risks, credential harvesting, hook abuse, MCP server abuse, sandbox escape attempts, and permission escalation. Optionally runs `cc-audit` external scanner. |
| `/cpv-validate-scoring` | `cpv-validate-scoring` | **Quality score.** Computes a weighted score across categories: structure, documentation, security, testing, hooks, skills, and compliance. Helps prioritize what to improve. |
| `/cpv-validate-marketplace` | `cpv-validate-marketplace` | **Marketplace manifest.** Validates `marketplace.json` structure, plugin entries, source references, git-subdir fields, metadata support, and field types. Aligned with official Anthropic spec. |
| `/cpv-validate-enterprise` | `cpv-validate-enterprise` | **Enterprise compliance.** Checks author info, license file, SPDX identifiers, keyword tags, context window declarations, and organizational metadata. |
| `/cpv-validate-mcp` | `cpv-validate-mcp` | **MCP server config.** Validates `.mcp.json` transport types (stdio, sse, streamable-http), required fields, OAuth configuration, environment variables, and cross-platform path portability. |
| `/cpv-validate-lsp` | `cpv-validate-lsp` | **LSP server config.** Checks LSP server definitions for valid command paths, language identifiers, file patterns, and configuration structure. |
| `/cpv-validate-documentation` | `cpv-validate-documentation` | **Documentation quality.** Checks README for required sections (installation, usage, troubleshooting), working links, valid image references, and completeness. |
| `/cpv-validate-encoding` | `cpv-validate-encoding` | **File encoding.** Verifies all files are valid UTF-8, detects byte-order marks (BOM), checks line endings (LF vs CRLF), and flags binary/control characters in text files. |
| `/cpv-validate-rules` | `cpv-validate-rules` | **Rule files.** Validates `.md` files in the `rules/` directory for correct structure, naming conventions, and content quality. |
| `/cpv-validate-xref` | `cpv-validate-xref` | **Cross-references.** Checks that agent `Task()` references match real agents, subagent types are valid, versions are consistent across `plugin.json` and `pyproject.toml`, command/skill references resolve, and hook scripts exist. |
| `/cpv-semantic-validation` | *(plugin only)* | **Deep AI analysis.** Uses Claude (opus model) to evaluate skill triggering effectiveness, instruction clarity, example quality, and workflow completeness. Produces A-F letter grades. **This is the only command that uses AI tokens.** Must be explicitly requested. |

### Remote Validation

You can validate plugins and marketplaces hosted on GitHub without downloading or installing them:

| Command | What It Does |
|---------|--------------|
| `/cpv-validate-github-plugin` | Validates a plugin from a GitHub repo. Add `--audit` for a security scan. |
| `/cpv-validate-github-marketplace` | Validates a marketplace from a GitHub repo. Add `--audit` for a security scan. |

---

## How to Read the Results

When you run a validator, it prints a summary showing how many issues were found at each severity level, and whether the plugin passed or failed.

### Severity Levels

| Level | Meaning | Action Required? |
|-------|---------|-----------------|
| **CRITICAL** | The plugin is broken and will not work correctly. | Yes -- must fix before using. |
| **MAJOR** | Significant problems that affect functionality or security. | Yes -- should fix before publishing. |
| **MINOR** | Small issues that don't break anything but reduce quality. | Recommended -- fix when you can. |
| **NIT** | Style nitpicks and minor suggestions. | Optional -- only enforced in `--strict` mode. |
| **WARNING** | Informational alerts about potential concerns. | No -- review at your discretion. |
| **INFO** | Neutral observations (e.g., "found 3 skills"). | No -- purely informational. |
| **PASSED** | A check that passed successfully. | No -- everything is fine. |

### Exit Codes

The exit code tells you the highest severity found, so you can use CPV in CI/CD pipelines:

| Exit Code | Meaning |
|-----------|---------|
| `0` | All checks passed (no CRITICAL, MAJOR, or MINOR issues) |
| `1` | CRITICAL issues found |
| `2` | MAJOR issues found |
| `3` | MINOR issues found |
| `4` | NIT issues found (only in `--strict` mode) |

### Common Options

These options work with all validators:

| Option | What It Does |
|--------|-------------|
| `--verbose` or `-v` | Show all results, including checks that passed. Without this, only failures are shown. |
| `--report PATH` | Save the full detailed report to a file. The terminal shows a compact summary. |
| `--json` | Output results as JSON (useful for scripts and CI/CD). |
| `--strict` | Treat NIT issues as failures too (stricter quality bar). |
| `--marketplace-only` | Skip `plugin.json` requirement (for marketplace-only distribution). |

---

## Plugin Management Commands

In addition to validation, CPV provides tools for managing the full plugin lifecycle.

### Install, Update, Remove

| Command | What It Does |
|---------|--------------|
| `/cpv-install-plugin-from-local-mp` | Install a plugin from a local directory or archive into a local marketplace. |
| `/cpv-uninstall-plugin-from-local-mp` | Remove a plugin from a local marketplace and clean up settings. |
| `/cpv-update-plugin` | Update an installed plugin from a new source. |
| `/cpv-manage-remote-plugins` | Install, update, or remove plugins from GitHub marketplaces. |

### Enable and Disable

| Command | What It Does |
|---------|--------------|
| `/cpv-enable-plugin` | Turn on a disabled plugin. Supports `--scope user` (global) or `--scope local` (project only). |
| `/cpv-disable-plugin` | Turn off a plugin without removing it. Same scope options. |

Both commands support **smart name resolution**: you can use just the plugin name, `name@marketplace`, or `name@owner/marketplace`.

### Browse and Search

| Command | What It Does |
|---------|--------------|
| `/cpv-list-plugins` | List all installed plugins with version, enabled status, and components. |
| `/cpv-list-mp-plugins` | List all plugins available in a marketplace. |
| `/cpv-search-plugins` | Search installed plugins by component type (skills, hooks, agents, etc.) or text. |
| `/cpv-manage-marketplaces` | Add, remove, list, or update GitHub plugin marketplaces. |
| `/cpv-version` | Show the CPV version. |

### Create and Publish

| Command | What It Does |
|---------|--------------|
| `/cpv-create-local-plugin` | Create a new plugin from scratch with all standard files. |
| `/cpv-create-local-marketplace` | Create a new marketplace hub locally. |
| `/cpv-publish-a-plugin-as-github-repo` | Full pipeline: validate, standardize, create GitHub repo, push, set up CI/CD. |
| `/cpv-create-a-github-marketplace` | Create a GitHub marketplace with automation and CI/CD. |
| `/cpv-publish-a-plugin-to-a-github-marketplace` | Register a plugin in an existing marketplace. |
| `/cpv-standardize` | Audit a plugin or marketplace repo and fix it to match CPV standards. |
| `/cpv-bump-version` | Bump the version number (patch, minor, or major). |

### Fix and Repair

| Command | What It Does |
|---------|--------------|
| `/cpv-fix-validation` | Automatically fix issues found in a validation report. |
| `/cpv-doctor` | Health-check all installed plugins, settings, and marketplaces. Add `--fix` to auto-repair problems. |

The **doctor** checks: CLI authentication, settings file integrity, marketplace registrations, plugin validation status, and orphaned entries. With `--fix`, it removes stale entries and repairs broken settings.

---

## For Developers

### Project Overview

| Category | Count | Description |
|----------|-------|-------------|
| Validation scripts | 17 | Python validators covering all plugin components |
| Management scripts | 12 | Plugin lifecycle, marketplace operations, scaffolding |
| Agents | 6 | AI-powered validation, fixing, and management |
| Skills | 11 | Validation, management, publishing workflows |
| Commands | 38 | Slash commands for all operations |
| Tests | 1549 | Full coverage across all modules |

### Scripts Reference

<details>
<summary><strong>Validation Scripts</strong> (click to expand)</summary>

| Script | Purpose |
|--------|---------|
| `validate_plugin.py` | Main orchestrator -- runs all 17 sub-validators |
| `validate_skill_comprehensive.py` | Comprehensive skill validator (190+ rules) |
| `validate_hook.py` | Hook configuration validator |
| `validate_agent.py` | Agent definition validator |
| `validate_command.py` | Command definition validator |
| `validate_mcp.py` | MCP server config validator |
| `validate_lsp.py` | LSP server config validator |
| `validate_marketplace.py` | Marketplace manifest validator |
| `validate_security.py` | Security vulnerability scanner |
| `validate_scoring.py` | Quality score calculator |
| `validate_enterprise.py` | Enterprise compliance validator |
| `validate_documentation.py` | Documentation quality checker |
| `validate_encoding.py` | File encoding validator |
| `validate_rules.py` | Rules directory validator |
| `validate_xref.py` | Cross-reference validator |
| `validate_skill.py` | Basic skill validator |
| `validate_marketplace_pipeline.py` | Marketplace pipeline validator |

</details>

<details>
<summary><strong>Management Scripts</strong> (click to expand)</summary>

| Script | Purpose |
|--------|---------|
| `manage_plugin.py` | Install, uninstall, update, enable, disable plugins |
| `manage_registry.py` | List and search installed plugins |
| `manage_doctor.py` | Health-check and auto-fix (`--fix`) |
| `manage_marketplace.py` | Marketplace registration (add/remove/list/update) |
| `manage_remote.py` | Remote plugin operations via Claude CLI |
| `manage_github_validate.py` | Validate GitHub repos without installing |
| `bump_version.py` | Semantic version bumping |
| `cpv_management_common.py` | Shared infrastructure (JSONC parser, safe I/O, archives) |

</details>

<details>
<summary><strong>Creation and Utility Scripts</strong> (click to expand)</summary>

| Script | Purpose |
|--------|---------|
| `generate_plugin_repo.py` | Scaffold a complete plugin repository |
| `generate_marketplace_repo.py` | Scaffold a marketplace hub repository |
| `standardize_plugin.py` | Audit and fix plugin repo to match standards |
| `standardize_marketplace.py` | Audit and fix marketplace repo |
| `lint_files.py` | Read-only file linting for 15 languages |
| `cpv_token_cost.py` | Token cost reporter |
| `smart_exec.py` | Cross-platform script executor |
| `cli.py` | CLI entry points for uvx/pip |

</details>

### Agents

| Agent | What It Does |
|-------|-------------|
| **plugin-validator** | Runs validation scripts, interprets results, and produces reports. |
| **skill-validation-agent** | Specialized skill validation with strict-mode checks. |
| **plugin-fixer** | Reads validation reports and automatically fixes the issues found. |
| **semantic-validator** | Deep AI-driven analysis of skill and agent quality (uses opus). |
| **plugin-manager** | Manages plugin lifecycle: install, enable, disable, doctor. |
| **plugin-creator** | Scaffolds new plugins and marketplaces with CI/CD pipelines. |

---

## Requirements

- **Python 3.12** or newer
- **uv** ([install uv](https://docs.astral.sh/uv/getting-started/installation/)) -- the fast Python package manager

That's it. No API keys, no accounts, no cloud services needed for validation.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `uvx` command not found | Install uv: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Plugin not loading after install | Restart Claude Code, then run `/cpv-doctor` to diagnose |
| Import errors when running scripts | Run `uv sync` in the CPV directory |
| CRITICAL or MAJOR issues (exit 1-2) | These must be fixed -- the plugin may be broken or insecure |
| MINOR issues (exit 3) | Recommendations for quality -- fix when convenient |
| NIT issues (exit 4) | Only appear in `--strict` mode -- optional polish |
| Validation takes too long | Use `--report file.md` to save output; use specific validators instead of full scan |

## License

MIT License -- see LICENSE file.

## Author

Emasoft (713559+Emasoft@users.noreply.github.com)
