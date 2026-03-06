---
name: skill-validation-skill
description: |
  Validate skills using 190+ rules from AgentSkills OpenSpec, Nixtla, and Meta-Skill frameworks.
  Use when validating SKILL.md files or auditing skill quality. Trigger with /cpv-validate-skill.
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep
user-invocable: false
---

# Skill Validation Skill

Validates skill directories using 190+ validation rules from:
- **AgentSkills OpenSpec** — 44 rules
- **Nixtla Quality Standards** — 52 rules
- **Meta-Skill Validation** — 47 rules
- **Component Validators** — 25 rules

## Overview

Script-based validation of skill structure, frontmatter, content quality, and pillar coverage. For deep semantic analysis (description triggering, example quality), use `/cpv-semantic-validation` instead.

## Prerequisites

- Python 3.12+ with `pyyaml` installed
- Skill directory containing `SKILL.md`

## Instructions

1. Navigate to the claude-plugins-validation directory
2. Run basic validation:
   ```bash
   uv run python scripts/validate_skill_comprehensive.py path/to/skill/ --report docs_dev/validate_skill_YYYYMMDD.md
   ```
3. Optionally add mode flags: `--strict` (Nixtla), `--openspec` (AgentSkills whitelist), `--pillars` (8+1 for lang-*/convert-*)
4. Review the compact summary output (full report saved to file via `--report`)
5. Fix issues using `/cpv-fix-validation <report_path>`
6. Re-run validation until exit code 0

## Output

- **Syntactic Score**: 0-100 numeric with tier (PASS / CONDITIONAL_PASS / FAIL)
- **Exit Code**: 0 (pass), 1 (CRITICAL), 2 (MAJOR), 3 (MINOR), 4 (NIT)
- **Report File**: Full output saved to `docs_dev/validate_skill_YYYYMMDD.md`

> For **Semantic Quality Grading** (A-F letter grades), use `/cpv-semantic-validation` instead.

**ALWAYS use `--report`** — never let verbose output consume context.

## Error Handling

- **"SKILL.md not found"**: Ensure path points to a skill directory, not a file
- **"Malformed YAML"**: Fix frontmatter syntax (check `---` delimiters, quotes, colons)
- **"Directory name must match skill name"**: Rename directory or update `name` field

## Examples

### Example 1: Basic Validation

```bash
uv run python scripts/validate_skill_comprehensive.py ./skills/my-skill/ --report docs_dev/validate_skill_YYYYMMDD.md
```

### Example 2: Full Validation with Pillars

```bash
uv run python scripts/validate_skill_comprehensive.py ./skills/lang-rust-dev/ --strict --pillars --verbose --report docs_dev/validate_skill_YYYYMMDD.md
```

## Resources

- [Validation Rules](references/validation-rules.md) — Complete 190+ rules reference
- [Frontmatter Schema](references/frontmatter-schema.md) — Field requirements
- [Pillars Coverage](references/pillars-coverage.md) — 8+1 Pillars validation guide
- [Scoring System](references/scoring-system.md) — Grading and scoring details

## Related

- `/cpv-validate-skill` — Invoke this validation
- `/cpv-fix-validation` — Fix issues from a report
- `/cpv-semantic-validation` — Deep semantic analysis (uses opus)
