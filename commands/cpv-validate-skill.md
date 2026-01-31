---
name: cpv-validate-skill
description: |
  Validate skill directories using 84+ validation rules from AgentSkills OpenSpec,
  Nixtla Quality Standards, and Meta-Skill frameworks. Use when checking skill quality,
  auditing SKILL.md files, or preparing skills for deployment. Returns letter grade (A-F)
  and detailed issue report.
allowed-tools: Read, Bash, Glob, Grep
argument-hint: "<skill_path> [--strict] [--openspec] [--pillars] [--verbose]"
user-invocable: true
---

# /cpv-validate-skill Command

Validates a skill directory using the comprehensive skill validator (84+ rules).

## Usage

```
/cpv-validate-skill <skill_path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `skill_path` | Yes | Path to the skill directory to validate |

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
- SKILL.md line count (warning at 500, error at 800+)
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
- **Summary**: Count of issues by severity (CRITICAL, MAJOR, MINOR)
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

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Grade A/B (pass) |
| 1 | CRITICAL issues (Grade F) |
| 2 | MAJOR issues (Grade D) |
| 3 | MINOR issues (Grade C) |

## Execution

```bash
uv run python scripts/validate_skill_comprehensive.py "$SKILL_PATH" $OPTIONS
```

Where `$SKILL_PATH` is the provided path and `$OPTIONS` are the flags passed.

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation (includes skills)
- `/cpv-validate-hooks` - Hook configuration validation
- `/cpv-validate-agents` - Agent definition validation
- `/cpv-validate-mcp` - MCP server configuration validation
