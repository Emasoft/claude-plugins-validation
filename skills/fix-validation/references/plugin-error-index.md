# Plugin Error-to-Fix Index

## Table of Contents

- [1. validate_plugin.py](#1-validate_pluginpy)
- [2. validate_skill.py](#2-validate_skillpy)
- [3. validate_skill_comprehensive.py](#3-validate_skill_comprehensivepy)
- [4. validate_hook.py](#4-validate_hookpy)
- [5. validate_agent.py](#5-validate_agentpy)
- [6. validate_command.py](#6-validate_commandpy)
- [7. validate_mcp.py](#7-validate_mcppy)
- [8. validate_lsp.py](#8-validate_lsppy)
- [9. validate_security.py](#9-validate_securitypy)
- [10. validate_rules.py](#10-validate_rulespy)
- [11. validate_xref.py](#11-validate_xrefpy)
- [12. validate_settings_marketplace.py](#12-validate_settings_marketplacepy)
- [13. validate_documentation.py](#13-validate_documentationpy)
- [14. validate_encoding.py](#14-validate_encodingpy)
- [15. validate_enterprise.py](#15-validate_enterprisepy)
- [16. validate_scoring.py](#16-validate_scoringpy)

---

Maps each **plugin-scope** CPV validator to its fix reference guide with section numbers. This index covers the 16 validators that operate on a single plugin directory. For marketplace-level validators (`validate_marketplace.py`, `validate_marketplace_pipeline.py`) see [marketplace-error-index.md](marketplace-error-index.md).

Entries tagged `[NEW]` were added in recent releases (v2.11.x / v2.12.x) and correspond to items tracked in `docs_dev/validator_error_inventory_20260412.md`.

---

## 1. validate_plugin.py

Primary fix guide: [plugin-structure-fixes.md](plugin-structure-fixes.md)

| Error topic | Fix guide section |
|---|---|
| Manifest (plugin.json) | plugin-structure-fixes §1 |
| Directory structure (components at root vs. `.claude-plugin/`) | plugin-structure-fixes §2 |
| Commands (frontmatter, name, description) | plugin-structure-fixes §3 |
| Agents (frontmatter, description) | plugin-structure-fixes §4 |
| Hooks configuration location | plugin-structure-fixes §5 |
| MCP server manifest entries | plugin-structure-fixes §6 |
| Scripts quality (ruff/mypy/shellcheck/go vet/cargo/PSScriptAnalyzer) | plugin-structure-fixes §7 |
| Cross-platform compatibility (shell/bat/ps1/compiled-lang sources) | plugin-structure-fixes §8 |
| Skill validation entry point | plugin-structure-fixes §9 |
| README.md / LICENSE | plugin-structure-fixes §10 |
| Rules directory | plugin-structure-fixes §11 |
| Path and private info | plugin-structure-fixes §12 |
| `.gitignore` coverage and virtual-env leakage | plugin-structure-fixes §13 |
| Workflow inline-Python patterns | plugin-structure-fixes §14 |
| `bin/` executables and platform naming **[NEW]** | plugin-structure-fixes §8 (bin/ subsections) |
| `userConfig` schema validation **[NEW]** | plugin-structure-fixes "New Validations (v2.11.0+)" |
| `channels` schema validation **[NEW]** | plugin-structure-fixes "New Validations (v2.11.0+)" |
| `lspServers` in plugin.json **[NEW]** | plugin-structure-fixes "New Validations (v2.11.0+)" |
| `output-styles/` frontmatter **[NEW]** | plugin-structure-fixes "New Validations (v2.11.0+)" |
| `settings.json` agent value matching **[NEW]** | plugin-structure-fixes "New Validations (v2.11.0+)" |
| Submodule-containment INFO **[NEW]** | plugin-structure-fixes §2 (submodule advisory) |
| Language-detection INFO / orphan lockfiles **[NEW]** | plugin-structure-fixes §7 (script quality) |
| Monitor tool recognition (valid) **[NEW]** | plugin-structure-fixes §4 (valid tool names) — Monitor (v2.1.98) is accepted in agent `tools:` |
| Plugin-shipped agent restrictions (`hooks`/`mcpServers`/`permissionMode` forbidden) **[NEW]** | plugin-structure-fixes §4 (Agent frontmatter) |

Common crash-category CRITICALs (from `validate_scoring.py`) land here too when the plugin validator raises an exception.

---

## 2. validate_skill.py

Primary fix guide: [skill-fixes.md](skill-fixes.md)

| Error topic | Fix guide section |
|---|---|
| SKILL.md missing / not a directory | skill-fixes §1 |
| No YAML frontmatter | skill-fixes §2 |
| Invalid name / name-type | skill-fixes §3 |
| Missing or empty description | skill-fixes §4 |
| Empty or invalid `allowed-tools` | skill-fixes §3 (allowed-tools subsection) |
| Content after frontmatter missing | skill-fixes §5 |

This shim-style validator performs basic-only checks (29 entries). For the comprehensive rule suite see §3 below.

---

## 3. validate_skill_comprehensive.py

Primary fix guide: [skill-fixes.md](skill-fixes.md)

| Error topic | Fix guide section |
|---|---|
| Structure (path missing / not directory) | skill-fixes §1 |
| Frontmatter parsing | skill-fixes §2 |
| `name` field (type, length, case, reserved words, XML tags) | skill-fixes §3 |
| `description` quality (length, XML, first/second person, "Use when" phrase) | skill-fixes §4 |
| Token budget and progressive disclosure | skill-fixes §5 |
| Required sections (strict mode) | skill-fixes §6 |
| Reference files and TOC embedding | skill-fixes §7 |
| `allowed-tools` field (empty, unknown tools, scoping) | skill-fixes §3 (allowed-tools) |
| `effort` field validation **[NEW]** | skill-fixes "Frontmatter fields — v2.8.x" |
| `shell` field validation **[NEW]** | skill-fixes "Frontmatter fields — v2.8.x" |
| `paths` field validation **[NEW]** | skill-fixes "Frontmatter fields — v2.8.x" |
| `hooks` / `mcpServers` shape validation | skill-fixes §2 |
| Metadata / tags / author / license / argument-hint | skill-fixes §2 (optional fields) |
| Monitor tool strict-mode restriction (unscoped `Monitor` forbidden in strict mode, same rule as unscoped `Bash`) **[NEW]** | skill-fixes §3 (allowed-tools strict-mode subsection) |
| TaskOutput deprecation WARNING **[NEW]** | skill-fixes §3 — migrate to `Read` on the task's output file path |
| Task → Agent rename WARNING (alias still accepted) **[NEW]** | skill-fixes §3 |
| TodoRead / Notebook / MultiEdit legacy WARNING **[NEW]** | skill-fixes §3 — verify existence before shipping |
| `CLAUDE_PLUGIN_OPTION_*` env var recognition (accepted) **[NEW]** | skill-fixes "Environment variables" — accepted alongside `VALID_PLUGIN_ENV_VARS` |

---

## 4. validate_hook.py

Primary fix guide: [hook-fixes.md](hook-fixes.md)

| Error topic | Fix guide section |
|---|---|
| hooks.json structure (root object, 'description', 'disableAllHooks') | hook-fixes §1 |
| Event types (valid events, unknown events) | hook-fixes §2 |
| Matchers (regex, tool names, matcher blocks) | hook-fixes §3 |
| Hook types (command, prompt, agent, http) | hook-fixes §4 |
| Command hooks (`command` field, absolute paths, `cd`, interpreters) | hook-fixes §5 |
| Prompt hooks (`prompt` field, `$ARGUMENTS`, model) | hook-fixes §6 |
| HTTP hooks (url, headers, allowedEnvVars, timeout) | hook-fixes §7 |
| Agent hooks (agent name, model, timeout) | hook-fixes §8 |
| Timeouts (units, limits) | hook-fixes §8 (timeouts subsection) |
| Hook scripts (shebang, permissions, ruff/mypy/eslint/shellcheck) | hook-fixes §9–10 |
| `CLAUDE_ENV_FILE` event restriction | hook-fixes §5 (command hook special fields) |
| Setup event legacy WARNING **[NEW]** | hook-fixes §2 — Setup is not in current official spec (v2.1.86+); migrate logic to SessionStart or remove if unused |
| `PermissionDenied` event matcher (tool_name) **[NEW]** (v2.1.89) | hook-fixes §2 |
| `TaskCreated` event **[NEW]** (v2.1.84, no matcher) | hook-fixes §2 |
| `StopFailure` event matcher (rate_limit, billing_error, …) **[NEW]** (v2.1.78) | hook-fixes §2 |
| `CwdChanged` event **[NEW]** (v2.1.83, no matcher) | hook-fixes §2 |
| `FileChanged` event matcher (filename pattern) **[NEW]** (v2.1.83) | hook-fixes §2 |
| `PostCompact` event **[NEW]** (v2.1.76) | hook-fixes §2 |
| `ElicitationResult` / `Elicitation` events **[NEW]** (v2.1.76) | hook-fixes §2 |
| `InstructionsLoaded` event matcher **[NEW]** (v2.1.69) | hook-fixes §2 |

---

## 5. validate_agent.py

Primary fix guide: [plugin-structure-fixes.md](plugin-structure-fixes.md) §4 (Agent File Issues)

| Error topic | Fix guide section |
|---|---|
| Frontmatter structure and YAML parsing | plugin-structure-fixes §4 |
| Required fields (`name`, `description`) | plugin-structure-fixes §4 |
| `tools` field (allowed tool names, empty, type) | plugin-structure-fixes §4 |
| `model` field (short name, full model ID, inherit) | plugin-structure-fixes §4 |
| `color`, `capabilities`, `permissionMode`, `memory`, `isolation` | plugin-structure-fixes §4 |
| `maxTurns`, `background`, `initialPrompt` | plugin-structure-fixes §4 |
| `mcpServers`, `hooks` on agents | plugin-structure-fixes §4 |
| `disallowedTools` | plugin-structure-fixes §4 |
| `skills` list field | plugin-structure-fixes §4 |
| Content body after frontmatter | plugin-structure-fixes §4 |
| Security: agent prompt injection / abuse patterns | [security-fixes.md](security-fixes.md) §2 |
| `effort` field validation **[NEW]** | plugin-structure-fixes §4 (effort subsection) |
| Plugin-shipped agent restrictions: `hooks`/`mcpServers`/`permissionMode` forbidden **[NEW]** | plugin-structure-fixes §4 (plugin-shipped restrictions subsection) |
| TaskOutput deprecation WARNING **[NEW]** | plugin-structure-fixes §4 (tools subsection) — migrate to `Read` on the task's output file path |
| Task → Agent rename WARNING (alias still accepted) **[NEW]** | plugin-structure-fixes §4 (tools subsection) |
| Legacy-field warnings: `capabilities` / `context` / `agent` / `user-invocable` / `system-prompt` **[NEW]** | plugin-structure-fixes §4 — verify these fields are still intended |
| TodoRead / Notebook / MultiEdit legacy tool WARNING **[NEW]** | plugin-structure-fixes §4 (tools subsection) |
| `dangerouslySkipPermissions` warning (valid for worktree agents) **[NEW]** | [security-fixes.md](security-fixes.md) §7 |

---

## 6. validate_command.py

Primary fix guide: [plugin-structure-fixes.md](plugin-structure-fixes.md) §3 (Command File Issues)

| Error topic | Fix guide section |
|---|---|
| Frontmatter structure and YAML parsing | plugin-structure-fixes §3 |
| Required fields (`name`, `description`) | plugin-structure-fixes §3 |
| `allowed-tools` (empty, invalid patterns) | plugin-structure-fixes §3 |
| `model` field | plugin-structure-fixes §3 |
| `argument-hint` field | plugin-structure-fixes §3 |
| Content body after frontmatter | plugin-structure-fixes §3 |
| Security: command injection / dangerous patterns | [security-fixes.md](security-fixes.md) §2 |

---

## 7. validate_mcp.py

Primary fix guide: [mcp-fixes.md](mcp-fixes.md)

| Error topic | Fix guide section |
|---|---|
| .mcp.json structure (or inline `mcpServers` in plugin.json) | mcp-fixes §1 |
| Server config (type, required fields by transport) | mcp-fixes §2 |
| Transport (stdio, sse, http, remote) | mcp-fixes §3–5 |
| `command` / `args` validation | mcp-fixes §2 |
| Env vars (`${...}` syntax, defaults, absolute paths, `${CLAUDE_PLUGIN_ROOT}`) | mcp-fixes §6 |
| `headers` / hardcoded credentials | mcp-fixes §6 |
| `oauth` object (clientId, callbackPort, authServerMetadataUrl) | mcp-fixes §11 |
| Supply-chain patterns (`npx`/`uvx` remote packages) | mcp-fixes §2 (command validation) |
| Deprecated `sse` transport MINOR | mcp-fixes §3 |

---

## 8. validate_lsp.py

Primary fix guide: [lsp-fixes.md](lsp-fixes.md)

| Error topic | Fix guide section |
|---|---|
| LSP config file discovery | lsp-fixes §1 |
| Server structure and required fields | lsp-fixes §2 |
| `command` (executable, PATH, type) | lsp-fixes §4 |
| `args`, `env`, `cwd` | lsp-fixes §4 |
| `filetypes`, `rootPatterns` | lsp-fixes §7 |
| `initializationOptions`, `settings` | lsp-fixes §7 |
| Timeouts, `maxRestarts`, `restartOnCrash` | lsp-fixes §7 |

---

## 9. validate_security.py

Primary fix guide: [security-fixes.md](security-fixes.md)

| Error topic | Fix guide section |
|---|---|
| Plugin path issues | security-fixes §1 |
| Injection detection (dangerous bash/python patterns) | security-fixes §2 |
| Path traversal patterns | security-fixes §3 |
| Secret detection (API keys, tokens, passwords) | security-fixes §4 |
| Hardcoded user path issues | security-fixes §5 |
| Dangerous files (cookies.txt, .pem, .ssh/, …) | security-fixes §6 |
| Script permissions (world-writable, executable, shebangs) | security-fixes §7 |
| File read issues | security-fixes §8 |
| IDE-config secret scan (`.vscode/`, `.idea/`, `.cursor/`, `.zed/`) **[NEW]** | security-fixes §9 |
| AI-facing markdown injection scanning **[NEW]** | security-fixes §2 (AI-facing markdown subsection) |
| Hook-abuse patterns (PreToolUse/PostToolUse HTTP exfiltration) **[NEW]** | security-fixes §2 (hook-abuse subsection) |
| MCP server supply-chain / remote-URL WARNINGs **[NEW]** | security-fixes §2 (MCP abuse subsection) |
| Frontmatter abuse WARNING **[NEW]** | security-fixes §2 |
| cc-audit integration (100+ external rules) **[NEW]** | security-fixes §8 (cc-audit subsection) — requires `npx` |
| Permission escalation WARNINGs (`dangerouslySkipPermissions`, `bypass`) **[NEW]** | security-fixes §7 |

---

## 10. validate_rules.py

Primary fix guide: [rules-fixes.md](rules-fixes.md)

| Error topic | Fix guide section |
|---|---|
| `rules/` directory presence | rules-fixes §1 |
| UTF-8 encoding / empty file | rules-fixes §2 |
| Rule content: secrets, private paths | rules-fixes §3 |
| Frontmatter (YAML mapping, `paths` glob list) | rules-fixes §4 |
| `paths` field validation (array of non-empty strings) | rules-fixes §4 |

---

## 11. validate_xref.py

Primary fix guide: [xref-fixes.md](xref-fixes.md)

| Error topic | Fix guide section |
|---|---|
| Agent file references (`Task()` calls, `Agent(subagent_type=…)`) | xref-fixes §2 |
| `subagent_type` values must match an agent in `agents/` | xref-fixes §3 |
| Version synchronization (plugin.json ↔ marketplace.json ↔ pyproject.toml) | xref-fixes §4 |
| Skill references (`skill:`, `Skill(…)`) | xref-fixes §6 |
| Command → agent references | xref-fixes §2 |
| Hook script references | xref-fixes §2 |

---

## 12. validate_settings_marketplace.py **[NEW validator]**

Primary fix guide: [settings-marketplace-fixes.md](settings-marketplace-fixes.md)

| Error topic | Fix guide section |
|---|---|
| settings.json not found / not a file / invalid JSON / not an object | settings-marketplace-fixes §1 |
| `extraKnownMarketplaces` structure (must be object) | settings-marketplace-fixes §1 |
| Marketplace entry: missing `name` / missing `source` | settings-marketplace-fixes §2 |
| Source type `github` (requires `repo: owner/name`) | settings-marketplace-fixes §3 |
| Source type `url` (requires `url` string) | settings-marketplace-fixes §3 |
| Source type `git-subdir` (requires `url` + `path`) | settings-marketplace-fixes §3 |
| Source type `npm` (requires `package`) | settings-marketplace-fixes §3 |
| Source type `settings` (inline marketplace — requires `name` + `plugins`) **[NEW]** | settings-marketplace-fixes §3 |
| Source type `git` (generic git URL) | settings-marketplace-fixes §3 |
| Source type `directory` (local filesystem, dev-only) | settings-marketplace-fixes §3 |
| `EXTRA_KNOWN_MARKETPLACES_KEY` empty block | settings-marketplace-fixes §2 |

NOTE: this validator is distinct from `validate_marketplace.py`. It checks the `extraKnownMarketplaces` key inside a `settings.json` file, not the per-plugin `source` entries inside a `marketplace.json`.

---

## 13. validate_documentation.py

Primary fix guide: [documentation-fixes.md](documentation-fixes.md)

| Error topic | Fix guide section |
|---|---|
| README.md existence and title | documentation-fixes §1 |
| README content sections (Installation, Usage) | documentation-fixes §2 |
| Internal markdown links | documentation-fixes §3 |
| CHANGELOG.md presence and structure | documentation-fixes §4 |
| Heading hierarchy | documentation-fixes §5 |
| Code blocks / lists / tables / images | documentation-fixes §6–9 |

---

## 14. validate_encoding.py

Primary fix guide: [encoding-fixes.md](encoding-fixes.md)

| Error topic | Fix guide section |
|---|---|
| Not valid UTF-8 | encoding-fixes §2 |
| UTF-8 BOM | encoding-fixes §3 |
| UTF-16 / UTF-32 BOM (LE/BE) **[NEW]** | encoding-fixes §3 |
| JSON Unicode errors | encoding-fixes §4 |
| Raw control characters | encoding-fixes §5 |
| Shell-script CRLF / CR-only / mixed line endings | encoding-fixes §6 |
| Source-file CRLF / mac-style CR | encoding-fixes §7 |
| Batch-script CR line endings | encoding-fixes §8 |

---

## 15. validate_enterprise.py

Primary fix guide: [enterprise-fixes.md](enterprise-fixes.md)

| Error topic | Fix guide section |
|---|---|
| Plugin path / skills & agents directories | enterprise-fixes §1 |
| Skills frontmatter (enterprise fields) | enterprise-fixes §2 |
| Metadata / `tags` recommendations | enterprise-fixes §3 |
| Author / license | enterprise-fixes §4–5 |
| `context` field (missing / `fork` / `main`) | enterprise-fixes §2 |
| `agent` field without `context: fork` (has no effect) | enterprise-fixes §2 |

---

## 16. validate_scoring.py

Primary fix guide: [scoring-fixes.md](scoring-fixes.md)

| Error topic | Fix guide section |
|---|---|
| `Plugin validation failed: ...` (orchestration crash) | scoring-fixes §4 |
| `Security validation failed: ...` | scoring-fixes §4 |
| `Hook validation failed: ...` | scoring-fixes §4 |
| `MCP validation failed: ...` | scoring-fixes §4 |
| `Agent validation failed for {name}: ...` | scoring-fixes §4 |
| `Skill validation failed for {name}: ...` | scoring-fixes §4 |
| `Command validation failed for {name}: ...` | scoring-fixes §4 |
| Low aggregate scores | scoring-fixes §6 |

`validate_scoring.py` is an orchestration validator — it runs the other validators and emits one CRITICAL per crashing subvalidator. Check the underlying validator (using its own fix guide entry above) before assuming a scoring bug.
