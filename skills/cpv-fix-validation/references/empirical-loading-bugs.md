# Empirical Plugin-Loading Bugs (CC v2.1.x as of 2026-04-18)

## Table of Contents

- [Path-form acceptance matrix](#path-form-acceptance-matrix-claude-plugin-validate)
- [Override-vs-default semantics](#override-vs-default-semantics-verified-runtime--debug-log)
- [Three silent footguns CC does NOT catch](#three-silent-footguns-cc-does-not-catch)
- [CPV validators added 2026-04-18](#cpv-validators-added-2026-04-18)
- [Anthropic docs corrections](#anthropic-docs-corrections)
- [Round 2 confirmations](#round-2-confirmations)
- [Tests added](#tests-added-all-passing)
- [Untestable in headless mode](#untestable-in-headless-mode-acknowledged-limitations)
- [v2.23.2 false-positive sweep](#v2232--false-positive-sweep-2026-04-19)

---

This document records the empirical evidence behind the five validators CPV added on 2026-04-18 to catch silent-failure modes in Claude Code's plugin loader. These failure modes are NOT detected by `claude plugin validate` (CC's own validator).

## Path-form acceptance matrix (`claude plugin validate`)

All four forms tested — string-folder, string-file, array-folder, array-file:

| Field | array-folder | array-file | string-folder | string-file |
|---|---|---|---|---|
| skills, commands, outputStyles | ✅ | ✅ | ✅ | ✅ |
| **agents** | ❌ `Invalid input` | ✅ | ❌ `Invalid input` | ✅ |

Only `agents` rejects folder paths — undocumented. The official docs' own complete-schema example `"./custom/agents/"` would actually fail this check.

## Override-vs-default semantics (verified runtime + debug log)

| Field | Override path semantics | Notes |
|---|---|---|
| skills | REPLACE (only override loaded) | matches docs |
| commands | REPLACE | matches docs |
| outputStyles | REPLACE (debug log: `Loaded 1 output styles ... custom path`) | matches docs |
| monitors | UNTESTABLE (headless `-p` mode skips monitors per docs) | — |
| **hooks** | ADDITIVE — but override = default file CASCADES | see footgun #1 below |
| **mcpServers** | ADDITIVE — inline silently wins on collision | see footgun #3 below |
| **lspServers** | ADDITIVE — inline silently wins on collision | verified via LSP_WINNER probe |

## Three silent footguns CC does NOT catch

### Footgun 1 — `hooks: "./hooks/hooks.json"` cascades to disable MCP

- `claude plugin validate`: passes silently (no error)
- Runtime: hook itself fires once (CC dedupes), BUT debug log shows:
  ```
  [ERROR] Duplicate hooks file detected: ./hooks/hooks.json resolves to already-loaded file
  [DEBUG] Plugin not available for MCP: <plugin>@inline - error type: hook-load-failed
  ```
  **The plugin's MCP servers are silently disabled.**
- CPV emits MAJOR with cascade explanation. Empirical evidence: test plugin `cpv-hooks-doublefire-test`. Confirmed scope: cascade is SPECIFIC to default-path collision (test `cpv-hooks-nondefault-test` — `hooks: "./hooks/extra.json"` loads cleanly with no cascade).

### Footgun 2 — `agents: <any-folder-path>` silently drops agents

- `claude plugin validate`: rejects with cryptic `agents: Invalid input` (no helpful explanation)
- If author skips validate: runtime silently drops the plugin's agents — no error in `--debug`, agents simply don't appear in the agent list.
- CPV emits MAJOR pre-empting CC's cryptic message with a clear fix recipe (use `.md` file paths only).

### Footgun 3 — MCP/LSP same server name in two sources → silent shadow

- Both sources load additively at runtime.
- Per-name collision: inline `plugin.json` version wins; the other source's definition is silently dropped without warning at validate or runtime.
- CPV emits MAJOR per duplicate name with note that inline wins.

## CPV validators added 2026-04-18

1. `validate_plugin.py` — `agents` folder-path rejection (MAJOR per folder path; skips the exact default `./agents/` to avoid double-firing with the existing default-folder CRITICAL)
2. `validate_plugin.py` — `hooks: "./hooks/hooks.json"` upgraded WARNING → MAJOR with cascade explanation. Handles string and array forms with path normalization.
3. `validate_mcp.py` — cross-source duplicate server names (MAJOR per name)
4. `validate_mcp.py` — `mcpServers: "./.mcp.json"` redundancy nudge (MINOR). Handles string and array forms with path normalization.
5. `validate_mcp.py` — array form for `mcpServers` field now properly supported (was previously rejected as MAJOR; per docs schema `mcpServers` accepts `string|array|object`)
6. `validate_lsp.py` — cross-source duplicate server names (MAJOR per name)
7. `validate_lsp.py` — `_extract_lsp_server_names_from_config_file` handles both wrapped and unwrapped `.lsp.json` formats
8. `validate_lsp.py` — `lspServers` field now supports all three forms (string path, array of paths, inline dict)

## Anthropic docs corrections

These are points where the official Claude Code docs misrepresent or omit important behavior, empirically verified this session:

1. **`agents` field is FILE-only** despite schema saying `string|array` and docs example showing folder. Bug.
2. **"Plugins define MCP servers in `.mcp.json` ... or inline in `plugin.json`"** is misleading — both can coexist additively, with silent inline-wins on collisions.
3. **"Hooks/MCP/LSP have different semantics for handling multiple sources"** in the path-behavior section understates the runtime cascade for hooks (disables MCP).
4. **The "replace" claim for skills/commands/outputStyles is correct** and empirically confirmed.

## Round 2 confirmations

| Test | Result | Implication |
|---|---|---|
| `hooks: "./hooks/extra.json"` (NON-default path) | Clean load, hook fires once, NO cascade | Hooks-MAJOR rule's default-path-only scope is CORRECT |
| `agents: ["./extras/override.md"]` (file path) | REPLACE — default `agents/` not scanned | Matches docs |
| `skills: ["./skills/", "./extras/"]` (include-default-in-array) | BOTH scanned | Matches docs |
| `mcpServers: "./.mcp.json"` (override = default file) | Single load, no cascade (unlike hooks) | MINOR nudge correct, no MAJOR needed |
| Components inside `.claude-plugin/agents/` or `.claude-plugin/skills/` | Silently NOT loaded by CC | CPV already catches with CRITICAL |
| Absolute paths in skills/agents fields | CC rejects with cryptic `Invalid input` | CPV already catches with helpful `must start with './'` MAJOR |
| settings.json with keys other than `agent`/`subagentStatusLine` | Silently ignored by CC, no error | Matches docs |
| Monitors in headless `-p` mode | Skipped per docs (untestable from Bash) | Docs claim accepted |
| userConfig prompt flow in headless | Doesn't trigger (untestable from Bash) | Docs claim accepted |

## Tests added (all passing)

- `test_validate_mcp.py::TestCrossSourceDuplicateServerNames` — 6 tests
- `test_validate_mcp.py::test_mcpservers_pointing_at_default_*` — 2 tests
- `test_validate_lsp.py::TestLspCrossSourceDuplicateServerNames` — 5 tests
- `test_validate_plugin.py::TestEmpiricalDocsBugsAdded20260418` — 7 tests

Total: 20 new tests. Full suite: 2376 passing.

## Untestable in headless mode (acknowledged limitations)

- Monitors runtime override semantics (require interactive CLI)
- userConfig prompt + substitution end-to-end (require interactive enable flow)
- Marketplace dependency tag resolution (requires marketplace + git tag setup)
- Cross-marketplace allowlist blocking (requires multi-marketplace setup)

## v2.23.2 — false-positive sweep (2026-04-19)

Batch-scanned all 160 installed plugins under `~/.claude/plugins/cache/` to surface
patterns where CPV WARNINGs were systematically wrong. The empirical scan found
19 plugins flagged with "Non-standard directory" warnings — all were false positives
for legitimate patterns.

**Three classes of fix:**

### 1. Common dir names added to `known_dirs`

Added these to `validate_structure`'s `known_dirs` set (no longer warn):

`prompts`, `demo`, `demos`, `eval`, `evals`, `node_modules`, `output`, `outputs`,
`server`, `public`, `static`, `web`, `shared`, `settings`, `guidances`, `plugins`,
`rust`, `go`, `python`, `node`, `ts`, `js`, `java`, `kotlin`, `swift`, `ruby`,
`csharp`, `cpp`, `c`.

The language-source dirs (rust, go, etc.) cover plugins that ship native binaries
in `bin/` and bundle the source tree alongside (e.g. perfect-skill-suggester ships
both `rust/` source and `bin/<platform-binary>`).

### 2. Submodule pattern auto-allowance

Many plugins (especially Layout B nested marketplaces) have a subdirectory named
after the plugin itself, e.g. `web-automation-suite/web-automation-suite/`. The
validator now reads `.claude-plugin/plugin.json:name` and auto-allows a sibling
directory matching that name (case-insensitive).

### 3. Marketplace short-circuit in `main()`

If `validate_plugin.py` is pointed at a folder with `.claude-plugin/marketplace.json`
but no `plugin.json`, it now exits 1 with a clear "this is a marketplace, use
validate_marketplace.py" hint instead of running plugin checks (which produced
dozens of false WARNINGs for the per-plugin subfolders).

**Result:** False-positive count for "Non-standard directory" warnings dropped
from 19 plugins (across the 160 surveyed) to 1 remaining — and that 1 is a
genuine non-standard dir (`web-automation-suite/task-manager/`) that the plugin
author should rename or document.

**Tests added:**

- `test_validate_plugin.py::TestKnownDirsExpandedV2_23_2` — 29 parametrized cases
- `test_validate_plugin.py::TestSubmodulePatternAllowance` — 3 tests
- `test_validate_plugin.py::TestMarketplaceShortCircuit` — 2 tests

Total tests after v2.23.2: 2426 (up from 2392).
