---
trdd-id: 2be75e88
title: Scope validators (project-scope and local-scope)
column: complete
updated: 2026-08-25T17:25:22+0200
---

# TRDD-2be75e88-3c23-4c91-9e4e-5ce315b497dc — Scope validators (project-scope & local-scope)

**TRDD ID:** `2be75e88-3c23-4c91-9e4e-5ce315b497dc`
**Filename:** `design/tasks/TRDD-2be75e88-3c23-4c91-9e4e-5ce315b497dc-scope-validators.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Done (shipped in v2.15.0, hardened in v2.15.1)
**Author:** Emasoft (via Claude session)
**Created:** 2026-04-14

---

## User request (verbatim)

> Plugins and skills cannot be the only things we validate. Let's extend the plugin powers:
> - add a new command to the plugin: `cpv-validate-project-scope`. It will validate the elements that are configured as PROJECT scoped and git tracked.
> - add a new command to the plugin: `cpv-validate-local-scope`. It will validate the elements that are configured as USER scoped and NOT git tracked.
> read this as reference: https://code.claude.com/docs/en/settings.md

Clarifications:
- Both commands take a **project path as argument**. No system-wide scan.
- "Project scope" = git-tracked elements under `<project>/.claude/` + git-tracked `.mcp.json` at repo root.
- "Local scope" = NOT-git-tracked elements. The user's exact words: *"all folders not git tracked under .claude/ are to be considered locally scoped. for example if agents/ is not tracked is local scoped, but if it is git tracked is project scoped. the same for skills, rules, commands, etc. And mcp has a very special json file outside the .claude folder."*
- The `.mcp.json` special file is at repo ROOT (not inside `.claude/`) and is normally git-tracked per Claude Code docs.

"Local scope" in the user's wording maps to Claude Code's documented **Local scope** (`.claude/settings.local.json` and anything else gitignored inside the project), *not* the **User scope** (`~/.claude/`). The user-global `~/.claude/` directory is out of scope for this TRDD — if wanted later, it becomes a third command `cpv-validate-user-scope` with no path arg.

---

## 1. Background

Per https://code.claude.com/docs/en/settings.md, Claude Code defines **four scopes**:

| Scope       | Location                                                                    | Git-tracked? |
|:------------|:----------------------------------------------------------------------------|:------------:|
| **Managed** | OS policy / `/Library/Application Support/ClaudeCode/managed-settings.json` | N/A (MDM)    |
| **User**    | `~/.claude/`                                                                | No           |
| **Project** | `<repo>/.claude/` + `<repo>/.mcp.json`                                      | **Yes**      |
| **Local**   | `<repo>/.claude/settings.local.json` + gitignored folders under `<repo>/.claude/` | **No**       |

**The key insight**: the distinction between project scope and local scope is **git-tracking status**, not filename. The same folder (e.g., `.claude/agents/`) can be either project-scope or local-scope depending on whether the project's `.gitignore` excludes it.

CPV currently validates plugin packages (marketplace structure, plugin.json, skills, hooks inside plugins). It does **not** validate the consumer side — the `.claude/` folders and config files that users create in their own repositories when they use Claude Code. These two new validators close that gap.

---

## 2. Goals

1. `cpv-validate-project-scope <path>` — validates only the git-tracked Claude Code configuration under `<path>`. Enforces rules that matter for shared team configs (no secrets, no user-specific absolute paths, no keys that Claude Code ignores in project scope).
2. `cpv-validate-local-scope <path>` — validates only the non-git-tracked Claude Code configuration under `<path>`. Enforces rules that matter for personal overrides (file is actually gitignored, no managed-only keys, suggestion to move shareable config to project scope).

Non-goals:
- No system-wide scan of all known projects
- No validation of `~/.claude/` (user-global scope) — deferred to future `cpv-validate-user-scope`
- No fixing / auto-remediation — these are read-only validators like the rest of CPV
- No validation of managed-scope files — managed settings are org-deployed and out of scope

---

## 3. Architecture

### 3.1 New files

| File | Purpose |
|------|---------|
| `scripts/validate_project_scope.py` | New orchestrator — walks `<path>/.claude/`, filters to git-tracked, dispatches to sub-validators and scope-specific settings rules. |
| `scripts/validate_local_scope.py` | New orchestrator — walks `<path>/.claude/`, filters to non-git-tracked, dispatches. |
| `scripts/cc_scope_rules.py` | New shared module — Claude Code settings key taxonomy (managed-only keys, global-config keys, project-rejected keys, managed-only patterns, secret-detection regex). Both orchestrators import from it. |
| `commands/cpv-validate-project-scope.md` | New `.md` command file with frontmatter. |
| `commands/cpv-validate-local-scope.md` | New `.md` command file with frontmatter. |
| `tests/test_validate_project_scope.py` | New test suite (target: ~30 tests). |
| `tests/test_validate_local_scope.py` | New test suite (target: ~30 tests). |
| `tests/test_cc_scope_rules.py` | New test suite for the shared rules module (target: ~15 tests). |

### 3.2 Modified files

| File | Change |
|------|--------|
| `scripts/cli.py` | Add `validate_project_scope()` and `validate_local_scope()` entry functions. |
| `pyproject.toml` | Add `cpv-validate-project-scope` and `cpv-validate-local-scope` to `[project.scripts]`. |
| `README.md` | Document the two new commands. |
| `agents/plugin-validator.md` | Add the two commands to the First Contact menu. |

> **Note**: This TRDD originally referenced `.claude-plugin/marketplace.json`,
> but CPV is a standalone plugin (Layout A) — commands are auto-discovered
> from the `commands/` folder at install time, so no manifest update is
> needed. The reference was removed during the v2.15.1 audit pass.

### 3.3 Data flow

```
            cpv-validate-project-scope <path>
                        │
                        ▼
            validate_project_scope.main()
                        │
          ┌─────────────┴─────────────┐
          │                           │
          ▼                           ▼
   discover .claude/       classify each file as
   tree and .mcp.json      git-tracked via `git ls-files`
          │                           │
          └─────────────┬─────────────┘
                        ▼
          for each element type where
          the folder/file IS tracked:
                        │
          ┌──────┬──────┼──────┬──────┬──────┐
          ▼      ▼      ▼      ▼      ▼      ▼
     settings  mcp   agents  skills  cmds  rules/hooks/CLAUDE.md
          │     │      │       │      │        │
          └─────┴──────┴───────┴──────┴────────┘
                        │
                        ▼
              cc_scope_rules.* +
              existing sub-validators
                        │
                        ▼
          ValidationReport -> save_report_and_print_summary
```

The local-scope orchestrator is identical except the classification step inverts: it keeps files where the folder/file is NOT git-tracked.

### 3.4 Git-tracking classifier

New helper in `scripts/cc_scope_rules.py`:

```python
def is_git_tracked(path: Path, repo_root: Path) -> bool:
    """Return True iff `path` is tracked by git under `repo_root`.

    Uses `git ls-files --error-unmatch <rel>`. Returns False if the repo
    has no .git (not a git repo — every file is treated as 'local' by
    convention), if git is unavailable, or if the file is in .gitignore.
    """

def is_git_ignored(path: Path, repo_root: Path) -> bool:
    """Return True iff `path` is matched by .gitignore under `repo_root`.

    Uses `git check-ignore -q <rel>`. Exit 0 = ignored. Exit 1 = not ignored.
    Returns False (= not ignored) if the repo has no .git.
    """
```

**Classification rule** for a folder like `.claude/agents/`:
1. If the folder doesn't exist → skip, no error
2. If `git check-ignore -q .claude/agents` returns 0 → folder is ignored → **local scope**
3. If `git ls-files .claude/agents/` returns ≥ 1 line → folder has tracked files → **project scope**
4. Otherwise → folder exists but has zero tracked files → treat as **local scope** (untracked)

For individual files (like `.claude/settings.local.json` or `CLAUDE.local.md`), simpler:
1. File exists
2. `git check-ignore -q <file>` → exit 0 → ignored → **local scope**
3. `git ls-files --error-unmatch <file>` → exit 0 → tracked → **project scope**
4. File exists but neither tracked nor ignored → **untracked** → **local scope** (new personal file that hasn't been committed yet)

**Non-git-repo case**: if `<path>/.git` doesn't exist, `cpv-validate-project-scope` emits a WARNING *"not a git repository — no files can be classified as project-scope"* and exits 0. `cpv-validate-local-scope` emits an INFO *"not a git repository — every file under .claude/ is treated as local-scope"* and proceeds to validate everything.

---

## 4. What `cpv-validate-project-scope` validates

For each of the following, if and only if it is **git-tracked**:

### 4.1 `.claude/settings.json` (new scope-specific rules)

**CRITICAL** (rejected or silently dropped by Claude Code):
- `autoMemoryDirectory` present → per settings.md: *"Not accepted in project settings to prevent shared repos from redirecting memory writes to sensitive locations"*
- `autoMode` block present → *"Not read from shared project settings"*
- `useAutoModeDuringPlan` present → *"Not read from shared project settings"*
- `skipDangerousModePermissionPrompt` (anywhere under `permissions.skipDangerousModePermissionPrompt`) → *"Ignored when set in project settings"*

**MAJOR** (silently ignored / schema error):
- Any managed-only key present: `allowedChannelPlugins`, `allowedMcpServers`, `deniedMcpServers`, `allowManagedHooksOnly`, `allowManagedMcpServersOnly`, `allowManagedPermissionRulesOnly`, `blockedMarketplaces`, `channelsEnabled`, `forceRemoteSettingsRefresh`, `pluginTrustMessage`, `strictKnownMarketplaces`
- Any global-config-only key in `settings.json`: `autoConnectIde`, `autoInstallIdeExtension`, `editorMode`, `showTurnDuration`, `terminalProgressBarEnabled`, `teammateMode` (these live in `~/.claude.json`, not settings.json)

**MINOR** (shareability / security):
- `apiKeyHelper` / `awsAuthRefresh` / `awsCredentialExport` / `otelHeadersHelper` pointing to an absolute path containing `/Users/<name>/`, `/home/<name>/`, `C:\\Users\\<name>\\`, or any path under `$HOME` — machine-specific, breaks for other team members
- `statusLine.command` / `fileSuggestion.command` with an absolute home path (same patterns)
- `hooks.<event>[].command` or `hooks.<event>[].hooks[].command` that contain an absolute user path AND do NOT start with `$CLAUDE_PROJECT_DIR` or `${CLAUDE_PLUGIN_ROOT}` (see hooks.md)
- `env` object values matching secret patterns (key names: `*_KEY`, `*_TOKEN`, `*_SECRET`, `AUTH_*`, `*_PASSWORD`) unless the value is `${VAR}` or `${VAR:-default}`
- `additionalDirectories` / `sandbox.filesystem.allowWrite` / `sandbox.filesystem.allowRead` containing absolute home paths
- `claudeMdExcludes` with absolute home paths

**NIT** (style):
- `$schema` field missing (should point to `https://json.schemastore.org/claude-code-settings.json`)
- Keys in non-canonical order vs the documented order — skip, not worth the noise

### 4.2 `.mcp.json` at repo root

If the file exists AND is git-tracked:
- **CRITICAL**: Malformed JSON
- **MAJOR**: Top-level not an object; missing `mcpServers` key
- **MINOR**: Any `mcpServers.<name>.env.<KEY>` value that looks like a literal secret (e.g., API keys starting with `sk-`, `ghp_`, `AIza`, JWT pattern) instead of using `${VAR}` expansion
- **MINOR**: Any `mcpServers.<name>.command` or `args` containing an absolute home path
- **NIT**: `${VAR:-default}` syntax recommended over bare `${VAR}` for optional values

Reuse `validate_mcp.py` where possible — call its per-server validator functions directly if they exist.

### 4.3 `.claude/agents/*.md`

For each agent file in the git-tracked folder:
- Reuse the existing agent validator logic (frontmatter shape, tools field, name kebab-case, description triggering, etc.)
- **New scope-specific check**: agent `system-prompt` or `initialPrompt` fields referencing absolute user paths → MINOR
- **New**: `mcpServers` / `hooks` / `permissionMode` fields are OK at project scope (unlike plugin-shipped agents where they're forbidden)

### 4.4 `.claude/skills/<name>/SKILL.md`

Reuse existing `validate_skill.py` / `validate_skill_comprehensive.py` logic per skill folder.

### 4.5 `.claude/commands/*.md` (legacy commands directory)

Reuse existing `validate_command.py` logic. Note that per docs, commands and skills are now unified, but the legacy commands folder is still supported.

### 4.6 `.claude/rules/*.md`

Reuse existing `validate_rules.py` logic. Check YAML frontmatter, `paths:` field if present.

### 4.7 `.claude/output-styles/*.md`

Basic markdown + frontmatter validation (new; no existing sub-validator — implement inline or defer to NIT-only).

### 4.8 `CLAUDE.md` or `.claude/CLAUDE.md`

- Check for absolute user paths in content (`/Users/<name>/`, `/home/<name>/`, `C:\\Users\\<name>\\`) → MINOR
- Check for recognizable secret patterns → MAJOR
- Check `@<path>` imports don't reference absolute user paths → MINOR

### 4.9 `.claude/hooks/*.sh` / `.claude/hooks/*.py` (if a `hooks/` folder exists)

If any script files in `.claude/hooks/` reference themselves with absolute paths → MINOR.
Most hook config lives in `settings.json`'s `hooks` block (validated in 4.1); the folder itself usually holds the scripts those commands invoke.

### 4.10 `.gitignore` sanity

If the repo has a `.gitignore`:
- INFO: `.claude/settings.local.json` is present in `.gitignore` (good)
- WARNING if missing: *"`.claude/settings.local.json` is not in .gitignore — Claude Code auto-adds it on first creation but it's worth pinning"*
- INFO: `CLAUDE.local.md` is present in `.gitignore` (good)

---

## 5. What `cpv-validate-local-scope` validates

For each of the following, if and only if it is **NOT git-tracked** (ignored or untracked):

### 5.1 `.claude/settings.local.json`

**CRITICAL** (structural):
- Malformed JSON / JSONC parse error
- Top-level not an object

**MAJOR**:
- File exists AND is tracked by git (contradiction: should be gitignored) → this is the "file in wrong scope" error
- Any managed-only key present (same list as 4.1) — silently ignored
- Any global-config-only key (same list as 4.1) — schema error

**MINOR** (move-to-shared hints):
- Keys that are typically shared and SHOULD live in `.claude/settings.json` instead:
  - `extraKnownMarketplaces` — marketplaces are usually team-wide
  - `enabledPlugins` for plugins that the whole team should have (can't detect intent, so skip this or make it INFO)
  - `permissions.deny` patterns that look like universal exclusions (`Read(./.env)`, etc.) — suggest they move to shared
- `includeCoAuthoredBy` is deprecated → NIT

**NIT**:
- `$schema` missing

### 5.2 `CLAUDE.local.md`

If the file exists:
- **MAJOR**: file is git-tracked (should be gitignored per memory.md)
- **NIT**: `.gitignore` does not list `CLAUDE.local.md`

### 5.3 `.claude/agents/*.md` (when `.claude/agents/` folder is gitignored)

If `.claude/agents/` is listed in `.gitignore` or is untracked:
- Basic frontmatter validation (same sub-validator as project-scope, but with RELAXED rules: user-specific absolute paths are OK at local scope since only this user reads them)
- No secret detection (local is fine)

### 5.4 `.claude/skills/<name>/`, `.claude/commands/*.md`, `.claude/rules/*.md`, `.claude/output-styles/*.md`

Same pattern: if the folder is gitignored, validate the file structure only with relaxed rules.

### 5.5 Per-project MCP state from `~/.claude.json`

New: read `~/.claude.json` and look up `projects[<abs_path>]`. If it has `mcpServers`, validate each entry (basic shape: `type`, `command`/`url`, `args`, `env`). Flag as INFO, not CRITICAL — this file is user-managed and Claude Code rewrites it.

If `~/.claude.json` doesn't exist or doesn't have an entry for `<abs_path>`, skip with INFO *"No per-project local MCP servers registered."*

### 5.6 `.mcp.json` at repo root (only if NOT git-tracked — unusual case)

Per Claude Code docs, `.mcp.json` is normally committed. If it exists but isn't tracked:
- **WARNING**: `.mcp.json` is expected to be git-tracked per mcp.md — is this intentional?
- Apply basic shape checks

### 5.7 `.gitignore` completeness

- **INFO** (missing but recommended): `.claude/settings.local.json` not in `.gitignore`
- **INFO** (missing but recommended): `CLAUDE.local.md` not in `.gitignore`

---

## 6. Command file (`commands/cpv-validate-project-scope.md`)

Frontmatter shape (matching `cpv-validate-plugin.md`):

```yaml
---
name: cpv-validate-project-scope
description: Validate git-tracked Claude Code configuration (project scope) under a given project path — shared team settings, .mcp.json, agents/skills/commands/rules/hooks/CLAUDE.md.
allowed-tools: Read, Bash, Glob, Grep
argument-hint: "<project_path> [--verbose] [--json] [--report <file>] [--strict]"
user-invocable: true
---
```

Content sections: Usage, What Gets Validated, Exit Codes, Execution.

Same schema for `commands/cpv-validate-local-scope.md`, with different description and scope.

---

## 7. CLI wiring (`scripts/cli.py`)

```python
def validate_project_scope() -> None:
    """Validate git-tracked project-scope Claude Code config under a project path."""
    from validate_project_scope import main
    sys.exit(main())

def validate_local_scope() -> None:
    """Validate non-git-tracked local-scope Claude Code config under a project path."""
    from validate_local_scope import main
    sys.exit(main())
```

`pyproject.toml` `[project.scripts]`:
```toml
cpv-validate-project-scope = "scripts.cli:validate_project_scope"
cpv-validate-local-scope = "scripts.cli:validate_local_scope"
```

---

## 8. Exit codes (standard CPV convention)

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | At least one CRITICAL |
| 2 | At least one MAJOR |
| 3 | At least one MINOR |
| 4 | At least one NIT (only in --strict mode) |

---

## 9. Test scenarios

### 9.1 `test_validate_project_scope.py` (≥30 tests)

Fixtures to build under `tmp_path`:
- **Clean git repo** with `.claude/settings.json` containing only shareable keys
- **Repo with autoMemoryDirectory in project settings** → CRITICAL
- **Repo with hardcoded secret in .mcp.json env** → MINOR
- **Repo with absolute user path in statusLine.command** → MINOR
- **Repo with managed-only key (allowedMcpServers) in project settings** → MAJOR
- **Repo with global-config key (editorMode) in settings.json** → MAJOR
- **Repo with skipDangerousModePermissionPrompt under permissions** → CRITICAL
- **Repo without .git** → WARNING, returns 0
- **Repo with .claude/ but no settings.json** → INFO, returns 0
- **Repo with agents/, skills/, commands/, rules/ all tracked** → passes with INFO per element
- **Repo with .claude/settings.local.json committed by mistake** → ignored (local-scope validator's job)

Each test uses `git init`, `git add`, `git commit`, optionally `git update-index --assume-unchanged` via subprocess to mark files ignored. Tests run the real `git` binary inside `tmp_path` (NO mocks per project rule: "Do not use mockup tests").

### 9.2 `test_validate_local_scope.py` (≥30 tests)

Fixtures:
- **settings.local.json exists AND is gitignored** → passes
- **settings.local.json exists AND is tracked** → MAJOR
- **settings.local.json with managed-only key** → MAJOR
- **CLAUDE.local.md exists, gitignored** → passes
- **CLAUDE.local.md tracked** → MAJOR
- **`.claude/agents/` in .gitignore with a file inside** → validates with relaxed rules
- **`.claude/agents/` tracked** → skipped (project-scope's job)
- **`.gitignore` missing entries for settings.local.json / CLAUDE.local.md** → INFO
- **`~/.claude.json` present with mcpServers for this project** → validates
- **`~/.claude.json` missing** → INFO skip
- **Not a git repo at all** → INFO + validates everything as local

### 9.3 `test_cc_scope_rules.py` (≥15 tests)

- `is_git_tracked` with a tracked file → True
- `is_git_tracked` with an ignored file → False
- `is_git_tracked` with an untracked-but-existing file → False
- `is_git_tracked` on a non-git directory → False (+ no crash)
- `is_git_ignored` on an ignored file → True
- `classify_folder_scope` on a tracked folder with multiple files → "project"
- `classify_folder_scope` on an ignored folder → "local"
- `classify_folder_scope` on a folder with NO tracked files → "local"
- Rule table constants (MANAGED_ONLY_KEYS, GLOBAL_CONFIG_KEYS, PROJECT_REJECTED_KEYS) are non-empty and contain documented keys
- Secret pattern regex catches `sk-...`, `ghp_...`, `AIza...` examples

---

## 10. Implementation phases

### Phase 1 — Shared module + git classifier
1. Create `scripts/cc_scope_rules.py` with:
   - `MANAGED_ONLY_KEYS`, `GLOBAL_CONFIG_KEYS`, `PROJECT_REJECTED_KEYS` constants
   - Secret-detection regex list
   - `is_git_tracked()`, `is_git_ignored()`, `classify_folder_scope()` helpers
2. Write `tests/test_cc_scope_rules.py` (≥15 tests)
3. Run tests, lint, typecheck. Must be green before Phase 2.

### Phase 2 — Project-scope validator
1. Create `scripts/validate_project_scope.py` with `main()` entry point, argparse, orchestration
2. Implement per-element validators:
   - `_validate_settings_json_project_scope()`
   - `_validate_mcp_json()`
   - `_validate_agents_tracked()`, `_validate_skills_tracked()`, `_validate_commands_tracked()`, `_validate_rules_tracked()`
   - `_validate_claude_md()`
   - `_validate_gitignore_completeness()`
3. Create `commands/cpv-validate-project-scope.md`
4. Update `scripts/cli.py` + `pyproject.toml`
5. Write `tests/test_validate_project_scope.py` (≥30 tests)
6. Run tests, lint, typecheck

### Phase 3 — Local-scope validator
1. Create `scripts/validate_local_scope.py` mirroring Phase 2 structure
2. Implement per-element validators with relaxed rules
3. Create `commands/cpv-validate-local-scope.md`
4. Update `scripts/cli.py` + `pyproject.toml`
5. Write `tests/test_validate_local_scope.py` (≥30 tests)
6. Run tests, lint, typecheck

### Phase 4 — Plugin registration + docs
1. Update `.claude-plugin/marketplace.json` to include both new commands
2. Update `README.md` command table
3. Run full test suite (target: 1800 → 1875+ passing)
4. Run full `scripts/validate_plugin.py .` — must pass 0 errors
5. Publish via `scripts/publish.py` (auto-bump will pick minor due to new feature)

---

## 11. Open questions / future work

1. **User scope validator** (`~/.claude/`) — deferred. Different shape: no path arg, no git-tracking classification, just validate everything at `~/.claude/`.
2. **Managed scope validator** — deferred. Would read `/Library/Application Support/ClaudeCode/managed-settings.json` on macOS / `/etc/claude-code/` on Linux / `C:\Program Files\ClaudeCode\` on Windows.
3. **Merged-view validator** — showing the effective settings after all scopes merge, then validating the merged result. This is what `/status` does inside Claude Code. Possibly useful as a `cpv-validate-effective-scope` in the future.
4. **Auto-fix support** — none of the three validators would fix files today. Future enhancement could add `--fix` to relocate keys from project to local scope (or vice versa), but that's a separate TRDD.

---

## 12. Success criteria

1. Both commands appear in `cpv-list-plugins` output
2. `uv run python scripts/validate_project_scope.py <project>` runs and produces a report
3. `uv run python scripts/validate_local_scope.py <project>` runs and produces a report
4. The full test suite passes (1800 existing + ~75 new tests)
5. `scripts/publish.py` succeeds and the new version is released
6. Running `cpv-validate-project-scope` against CPV's own `.claude/` folder produces a clean pass (or discovers real issues we should fix)

---

## References

- `docs_dev/claude-code-settings-scoping-research-20260414.md` — full settings docs research
- `docs_dev/cpv-validator-pattern-anatomy-20260414.md` — existing CPV validator anatomy
- https://code.claude.com/docs/en/settings.md (canonical)
- https://code.claude.com/docs/en/mcp.md (.mcp.json details)
- https://code.claude.com/docs/en/memory.md (CLAUDE.md and CLAUDE.local.md)
- https://code.claude.com/docs/en/permissions.md (managed-only keys)

## Approval log

- 2026-08-25T17:25:22+0200 — CLOSED as complete by the CPV session (board drain;
  authority delegated by USER 2026-08-25). SHIPPED v2.15.0/.1 — cc_scope_rules.py
  + scope validators live (commands renamed cpv-batch-scope-*) (batch_ag).
