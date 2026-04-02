# Claude Plugins Validation (CPV)

<!--BADGES-START-->
![Version](https://img.shields.io/badge/version-2.6.4-blue)
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
  - [AI Agents — The Main Interface](#ai-agents--the-main-interface)
  - [Slash Commands](#slash-commands)
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

- [Discover plugins](https://code.claude.com/docs/en/discover-plugins) -- finding and installing plugins
- [Plugins reference](https://code.claude.com/docs/en/plugins-reference) -- plugin.json schema, CLI commands, component specs
- [CLI commands reference](https://code.claude.com/docs/en/plugins-reference#cli-commands-reference) -- install, uninstall, enable, disable, update
- [Skills reference](https://code.claude.com/docs/en/skills) -- SKILL.md frontmatter, substitutions, dynamic context
- [Hooks reference](https://code.claude.com/docs/en/hooks) -- 27 hook events, matchers, hook types
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

For the full CLI commands reference, see the [official Anthropic docs](https://code.claude.com/docs/en/plugins-reference#cli-commands-reference).

```bash
# Add the Emasoft marketplace (first time only)
claude plugin marketplace add emasoft-plugins --url https://github.com/Emasoft/emasoft-plugins

# Install CPV (--scope user = available in all your projects, recommended)
claude plugin install claude-plugins-validation@emasoft-plugins --scope user

# OR install for this project only (--scope local, gitignored)
claude plugin install claude-plugins-validation@emasoft-plugins --scope local

# IMPORTANT: Restart Claude Code after installing, or run /reload-plugins
```

### Managing the Plugin

```bash
# Update to the latest version
claude plugin update claude-plugins-validation@emasoft-plugins --scope user

# Disable without removing
claude plugin disable claude-plugins-validation@emasoft-plugins --scope user

# Re-enable
claude plugin enable claude-plugins-validation@emasoft-plugins --scope user

# Uninstall completely
claude plugin uninstall claude-plugins-validation@emasoft-plugins --scope user

# Uninstall but keep persistent data
claude plugin uninstall claude-plugins-validation@emasoft-plugins --scope user --keep-data
```

Replace `--scope user` with `--scope local` or `--scope project` depending on where you installed it:

| Scope | Settings file | Who can use it |
|-------|--------------|----------------|
| `user` | `~/.claude/settings.json` | You, in all projects (default) |
| `project` | `.claude/settings.json` | Everyone who clones the repo |
| `local` | `.claude/settings.local.json` | You, in this project only (gitignored) |

For development: `claude --plugin-dir ./claude-plugins-validation`

### AI Agents — The Main Interface

When used as a Claude Code plugin, **agents are the primary way to interact with CPV**. Each agent is a specialized AI assistant that knows which scripts to run, which skills to consult, and how to guide you through the process. Just tell Claude what you need and the right agent takes over.

| Agent | What It Does | Ask it when you want to... |
|-------|-------------|---------------------------|
| **plugin-validator** | Runs validation scripts and returns severity reports | "Validate my plugin", "Check if this is ready to publish" |
| **skill-validation-agent** | Specialized skill validation (basic, strict, OpenSpec, Pillars) | "Validate this skill", "Check my SKILL.md" |
| **plugin-fixer** | Reads a validation report and automatically fixes the issues | "Fix the validation errors", "How do I fix these issues?" |
| **semantic-validator** | Deep AI quality analysis — catches things scripts cannot (see below) | "Check if descriptions actually match what skills do" |
| **plugin-manager** | Full plugin lifecycle: install, update, enable, disable, search, health-check | "Install a plugin", "List my plugins", "Run doctor" |
| **plugin-creator** | Scaffolds plugins, marketplaces, publishes to GitHub with CI/CD | "Create a new plugin", "Publish to GitHub", "Set up a marketplace" |

Every agent presents a menu when invoked, asks what you need, and guides you step by step. You don't need to remember command names or flags.

#### Script Validation vs. Semantic Validation

CPV has two validation layers:

| | Script Validation | Semantic Validation |
|---|---|---|
| **How** | Python scripts check structure, syntax, types, cross-references | AI agent (Opus) reads files and evaluates actual content |
| **Cost** | Zero tokens — runs locally, instant results | ~10-50x more expensive — uses Opus with 1M context |
| **Catches** | Missing files, wrong types, broken paths, encoding, security patterns | Wrong descriptions, unclear instructions, missing checkpoints, unrealistic examples, workflows without exit conditions |
| **Coverage** | ~95% of real issues | The remaining ~5% that only a reader can catch |
| **When** | Always. Run this first. | Only when script validation passes clean but something still feels wrong. |

The semantic validator always warns about the cost and asks for confirmation before running. In most cases, script validation is all you need.

### Slash Commands

13 commands — 8 run scripts directly (zero AI tokens), 5 spawn the right agent.

#### Script Commands (free — no AI tokens)

| Command | What It Does |
|---------|--------------|
| `/cpv-validate-plugin <path>` | **Full validation** -- runs all 17 sub-validators |
| `/cpv-validate-skill <path>` | Skill validation (190+ rules) |
| `/cpv-validate-github-plugin <owner/repo>` | Validate a GitHub plugin without installing |
| `/cpv-validate-github-marketplace <owner/repo>` | Validate a GitHub marketplace without registering |
| `/cpv-doctor` | Health-check installed plugins, settings, marketplaces |
| `/cpv-list-plugins` | List installed plugins with version and status |
| `/cpv-bump-version <path>` | Bump plugin version (patch, minor, major) |
| `/cpv-version` | Show CPV version |

#### Agent Commands (interactive — uses AI tokens)

| Command | Agent | What It Does |
|---------|-------|--------------|
| `/cpv-validate` | plugin-validator | Interactive: asks what to validate, runs the right script |
| `/cpv-manage` | plugin-manager | Interactive: install, update, enable, disable, search, doctor |
| `/cpv-create` | plugin-creator | Interactive: create plugins, marketplaces, publish to GitHub |
| `/cpv-fix-validation <report>` | plugin-fixer | Reads a validation report and fixes all issues |
| `/cpv-semantic-validation <path>` | semantic-validator | Deep AI quality analysis (Opus, expensive, explicit opt-in) |

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
| Commands | 13 | 8 direct script + 5 agent commands |
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
