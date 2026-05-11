# Agent-Emission Audit — Static surface analysis

**TRDD:** b4c6cbe7
**Generated:** 2026-05-11

> This is a STATIC analysis. A row tagged `yes` means the agent (or one of its loaded skills) at least *mentions* the topic. It does NOT prove the agent emits spec-correct output. A row tagged `no` is a near-certain gap — the agent has no explicit guidance on the topic.

## 1. Per-agent missing-skill audit

| Agent | Body present? | Skills missing (cannot resolve) |
|---|:---:|---|
| `plugin-creator` (agent) | yes | _none_ |
| `plugin-fixer` (agent) | yes | _none_ |
| `marketplace-fixer` (agent) | yes | _none_ |
| `cpv-upgrade-plugin` (command) | yes | _none_ |
| `cpv-migrate-marketplace` (command) | yes | _none_ |

## 2. Coverage matrix (agent × topic)

| Topic | Severity | `plugin-creator` | `plugin-fixer` | `marketplace-fixer` | `cpv-upgrade-plugin` | `cpv-migrate-marketplace` |
|---|---|---|---|---|---|---|
| `plugin-name-kebab` | `CRITICAL` | yes | **no** | yes | **no** | **no** |
| `plugin-version-semver` | `CRITICAL` | yes | yes | yes | yes | **no** |
| `unknown-root-keys` | `CRITICAL` | **no** | **no** | **no** | **no** | **no** |
| `author-object-shape` | `MAJOR` | **no** | **no** | **no** | **no** | **no** |
| `hooks-event-coverage` | `MAJOR` | **no** | yes | **no** | **no** | **no** |
| `hooks-type-coverage` | `MAJOR` | yes | yes | **no** | **no** | **no** |
| `hook-script-resolve` | `MAJOR` | yes | yes | yes | **no** | yes |
| `skill-frontmatter-fields` | `MAJOR` | yes | yes | yes | yes | yes |
| `skill-argument-substitution` | `MAJOR` | yes | **no** | **no** | yes | **no** |
| `skill-paths-field` | `MINOR` | yes | yes | yes | **no** | **no** |
| `agent-frontmatter` | `MAJOR` | yes | yes | yes | yes | **no** |
| `agent-color-named` | `MINOR` | **no** | **no** | **no** | **no** | **no** |
| `mcpServers-schema` | `MAJOR` | yes | yes | yes | yes | **no** |
| `lspServers-schema` | `MINOR` | yes | yes | yes | **no** | **no** |
| `marketplace-source-types` | `MAJOR` | **no** | **no** | **no** | **no** | **no** |
| `marketplace-source-shape-mismatch` | `MAJOR` | **no** | **no** | **no** | **no** | yes |
| `marketplace-name-equals-plugin` | `CRITICAL` | **no** | **no** | **no** | **no** | **no** |
| `layout-a-b-c-awareness` | `MAJOR` | yes | yes | yes | **no** | **no** |
| `gitignore-defaults` | `MINOR` | yes | yes | yes | yes | **no** |
| `env-example-no-secrets` | `MAJOR` | **no** | **no** | **no** | **no** | **no** |
| `license-presence` | `MINOR` | yes | yes | yes | yes | **no** |
| `readme-install-command` | `MINOR` | **no** | **no** | **no** | **no** | **no** |
| `publish-py-idempotent` | `MAJOR` | yes | yes | yes | yes | **no** |
| `cross-platform-paths` | `MINOR` | yes | yes | **no** | yes | **no** |

## 3. Gap roll-up per agent

### `plugin-creator` (agent)

_Scaffolds new plugin or marketplace repos from scratch._

**Gap counts:** CRITICAL=2 MAJOR=5 MINOR=2

**Critical gaps (highest priority for child TRDDs):**
- `unknown-root-keys` — CLI rejects unknown top-level keys in plugin.json (e.g. our own `cpv`)
- `marketplace-name-equals-plugin` — marketplace.json.plugins[].name MUST equal upstream plugin.json.name

**Major gaps:**
- `author-object-shape` — CLI requires author as object with name/email/url, not bare string
- `hooks-event-coverage` — Hook events differ per Claude Code version; agent must use spec-aligned names
- `marketplace-source-types` — 6 valid source types — agent must scope each one correctly per layout
- `marketplace-source-shape-mismatch` — Older marketplaces use source.url; canonical is source.repo for type=github
- `env-example-no-secrets` — .env.example must not embed real values; agents often paste placeholder API keys

**Minor gaps:**
- `agent-color-named` — Agent `color:` should be one of 8 named colors; hex codes NIT but accepted
- `readme-install-command` — README install command must use canonical plugin name from plugin.json

### `plugin-fixer` (agent)

_Applies fix recipes from a validation report._

**Gap counts:** CRITICAL=3 MAJOR=5 MINOR=2

**Critical gaps (highest priority for child TRDDs):**
- `plugin-name-kebab` — plugin.json.name must be kebab-case; CLI rejects underscores/CamelCase
- `unknown-root-keys` — CLI rejects unknown top-level keys in plugin.json (e.g. our own `cpv`)
- `marketplace-name-equals-plugin` — marketplace.json.plugins[].name MUST equal upstream plugin.json.name

**Major gaps:**
- `author-object-shape` — CLI requires author as object with name/email/url, not bare string
- `skill-argument-substitution` — `$<name>` substitution requires matching `arguments:` declaration (v2.1.121)
- `marketplace-source-types` — 6 valid source types — agent must scope each one correctly per layout
- `marketplace-source-shape-mismatch` — Older marketplaces use source.url; canonical is source.repo for type=github
- `env-example-no-secrets` — .env.example must not embed real values; agents often paste placeholder API keys

**Minor gaps:**
- `agent-color-named` — Agent `color:` should be one of 8 named colors; hex codes NIT but accepted
- `readme-install-command` — README install command must use canonical plugin name from plugin.json

### `marketplace-fixer` (agent)

_Applies fix recipes against a marketplace.json._

**Gap counts:** CRITICAL=2 MAJOR=7 MINOR=3

**Critical gaps (highest priority for child TRDDs):**
- `unknown-root-keys` — CLI rejects unknown top-level keys in plugin.json (e.g. our own `cpv`)
- `marketplace-name-equals-plugin` — marketplace.json.plugins[].name MUST equal upstream plugin.json.name

**Major gaps:**
- `author-object-shape` — CLI requires author as object with name/email/url, not bare string
- `hooks-event-coverage` — Hook events differ per Claude Code version; agent must use spec-aligned names
- `hooks-type-coverage` — 5 hook types as of v2.1.118 — agent must scope command/prompt/http/mcp_tool/agent correctly
- `skill-argument-substitution` — `$<name>` substitution requires matching `arguments:` declaration (v2.1.121)
- `marketplace-source-types` — 6 valid source types — agent must scope each one correctly per layout
- `marketplace-source-shape-mismatch` — Older marketplaces use source.url; canonical is source.repo for type=github
- `env-example-no-secrets` — .env.example must not embed real values; agents often paste placeholder API keys

**Minor gaps:**
- `agent-color-named` — Agent `color:` should be one of 8 named colors; hex codes NIT but accepted
- `readme-install-command` — README install command must use canonical plugin name from plugin.json
- `cross-platform-paths` — All shipped scripts must use pathlib + subprocess.run, not bash globs

### `cpv-upgrade-plugin` (command)

_Upgrades an existing plugin to current CPV pipeline standards._

**Gap counts:** CRITICAL=3 MAJOR=8 MINOR=4

**Critical gaps (highest priority for child TRDDs):**
- `plugin-name-kebab` — plugin.json.name must be kebab-case; CLI rejects underscores/CamelCase
- `unknown-root-keys` — CLI rejects unknown top-level keys in plugin.json (e.g. our own `cpv`)
- `marketplace-name-equals-plugin` — marketplace.json.plugins[].name MUST equal upstream plugin.json.name

**Major gaps:**
- `author-object-shape` — CLI requires author as object with name/email/url, not bare string
- `hooks-event-coverage` — Hook events differ per Claude Code version; agent must use spec-aligned names
- `hooks-type-coverage` — 5 hook types as of v2.1.118 — agent must scope command/prompt/http/mcp_tool/agent correctly
- `hook-script-resolve` — Hook command paths must resolve; ${CLAUDE_PLUGIN_ROOT} required for portability
- `marketplace-source-types` — 6 valid source types — agent must scope each one correctly per layout
- `marketplace-source-shape-mismatch` — Older marketplaces use source.url; canonical is source.repo for type=github
- `layout-a-b-c-awareness` — Three valid layouts (A/B/C); agent must scope marketplace.json placement correctly
- `env-example-no-secrets` — .env.example must not embed real values; agents often paste placeholder API keys

**Minor gaps:**
- `skill-paths-field` — `paths:` field declares bundled resources; often omitted by agents
- `agent-color-named` — Agent `color:` should be one of 8 named colors; hex codes NIT but accepted
- `lspServers-schema` — LSP server shape often missed entirely — most agents don't mention it
- `readme-install-command` — README install command must use canonical plugin name from plugin.json

### `cpv-migrate-marketplace` (command)

_Normalises an existing marketplace.json (source.url -> source.repo, etc.)._

**Gap counts:** CRITICAL=4 MAJOR=10 MINOR=7

**Critical gaps (highest priority for child TRDDs):**
- `plugin-name-kebab` — plugin.json.name must be kebab-case; CLI rejects underscores/CamelCase
- `plugin-version-semver` — plugin.json.version must be valid semver; CLI rejects free strings
- `unknown-root-keys` — CLI rejects unknown top-level keys in plugin.json (e.g. our own `cpv`)
- `marketplace-name-equals-plugin` — marketplace.json.plugins[].name MUST equal upstream plugin.json.name

**Major gaps:**
- `author-object-shape` — CLI requires author as object with name/email/url, not bare string
- `hooks-event-coverage` — Hook events differ per Claude Code version; agent must use spec-aligned names
- `hooks-type-coverage` — 5 hook types as of v2.1.118 — agent must scope command/prompt/http/mcp_tool/agent correctly
- `skill-argument-substitution` — `$<name>` substitution requires matching `arguments:` declaration (v2.1.121)
- `agent-frontmatter` — Agent frontmatter has 15+ fields; agents miss `permissionMode`, `effort`
- `mcpServers-schema` — MCP server schema has per-server fields (command, args, url, env, headersHelper)
- `marketplace-source-types` — 6 valid source types — agent must scope each one correctly per layout
- `layout-a-b-c-awareness` — Three valid layouts (A/B/C); agent must scope marketplace.json placement correctly
- `env-example-no-secrets` — .env.example must not embed real values; agents often paste placeholder API keys
- `publish-py-idempotent` — publish.py must be idempotent — re-runs MUST NOT double-tag or republish

**Minor gaps:**
- `skill-paths-field` — `paths:` field declares bundled resources; often omitted by agents
- `agent-color-named` — Agent `color:` should be one of 8 named colors; hex codes NIT but accepted
- `lspServers-schema` — LSP server shape often missed entirely — most agents don't mention it
- `gitignore-defaults` — .gitignore must exclude /reports/ + /reports_dev/ per agent-reports-location rule
- `license-presence` — LICENSE file with recognised SPDX identifier expected by marketplace hosts
- `readme-install-command` — README install command must use canonical plugin name from plugin.json
- `cross-platform-paths` — All shipped scripts must use pathlib + subprocess.run, not bash globs

## 4. Cross-agent gap leaderboard

Topics whose `no` count is highest are the most under-covered across the agent fleet — they are the strongest child-TRDD candidates.

| Topic | Severity | `no` count | Agents missing it |
|---|---|---:|---|
| `unknown-root-keys` | `CRITICAL` | 5 | plugin-creator, plugin-fixer, marketplace-fixer, cpv-upgrade-plugin, cpv-migrate-marketplace |
| `marketplace-name-equals-plugin` | `CRITICAL` | 5 | plugin-creator, plugin-fixer, marketplace-fixer, cpv-upgrade-plugin, cpv-migrate-marketplace |
| `plugin-name-kebab` | `CRITICAL` | 3 | plugin-fixer, cpv-upgrade-plugin, cpv-migrate-marketplace |
| `plugin-version-semver` | `CRITICAL` | 1 | cpv-migrate-marketplace |
| `author-object-shape` | `MAJOR` | 5 | plugin-creator, plugin-fixer, marketplace-fixer, cpv-upgrade-plugin, cpv-migrate-marketplace |
| `marketplace-source-types` | `MAJOR` | 5 | plugin-creator, plugin-fixer, marketplace-fixer, cpv-upgrade-plugin, cpv-migrate-marketplace |
| `env-example-no-secrets` | `MAJOR` | 5 | plugin-creator, plugin-fixer, marketplace-fixer, cpv-upgrade-plugin, cpv-migrate-marketplace |
| `hooks-event-coverage` | `MAJOR` | 4 | plugin-creator, marketplace-fixer, cpv-upgrade-plugin, cpv-migrate-marketplace |
| `marketplace-source-shape-mismatch` | `MAJOR` | 4 | plugin-creator, plugin-fixer, marketplace-fixer, cpv-upgrade-plugin |
| `hooks-type-coverage` | `MAJOR` | 3 | marketplace-fixer, cpv-upgrade-plugin, cpv-migrate-marketplace |
| `skill-argument-substitution` | `MAJOR` | 3 | plugin-fixer, marketplace-fixer, cpv-migrate-marketplace |
| `layout-a-b-c-awareness` | `MAJOR` | 2 | cpv-upgrade-plugin, cpv-migrate-marketplace |
| `hook-script-resolve` | `MAJOR` | 1 | cpv-upgrade-plugin |
| `agent-frontmatter` | `MAJOR` | 1 | cpv-migrate-marketplace |
| `mcpServers-schema` | `MAJOR` | 1 | cpv-migrate-marketplace |
| `publish-py-idempotent` | `MAJOR` | 1 | cpv-migrate-marketplace |
| `skill-frontmatter-fields` | `MAJOR` | 0 | _none_ |
| `agent-color-named` | `MINOR` | 5 | plugin-creator, plugin-fixer, marketplace-fixer, cpv-upgrade-plugin, cpv-migrate-marketplace |
| `readme-install-command` | `MINOR` | 5 | plugin-creator, plugin-fixer, marketplace-fixer, cpv-upgrade-plugin, cpv-migrate-marketplace |
| `skill-paths-field` | `MINOR` | 2 | cpv-upgrade-plugin, cpv-migrate-marketplace |
| `lspServers-schema` | `MINOR` | 2 | cpv-upgrade-plugin, cpv-migrate-marketplace |
| `cross-platform-paths` | `MINOR` | 2 | marketplace-fixer, cpv-migrate-marketplace |
| `gitignore-defaults` | `MINOR` | 1 | cpv-migrate-marketplace |
| `license-presence` | `MINOR` | 1 | cpv-migrate-marketplace |
