# Claude Plugins Validation (CPV)

<!--BADGES-START-->
![Version](https://img.shields.io/badge/version-2.5.2-blue)
![Tests](https://img.shields.io/badge/tests-1549%20passed-brightgreen)
![Validation](https://img.shields.io/badge/validation-0%20issues-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)
<!--BADGES-END-->

**Check if your Claude Code plugin is correct, secure, and ready to publish -- without spending a single token.**

CPV is a suite of Python scripts that analyze Claude Code plugins locally on your machine. Every check runs offline, produces instant results, and costs nothing. No AI calls, no API keys, no cloud services needed.

There are **two ways to use CPV**. Pick the one that fits your workflow:

| | **Standalone (via uvx)** | **Plugin (inside Claude Code)** |
|---|---|---|
| **What** | Run validation scripts from your terminal | Get slash commands, AI agents, and management tools inside Claude Code |
| **Install** | Nothing to install -- runs on the fly | Install once as a Claude Code plugin |
| **Best for** | Quick one-off checks, CI/CD pipelines, automation | Daily development, plugin management, AI-assisted fixing |
| **Jump to** | [Part 1: Standalone Validation](#part-1-standalone-validation-via-uvx) | [Part 2: Claude Code Plugin](#part-2-claude-code-plugin) |

---

## Table of Contents

- [What Does CPV Check?](#what-does-cpv-check)
  - [Claude Code Documentation](#claude-code-documentation)
- **[Part 1: Standalone Validation (via uvx)](#part-1-standalone-validation-via-uvx)**
  - [Getting Started](#getting-started)
  - [Available Validators](#available-validators)
  - [Options](#options)
  - [Reading the Results](#reading-the-results)
- **[Part 2: Claude Code Plugin](#part-2-claude-code-plugin)**
  - [Installation](#installation)
  - [Validation Commands](#validation-commands)
  - [Plugin Management Commands](#plugin-management-commands)
  - [AI Agents](#ai-agents)
- [For Developers](#for-developers)
- [Requirements](#requirements)
- [Troubleshooting](#troubleshooting)

---

## What Does CPV Check?

CPV runs **17 specialized validators** covering **190+ rules** across every part of a Claude Code plugin:

| Area | Examples of what CPV catches |
|------|------------------------------|
| **Structure** | Missing `plugin.json`, wrong directory layout, missing required files |
| **Hooks** | Invalid event names, broken script paths, unsafe shell commands |
| **Skills** | Poor descriptions that won't trigger, missing sections, files too large |
| **Security** | Hardcoded secrets, path traversal, command injection, prompt injection |
| **Compatibility** | Windows/macOS/Linux path issues, encoding problems, broken references |
| **Quality** | Missing documentation, no license, inconsistent versions, dead links |

All checks run as pure Python -- no API calls, no tokens consumed, no data sent anywhere.

### Claude Code Documentation

CPV validates plugins against the official Claude Code specification. If you are building a plugin, these are the key references:

- [Discover plugins](https://code.claude.com/docs/en/discover-plugins) -- official guide to Claude Code plugins
- [Claude Code release notes](https://docs.anthropic.com/en/release-notes/claude-code) -- latest changes and plugin updates

---

## Part 1: Standalone Validation (via uvx)

> **No installation needed.** Just run the command and point it at your plugin folder.

### Getting Started

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) installed (the fast Python package manager). Then:

```bash
# Validate a plugin (runs all 17 checks)
uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-validate /path/to/your-plugin
```

That's it. `uvx` downloads CPV temporarily into an isolated environment, runs the validation, and shows the results. Nothing is installed permanently on your system.

The `--with pyyaml` flag ensures the YAML parser dependency is available. While `uvx` normally installs dependencies automatically, adding `--with` explicitly guarantees they are present.

More examples:

```bash
# Show detailed output (including checks that passed)
uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-validate /path/to/your-plugin --verbose

# Save a full report to a file
uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-validate /path/to/your-plugin --report report.md

# Run only the security scanner
uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-validate-security /path/to/your-plugin

# Run only the skill validator
uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-validate-skill /path/to/your-plugin
```

> **Tip:** The commands are long because of the GitHub URL. Create a shell alias to shorten them:
> ```bash
> alias cpv='uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml'
> ```
> Then just use: `cpv cpv-validate /path/to/plugin`

### Available Validators

| CLI Command | What It Checks |
|-------------|----------------|
| `cpv-validate` | **Everything.** Runs all 17 validators below in sequence. Start here. |
| `cpv-validate-skill` | **Skills.** Checks SKILL.md for frontmatter, required sections, triggering phrases, description length, token budget, and reference file integrity. 190+ rules. |
| `cpv-validate-hooks` | **Hooks.** Validates `hooks.json` against 26 event types and 4 hook types. Checks script paths, bash portability, and environment variables. |
| `cpv-validate-agents` | **Agents.** Checks agent `.md` files for frontmatter fields, naming, tool references, and markdown structure. |
| `cpv-validate-command` | **Commands.** Checks command `.md` files for required frontmatter, valid tool names, and naming conventions. |
| `cpv-validate-security` | **Security.** Scans for injection, path traversal, secrets, prompt injection, data exfiltration, supply chain risks, credential harvesting, hook/MCP abuse, and sandbox escape. |
| `cpv-validate-scoring` | **Quality score.** Computes a weighted score across structure, documentation, security, testing, hooks, skills, and compliance. |
| `cpv-validate-marketplace` | **Marketplace.** Validates `marketplace.json` structure, plugin entries, source references, and field types. |
| `cpv-validate-enterprise` | **Enterprise compliance.** Checks author info, license, SPDX identifiers, keyword tags, and organizational metadata. |
| `cpv-validate-mcp` | **MCP servers.** Validates `.mcp.json` transport types, required fields, OAuth config, and cross-platform paths. |
| `cpv-validate-lsp` | **LSP servers.** Checks LSP definitions for valid command paths, language identifiers, and file patterns. |
| `cpv-validate-documentation` | **Documentation.** Checks README for required sections, working links, and image references. |
| `cpv-validate-encoding` | **Encoding.** Verifies UTF-8, detects BOM, checks line endings, and flags binary characters in text files. |
| `cpv-validate-rules` | **Rules.** Validates `.md` files in the `rules/` directory for structure and content. |
| `cpv-validate-xref` | **Cross-references.** Checks agent refs, subagent types, version consistency, and that referenced files exist. |
| `cpv-doctor` | **Health check.** Diagnoses installed plugins, settings, and marketplaces. |
| `cpv-standardize` | **Standards.** Audits and fixes a plugin or marketplace repo to match CPV standards. |

### Options

These flags work with all validators:

| Flag | What It Does |
|------|-------------|
| `--verbose` or `-v` | Show all results, including checks that passed. |
| `--report PATH` | Save full report to a file; print compact summary to terminal. |
| `--json` | Output as JSON (for scripts and CI/CD). |
| `--strict` | Treat NIT-level issues as failures too. |
| `--marketplace-only` | Skip `plugin.json` requirement (for marketplace-only distribution). |

### Reading the Results

#### Severity Levels

| Level | What It Means | Must Fix? |
|-------|--------------|-----------|
| **CRITICAL** | Plugin is broken -- won't work correctly | Yes |
| **MAJOR** | Significant problem affecting functionality or security | Yes |
| **MINOR** | Small issue that reduces quality but doesn't break anything | Recommended |
| **NIT** | Style nitpick or minor suggestion | Only in `--strict` mode |
| **WARNING** | Potential concern worth reviewing | No |
| **INFO** | Neutral observation (e.g., "found 3 skills") | No |
| **PASSED** | Check passed successfully | No |

#### Exit Codes

For use in scripts and CI/CD pipelines:

| Exit Code | Meaning |
|-----------|---------|
| `0` | All checks passed |
| `1` | CRITICAL issues found |
| `2` | MAJOR issues found |
| `3` | MINOR issues found |
| `4` | NIT issues found (only in `--strict` mode) |

---

## Part 2: Claude Code Plugin

> **For Claude Code users.** Install CPV once to get slash commands, AI agents, and plugin management tools directly inside Claude Code.

### Installation

```bash
# Add the Emasoft marketplace (first time only)
claude plugin marketplace add emasoft-plugins --url https://github.com/Emasoft/emasoft-plugins

# Install CPV (--scope user makes it available in all projects)
claude plugin install claude-plugins-validation@emasoft-plugins --scope user

# IMPORTANT: Restart Claude Code after installing
```

For development: `claude --plugin-dir ./claude-plugins-validation`

### Validation Commands

Once installed, use these slash commands inside Claude Code:

| Command | What It Validates |
|---------|-------------------|
| `/cpv-validate-plugin` | **Full validation** -- runs all 17 sub-validators. Start here. |
| `/cpv-validate-skill` | Skill quality (190+ rules, Nixtla strict-mode) |
| `/cpv-validate-hooks` | Hook configuration (26 events, 4 hook types, bash portability) |
| `/cpv-validate-agents` | Agent definitions (frontmatter, tools, naming) |
| `/cpv-validate-command` | Command definitions (frontmatter, tool names, arguments) |
| `/cpv-validate-security` | Security scan (injection, secrets, prompt injection, exfiltration) |
| `/cpv-validate-scoring` | Quality score (weighted across 7 categories) |
| `/cpv-validate-marketplace` | Marketplace manifest (structure, entries, field types) |
| `/cpv-validate-enterprise` | Enterprise compliance (license, author, SPDX, tags) |
| `/cpv-validate-mcp` | MCP server config (transport, OAuth, paths) |
| `/cpv-validate-lsp` | LSP server config (commands, languages, patterns) |
| `/cpv-validate-documentation` | Documentation quality (README sections, links, images) |
| `/cpv-validate-encoding` | File encoding (UTF-8, BOM, line endings) |
| `/cpv-validate-rules` | Rule files (structure, naming, content) |
| `/cpv-validate-xref` | Cross-references (agent refs, versions, scripts) |
| `/cpv-validate-github-plugin` | Validate a GitHub plugin without installing (add `--audit` for security) |
| `/cpv-validate-github-marketplace` | Validate a GitHub marketplace without registering (add `--audit`) |
| `/cpv-semantic-validation` | **Deep AI analysis** using Claude opus. A-F grades for skill quality. **This is the only command that uses AI tokens.** |

### Plugin Management Commands

CPV also provides tools for the full plugin lifecycle:

#### Install, Update, Remove

| Command | What It Does |
|---------|--------------|
| `/cpv-install-plugin-from-local-mp` | Install from a local directory or archive |
| `/cpv-uninstall-plugin-from-local-mp` | Remove and clean up settings |
| `/cpv-update-plugin` | Update from a new source |
| `/cpv-manage-remote-plugins` | Install/update/remove from GitHub marketplaces |

#### Enable and Disable

| Command | What It Does |
|---------|--------------|
| `/cpv-enable-plugin` | Turn on a plugin (`--scope user` or `--scope local`) |
| `/cpv-disable-plugin` | Turn off without removing (`--scope user` or `--scope local`) |

Smart name resolution: use `plugin-name`, `name@marketplace`, or `name@owner/marketplace`.

#### Browse and Search

| Command | What It Does |
|---------|--------------|
| `/cpv-list-plugins` | List installed plugins with version and status |
| `/cpv-list-mp-plugins` | List plugins in a marketplace |
| `/cpv-search-plugins` | Search by component type or text |
| `/cpv-manage-marketplaces` | Add, remove, list, update marketplaces |
| `/cpv-version` | Show CPV version |

#### Create and Publish

| Command | What It Does |
|---------|--------------|
| `/cpv-create-local-plugin` | Scaffold a new plugin with all standard files |
| `/cpv-create-local-marketplace` | Scaffold a new marketplace hub |
| `/cpv-publish-a-plugin-as-github-repo` | Full pipeline: validate, push, set up CI/CD |
| `/cpv-create-a-github-marketplace` | Create a GitHub marketplace with CI/CD |
| `/cpv-publish-a-plugin-to-a-github-marketplace` | Register a plugin in a marketplace |
| `/cpv-standardize` | Audit and fix a repo to match standards |
| `/cpv-bump-version` | Bump version (patch, minor, major) |

#### Fix and Repair

| Command | What It Does |
|---------|--------------|
| `/cpv-fix-validation` | Auto-fix issues from a validation report |
| `/cpv-doctor` | Health-check plugins, settings, marketplaces (`--fix` to auto-repair) |

### AI Agents

These agents work inside Claude Code to automate complex tasks:

| Agent | What It Does |
|-------|-------------|
| **plugin-validator** | Runs validation scripts and produces reports |
| **skill-validation-agent** | Specialized skill validation with strict-mode |
| **plugin-fixer** | Reads a validation report and automatically fixes the issues |
| **semantic-validator** | Deep AI quality analysis (uses opus, explicit opt-in) |
| **plugin-manager** | Plugin lifecycle: install, enable, disable, doctor |
| **plugin-creator** | Scaffolds plugins and marketplaces with CI/CD |

---

## For Developers

<details>
<summary><strong>Project Overview</strong></summary>

| Category | Count | Description |
|----------|-------|-------------|
| Validation scripts | 17 | Python validators covering all plugin components |
| Management scripts | 12 | Plugin lifecycle, marketplace operations, scaffolding |
| Agents | 6 | AI-powered validation, fixing, and management |
| Skills | 11 | Validation, management, publishing workflows |
| Commands | 38 | Slash commands for all operations |
| Tests | 1549 | Full coverage across all modules |

</details>

<details>
<summary><strong>Validation Scripts</strong></summary>

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
<summary><strong>Management Scripts</strong></summary>

| Script | Purpose |
|--------|---------|
| `manage_plugin.py` | Install, uninstall, update, enable, disable |
| `manage_registry.py` | List and search installed plugins |
| `manage_doctor.py` | Health-check and auto-fix |
| `manage_marketplace.py` | Marketplace registration |
| `manage_remote.py` | Remote plugin operations |
| `manage_github_validate.py` | Validate GitHub repos without installing |
| `bump_version.py` | Semantic version bumping |
| `cpv_management_common.py` | Shared infrastructure |

</details>

<details>
<summary><strong>Creation and Utility Scripts</strong></summary>

| Script | Purpose |
|--------|---------|
| `generate_plugin_repo.py` | Scaffold a complete plugin repository |
| `generate_marketplace_repo.py` | Scaffold a marketplace hub |
| `standardize_plugin.py` | Audit and fix plugin repo |
| `standardize_marketplace.py` | Audit and fix marketplace repo |
| `lint_files.py` | Read-only linting for 15 languages |
| `cpv_token_cost.py` | Token cost reporter |
| `smart_exec.py` | Cross-platform script executor |
| `cli.py` | CLI entry points for uvx/pip |

</details>

---

## Requirements

### For standalone validation (Part 1 -- via uvx)

| Requirement | Why | How to Install |
|-------------|-----|----------------|
| **Python 3.12+** | Runtime for all validation scripts | [python.org](https://www.python.org/downloads/) or your OS package manager |
| **uv** | Runs CPV via `uvx` without permanent installation | `curl -LsSf https://astral.sh/uv/install.sh \| sh` ([docs](https://docs.astral.sh/uv/getting-started/installation/)) |

That's all you need. Python dependencies (like `pyyaml`) are installed automatically by `uvx` into a temporary environment.

### For the Claude Code plugin (Part 2)

| Requirement | Why | How to Install |
|-------------|-----|----------------|
| **Claude Code** | The AI coding tool that CPV extends | [docs.anthropic.com](https://docs.anthropic.com/en/docs/claude-code/overview) |
| **Python 3.12+** | Runtime for validation scripts | [python.org](https://www.python.org/downloads/) |
| **uv** | Runs Python scripts inside the plugin | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

### Optional tools (enhance some checks)

These are **not required**. If missing, CPV skips the checks that need them and tells you.

| Tool | What It Enables | How to Install |
|------|----------------|----------------|
| **Node.js / npx** | `cc-audit` external security scanner (100+ extra rules) | [nodejs.org](https://nodejs.org/) |
| **shellcheck** | Bash script portability checks in hooks | `brew install shellcheck` or [shellcheck.net](https://www.shellcheck.net/) |
| **gh** (GitHub CLI) | Remote plugin/marketplace validation and publishing | `brew install gh` or [cli.github.com](https://cli.github.com/) |

No API keys, accounts, or cloud services needed for any validation.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `uvx` command not found | Install uv: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Plugin not loading after install | Restart Claude Code, then run `/cpv-doctor` |
| Import errors | Run `uv sync` in the CPV directory |
| CRITICAL/MAJOR issues (exit 1-2) | Must fix -- plugin may be broken or insecure |
| MINOR issues (exit 3) | Recommendations -- fix when convenient |
| NIT issues (exit 4) | Only in `--strict` mode -- optional |

## License

MIT License -- see LICENSE file.

## Author

Emasoft (713559+Emasoft@users.noreply.github.com)
