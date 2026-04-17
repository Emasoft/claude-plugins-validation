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
If any of these folders are gitignored, the validator walks them and
applies lightweight frontmatter checks (YAML parseable, `name` present).
Absolute home paths and secrets are **not** flagged here — only at
project scope.

### 5. `~/.claude.json` per-project MCP state
Reads the current user's `~/.claude.json` and reports any
`projects[<abs_path>].mcpServers` entries as INFO so you can see which
local MCP servers Claude Code has registered for this project.

### 6. `.gitignore` coverage
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

### From within a CPV checkout (development)

```bash
uv run python scripts/validate_local_scope.py "$PROJECT_PATH" \
  --report docs_dev/validate_local_scope_$(date +%Y%m%d).md
```

### From the plugin cache / installed CLI / uvx (remote execution)

When CPV runs OUTSIDE its own checkout (e.g. from the Claude Code plugin
cache or via `uvx`), direct invocation is blocked by the remote-execution
guard — this prevents the target project's local config files from being
mis-applied to the validator's own environment. Use the launcher instead:

```bash
# Via the plugin cache (most common — how Claude Code runs slash commands):
uv run python "${CPV_ROOT}/scripts/remote_validation.py" \
  local-scope "$PROJECT_PATH" \
  -o "${PROJECT_PATH}/docs_dev/validate_local_scope_$(date +%Y%m%d).md"

# Via uvx (one-shot, no checkout needed):
uvx --from git+https://github.com/Emasoft/claude-plugins-validation \
  --with pyyaml \
  cpv-remote-validate local-scope "$PROJECT_PATH" \
  -o docs_dev/validate_local_scope_$(date +%Y%m%d).md
```

The launcher accepts several equivalent aliases (pick whichever you
remember most easily):

- `local-scope` (short — matches the help-menu listing)
- `validate_local_scope` (full script name)
- `cpv-validate-local-scope` (installed CLI entry-point name)

All three resolve to the same script and produce identical reports.

## Related Commands

- `/cpv-validate-project-scope` — companion command for git-tracked elements.
- `/cpv-validate-settings-marketplace` — validates the
  `extraKnownMarketplaces` block specifically.

## References

- https://code.claude.com/docs/en/settings.md (scope definitions)
- https://code.claude.com/docs/en/memory.md (CLAUDE.local.md convention)
- https://code.claude.com/docs/en/mcp.md (per-project local MCP in ~/.claude.json)
- `design/tasks/TRDD-2be75e88-...-scope-validators.md`
