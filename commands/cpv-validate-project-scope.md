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

### 3. `.claude/agents/*.md`
Frontmatter YAML parseable, `name` and `description` present, no absolute
home paths in `system-prompt`/`initialPrompt` or body.

### 4. `.claude/skills/<name>/SKILL.md`
Same lightweight frontmatter + absolute-path checks.

### 5. `.claude/commands/*.md` (legacy commands directory)
Same lightweight frontmatter + absolute-path checks.

### 6. `.claude/rules/*.md`
Body scanned for absolute home paths.

### 7. `CLAUDE.md` or `.claude/CLAUDE.md`
Body scanned for absolute home paths and literal credentials.

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

```bash
uv run python scripts/validate_project_scope.py "$PROJECT_PATH" \
  --report docs_dev/validate_project_scope_$(date +%Y%m%d).md
```

Or via the installed entry point:

```bash
uvx --from git+https://github.com/Emasoft/claude-plugins-validation \
  cpv-validate-project-scope "$PROJECT_PATH"
```

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
