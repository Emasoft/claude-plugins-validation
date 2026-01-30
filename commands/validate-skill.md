---
name: validate-skill
description: |
  Validate skill directories using 168+ validation rules from AgentSkills OpenSpec,
  Nixtla Quality Standards, and Meta-Skill frameworks. Use when checking skill quality,
  auditing SKILL.md files, or preparing skills for deployment. Returns letter grade (A-F)
  and detailed issue report.
allowed-tools: Read, Bash, Glob, Grep
argument-hint: "<skill_path> [--strict] [--openspec] [--pillars] [--verbose]"
user-invocable: true
---

# /validate-skill Command

Validates a skill directory using the comprehensive skill validator.

## Usage

```
/validate-skill <skill_path> [options]
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `skill_path` | Yes | Path to the skill directory to validate |

## Options

| Option | Description |
|--------|-------------|
| `--strict` | Enable Nixtla strict mode (required sections, description quality) |
| `--openspec` | Enable AgentSkills OpenSpec strict mode (field whitelist) |
| `--pillars` | Enable 8+1 Pillars validation (for lang-*/convert-* skills) |
| `--verbose` | Show all checks including passed |
| `--json` | Output results as JSON |

## Examples

### Basic Validation

```
/validate-skill ./skills/my-skill/
```

### Strict Mode Validation

```
/validate-skill ./skills/my-skill/ --strict
```

### Full Validation with All Modes

```
/validate-skill ./skills/lang-rust-dev/ --strict --openspec --pillars --verbose
```

### JSON Output for CI/CD

```
/validate-skill ./skills/my-skill/ --json
```

## Output

Returns:
- **Grade**: A-F letter grade with percentage score
- **Summary**: Count of issues by severity
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

## Related

- `skill-validation-skill` - Full validation skill with references
- `skill-validation-agent` - Agent for batch skill audits
- `/validate-plugin` - Plugin-level validation command
