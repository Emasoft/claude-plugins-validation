---
name: cpv-validate-agents
description: |
  Validate Claude Code agent definition files (.md). Checks YAML frontmatter, required
  fields, name format, description quality, tool lists, and model specifications. Use
  when creating or auditing agent definitions.
allowed-tools: Read, Bash, Glob, Grep
argument-hint: "<agent_path> [--verbose] [--json]"
user-invocable: true
---

# /cpv-validate-agents Command

Validates Claude Code agent definition files (markdown with YAML frontmatter).

## Usage

```
/cpv-validate-agents <agent_path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `agent_path` | Yes | Path to agent .md file or directory containing agents |

## Options

| Option | Description |
|--------|-------------|
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

## Output Example

```
============================================================
Agent Validation: ./agents/my-agent.md
============================================================

Summary:
  CRITICAL: 0
  MAJOR:    0
  MINOR:    2

Details:
  [MINOR] Description lacks "use when" phrase
  [MINOR] No example blocks found in agent body

------------------------------------------------------------
✓ Agent validation passed
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | CRITICAL issues (agent will not work) |
| 2 | MAJOR issues (significant problems) |
| 3 | MINOR issues (may affect UX) |

## Execution

```bash
uv run python scripts/validate_agent.py "$AGENT_PATH" $OPTIONS
```

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-skill` - Skill validation
- `/cpv-validate-hooks` - Hook validation
- `/cpv-validate-mcp` - MCP server validation
