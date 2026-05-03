# Claude Plugins Validation (CPV)

<!--BADGES-START-->
![Version](https://img.shields.io/badge/version-2.61.0-blue)
![Tests](https://img.shields.io/badge/tests-2336%20passed-brightgreen)
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

CPV runs **20 specialized validators** covering **190+ rules** across every part of a Claude Code plugin (17 plugin validators + 3 marketplace/settings validators):

| Area | Examples of what CPV catches |
|------|------------------------------|
| **Structure** | Missing `plugin.json`, wrong directory layout, missing required files |
| **Hooks** | Invalid event names, broken script paths, unsafe shell commands |
| **Skills** | Poor descriptions that won't trigger, missing sections, files too large |
| **Security** | Hardcoded secrets, path traversal, command injection, prompt injection |
| **Compatibility** | Windows/macOS/Linux path issues, encoding problems, broken references |
| **Quality** | Missing documentation, no license, inconsistent versions, dead links |
| **Empirical Loading Bugs** *(v2.23.0+)* | Silent-failure modes in CC's plugin loader that `claude plugin validate` doesn't catch — see below |

### Empirical Plugin-Loading Bugs CPV Catches

Through extensive empirical testing of Claude Code's plugin loader (April 2026), CPV identified five silent-failure modes that the official docs hide and that CC's own `claude plugin validate` does NOT detect. CPV catches all five:

1. **`agents` field with folder paths** — CC rejects with cryptic `agents: Invalid input`. If validate is skipped, agents are silently dropped at runtime. CPV emits MAJOR with helpful fix recipe (`.md` file paths only). The official docs' own complete-schema example showing `"./custom/agents/"` is incorrect.
2. **`hooks: "./hooks/hooks.json"` cascade** — pointing the override at the auto-discovered default file passes validation silently, but at runtime CC emits `Duplicate hooks file detected` AND **disables the plugin's MCP servers** with `error type: hook-load-failed`. CPV emits MAJOR.
3. **MCP cross-source server-name collision** — same server name in both `.mcp.json` and inline `plugin.json:mcpServers` causes silent inline-wins shadowing; the `.mcp.json` declaration is dropped without warning. CPV emits MAJOR per duplicate.
4. **LSP cross-source server-name collision** — same silent-shadow risk for LSP servers. CPV emits MAJOR per duplicate.
5. **`mcpServers: "./.mcp.json"` redundancy** — points the override at the auto-discovered default file. Harmless single load (no cascade like hooks) but redundant and confusing. CPV emits MINOR nudge.

All five rules are documented with empirical evidence in [`skills/fix-validation/references/empirical-loading-bugs.md`](skills/fix-validation/references/empirical-loading-bugs.md) (13 test plugin scenarios, debug-log excerpts, runtime probes).

All in-process checks run as pure Python — no API calls, no tokens consumed, no data sent anywhere.

### External Security Scanners (always-run, programmatic-only)

`validate_security.py` orchestrates **five external scanners** alongside its in-process rule packs. Each is invoked unconditionally on every scan and self-skips with an INFO advisory when its source binary cannot be resolved on PATH or installed from its source URL. There is **no opt-out flag** — preventing a caller from accidentally silencing coverage. The `enable_*` keyword arguments on `validate_security()` survive only as test-isolation knobs.

| # | Scanner | Source | What it adds | Resolution path |
|---|---------|--------|--------------|-----------------|
| 16 | **cc-audit** | [ryo-ebata/cc-audit](https://github.com/ryo-ebata/cc-audit) | 100+ AI-specific threat rules tailored to Claude Code plugins | persistent `cc-audit` (preferred — `npm install -g @cc-audit/cc-audit`) → `npx --yes @cc-audit/cc-audit` fallback |
| 17 | **tirith** | [sheeki03/tirith](https://github.com/sheeki03/tirith) | Terminal-security, homograph domains, ANSI/bidi/zero-width injection, hidden Unicode, supply-chain pipe-to-shell | PATH → docker → nix → auto-install (pipx/brew/npm/cargo); set `CPV_NO_TIRITH_INSTALL=1` to disable the install fallback |
| 18 | **trufflehog** | [trufflesecurity/trufflehog](https://github.com/trufflesecurity/trufflehog) | ~700 verified-secret detectors (Stripe, Slack, AWS, GitHub, …) — runs with `--concurrency=cpu_count` for parallel scans | `brew install trufflehog` or `go install github.com/trufflesecurity/trufflehog/v3@latest` |
| 19 | **semgrep** | [semgrep/semgrep](https://github.com/semgrep/semgrep) | Thousands of static-analysis rules via the `p/security-audit` and `p/secrets` rule packs | `brew install semgrep` or `pipx install semgrep` |
| 20 | **Cisco AI Defense skill-scanner** | [cisco-ai-defense/skill-scanner](https://github.com/cisco-ai-defense/skill-scanner) | Static (YAML+YARA), Bytecode, Pipeline (command taint), Behavioral (AST dataflow), Trigger (vague-description) — programmatic-only mode (no LLM/Meta/VirusTotal/AI Defense cloud, all of which need API keys) | persistent `skill-scanner` (preferred — `uv tool install cisco-ai-skill-scanner`) → `uvx --from cisco-ai-skill-scanner skill-scanner` fallback (set `CPV_CISCO_SCAN_TIMEOUT_S=<sec>` to override the 600s default) |

**v2.48 — gitleaks removed.** trufflehog (~700 detectors with `--concurrency` parallel-scan support) provides superset coverage. gitleaks shipped ~150 detectors and crashed under reliable parallel scanning, so it has been retired from the external-scanner roster.

**v2.48 — `cpv-doctor --install-scanners`.** A new batch installer fetches every scanner CPV uses with one command (silent and idempotent). It also installs **fclones** ([pkolaczk/fclones](https://github.com/pkolaczk/fclones)), a Rust-based duplicate-file finder used as the first step of every CPV scan to skip duplicate files (cross-plugin shared READMEs, vendored libs, identical SKILL.md templates). Per-platform cascade: macOS = `brew install fclones`, Linux = `snap install fclones` then `cargo install fclones`, Windows = GitHub-release download then `cargo install fclones`. Per-tool opt-outs: `CPV_NO_<TOOL>_INSTALL=1` (e.g. `CPV_NO_FCLONES_INSTALL=1`).

**v2.48 — `cpv-doctor --prune-old-versions`.** Frees disk space accumulated when `claude plugin update` doesn't remove old versions. Two-stage UX: `--prune-dry-run` previews; `--prune-old-versions` actually deletes. Always keeps the active version (whichever Claude Code references in `enabledPlugins`). `--prune-keep N` to retain N newest per plugin (default 1).

**v2.48 — URL / archive ingestion + loose mode.** The positional argument now accepts:

- **GitHub URLs**: `https://github.com/owner/repo` or shorthand `github:owner/repo` — cloned with `gh repo clone --depth 1` to a tmpdir, scanned, then cleaned up automatically.
- **Local archives**: `*.zip`, `*.tar.gz`, `*.tgz`, `*.tar.bz2`, `*.tar.xz`, `*.tar` — extracted to a tmpdir (with path-traversal protection), scanned, cleaned up.
- **Loose mode** for flat skill packs: `--loose` (alias for `--bare-folder`) bypasses the `.claude-plugin/` precondition. CPV auto-detects flat packs (5+ `*.md` files, no `plugin.json`, no canonical `skills/<name>/SKILL.md` layout) and prints a HINT recommending `--loose` in the error message.

**v2.48 — Marketplace tree-scan-once with dedup.** `--marketplace <spec>` now stages every plugin under one tmpdir, runs `fclones` ONCE on the entire corpus, deletes duplicate hardlinks (cache untouched — hardlinks share inodes safely), then scans each plugin's deduped staging. Findings on canonical files automatically propagate to peer plugins that originally contained a copy of the same content (no coverage hole from dedup). Real-world: ai-maestro-plugins (10 plugins) sees ~1,849 duplicate files / ~21 MB saved per scan run.

**v2.48 — `cpv-main-menu`.** Single interactive entry-point routes through every CPV command via nested AskUserQuestion sub-menus (Validate / GitHub / Fix / Create / Manage / GitHub setup / Semantic / Help). Every menu/sub-menu includes a Cancel/Exit option. Use this when you can't remember which slash command to invoke directly.

Every external scanner's findings are routed through the same self-scan filter chain CPV applies to its own rules (`cpv_self_scan_skip` → vendored-deps → dev-scratch → test-files → FP-corpus markdown → per-line catalog/docstring/comment pattern-source predicate). This guarantees that scanning CPV with CPV — or scanning any plugin that ships its own rule catalogs — never surfaces the catalog source as a finding. The aggregator then groups all findings by `(level, rule_id)` so each vulnerability TYPE shows its full explanation exactly once, followed by an occurrence count and a capped file:line list — bounded report size, no findings ever silently dropped.

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
# Validate a plugin (runs all 20 checks + linting)
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
| `plugin` | **Everything.** Runs all 20 sub-validators + linting. Start here. |
| `skill` | **Skills.** SKILL.md frontmatter, required sections, description quality. 190+ rules. |
| `hook` | **Hooks.** 28 event types, 5 hook types (incl. v2.1.118+ `mcp_tool`), script paths, bash portability. |
| `agent` | **Agents.** Frontmatter fields, naming, tools, model, skills. |
| `command` | **Commands.** Frontmatter, tool names, arguments, naming. |
| `security` | **Security.** Injection, path traversal, secrets, prompt injection, exfiltration. v2.48: 5 external scanners + fclones cross-plugin dedup. |
| `cache` | **Prompt-cache audit (v2.27.0+).** CA-01..CA-06 — dynamic placeholders, hook mutations, model-fork, unbounded output. |
| `telemetry` | **OTEL telemetry supply-chain risks.** otelHeadersHelper, OTEL_LOG_RAW_API_BODIES, OTEL_LOG_USER_PROMPTS in plugin env. |
| `scoring` | **Quality score.** Weighted across structure, docs, security, testing. |
| `marketplace` | **Marketplace.** Manifest structure, plugin entries, source references. Supports Layouts A/B/C. |
| `settings-marketplace` | **Inline marketplace in settings.** Validates `marketplaces` entries embedded in Claude Code settings files. |
| `local-scope` | **Local scope.** (v2.21.0+) Validates non-git-tracked `.claude/` elements under a project — `settings.local.json`, gitignored agents/skills/commands/rules, `enabledPlugins` from local settings. Each tracked element is passed to the FULL per-element validator. |
| `project-scope` | **Project scope.** (v2.21.0+) Validates git-tracked `.claude/` elements — `settings.json`, tracked agents/skills/commands/rules/hooks, `.mcp.json`, `CLAUDE.md`. Same deep per-element pipeline. |
| `enterprise` | **Enterprise.** Author, license, SPDX, keywords, metadata. |
| `mcp` | **MCP.** Transport types, required fields, OAuth, paths. |
| `lsp` | **LSP.** Command paths, language IDs, file patterns. |
| `docs` | **Documentation.** README sections, links, images. |
| `encoding` | **Encoding.** UTF-8, BOM, line endings, binary detection. |
| `rules` | **Rules.** Structure and content of rules/*.md files. |
| `xref` | **Cross-references.** Agent refs, versions, scripts. |
| `doctor` | **Health check.** Plugins, settings, marketplaces. `--install-scanners` (v2.48) installs all 5 external scanners + fclones with one command. |
| `lint` | **Lint only.** All 15 languages (Python, JS, Shell, Go, Rust, etc.). |
| `standardize` | **Audit + fix.** Standardize a plugin against CPV pipeline conventions (CI/CD, hooks, publish.py). |

### Options

These flags work with all validators:

| Flag | What It Does |
|------|-------------|
| `--verbose` or `-v` | Show all results, including INFO and PASSED. Also expands the report-file body. |
| `--report PATH` | Write the aggregated report to PATH explicitly. |
| `--json` | Output as JSON (for scripts and CI/CD). |
| `--strict` | Treat NIT-level issues as failures too. |
| `--marketplace-only` | Skip `plugin.json` requirement (for marketplace-only distribution). |

> **Default output is path-only.** Without `--json` or `--report`, `validate_security.py` auto-saves the aggregated report to `$CLAUDE_PROJECT_DIR/reports/security/<timestamp>-<plugin>.md` (or `$TMPDIR/reports/security/...` when `CLAUDE_PROJECT_DIR` is unset, e.g. on a remote `uvx` invocation) and prints **only** the compact summary (counts table + verdict + plugin path + report path) to stdout. This guarantees that an agent invoking the validator gets a tiny, predictable stdout payload that never floods its context window.
>
> **External scanners always run.** There are no `--no-tirith` / `--no-trufflehog` / `--no-semgrep` opt-out flags. Each external scanner self-skips with an INFO advisory if its source binary cannot be resolved on PATH or installed from its source URL. Set `CPV_NO_TIRITH_INSTALL=1` to disable tirith's auto-install fallback in CI sandboxes that block container pulls; set `CPV_CISCO_SCAN_TIMEOUT_S=<seconds>` to override the 600s default for very large trees. See [External Security Scanners](#external-security-scanners-always-run-programmatic-only) above for the full inventory.

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

22 commands — 13 run scripts directly (zero AI tokens), 7 spawn an agent, 2 are specialized utility scripts.

#### Script Commands (free — no AI tokens)

| Command | What It Does |
|---------|--------------|
| `/cpv-validate-plugin <path>` | **Full validation** -- runs all 20 sub-validators |
| `/cpv-validate-skill <path>` | Skill validation (190+ rules) |
| `/cpv-validate-cache <path>` | **Cache-audit** — runs `validate_cache.py` against a plugin OR project root. Catches the six documented Anthropic prompt-cache invalidation patterns (CA-01..CA-06) that silently multiply per-turn API costs by 5-10x. Default output is path-only. See [the cache section above](#external-security-scanners-always-run-programmatic-only) for context, and [`commands/cpv-validate-cache.md`](commands/cpv-validate-cache.md) for the per-rule reference. |
| `/cpv-validate-github-plugin <owner/repo>` | Validate a GitHub plugin without installing |
| `/cpv-validate-github-marketplace <owner/repo>` | Validate a GitHub marketplace without registering |
| `/cpv-validate-project-scope <path>` | Validate git-tracked (project-scope) Claude Code config under a project: `.claude/settings.json`, `.mcp.json`, agents, skills, commands, rules, `CLAUDE.md`, tracked hooks/mcp/lsp subtrees. **v2.21.0+:** every tracked element now runs the FULL per-element validator pipeline (same as `cpv-validate-plugin`). Rejects `autoMemoryDirectory`, managed-only keys, secrets in env, absolute home paths. |
| `/cpv-validate-local-scope <path>` | Validate non-git-tracked (local-scope) Claude Code config under a project: `.claude/settings.local.json`, `CLAUDE.local.md`, gitignored agents/skills/commands/rules, per-project MCP state in `~/.claude.json`, and locally-enabled plugins from `enabledPlugins`. **v2.21.0+:** deep per-element pipeline runs even on untracked content; absolute-path rules remain relaxed (personal paths OK), but managed-only/global-config keys are still rejected. |
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
| `/cpv-cache-optimize <path-or-report> [--broader]` | cache-optimizer-agent | **Cache-aware optimizer.** Runs the full validate → fix → re-validate loop for prompt-cache rules (CA-01..CA-06). With `--broader`, also performs cache-aware refactoring of the plugin's skills / agents / commands / `CLAUDE.md` / rules — works on any project that uses Claude Code, not just plugins. Each material refactor is approved via `AskUserQuestion` before the edit lands. |
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
| Commands | 20 | 12 direct script + 6 agent-backed + 2 specialized utility commands |
| Tests | 2056+ | Full coverage across all modules |

</details>

<details>
<summary><strong>Validation Scripts</strong></summary>

| Script | Purpose |
|--------|---------|
> **Always invoke validators via the launcher.** Direct invocation from
> the plugin cache is refused by `check_remote_execution_guard()`.
> Use `python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" <alias> <target>`.
> See [Canonical Launcher Invocation](#canonical-launcher-invocation) below
> or `python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" --help` for
> the full alias table.

| Script | Launcher alias | Purpose |
|--------|---|---------|
| `validate_plugin.py` | `plugin` | Main orchestrator -- runs all 20 sub-validators |
| `validate_skill_comprehensive.py` | `skill` | Comprehensive skill validator (190+ rules) |
| `validate_hook.py` | `hook` | Hook configuration validator (28 events, 5 types) |
| `validate_agent.py` | `agent` | Agent definition validator |
| `validate_command.py` | `command` | Command definition validator |
| `validate_mcp.py` | `mcp` | MCP server config validator |
| `validate_lsp.py` | `lsp` | LSP server config validator |
| `validate_marketplace.py` | `marketplace` | Marketplace manifest validator (Layouts A/B/C) |
| `validate_marketplace_pipeline.py` | `validate_marketplace_pipeline` | Marketplace release pipeline validator (publish.py, CI, CHANGELOG) |
| `validate_settings_marketplace.py` | `settings-marketplace` | Inline marketplace-in-settings validator |
| `validate_security.py` | `security` | Security vulnerability scanner (5 external scanners + fclones dedup) |
| `validate_cache.py` | `cache` | Prompt-cache invalidation audit (CA-01..CA-06, v2.27.0+) |
| `validate_telemetry.py` | `telemetry` | OTEL telemetry supply-chain risk validator |
| `validate_scoring.py` | `scoring` | Quality score calculator |
| `validate_enterprise.py` | `enterprise` | Enterprise compliance validator |
| `validate_documentation.py` | `docs` | Documentation quality checker |
| `validate_encoding.py` | `encoding` | File encoding validator |
| `validate_rules.py` | `rules` | Rules directory validator |
| `validate_xref.py` | `xref` | Cross-reference validator |
| `validate_local_scope.py` | `local-scope` | Local-scope `.claude/` validator (v2.21.0+) — non-git-tracked settings/agents/skills/commands/rules + enabledPlugins |
| `validate_project_scope.py` | `project-scope` | Project-scope `.claude/` validator (v2.21.0+) — git-tracked settings/agents/skills/commands/rules + tracked hooks/mcp/lsp subtrees |

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
