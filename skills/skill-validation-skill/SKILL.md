---
name: skill-validation-skill
description: |
  Validate skills using 190+ rules from AgentSkills OpenSpec, Nixtla, and Meta-Skill frameworks.
  Use when validating SKILL.md files or auditing skill quality. Loaded by skill-validation-agent.
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

Script-based validation of structure, frontmatter, content, and pillars. For semantic analysis use `/cpv-semantic-validation`.

## Prerequisites

- Python 3.12+ with `pyyaml` installed
- Skill directory containing a SKILL.md file

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
- **"Dir must match skill name"**: Rename directory or update `name` field

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

- [Validation Rules](references/validation-rules.md)
  > Structure Validation Rules (8 rules) · Frontmatter Validation Rules (25 rules) · Name Field Validation Rules (12 rules) · Description Quality Rules (15 rules) · Token Budget Rules (8 rules) · Required Sections Rules (9 rules) · Path Format Rules (6 rules) · Resource Reference Rules (8 rules) · Allowed-Tools Rules (10 rules) · 8+1 Pillars Rules (18 rules) · Progressive Disclosure Rules (12 rules) · Content Quality Rules (15 rules) · Agent-Specific Rules (22 rules)
- [Frontmatter Schema](references/frontmatter-schema.md)
  > Required Fields · Optional Fields (Claude Code) · Enterprise Fields · Field Validation Details · Field Whitelist Modes · Examples
- [Pillars Coverage](references/pillars-coverage.md)
  > When to Apply Pillars Validation · The 8 Core Pillars · The 9th Pillar (REPL/Workflow) · Scoring System · Coverage Thresholds · Gap Mitigation Strategies · Example Evaluation
- [Scoring System](references/scoring-system.md)
  > Multi-Scale Criterion Scoring (0-3) · Tier System (PASS / CONDITIONAL_PASS / FAIL) · Severity Levels · Category Weighting · Overall Score Calculation · Exit Codes · Interpreting Results · Two Scoring Systems

## Token Optimization

Always `--report <path>` — share path, don't read. Prefer LLM Externalizer MCP for report analysis.

## Checklist
Copy this checklist and track your progress:
- [ ] Run validate_skill_comprehensive.py --report
- [ ] Fix issues, re-run until exit 0
