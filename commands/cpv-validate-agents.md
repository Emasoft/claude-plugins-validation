---
name: cpv-validate-agents
description: Validate agent .md files in a plugin
allowed-tools: Read, Bash, Glob, Grep, AskUserQuestion
argument-hint: "<agent_path_or_name> [--verbose] [--json]"
agent: plugin-validator
user-invocable: true
---

# /cpv-validate-agents Command

Validates Claude Code agent definition files (markdown with YAML frontmatter).

## Privacy Check (REQUIRED)

Before running validation, ensure private path detection is configured:

1. **Auto-detect username**: `uv run python -c "import getpass; print(getpass.getuser())"`
2. **If auto-detection fails**, ask the user for their system username
3. **Pass to script**: `CLAUDE_PRIVATE_USERNAMES="username" uv run python scripts/...`

## Usage

```
/cpv-validate-agents <agent_path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `agent_path_or_name` | Yes | Path to agent .md file, directory, OR just the agent name for auto-discovery |

### Auto-Discovery

If you provide just a name (e.g., `my-agent`), the agent will search for it in:
1. Agents folder (`./agents/my-agent.md`)
2. Current directory (`./my-agent.md` if has agent frontmatter)
3. OUTPUT_SKILLS plugins (`./OUTPUT_SKILLS/**/agents/my-agent.md`)

If multiple matches are found, you'll be asked to choose.

### Typo Tolerance

Names are normalized before searching:
- Converted to lowercase: `My-Agent` → `my-agent`
- Underscores become hyphens: `my_agent` → `my-agent`

If no exact match is found, fuzzy matching is used (e.g., `plugn-validator` → `plugin-validator`).
**Fuzzy matches always require your confirmation before proceeding.**

## Options

| Option | Description |
|--------|-------------|
| `--strict` | Treat NIT issues as blocking (exit 4) |
| `--verbose` | Show all checks including passed |
| `--json` | Output results as JSON |

## What Gets Validated

### 1. YAML Frontmatter
- Frontmatter exists (starts with `---`)
- Valid YAML syntax
- Closing `---` delimiter present

### 2. Known Frontmatter Fields
Valid agent frontmatter fields:
- `name` - Agent identifier (kebab-case)
- `description` - What the agent does
- `tools` - Allowed tools list
- `model` - Model specification (sonnet, opus, haiku)
- `color` - UI color for agent
- `capabilities` - Agent capabilities
- `context` - Context mode (`fork`)
- `agent` - Specialized agent type
- `user-invocable` - Whether users can invoke directly
- `system-prompt` - Custom system prompt
- `skills` - List of skills the agent can use

### 3. Name Validation
- Maximum 64 characters
- Must be lowercase
- Must be kebab-case (letters, numbers, hyphens)
- No consecutive hyphens
- No leading/trailing hyphens

### 4. Description Validation
- Maximum 1024 characters
- Non-empty recommended
- Third-person voice recommended
- "Use when" phrases encouraged

### 5. Tools Validation
- Space-delimited format
- Validates against known tool names
- Warns about unknown tools

### 6. Model Validation
Valid model values:
- `sonnet` - Claude Sonnet (recommended)
- `opus` - Claude Opus
- `haiku` - Claude Haiku (note: less reliable for complex tasks)
- `inherit` - Inherit from parent

### 7. Context Field
- Valid value: `fork` only

### 8. Agent Types
Valid specialized agent types:
- `api-coordinator`
- `test-engineer`
- `deploy-agent`
- `debug-specialist`
- `code-reviewer`

### 9. Content Quality
- Checks for placeholder text (TODO, FIXME, PLACEHOLDER)
- Validates system prompt completeness
- Checks for example blocks

## Examples

### Validate Single Agent

```
/cpv-validate-agents ./agents/my-agent.md
```

### Validate All Agents in Directory

```
/cpv-validate-agents ./agents/
```

### Verbose Output

```
/cpv-validate-agents ./agents/my-agent.md --verbose
```

### JSON Output

```
/cpv-validate-agents ./agents/ --json
```

## Output & Exit Codes

Uses standard CPV severity levels and exit codes. With `--report`, saves full output to file and prints only a compact summary. See `/cpv-validate-plugin` for details.

## Execution

```bash
uv run python scripts/validate_agent.py "$AGENT_PATH" $OPTIONS --report docs_dev/validate_agent_$(date +%Y%m%d).md
```

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-skill` - Skill validation
- `/cpv-validate-hooks` - Hook validation
- `/cpv-validate-mcp` - MCP server validation
- `/cpv-fix-validation` — Fix issues from a validation report
- `/cpv-semantic-validation` — Deep semantic analysis (uses opus)
