# Claude Plugins Validation (CPV)

<!--BADGES-START-->
![Version](https://img.shields.io/badge/version-2.15.0-blue)
![Tests](https://img.shields.io/badge/tests-1947%20passed-brightgreen)
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
  - [Available Scripts](#available-scripts)
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

CPV runs **18 specialized validators** covering **190+ rules** across every part of a Claude Code plugin (15 plugin validators + 3 marketplace validators):

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

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) installed. Then:

```bash
# Validate a plugin (runs all 18 checks + linting)
uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml \
    cpv-remote-validate validate_plugin /path/to/your-plugin
```

That's it. `uvx` downloads CPV temporarily into an isolated environment, runs the validation with full environment isolation (the target plugin's local configs can't interfere), and shows results. Nothing is installed permanently.

The `--with pyyaml` flag ensures the YAML parser dependency is available.

### The Remote Launcher

`cpv-remote-validate` is the recommended way to validate external plugins. It wraps any CPV script with environment isolation so that the target plugin's local files (`pyproject.toml`, `.mypy.ini`, stale module copies) cannot interfere with validation.

```bash
# Full plugin validation (short alias)
uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml \
    cpv-remote-validate plugin /path/to/plugin

# Save a report to a file
uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml \
    cpv-remote-validate plugin /path/to/plugin -o report.md

# Validate a single skill with strict mode
uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml \
    cpv-remote-validate skill /path/to/skill --strict

# Security scan
uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml \
    cpv-remote-validate security /path/to/plugin

# Show help and all available commands
uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml \
    cpv-remote-validate --help

# From the CPV plugin cache (inside Claude Code):
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" plugin /path/to/plugin
```

> **Tip:** Create a shell alias to shorten the commands:
> ```bash
> alias cpv='uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-remote-validate'
> ```
> Then just: `cpv plugin /path/to/plugin` or `cpv skill /path/to/skill --strict`

### Available Scripts

Any of these can be passed as the first argument to `cpv-remote-validate`. Short aliases and full script names both work.

| Command | What It Checks |
|---------|----------------|
| `plugin` | **Everything.** Runs all 18 sub-validators + linting. Start here. |
| `skill` | **Skills.** SKILL.md frontmatter, required sections, description quality. 190+ rules. |
| `hook` | **Hooks.** 27 event types, 4 hook types, script paths, bash portability. |
| `agent` | **Agents.** Frontmatter fields, naming, tools, model, skills. |
| `command` | **Commands.** Frontmatter, tool names, arguments, naming. |
| `security` | **Security.** Injection, path traversal, secrets, prompt injection, exfiltration. |
| `scoring` | **Quality score.** Weighted across structure, docs, security, testing. |
| `marketplace` | **Marketplace.** Manifest structure, plugin entries, source references. Supports Layout A (hub-and-spoke) and Layout B (nested). |
| `settings-marketplace` | **Inline marketplace in settings.** Validates `marketplaces` entries embedded in Claude Code settings files. |
| `enterprise` | **Enterprise.** Author, license, SPDX, keywords, metadata. |
| `mcp` | **MCP.** Transport types, required fields, OAuth, paths. |
| `lsp` | **LSP.** Command paths, language IDs, file patterns. |
| `docs` | **Documentation.** README sections, links, images. |
| `encoding` | **Encoding.** UTF-8, BOM, line endings, binary detection. |
| `rules` | **Rules.** Structure and content of rules/*.md files. |
| `xref` | **Cross-references.** Agent refs, versions, scripts. |
| `doctor` | **Health check.** Plugins, settings, marketplaces. |
| `lint` | **Lint only.** All 15 languages (Python, JS, Shell, Go, Rust, etc.). |

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
| **plugin-fixer** | Fixes **plugin** validation errors (mechanical per-error remediation) | "Fix the validation errors in my plugin", "Fix this plugin.json report" |
| **marketplace-fixer** | Fixes **marketplace** validation errors *and* runs interactive architectural migrations between Layout A and Layout B | "Fix the marketplace errors", "Migrate my marketplace to the nested layout" |
| **semantic-validator** | Deep AI quality analysis — catches things scripts cannot (see below) | "Check if descriptions actually match what skills do" |
| **plugin-manager** | Full plugin lifecycle: install, update, enable, disable, search, health-check | "Install a plugin", "List my plugins", "Run doctor" |
| **plugin-creator** | Scaffolds plugins, marketplaces, publishes to GitHub with CI/CD | "Create a new plugin", "Publish to GitHub", "Set up a marketplace" |

Every agent presents a menu when invoked, asks what you need, and guides you step by step. You don't need to remember command names or flags.

#### Separation of Concerns: Plugins vs. Marketplaces

CPV treats **plugins** and **marketplaces** as two distinct concerns with dedicated validators, fix agents, and error indexes. This avoids the kitchen-sink fixer problem where one agent tries to reason about both an individual plugin's `plugin.json` and a marketplace's `marketplace.json` + release pipeline at the same time.

| Concern | Validators | Fix agent | Error index |
|---------|------------|-----------|-------------|
| Plugin | `validate_plugin.py` (orchestrator) + 14 component sub-validators (hook, skill, agent, command, mcp, lsp, security, scoring, enterprise, docs, encoding, rules, xref, skill-basic) | `plugin-fixer` | [`skills/fix-validation/references/plugin-error-index.md`](skills/fix-validation/references/plugin-error-index.md) |
| Marketplace | `validate_marketplace.py`, `validate_marketplace_pipeline.py`, `validate_settings_marketplace.py` | `marketplace-fixer` | [`skills/fix-validation/references/marketplace-error-index.md`](skills/fix-validation/references/marketplace-error-index.md) |

**Mechanical fixes vs. architectural migrations.** The `fix-validation` and `fix-marketplace-validation` skills do **mechanical per-error remediation** — one rule, one fix, minimal user input. The separate `migrate-marketplace-architecture` skill (loaded by `marketplace-fixer`) does **interactive architectural conversion** with extensive `AskUserQuestion` prompts: it walks you through converting a Layout A hub-and-spoke marketplace into a Layout B nested single-repo marketplace or vice versa, including publish pipeline, CI, and CHANGELOG restructuring.

#### Supported Marketplace Layouts

CPV validates and supports two marketplace layouts — both are first-class:

| Layout | Shape | When to use |
|--------|-------|-------------|
| **Layout A — Hub and spoke** | One marketplace repo that references plugins living in **separate GitHub repos** (one repo per plugin). | Multiple authors, plugins from different organizations, plugins with independent release cadences. |
| **Layout B — Nested single-repo** | One marketplace repo that contains **all its plugins as subdirectories**, with a single author and full CPV release discipline: `publish.py`, `cliff.toml`, `CHANGELOG.md`, CI, single-version bumps. | Single author, atomic cross-plugin releases, unified CI pipeline. |

Full layout specifications and decision criteria: [`skills/create-plugin/references/marketplace-layouts.md`](skills/create-plugin/references/marketplace-layouts.md).

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

20 commands — 12 run scripts directly (zero AI tokens), 6 spawn an agent, 2 are specialized utility scripts.

#### Script Commands (free — no AI tokens)

| Command | What It Does |
|---------|--------------|
| `/cpv-validate-plugin <path>` | **Full validation** -- runs all 18 sub-validators |
| `/cpv-validate-skill <path>` | Skill validation (190+ rules) |
| `/cpv-validate-github-plugin <owner/repo>` | Validate a GitHub plugin without installing |
| `/cpv-validate-github-marketplace <owner/repo>` | Validate a GitHub marketplace without registering |
| `/cpv-validate-project-scope <path>` | Validate git-tracked (project-scope) Claude Code config under a project: `.claude/settings.json`, `.mcp.json`, agents, skills, commands, rules, `CLAUDE.md`. Rejects `autoMemoryDirectory`, managed-only keys, secrets in env, absolute home paths. |
| `/cpv-validate-local-scope <path>` | Validate non-git-tracked (local-scope) Claude Code config under a project: `.claude/settings.local.json`, `CLAUDE.local.md`, gitignored agents/skills/commands/rules, per-project MCP state in `~/.claude.json`. Rules are relaxed (personal paths are OK), but managed-only/global-config keys still rejected. |
| `/cpv-doctor` | Health-check installed plugins, settings, marketplaces |
| `/cpv-list-plugins` | List installed plugins with version and status |
| `/cpv-bump-version <path>` | Bump plugin version (patch, minor, major) |
| `/cpv-setup-branch-rules <owner/repo>` | Create/update the GitHub ruleset that enforces CI as a required status check (server-side gate — idempotent, auto-detects plugin vs marketplace, preserves existing bot bypass actors) |
| `/cpv-setup-branch-rules-generic <owner/repo> --check "job-name"` | Project-agnostic variant that works on ANY GitHub repo (not just CPV plugins). Requires explicit `--check` contexts, no hardcoded defaults. Also available as `uvx branch-rules-install` or as a self-contained bash script (`scripts/branch_rules_install.sh`) that you can drop into any project folder — only `gh` + `jq` required, no Python. |
| `/cpv-version` | Show CPV version |

#### Agent Commands (interactive — uses AI tokens)

| Command | Agent | What It Does |
|---------|-------|--------------|
| `/cpv-validate` | plugin-validator | Interactive: asks what to validate, runs the right script |
| `/cpv-manage` | plugin-manager | Interactive: install, update, enable, disable, search, doctor |
| `/cpv-create` | plugin-creator | Interactive: create plugins, marketplaces, publish to GitHub |
| `/cpv-fix-validation <report>` | plugin-fixer | Fixes **plugin** validation issues from a report |
| `/cpv-fix-marketplace-validation <report>` | marketplace-fixer | Fixes **marketplace** validation issues and runs architectural migrations |
| `/cpv-semantic-validation <path>` | semantic-validator | Deep AI quality analysis (Opus, expensive, explicit opt-in) |

#### Specialized Utility Commands

| Command | What It Does |
|---------|--------------|
| `/cpv-link-plugin <path>` | Link a local plugin directory into Claude Code for live development |
| `/cpv-validate-settings-marketplace <path>` | Validate inline `marketplaces` entries embedded in a settings file |

---

## For Developers

<details>
<summary><strong>Project Overview</strong></summary>

| Category | Count | Description |
|----------|-------|-------------|
| Validation scripts | 20 | Python validators (15 plugin + 3 marketplace + 2 scope) covering plugin packages, marketplaces, and end-user `.claude/` configuration |
| Management scripts | 13 | Plugin lifecycle, marketplace operations, scaffolding |
| Agents | 7 | AI-powered validation, fixing, and management |
| Skills | 14 | Validation, management, publishing, fix, migration, and auto-notify workflows |
| Commands | 18 | 10 direct script + 6 agent-backed + 2 specialized utility commands |
| Tests | 1800 | Full coverage across all modules |

</details>

<details>
<summary><strong>Validation Scripts</strong></summary>

| Script | Purpose |
|--------|---------|
| `validate_plugin.py` | Main orchestrator -- runs all 18 sub-validators |
| `validate_skill_comprehensive.py` | Comprehensive skill validator (190+ rules) |
| `validate_hook.py` | Hook configuration validator |
| `validate_agent.py` | Agent definition validator |
| `validate_command.py` | Command definition validator |
| `validate_mcp.py` | MCP server config validator |
| `validate_lsp.py` | LSP server config validator |
| `validate_marketplace.py` | Marketplace manifest validator (supports Layout A and Layout B) |
| `validate_marketplace_pipeline.py` | Marketplace release pipeline validator (publish.py, CI, CHANGELOG) |
| `validate_settings_marketplace.py` | Inline marketplace-in-settings validator |
| `validate_security.py` | Security vulnerability scanner |
| `validate_scoring.py` | Quality score calculator |
| `validate_enterprise.py` | Enterprise compliance validator |
| `validate_documentation.py` | Documentation quality checker |
| `validate_encoding.py` | File encoding validator |
| `validate_rules.py` | Rules directory validator |
| `validate_xref.py` | Cross-reference validator |
| `validate_skill.py` | Basic skill validator |

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

### Required

| Requirement | Why | How to Install |
|-------------|-----|----------------|
| **Python 3.12+** | Runtime for all validation scripts | [python.org](https://www.python.org/downloads/) or your OS package manager |
| **uv** | Runs CPV scripts and manages dependencies. Also provides `uvx` for running CPV from GitHub without installing | `curl -LsSf https://astral.sh/uv/install.sh \| sh` ([docs](https://docs.astral.sh/uv/getting-started/installation/)) |
| **Claude Code** | Required for Part 2 (plugin use). Not needed for Part 1 (standalone) | [code.claude.com](https://code.claude.com) |

CPV uses `uv` to run scripts (`uv run`), install Python linters (`ruff`, `mypy`) when not found locally (`uvx`), and manage the virtual environment. Python dependencies like `pyyaml` are installed automatically.

### Optional tools (enhance linting)

CPV validates scripts in **6 languages**. For each language, it tries the local install first, then `uvx`/`npx`. If no linter is found, CPV reports what's missing and skips that check.

| Language | Linter | How to Install |
|----------|--------|----------------|
| **Python** (.py) | ruff + mypy | Included via `uv` — always available |
| **Shell** (.sh, .bash) | shellcheck | `brew install shellcheck` or [shellcheck.net](https://www.shellcheck.net/) |
| **JavaScript/TypeScript** (.js, .ts) | eslint | `npm install -g eslint` or [nodejs.org](https://nodejs.org/) |
| **PowerShell** (.ps1) | PSScriptAnalyzer | `pwsh -c 'Install-Module PSScriptAnalyzer -Scope CurrentUser'` |
| **Go** (.go) | go vet | [go.dev/dl](https://go.dev/dl/) |
| **Rust** (Cargo.toml) | cargo check | [rustup.rs](https://rustup.rs/) |

Other optional tools:

| Tool | What It Enables | How to Install |
|------|----------------|----------------|
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
