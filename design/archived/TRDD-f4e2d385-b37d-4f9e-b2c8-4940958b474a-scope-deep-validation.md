---
trdd-id: f4e2d385
title: TRDD-f4e2d385 — Deep validation for local/project scope
column: complete
updated: 2026-08-25T17:25:39+0200
---

# TRDD-f4e2d385 — Deep validation for local/project scope

**TRDD ID:** `f4e2d385-b37d-4f9e-b2c8-4940958b474a`
**Filename:** `design/tasks/TRDD-f4e2d385-b37d-4f9e-b2c8-4940958b474a-scope-deep-validation.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

**Status:** Done (2026-05-10)
**Supersedes:** clarifies TRDD-2be75e88 (initial scope validators)

**Completion notes (2026-05-10):**
- Phases A, B, C, D, E shipped in commit `37ed357` (2026-04-17 — `feat(scope-validators): deep element validation + settings subtrees + plugin enum (TRDD-f4e2d385)`).
- Follow-up audit (2026-05-10) closed two §3.1/§3.3 gaps in `validate_project_scope.py`:
  1. `validate_project_rules_deep` — symmetric to `validate_local_rules_deep`, runs `validate_rules_directory` on the rules folder and filters out untracked findings via the same `rules_dir.parent` path-resolution fix that the local-scope side already uses.
  2. `_validate_mcp_json_file_deep_project` — runs the full `validate_mcp_config` pipeline on tracked `.mcp.json` files (transport schema, reserved-name detection, package-executor warnings) on top of the existing project-scope-specific shallow check (literal secrets in `env`, absolute home paths in `command`/`args`).
- 8 new tests added under `tests/test_validate_project_scope.py::TestProjectRulesDeepValidation` and `tests/test_validate_project_scope.py::TestProjectMcpJsonDeepValidation`. All 117 scope tests pass.
- Test count: scope test suite went from 109 to 117. Full test suite: 4490 passed (excluding 2 known-flaky perf/main-cli tests under TRDD-fa70f9b8 jurisdiction).
- `lspServers` deep validation NOT implemented (per v2.21.3 follow-up: `lspServers` is plugin-only — settings files reject it via the plugin-only-key CRITICAL, deep-validating an inline block would imply it is semantically valid).

## 1. User intent (verbatim)

> validate local scope is a command to validate the locally installed skills,
> agents, commands, rules, mcp, hooks, output style, etc. installed in the
> local .claude folder of a project (except for the mcp json that can be
> outside the .claude folder). This validation must check both standalone
> elements and plugins installed with local scope and enabled locally,
> including lsp servers plugins. It is very different from validating a
> plugin, even if the single elements are the same. Being installed
> locally, it means that git tracked elements must be skipped. settings.json
> must be ignored, and only settings.local.json must be checked.
>
> instead validating project scoped folders will still do the same except
> that it will ignore untracked elements and only scan git tracked elements.
> Also it will scan settings.json instead of settings.local.json. be careful.

## 2. Current state (v2.20.1) vs target

### 2.1. What the current `validate_local_scope.py` does

| Area | Behaviour | Gap vs spec |
|---|---|---|
| `.claude/settings.local.json` | validates managed-only / deprecated / schema keys | ✓ ok |
| `.claude/settings.json` (if untracked) | WIP-warn + strict rules | ✓ ok |
| `.claude/agents/*.md` (untracked) | **shallow frontmatter-only check** | ✗ should run full `validate_agent` |
| `.claude/skills/*/SKILL.md` (untracked) | **shallow frontmatter-only check** | ✗ should run full `validate_skill_comprehensive` |
| `.claude/commands/*.md` (untracked) | **shallow frontmatter-only check** | ✗ should run full `validate_command` |
| `.claude/rules/*.md` (untracked) | **shallow frontmatter-only check** | ✗ should run full `validate_rules_directory` |
| `.claude/output-styles/*.md` (untracked) | shallow frontmatter-only check | ≈ ok (no dedicated validator exists) |
| Hooks in `settings.local.json.hooks` subtree | **NOT VALIDATED** | ✗ should run `validate_hook` on the subtree |
| MCP in `settings.local.json.mcpServers` | **NOT VALIDATED** | ✗ should run `validate_mcp_config` |
| `.mcp.json` at project root | warns if untracked, CONTENT not validated | ✗ should run `validate_mcp_config` |
| `~/.claude.json` per-project MCP | reports as INFO only | ≈ acceptable (info level) |
| LSP servers in `settings.local.json.lspServers` | **NOT VALIDATED** | ✗ should run `validate_lsp_config` |
| Locally-enabled plugins (`enabledPlugins` in `settings.local.json`) | **NOT ENUMERATED / NOT VALIDATED** | ✗ should resolve each to `~/.claude/plugins/cache/...` and run `validate_plugin` |
| `CLAUDE.local.md` | tracks-check + basic content rules | ✓ ok |
| `.gitignore` coverage | minor checks | ✓ ok |

### 2.2. What `validate_project_scope.py` does (current)

Mirror of the above, but filtering `classify_*_scope` to `project` (git-tracked) rather than `local` / `no-git`. Same gap list applies — deep validation, hooks/MCP/LSP subtrees, plugin enumeration are all missing.

## 3. Design

### 3.1. Element validation — swap shallow for deep

Replace `_validate_markdown_frontmatter_only` calls with calls to the directory-level validators that already exist:

| Folder | Function to call | Module |
|---|---|---|
| `.claude/agents/` | `validate_agents_directory(agents_dir) -> list[AgentValidationReport]` | `validate_agent` |
| `.claude/skills/` | iterate subfolders, call `validate_skill_comprehensive` per-skill | `validate_skill_comprehensive` |
| `.claude/commands/` | walk `*.md`, call `validate_command_file` per-file | `validate_command` |
| `.claude/rules/` | `validate_rules_directory(rules_dir, report, plugin_root=None)` | `validate_rules` |
| `.claude/output-styles/` | keep shallow frontmatter-only (no dedicated validator) | (local helper) |

For each element, gate on git-tracked status as today:
- **local scope**: only validate files NOT tracked (including files in a gitignored subfolder)
- **project scope**: only validate files that ARE tracked

Results from the sub-validators are merged into the main `ValidationReport`.

### 3.2. Settings subtree validation

`settings.local.json` can contain inline `hooks`, `mcpServers`, `lspServers` blocks. For each:

- **hooks**: extract the `hooks` dict from settings, write to a temp `hooks.json` file, call `validate_hooks(temp_path, plugin_root=None)`, merge results. Don't actually need temp files — refactor `validate_hook.py` to expose a `validate_hooks_from_data(data: dict, report, ...)` alternative. If that refactor is too invasive, write to `/tmp` via `tempfile` module.
- **mcpServers**: build a minimal `{"mcpServers": {...}}` dict, write to temp file, call `validate_mcp_config(temp_path)`, merge results.
- **lspServers**: same pattern via `validate_lsp_config`.

### 3.3. `.mcp.json` deep validation

If the project has `.mcp.json` at root AND it is untracked (local-scope variant), call `validate_mcp_config(path)` and merge. Project-scope validator does the same for tracked `.mcp.json`.

### 3.4. Locally-enabled plugin enumeration

Read `enabledPlugins` map from `settings.local.json`:

```json
{
  "enabledPlugins": {
    "plugin-a@marketplace-x": true,
    "plugin-b@marketplace-y": false
  }
}
```

For each entry where value is `true`:

1. Parse `<plugin>@<marketplace>` into name+marketplace.
2. Resolve cache path: `~/.claude/plugins/cache/<marketplace>/<plugin>/` (version-agnostic — pick the highest version if multiple).
3. If the cache dir doesn't exist, emit MAJOR: "locally-enabled plugin X is not installed in the plugin cache — enabling a non-installed plugin has no effect".
4. If it does exist, call `validate_plugin(plugin_root, report)` and merge results, BUT prefix each finding with `[enabled plugin X@Y]` so the user can tell which plugin the finding came from.

Project-scope does the same but from `settings.json.enabledPlugins`.

### 3.5. The `be careful` — clearly distinguish scope semantics

| Rule | Local scope | Project scope |
|---|---|---|
| Which settings file? | `settings.local.json` ONLY (skip `settings.json`) | `settings.json` ONLY (skip `settings.local.json`) |
| Tracked vs untracked elements | validate UNTRACKED only | validate TRACKED only |
| `CLAUDE.local.md` | validate existence + must be gitignored | skip (not project's concern) |
| `CLAUDE.md` | skip | validate frontmatter + size |
| `.mcp.json` | validate content IF untracked | validate content IF tracked |
| `.gitignore` | check it covers `settings.local.json` / `CLAUDE.local.md` | check it does NOT accidentally ignore tracked elements |
| `enabledPlugins` source | `settings.local.json` | `settings.json` |

### 3.6. Non-goals

- **User-scope plugins** (`~/.claude/settings.json` enabledPlugins) are NOT validated here — that's `cpv-doctor`'s job.
- **Semantic validation** (description quality, trigger effectiveness) — stays in `cpv-semantic-validation`.
- **Marketplace validation** — `cpv-validate-marketplace` territory.

## 4. Implementation plan

### Phase A — Element deep validation swap (smallest blast radius)

1. Import directory-level validators in `validate_local_scope.py`.
2. Replace `_walk_local_markdown_folder` internals for agents/skills/commands/rules with per-folder wrappers that:
   - Determine untracked files
   - For each untracked element, invoke the real validator
   - Merge results
3. Prefix findings with `[local scope]` so users can tell which validator surfaced it.
4. Do the same in `validate_project_scope.py` but for tracked files.

### Phase B — Settings subtree validation

1. Add a helper `_extract_and_validate_settings_subtree(settings: dict, key: str, validator_fn)` that writes the subtree to a tempfile and invokes the validator.
2. Apply to `hooks`, `mcpServers`, `lspServers` from the respective settings file.

### Phase C — Plugin enumeration

1. Add `_validate_locally_enabled_plugins(enabled: dict, report)`:
   - Parse plugin keys
   - Resolve cache paths
   - Invoke `validate_plugin` on each
   - Prefix findings

### Phase D — Tests

Test file `tests/test_validate_local_scope_deep.py` (new):
- fixture with `.claude/agents/bad-agent.md` (missing name) → asserts agent MAJOR appears in local-scope report
- fixture with `.claude/settings.local.json.hooks` containing invalid hook → asserts hook validator MAJOR appears
- fixture with `enabledPlugins: {plugin@mkt: true}` but plugin missing → asserts MAJOR about non-installed plugin
- fixture with valid .mcp.json (untracked) → asserts MCP validator runs

Mirror tests for project-scope.

### Phase E — Doc updates

- `commands/cpv-validate-local-scope.md` "What Gets Validated" section reflects deep validation
- `skills/fix-validation/references/plugin-error-index.md` adds entries for new cross-validator findings (just pointers — each element validator already has its own fix guide)

## 5. Files to change

| File | Change type |
|---|---|
| `scripts/validate_local_scope.py` | Major — add imports + swap shallow for deep + settings subtrees + plugin enum |
| `scripts/validate_project_scope.py` | Major — same structure as local, inverted filter |
| `tests/test_validate_local_scope_deep.py` | New |
| `tests/test_validate_project_scope_deep.py` | New |
| `commands/cpv-validate-local-scope.md` | Minor — docs |
| `commands/cpv-validate-project-scope.md` | Minor — docs |
| `design/tasks/TRDD-f4e2d385-…md` | New (this file) |

## 6. Rollout

- Phase A + B + C ship as a single commit (they are tightly coupled).
- Tests in a second commit.
- Docs in a third commit.
- Publish as v2.21.0 (minor — substantial functional expansion, not a breaking change since the validators still accept the same CLI arguments).

## 7. Risk and mitigation

**Risk**: Deep element validation may flood reports with findings previously hidden behind shallow checks. Users may perceive this as a regression.
**Mitigation**: This is the correct behaviour per user's spec. Include a short migration note in the release description.

**Risk**: Plugin enumeration may fail if cache paths are non-standard.
**Mitigation**: Fall back to `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` highest-version match; emit an INFO if resolution fails rather than a hard failure.

**Risk**: Temp-file pattern for settings subtree validation leaks files.
**Mitigation**: Use `tempfile.TemporaryDirectory()` context manager — auto-cleanup.

## Approval log

- 2026-08-25T17:25:39+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED commits 37ed3570/14e2f810 merged 450095e6 (batch_ak)
