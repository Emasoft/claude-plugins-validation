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
- [17. validate_cache.py](#17-validate_cachepy)
- [18. validate_telemetry.py — plugin-shipped env-var hazards](#18-validate_telemetrypy--plugin-shipped-env-var-hazards)
- [19. Semantic pillar — Channel MCP Server Source-Code Security](#19-semantic-pillar--channel-mcp-server-source-code-security)
- [20. validate_marketplace cross-validation rules](#20-validate_marketplace-cross-validation-rules)

## Checklist

- [ ] Identify the validator that produced the finding (top of the report or message prefix)
- [ ] Jump to the matching section in the table below
- [ ] Open the fix reference it points to
- [ ] Apply the fix using Read+Edit (never scripted bulk edits)
- [ ] Re-validate — do NOT assume the fix worked without confirmation

---

Maps each **plugin-scope** CPV validator to its fix reference guide with section numbers. This index covers the 18 validators that operate on a single plugin directory. For marketplace-level validators (`validate_marketplace.py`, `validate_marketplace_pipeline.py`) see [marketplace-error-index.md](marketplace-error-index.md).

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
| `README.md has badge markdown but is missing the automation markers` (v2.26.0 — fires only when README already contains literal `[![badge]({url})]({href})` or shields.io URLs) **[UPDATED v2.26.0]** | plugin-structure-fixes §10 "README.md missing badge-automation markers" |
| `Broken file reference: [path] in <md_file> — file not found` — v2.26.0 message includes two legitimate fixes (fix path / create file, OR mark the path as a placeholder using `{brace}`, `<angle>`, known names, or fenced code blocks) **[UPDATED v2.26.0]** | plugin-structure-fixes §10 "Broken file reference" |
| Rules directory | plugin-structure-fixes §11 |
| Path and private info | plugin-structure-fixes §12 |
| `.gitignore` coverage and virtual-env leakage | plugin-structure-fixes §13 |
| `N git-tracked file(s) also match .gitignore — gitignore is not enforced … the plugin is INVALID` (MAJOR; gitignore-evasion hardening) — tracked+gitignored files ship but are marked ignored (scan-evasion vector) **[NEW]** | plugin-structure-fixes §13 ("Untrack tracked+gitignored files (`git rm --cached`)") |
| Workflow inline-Python patterns | plugin-structure-fixes §14 |
| `bin/` executables and platform naming **[NEW]** | plugin-structure-fixes §8 (bin/ subsections) |
| `userConfig` schema validation — `title` required, `type` required + must be one of `{string, number, boolean, directory, file}`, `default` must match `type` (CPV v2.22.4+) | plugin-structure-fixes "userConfig schema invalid" |
| `channels` schema validation **[NEW]** | plugin-structure-fixes "New Validations (v2.11.0+)" |
| `lspServers` in plugin.json **[NEW]** | plugin-structure-fixes "New Validations (v2.11.0+)" |
| `output-styles/` frontmatter **[NEW]** | plugin-structure-fixes "New Validations (v2.11.0+)" |
| `settings.json` agent value matching **[NEW]** | plugin-structure-fixes "New Validations (v2.11.0+)" |
| Submodule-containment INFO **[NEW]** | plugin-structure-fixes §2 (submodule advisory) |
| Language-detection INFO / orphan lockfiles **[NEW]** | plugin-structure-fixes §7 (script quality) |
| `agents` field with folder path (`agents: Invalid input`) **[NEW 2026-04-18]** | plugin-structure-fixes §1 ("agents field contains a folder path") |
| `hooks: "./hooks/hooks.json"` cascading MCP failure (upgraded WARNING→MAJOR) **[NEW 2026-04-18]** | plugin-structure-fixes §1 ("hooks points at the default file") |
| Monitor tool recognition (valid) **[NEW]** | plugin-structure-fixes §4 (valid tool names) — Monitor (v2.1.98) is accepted in agent `tools:` |
| Plugin-shipped agent restrictions (`hooks`/`mcpServers`/`permissionMode` forbidden) **[NEW]** | plugin-structure-fixes §4 (Agent frontmatter) |
| `Layout C: self-entry name does not match plugin.json.name` (v2.32.0+, Phase 16, marketplace-in-plugin) **[NEW]** | plugin-structure-fixes §15 (Layout C) |
| `Layout C: self-entry source is not "./"` (v2.32.0+) **[NEW]** | plugin-structure-fixes §15 (Layout C) |
| `Layout C: version mismatch between plugin.json and marketplace.json` (v2.32.0+) **[NEW]** | plugin-structure-fixes §15 (Layout C) |
| `'dependencies[i].marketplace' = '<x>' is not in the hosting marketplace's allowCrossMarketplaceDependenciesOn allowlist` (TRDD-20108ab7, 2026-05-10, plugin-dependencies.md:54-79) **[NEW]** | plugin-structure-fixes "Cross-marketplace dependency blocked" — Add `<x>` to root marketplace.json's `allowCrossMarketplaceDependenciesOn` array, OR remove `marketplace` sub-field on the dep, OR pass `--marketplace-context PATH` if hosting context was wrong. |
| `[RC-USERCFG-SHELL-INJECT] monitors[i].command interpolates ${user_config.<key>} into a SHELL-FORM command` (CRITICAL, CC v2.1.207) **[NEW v2.158.0]** | plugin-structure-fixes §18 — a monitor has NO exec form; read the option inside the script (`$CLAUDE_PLUGIN_OPTION_<KEY>` or a config file). Never quote/escape the value — the SHAPE is rejected. |
| `[RC-USERCFG-SHELL-INJECT] plugin.json inline <Event> hook interpolates ${user_config.<key>}` (CRITICAL, CC v2.1.207) **[NEW v2.158.0]** | [hook-fixes.md](hook-fixes.md) §14 — exec form (`args` array), or read `$CLAUDE_PLUGIN_OPTION_<KEY>` in the script. Exec form is legal and must NOT be flagged. |
| `[RC-USERCFG-PROJECT-SETTINGS] .claude/settings.json sets 'pluginConfigs'` (WARNING, non-blocking, CC v2.1.207) **[NEW v2.158.0]** | plugin-structure-fixes §18 — move the block to user settings (`~/.claude/settings.json`), `--settings`, or managed settings; project-level values are silently ignored at runtime. |
| `RC-SHIP-BINARY-ONLY` — a build-source git submodule OR in-tree compile-source ships (compiled-component canon, WARNING, issue #175) **[NEW v3.7.0]** | [ship-binary-only-fixes.md](ship-binary-only-fixes.md) "RC-SHIP-BINARY-ONLY" — extract compile source to a SEPARATE repo cloned by URL/tag in CI (`scripts/cpv_strip_dev.py`); ship only `bin/` binaries + dispatcher. NOT a submodule (CC ships submodule content on install). |
| `RC-SUBMODULE-SHIPS` — a non-build-source git submodule ships its content on install (WARNING, issue #175) **[NEW v3.9.0]** | [ship-binary-only-fixes.md](ship-binary-only-fixes.md) "RC-SUBMODULE-SHIPS" — reference dev/test/non-hinted submodule content out-of-tree (build CI clones by pinned URL/tag) and remove the `.gitmodules` entry. |
| `RC-SHIP-BINARY-ONLY-STRICT` — manifest opted into `cpv.canon: ship-only-binary` but a submodule/in-tree source ships (MAJOR, publish-blocking, issue #175) **[NEW v3.14.0]** | [ship-binary-only-fixes.md](ship-binary-only-fixes.md) "RC-SHIP-BINARY-ONLY-STRICT" — migrate to bin/-only + clone-by-URL source (remove every `.gitmodules` entry), OR drop the `cpv.canon` opt-in until migrated. A path rename does NOT clear it. |
| `RC-MIXED-COMPILED` — script-primary plugin (profile `standard`) also ships a compiled component (INFO, non-blocking, issue #175) **[NEW v3.13.0]** | [ship-binary-only-fixes.md](ship-binary-only-fixes.md) "RC-MIXED-COMPILED" — informational, no action; the compiled build is already covered by RC-SHIP-BINARY-ONLY + the publish.py G2e gate. |

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
| `body invokes a tool the allowed-tools field does not grant` (silent-failure CRITICAL; prose-mention WARNING) **[NEW v2.102.0]** | skill-fixes "body invokes a tool the allowed-tools field does not grant" |
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
| `Many tools permitted (N distinct tool surfaces)` (v2.26.0 — Bash sub-patterns collapsed, threshold 15, suppressed when `user-invocable: false`) **[NEW v2.26.0]** | skill-fixes §9 "Many tools permitted" |
| `Unknown variable reference: ${VAR}` — skill-local shell vars defined in code blocks are now whitelisted; inline-backticked `${VAR}` is treated as code, not prose (v2.26.0) **[NEW v2.26.0]** | skill-fixes §10a "String Substitutions" |
| `Link/Reference has N/M TOC headings embedded` — v2.26.0 message upgraded with explicit guidance: embed the full TOC verbatim OR reduce the reference file's own TOC (drop/merge headings). No summaries. No partial lists. **[UPDATED v2.26.0]** | skill-fixes §8 "TOC Embedding Issues" |

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
| Runtime-dep: plain `python3` + third-party imports **[NEW]** (TRDD-0028dd34) | hook-fixes §13.1 — switch to `uv run --quiet --script` + PEP 723 block. Do NOT substitute `uvx`. |
| Runtime-dep: `uv run --script` with no PEP 723 block **[NEW]** (TRDD-0028dd34) | hook-fixes §13.2 — add `# /// script` header with `dependencies` list |
| Runtime-dep: PEP 723 block is incomplete **[NEW]** (TRDD-0028dd34) | hook-fixes §13.3 — append missing PyPI names to `dependencies` |
| Runtime-dep: `uv run --with` flags incomplete **[NEW]** (TRDD-0028dd34) | hook-fixes §13.4 — add `--with <pkg>` per missing import |
| Runtime-dep: venv-python with no SessionStart setup **[NEW]** (TRDD-0028dd34) | hook-fixes §13.5 — add a SessionStart hook with `uv venv` / `pip install` targeting `${CLAUDE_PLUGIN_DATA}` |
| Module-scope `sys.exit` / `raise SystemExit` in hook script **[NEW]** (TRDD-0028dd34) | hook-fixes §13.6 — move to `if __name__ == '__main__':` guard OR raise ImportError instead |
| `unset VIRTUAL_ENV` + plain `python3` antipattern **[NEW]** (TRDD-0028dd34) | hook-fixes §13.7 — switch to `uv run --script`; the `unset` becomes unnecessary |
| HTTP hook on latency-sensitive event with long timeout **[NEW]** (TRDD-0028dd34) | hook-fixes §13.8 — add `"async": true` for fire-and-forget OR cap timeout at 5s |
| Path-traversal in hook command (`..` segments escape plugin root) **[NEW]** (TRDD-0028dd34) | hook-fixes §13.11 — rewrite path to anchor at `${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_PROJECT_DIR}` without `..`, or declare cross-plugin dependency in plugin.json |
| `[RC-USERCFG-SHELL-INJECT] hooks.json <Event> hook interpolates ${user_config.<key>} into a SHELL-FORM command` (CRITICAL, CC v2.1.207) **[NEW v2.158.0]** | hook-fixes §14 — exec form (move the value into the `args` array) OR read `$CLAUDE_PLUGIN_OPTION_<KEY>` inside the script. **Exec form is LEGAL** and is never flagged; do not "fix" it. Quoting/escaping is NOT a fix. |

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
| `Command name '<name>' collides with a built-in Claude Code slash command` (v2.31.0+, Phase 15) **[NEW]** | plugin-structure-fixes §16 (Bundled slash-command collision) |

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
| Cross-source duplicate server name (`MCP server '<name>' is declared in <src1> and <src2>`) **[NEW]** | mcp-fixes §13 |
| `mcpServers: "./.mcp.json"` redundancy nudge **[NEW 2026-04-18]** | mcp-fixes §12a |
| `[RC-USERCFG-SHELL-INJECT] server '<name>' headersHelper interpolates ${user_config.<key>}` (CRITICAL, CC v2.1.207) **[NEW v2.158.0]** | mcp-fixes §14 — `headersHelper` has NO exec form; read the value inside the helper script (the server's `env` block, or a config file). |

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
| Cross-source duplicate LSP server name (`LSP server '<name>' is declared in <src1> and <src2>`) **[NEW 2026-04-18]** | lsp-fixes "Cross-source duplicate" entry at end |
| `lspServers: "./.lsp.json"` redundancy nudge **[NEW 2026-04-19]** | lsp-fixes "lspServers redundancy nudge" |
| `Non-standard directory '<dir>/'` (auto-suppressed when referenced by manifest, since v2.23.1) **[UPDATED 2026-04-19]** | plugin-structure-fixes §2 (Non-standard directory found) |

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

---

## 17. validate_cache.py

Added in v2.27.0 (Phase 11). Six rules covering Anthropic prompt-cache hygiene.

Primary fix guide: [cache-fixes.md](cache-fixes.md)

| Error topic | Fix guide section |
|---|---|
| `[CA-01] Static prefix violation` ({{TIMESTAMP}}/$(date)/inline dynamic in CLAUDE.md, agents, SKILL.md) | cache-fixes CA-01 |
| `[CA-02] Hook writes to cached file` (SessionStart/UserPromptSubmit/PreCompact writing CLAUDE.md or settings.json) | cache-fixes CA-02 |
| `[CA-03] Hook flips MCP/permission state` (mutating enabledMcpServers/disabledMcpServers/permissions.allow/deny) | cache-fixes CA-03 |
| `[CA-04] model: frontmatter (any component) forces in-line model switch` | cache-fixes CA-04 |
| `[CA-05] Hook script runs unbounded output command` (`git status`, `find`, `ls -laR`, `cat <large>`) | cache-fixes CA-05 |
| `[CA-06] PreCompact/PostCompact/SubagentStart hook does not preserve cached prefix` | cache-fixes CA-06 |

Cache-audit findings cost real money — every miss re-renders the system prompt at full token rate. Fix them.

---

## 18. validate_telemetry.py — plugin-shipped env-var hazards

Added in v2.29.0 (Phase 13). Detects plugin-shipped env vars that bypass user/org controls.

Primary fix guide: [telemetry-hazard-fixes.md](telemetry-hazard-fixes.md)

| Error topic | Fix guide section |
|---|---|
| `CRITICAL: Plugin ships CLAUDE_CODE_PLUGIN_SEED_DIR` | telemetry-hazard-fixes (CRITICAL section) |
| `CRITICAL: Plugin ships CLAUDE_CODE_SHELL_PREFIX` | telemetry-hazard-fixes (CRITICAL section) |
| `CRITICAL: Plugin ships CLAUDE_CONFIG_DIR` | telemetry-hazard-fixes (CRITICAL section) |
| `CRITICAL: Plugin ships BETA_TRACING_ENDPOINT pointing at external host` | telemetry-hazard-fixes (CRITICAL section) |
| `CRITICAL: Plugin ships OTEL_LOG_RAW_API_BODIES=file:*` | telemetry-hazard-fixes (CRITICAL section) |
| `MAJOR: Plugin ships CLAUDE_CODE_USE_BEDROCK / VERTEX / FOUNDRY / MANTLE` (third-party-provider bypass) | telemetry-hazard-fixes (MAJOR section) |
| `MAJOR: Plugin ships OTEL_LOG_USER_PROMPTS=1 / OTEL_LOG_TOOL_DETAILS=1 / OTEL_LOG_TOOL_CONTENT=1` (privacy exfiltration) | telemetry-hazard-fixes (Reference table) |

Every fix is the same shape: REMOVE the env var from the plugin's `env` blocks (plugin.json, hooks, MCP servers, settings.json) and document it in README so the user can opt in themselves.

---

## 19. Semantic pillar — Channel MCP Server Source-Code Security

Added in v2.36+ (TRDD-26446eed). Runs ONLY under `/cpv-semantic-validation` (Opus, opt-in) AND only when `plugin.json` declares a non-empty `channels` array. Reads each MCP server entry-point source (TypeScript / JavaScript / Python) and verifies the spec-mandated sender-ID gating from `channels-reference.md`.

Primary reference: [`skills/cpv-semantic-validation-skill/references/channel-source-security.md`](../../cpv-semantic-validation-skill/references/channel-source-security.md)

Deterministic prefilter: `scripts/cpv_channel_source_predicate.py` (`classify_channel_source(plugin_root)`) — the agent runs it first to bound LLM reading. When the prefilter returns `in_scope=False` the pillar is skipped entirely.

| Error topic | Severity | Fix guide |
|---|---|---|
| `RULE-1-no-sender-gating` — Channel MCP server forwards `notifications/claude/channel` without a sender-ID allowlist | CRITICAL | channel-source-security.md "Rule 1 — Inbound Sender Gating (CRITICAL)" |
| `RULE-2-permission-capability-ungated` — Server declares `capabilities.experimental['claude/channel/permission']` without sender-gating in the permission handler | CRITICAL | channel-source-security.md "Rule 2 — Permission-Relay Capability Gate (CRITICAL)" |
| `RULE-3-chat-id-only-gating` — Forwarding gated only on chat/room ID, not sender ID | MAJOR | channel-source-security.md "Rule 3 — Room/Chat-ID-Only Gating (MAJOR)" |
| `RULE-0-no-forward-call-detected` — Channel server resolves but the prefilter found no forward call | INFO | The Opus pillar must read the file to verify the indirection is safe. |

Fix shape (all rules): add an early-return that compares the transport-specific sender-ID property (`message.from.id` for Telegram, `message.author.id` for Discord, `message.sender_id` for iMessage/SMS gateways, etc.) against an allowlist sourced from a constant or env var. NEVER use truthy-only checks (`if (msg.from)`), empty allowlists, or always-true guards. Chat-ID gating is allowed as a SECONDARY scope check but never as the sole gate.

---

## 20. validate_marketplace cross-validation rules

Added in v2.81.0 (TRDD-c0ee9543, Phase A + Phase B). The validator
`scripts/validate_marketplace.py` now cross-references every plugin
entry against its upstream `plugin.json` (Phase B) and enforces a
strict field allowlist (Phase A). All RC-MKPL-* codes are documented
in [marketplace-error-index.md §1.1](marketplace-error-index.md#11-rc-mkpl-upstream-cross-validation-codes-v2810);
the mechanical fix recipes live in
[marketplace-upstream-drift.md](marketplace-upstream-drift.md).

Note: this section appears in `plugin-error-index.md` (not just
`marketplace-error-index.md`) because the cross-validation diff
fetches an upstream **plugin.json** to compare against the
marketplace entry. Plugin-fixer pipelines that touch BOTH
manifests should consult this section.

| Phase | Code | Severity | Fix guide |
|---|---|---|---|
| A | `RC-MKPL-UNKNOWN-FIELD` | MAJOR | marketplace-upstream-drift.md §3 |
| A | `RC-MKPL-UNKNOWN-SOURCE-FIELD` | MAJOR | marketplace-upstream-drift.md §4 |
| B | `RC-MKPL-NAME-MISMATCH` | MAJOR | marketplace-upstream-drift.md §1 |
| B | `RC-MKPL-VERSION-DRIFT` | MINOR | marketplace-upstream-drift.md §2 |
| B | `RC-MKPL-METADATA-DRIFT` | NIT | marketplace-upstream-drift.md §6 |
| B | `RC-MKPL-UPSTREAM-UNREACHABLE` | WARNING | marketplace-upstream-drift.md §5 |
