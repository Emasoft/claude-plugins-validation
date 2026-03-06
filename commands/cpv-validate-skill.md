---
name: cpv-validate-skill
description: Validate skill directories in a plugin
allowed-tools: Read, Bash, Glob, Grep, Task, AskUserQuestion
argument-hint: "<skill_path_or_name> [--strict] [--openspec] [--pillars] [--verbose]"
agent: plugin-validator
user-invocable: true
---

# /cpv-validate-skill Command

Validates a skill directory using the comprehensive skill validator (190+ rules).

## Privacy Check (REQUIRED)

Before running validation, ensure private path detection is configured:

1. **Auto-detect username**: `python3 -c "import getpass; print(getpass.getuser())"`
2. **If auto-detection fails**, ask the user for their system username
3. **Pass to script**: `CLAUDE_PRIVATE_USERNAMES="username" uv run python scripts/...`

## Usage

```
/cpv-validate-skill <skill_path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `skill_path_or_name` | Yes | Path to the skill directory OR just the skill name for auto-discovery |

### Auto-Discovery

If you provide just a name (e.g., `my-skill`), the agent will search for it in:
1. Skills folder (`./skills/my-skill/`)
2. Current directory if it contains SKILL.md
3. OUTPUT_SKILLS plugins (`./OUTPUT_SKILLS/**/skills/my-skill/`)

If multiple matches are found, you'll be asked to choose.

### Typo Tolerance

Names are normalized before searching:
- Converted to lowercase: `My-Skill` → `my-skill`
- Underscores become hyphens: `my_skill` → `my-skill`

If no exact match is found, fuzzy matching is used (e.g., `valdiate-skill` → `validate-skill`).
**Fuzzy matches always require your confirmation before proceeding.**

## Options

| Option | Description |
|--------|-------------|
| `--strict` | Enable Nixtla strict mode (required sections, description quality) |
| `--openspec` | Enable AgentSkills OpenSpec strict mode (6-field whitelist only) |
| `--pillars` | Enable 8+1 Pillars validation (for lang-*/convert-* skills) |
| `--verbose` | Show all checks including passed |
| `--json` | Output results as JSON |

## Validation Categories

The comprehensive validator checks:

### Frontmatter (28 rules)
- Name format, length, Unicode NFKC normalization
- Description quality, third-person voice, "use when" phrases
- allowed-tools, context, agent, model validation
- metadata, compatibility, license fields

### Token Budget (4 rules)
- SKILL.md line count (WARNING at 500, ERROR at 800+)
- Body word count thresholds
- Frontmatter size limits

### Structure (8 rules)
- SKILL.md existence and casing
- References one level deep
- TOC in files >100 lines
- Script shebang and executable checks

### Content Quality (11 rules)
- MCP tool reference format
- Time-sensitive information detection
- Checklist and example patterns
- Workflow steps and feedback loops
- Template patterns (strict vs flexible)

### String Substitutions (4 rules)
- $ARGUMENTS, $ARGUMENTS[N], $N validation
- ${CLAUDE_SESSION_ID} usage

### OpenSpec Strict Mode (2 rules)
- 6-field whitelist enforcement
- Claude Code-specific field rejection

### 8+1 Pillars (9 rules)
- Module, Error, Concurrency, Metaprogramming coverage
- Zero/Default, Serialization, Build, Testing pillars
- 9th pillar for REPL languages

## Examples

### Basic Validation

```
/cpv-validate-skill ./skills/my-skill/
```

### Strict Mode Validation

```
/cpv-validate-skill ./skills/my-skill/ --strict
```

### Full Validation with All Modes

```
/cpv-validate-skill ./skills/lang-rust-dev/ --strict --openspec --pillars --verbose
```

### JSON Output for CI/CD

```
/cpv-validate-skill ./skills/my-skill/ --json
```

## Output

Returns:
- **Grade**: A-F letter grade with percentage score
- **Summary**: Count of issues by severity (CRITICAL, MAJOR, MINOR, NIT, WARNING)
- **Pillars Coverage**: 8+1 Pillars scores (if `--pillars`)
- **Details**: Categorized list of issues and passed checks

## Grade System

| Grade | Score | Status |
|-------|-------|--------|
| A | 90-100 | Production ready |
| B | 80-89 | Good, minor improvements |
| C | 70-79 | Acceptable |
| D | 60-69 | Reject, rework needed |
| F | <60 | Broken |

## Output & Exit Codes

Uses standard CPV severity levels and exit codes. With `--report`, saves full output to file and prints only a compact summary. See `/cpv-validate-plugin` for details.

## Execution

```bash
uv run python scripts/validate_skill_comprehensive.py "$SKILL_PATH" $OPTIONS --report docs_dev/validate_skill_$(date +%Y%m%d).md
```

Where `$SKILL_PATH` is the provided path and `$OPTIONS` are the flags passed.

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation (includes skills)
- `/cpv-validate-hooks` - Hook configuration validation
- `/cpv-validate-agents` - Agent definition validation
- `/cpv-validate-mcp` - MCP server configuration validation
