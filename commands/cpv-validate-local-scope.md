---
name: cpv-validate-local-scope
description: Validate non-git-tracked (local-scope) Claude Code configuration under a given project path — settings.local.json, CLAUDE.local.md, gitignored agents/skills/commands/rules, and per-project MCP state in ~/.claude.json.
allowed-tools: Read, Bash, Glob, Grep
argument-hint: "<project_path> [--verbose] [--json] [--strict] [--report <file>]"
user-invocable: true
---

# /cpv-validate-local-scope Command

Validates the **personal, non-shared** Claude Code configuration under a
given project directory. Only elements that are NOT git-tracked are
checked — tracked elements are the concern of `/cpv-validate-project-scope`.

Claude Code defines four scopes (settings.md): **Managed**, **User**,
**Project**, **Local**. This command validates the **Local** scope:
`.claude/settings.local.json`, `CLAUDE.local.md`, any folder under
`.claude/` that is gitignored, plus the per-project MCP state Claude
Code stores in `~/.claude.json`.

The rules at local scope are **deliberately relaxed** compared to project
scope:

- Machine-specific absolute paths (`/Users/alice/...`) are allowed.
- Environment variable expansions for secrets are still preferred but
  not enforced.
- Managed-only keys and global-config-only keys are **still rejected** —
  they never work outside their intended file, regardless of scope.
- Files named `settings.local.json` and `CLAUDE.local.md` must actually
  be gitignored — if they are committed, that is a MAJOR finding.

## Usage

```
/cpv-validate-local-scope <project_path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `project_path` | Yes | Path to a project root. May or may not have `.git/`. |

## Options

| Option | Description |
|--------|-------------|
| `--verbose` / `-v` | Show INFO and PASSED results. |
| `--strict` | Treat NIT issues as blocking (exit 4). |
| `--json` | Output JSON instead of text. |
| `--report PATH` | Save the full report to a file; print only the compact summary. |

## What Gets Validated

### 1. `.claude/settings.local.json`
- **CRITICAL**: JSON parse failure, non-object root.
- **MAJOR**: file is committed to git (scope violation); managed-only
  keys (`allowedMcpServers`, `deniedMcpServers`, `allowManagedHooksOnly`,
  `strictKnownMarketplaces`, etc.); global-config-only keys (`editorMode`,
  `autoConnectIde`, `teammateMode`, …).
- **MINOR**: keys that are typically shared with the whole team
  (`extraKnownMarketplaces`, `enableAllProjectMcpServers`,
  `enabledMcpjsonServers`, `disabledMcpjsonServers`) — suggest moving
  them to `.claude/settings.json`.
- **NIT**: deprecated `includeCoAuthoredBy`, missing `$schema`.

### 2. `CLAUDE.local.md`
- **MAJOR**: file is git-tracked (must be gitignored per memory.md).

### 3. `.claude/settings.json` (when untracked)
If `settings.json` exists but is not committed, that is unusual. The
validator emits a WARNING and still runs the local-scope rules on it.

### 4. Untracked `.claude/agents/`, `.claude/skills/`, `.claude/commands/`, `.claude/rules/`

> **v2.21.0 behavior change (TRDD-f4e2d385 §3.1):** untracked elements
> are now passed to the FULL per-element validator — the same pipeline
> `cpv-validate-plugin` uses for each bundled file. The shallow
> frontmatter walk still runs on top, but the deep pipeline is the
> authoritative source. Absolute-path / secret rules remain relaxed for
> local scope (personal config is allowed machine-specific paths) —
> but structural and semantic rules now fire consistently.

### 5. Locally-enabled plugins (`settings.local.json.enabledPlugins`)
For every `"<plugin>@<marketplace>": true` entry, CPV resolves the plugin's
cache directory at `~/.claude/plugins/cache/<marketplace>/<plugin>/<highest-version>/`
and runs the core plugin-validation pipeline on it. An enabled-but-not-installed
plugin produces a MAJOR. The resolver is symlink-confined to the cache root,
so a path that escapes `~/.claude/plugins/cache/` is rejected with a WARNING.

### 6. `~/.claude.json` per-project MCP state
Reads the current user's `~/.claude.json` and reports any
`projects[<abs_path>].mcpServers` entries as INFO so you can see which
local MCP servers Claude Code has registered for this project.

### 7. `.gitignore` coverage
- **MINOR**: `.claude/settings.local.json` is not covered by any
  `.gitignore` line (accepts `.claude/`, `.claude/settings.local.json`,
  or bare `settings.local.json`).
- **MINOR**: `CLAUDE.local.md` is not covered by any `.gitignore` line
  (accepts explicit name or `*.local.md` glob).

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | No blocking issues |
| 1 | CRITICAL |
| 2 | MAJOR |
| 3 | MINOR |
| 4 | NIT (only with `--strict`) |

## Execution

Slash commands and agents ALWAYS run the validator from the locally-installed
plugin — `${CLAUDE_PLUGIN_ROOT}` is set by Claude Code to the pinned,
version-locked plugin directory (`~/.claude/plugins/cache/<marketplace>/
<plugin>/<version>/`). **Never** fetch scripts from GitHub at runtime for
in-session validation; that would pull an unpinned `main` that drifts from
the behavior the user installed.

```bash
uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/validate_local_scope.py" \
  "$PROJECT_PATH" \
  --report "${PROJECT_PATH}/docs_dev/validate_local_scope_$(date +%Y%m%d).md"
```

This works out-of-the-box from any Claude Code session — no
`remote_validation.py` indirection, no `CPV_REMOTE_VALIDATION` env-var
bypass. The v2.21.1 fix to `check_remote_execution_guard` recognizes that
`${CLAUDE_PLUGIN_ROOT}`-rooted invocations are trusted (pinned plugin
installs are already sandboxed by Claude Code's plugin system).

### Alternative invocations (not for slash-command flow)

- **From a CPV checkout** (development): `uv run python scripts/validate_local_scope.py "$PROJECT_PATH"` — useful when iterating on the validator itself.
- **From uvx, CI, or a fresh machine without CPV installed**: `uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-remote-validate local-scope "$PROJECT_PATH"` — this IS an ephemeral GitHub-sourced invocation and routes through the remote-execution guard correctly. Use only in GitHub Actions or one-shot CI, never in an interactive session where the plugin is already installed.

## Related Commands

- `/cpv-validate-project-scope` — companion command for git-tracked elements.
- `/cpv-validate-settings-marketplace` — validates the
  `extraKnownMarketplaces` block specifically.

## References

- https://code.claude.com/docs/en/settings.md (scope definitions)
- https://code.claude.com/docs/en/memory.md (CLAUDE.local.md convention)
- https://code.claude.com/docs/en/mcp.md (per-project local MCP in ~/.claude.json)
- `design/tasks/TRDD-2be75e88-...-scope-validators.md`
