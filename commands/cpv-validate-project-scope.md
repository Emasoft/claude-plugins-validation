---
name: cpv-validate-project-scope
description: Validate git-tracked (project-scope) Claude Code configuration under a given project path — settings.json, .mcp.json, agents, skills, commands, rules, and CLAUDE.md that are shared with the team.
allowed-tools: Read, Bash, Glob, Grep
argument-hint: "<project_path> [--verbose] [--json] [--strict] [--report <file>]"
user-invocable: true
---

# /cpv-validate-project-scope Command

Validates the **shared team** Claude Code configuration under a given project
directory. Only elements that are **git-tracked** are checked — non-tracked
files are the concern of `/cpv-validate-local-scope`.

Claude Code defines four scopes (settings.md): **Managed**, **User**,
**Project**, **Local**. This command validates the **Project** scope: the
portion of `<project_path>/.claude/` plus `<project_path>/.mcp.json` that is
committed to git and shared with collaborators.

## Usage

```
/cpv-validate-project-scope <project_path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `project_path` | Yes | Path to a project root containing `.git/` and (typically) a `.claude/` folder. |

## Options

| Option | Description |
|--------|-------------|
| `--verbose` / `-v` | Show INFO and PASSED results. |
| `--strict` | Treat NIT issues as blocking (exit 4). |
| `--json` | Output JSON instead of text. |
| `--report PATH` | Save the full report to a file; print only the compact summary. |

## What Gets Validated

Every element below is checked **only if it is git-tracked** under the given
project root. If the project has no `.git/` directory, validation is skipped
with a WARNING.

### 1. `.claude/settings.json` (scope-specific rules)
- **CRITICAL**: project-rejected keys that Claude Code silently drops —
  `autoMemoryDirectory`, `autoMode`, `useAutoModeDuringPlan`,
  `permissions.skipDangerousModePermissionPrompt`.
- **MAJOR**: managed-only keys (`allowedMcpServers`, `deniedMcpServers`,
  `allowManagedHooksOnly`, `strictKnownMarketplaces`, `blockedMarketplaces`,
  etc.); global-config-only keys (`editorMode`, `autoConnectIde`,
  `teammateMode`, …).
- **MINOR**: literal secrets inside `env`, absolute user paths in
  `statusLine.command`, `fileSuggestion.command`, `apiKeyHelper`,
  `awsAuthRefresh`, `awsCredentialExport`, `otelHeadersHelper`,
  `hooks.<event>.*.command`, `permissions.additionalDirectories`,
  `sandbox.filesystem.*`, `claudeMdExcludes`.
- **NIT**: missing `$schema` declaration.

### 2. `.mcp.json` at the project root
- **CRITICAL**: JSON parse failure.
- **MAJOR**: root not an object, missing `mcpServers`.
- **MINOR**: literal secrets in `mcpServers.*.env` values (use `${VAR}`
  expansion per mcp.md), absolute home paths in `command` / `args`.

> **v2.21.0 behavior change (TRDD-f4e2d385 §3.1):** every tracked element
> under `.claude/` is now passed to the FULL per-element validator
> (the same pipeline `cpv-validate-plugin` uses for each bundled file).
> Expect more findings than in v2.20.x — this is the intended semantics,
> not a regression. The old shallow "frontmatter-parseable + name present"
> check still runs on top, but the deep pipeline is the authoritative
> source.

### 3. `.claude/agents/*.md`
Shallow: frontmatter YAML parseable, `name` and `description` present,
no absolute home paths in `system-prompt`/`initialPrompt` or body.
Deep (v2.21.0+): invoked via `validate_agent` — checks `tools` allowlist,
`model`, deprecated fields, `<example>` blocks, description triggers,
plugin-shipped restrictions, etc.

### 4. `.claude/skills/<name>/SKILL.md`
Shallow: frontmatter + absolute-path checks.
Deep (v2.21.0+): invoked via `validate_skill_comprehensive` — the 190-rule
pipeline covering Overview / Prerequisites / Output / Error Handling /
Examples / Resources sections, `argument-hint` format, `allowed-tools`
syntax, etc.

### 5. `.claude/commands/*.md` (legacy commands directory)
Shallow: frontmatter + absolute-path checks.
Deep (v2.21.0+): invoked via `validate_command`.

### 6. `.claude/rules/*.md`
Shallow: body scanned for absolute home paths.
Deep (v2.21.0+): invoked via `validate_rules` — catches stale references,
obsolete TODO/FIXME markers, large inline command blocks, etc.

### 7. `CLAUDE.md` or `.claude/CLAUDE.md`
Body scanned for absolute home paths and literal credentials.

### 7b. `.claude/loop.md` (v2.22.0, scheduled-tasks.md)
If the file is git-tracked, it is validated under project scope.
`.claude/loop.md` replaces the built-in `/loop` maintenance prompt
(project-level takes precedence over `~/.claude/loop.md`).
- **CRITICAL**: file is not UTF-8 decodable.
- **MAJOR**: file exceeds 25,000 bytes (scheduled-tasks.md truncates above
  this cap); or the path is a symlink escape outside the repo root.
- **INFO** otherwise: confirms the file is a maintenance instruction, not
  an inadvertent command. Untracked `loop.md` is the responsibility of
  `/cpv-validate-local-scope`.

### 8. `.gitignore` hygiene
- **INFO**: `.claude/settings.local.json` not pinned (Claude Code auto-adds
  it on first creation, pinning is recommended).
- **INFO**: `CLAUDE.local.md` not pinned.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | No blocking issues |
| 1 | CRITICAL |
| 2 | MAJOR |
| 3 | MINOR |
| 4 | NIT (only with `--strict`) |

WARNING and INFO never block. They are always printed.

## Execution

Slash commands ALWAYS run the validator from the installed plugin —
`${CLAUDE_PLUGIN_ROOT}` is set by Claude Code. **Never** fetch scripts
from GitHub at runtime for in-session validation.

> **Report location (mandatory):** `$MAIN_ROOT/reports/validate_project_scope/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md` at the **main-repo root** of the target project (never a linked worktree). Both `reports/` and `reports_dev/` are gitignored.

```bash
# Resolve the main-repo root of the project being validated (worktree-safe):
if git -C "$PROJECT_PATH" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  MAIN_ROOT="$(git -C "$PROJECT_PATH" worktree list | head -n1 | awk '{print $1}')"
else
  MAIN_ROOT="$PROJECT_PATH"
fi
REPORT_DIR="$MAIN_ROOT/reports/validate_project_scope"
mkdir -p "$REPORT_DIR"
REPORT_FILE="$REPORT_DIR/$(date +%Y%m%d_%H%M%S%z)-$(basename "$PROJECT_PATH").md"

uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/validate_project_scope.py" \
  "$PROJECT_PATH" \
  --report "$REPORT_FILE"
```

### Alternative invocations (not for slash-command flow)

- **From a CPV checkout** (development): `uv run python scripts/validate_project_scope.py "$PROJECT_PATH"`.
- **From uvx, CI, or a fresh machine without CPV installed**: `uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-remote-validate project-scope "$PROJECT_PATH"` — GitHub-sourced ephemeral invocation, use only in CI.

## Related Commands

- `/cpv-validate-local-scope` — companion command for non-git-tracked elements.
- `/cpv-validate-plugin` — validates a Claude Code **plugin package**
  (different scope: plugin.json + bundled files).
- `/cpv-validate-settings-marketplace` — validates the
  `extraKnownMarketplaces` block specifically.

## References

- https://code.claude.com/docs/en/settings.md
- https://code.claude.com/docs/en/mcp.md
- https://code.claude.com/docs/en/memory.md
- `design/tasks/TRDD-2be75e88-...-scope-validators.md`
