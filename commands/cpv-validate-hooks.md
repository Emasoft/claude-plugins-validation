---
name: cpv-validate-hooks
description: Validate hooks configuration in a plugin
allowed-tools: Read, Bash, Glob, Grep, Task, AskUserQuestion
argument-hint: "<hooks_path_or_plugin_name> [--plugin-root <path>] [--verbose] [--json]"
agent: plugin-validator
user-invocable: true
---

# /cpv-validate-hooks Command

Validates a Claude Code hooks.json configuration file.

## Privacy Check (REQUIRED)

Before running validation, ensure private path detection is configured:

1. **Auto-detect username**: `python3 -c "import getpass; print(getpass.getuser())"`
2. **If auto-detection fails**, ask the user for their system username
3. **Pass to script**: `CLAUDE_PRIVATE_USERNAMES="username" uv run python scripts/...`

## Usage

```
/cpv-validate-hooks <hooks_json_path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `hooks_path_or_plugin_name` | Yes | Path to hooks.json file OR plugin name for auto-discovery |

### Auto-Discovery

If you provide just a name (e.g., `my-plugin`), the agent will search for hooks in:
1. Plugin's hooks folder (`./my-plugin/hooks/hooks.json`)
2. Current hooks folder (`./hooks/hooks.json`)
3. Project settings (`./.claude/settings.json`)
4. OUTPUT_SKILLS plugins (`./OUTPUT_SKILLS/my-plugin/hooks/hooks.json`)

If multiple matches are found, you'll be asked to choose.

### Typo Tolerance

Names are normalized before searching:
- Converted to lowercase: `My-Plugin` → `my-plugin`
- Underscores become hyphens: `my_plugin` → `my-plugin`

If no exact match is found, fuzzy matching is used (e.g., `valdiate-hooks` → `validate-hooks`).
**Fuzzy matches always require your confirmation before proceeding.**

## Options

| Option | Description |
|--------|-------------|
| `--plugin-root <path>` | Plugin root directory for resolving script paths |
| `--strict` | Treat NIT issues as blocking (exit 4) |
| `--verbose` | Show all checks including passed |
| `--json` | Output results as JSON |

## What Gets Validated

### 1. JSON Structure
- Valid JSON syntax
- Required `hooks` object at root
- Optional `description` field

### 2. Event Names
Valid hook events (19 total):
- `PreToolUse` - Before tool execution (supports matchers)
- `PostToolUse` - After successful tool execution (supports matchers)
- `PostToolUseFailure` - After tool failure (supports matchers)
- `PermissionRequest` - When permission dialog shown (supports matchers)
- `UserPromptSubmit` - When user submits prompt (no matcher)
- `Notification` - When notifications sent (supports matchers)
- `Stop` - When agent attempts to stop (no matcher)
- `SubagentStop` - When subagent stops (no matcher)
- `SubagentStart` - When subagent starts (supports matchers)
- `SessionStart` - At session start (supports matchers)
- `SessionEnd` - At session end (no matcher)
- `PreCompact` - Before conversation compaction (supports matchers)
- `Setup` - Plugin setup (supports matchers)
- `ConfigChange` - When configuration changes (supports matchers)
- `TeammateIdle` - When a teammate session goes idle (no matcher)
- `TaskCompleted` - When a task is completed (no matcher)
- `WorktreeCreate` - When a git worktree is created (no matcher)
- `WorktreeRemove` - When a git worktree is removed (no matcher)
- `InstructionsLoaded` - When CLAUDE.md or rules files are loaded (no matcher)

### 3. Matcher Patterns
- Validates regex syntax
- Checks for common tool names
- Warns about matchers on events that don't support them

### 4. Hook Types
- `command` hooks: Requires `command` field, optional `timeout`
- `prompt` hooks: Requires `prompt` field, optional `timeout`

### 5. Script Validation
For command hooks referencing scripts:
- Checks script file exists
- Verifies executable permissions
- Lints shell scripts (shellcheck)
- Lints Python scripts (ruff, mypy)
- Lints JavaScript/TypeScript (eslint)

### 6. Environment Variables
- `${CLAUDE_PLUGIN_ROOT}` - Plugin root (plugins only)
- `${CLAUDE_PROJECT_DIR}` - Project root
- `${CLAUDE_ENV_FILE}` - Environment file (SessionStart/Setup only)
- `${CLAUDE_CODE_REMOTE}` - Remote flag

## Examples

### Validate Plugin Hooks

```
/cpv-validate-hooks ./my-plugin/hooks/hooks.json --plugin-root ./my-plugin/
```

### Validate Project Hooks

```
/cpv-validate-hooks ./.claude/settings.json --verbose
```

### JSON Output

```
/cpv-validate-hooks ./hooks/hooks.json --json
```

## Output & Exit Codes

Uses standard CPV severity levels and exit codes. With `--report`, saves full output to file and prints only a compact summary. See `/cpv-validate-plugin` for details.

## Execution

```bash
uv run python scripts/validate_hook.py "$HOOKS_PATH" $OPTIONS --report docs_dev/validate_hook_$(date +%Y%m%d).md
```

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-skill` - Skill validation
- `/cpv-validate-agents` - Agent validation
- `/cpv-validate-mcp` - MCP server validation
