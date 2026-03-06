---
name: skill-validation-skill
description: |
  Validate skills using 190+ rules from AgentSkills OpenSpec, Nixtla, and Meta-Skill frameworks.
  Use when validating SKILL.md files or auditing skill quality. Trigger with /cpv-validate-skill.
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep
user-invocable: false
---

# Skill Validation Skill

Validates skill directories using a comprehensive set of 190+ validation rules extracted from:
- **AgentSkills OpenSpec** (skills-ref library) - 44 rules
- **Nixtla Quality Standards** (strict mode) - 52 rules
- **Meta-Skill Validation** (8+1 Pillars, checklists) - 47 rules
- **Component Validators** (multi-scale scoring, grading) - 25 rules

## Quick Navigation

### [Validation Rules](references/validation-rules.md)
Complete list of 190+ validation rules
**Contents:**
- 1. Structure Validation Rules (8 rules)
- 2. Frontmatter Validation Rules (25 rules)
- 3. Name Field Validation Rules (12 rules)
- 4. Description Quality Rules (15 rules)
- 5. Token Budget Rules (8 rules)
- 6. Required Sections Rules (9 rules)
- 7. Path Format Rules (6 rules)
- 8. Resource Reference Rules (8 rules)
- 9. Allowed-Tools Rules (10 rules)
- 10. 8+1 Pillars Rules (18 rules)
- 11. Progressive Disclosure Rules (12 rules)
- 12. Content Quality Rules (15 rules)
- 13. Agent-Specific Rules (22 rules)

#### [Frontmatter Schema](references/frontmatter-schema.md)
Frontmatter field requirements and validation
**Contents:**
- 1. Required Fields
- 2. Optional Fields (Claude Code)
- 3. Enterprise Fields
- 4. Field Validation Details
- 5. Field Whitelist Modes
- 6. Examples

#### [Pillars Coverage](references/pillars-coverage.md)
8+1 Pillars validation for language skills
**Contents:**
- 1. When to Apply Pillars Validation
- 2. The 8 Core Pillars
- 3. The 9th Pillar (REPL/Workflow)
- 4. Scoring System
- 5. Coverage Thresholds
- 6. Gap Mitigation Strategies
- 7. Example Evaluation

#### [Scoring System](references/scoring-system.md)
Multi-scale scoring (0-3) and letter grading (A-F)
**Contents:**
- 1. Multi-Scale Criterion Scoring (0-3)
- 2. Letter Grade System (A-F)
- 3. Severity Levels
- 4. Category Weighting
- 5. Overall Score Calculation
- 6. Exit Codes
- 7. Interpreting Results

## Overview

This skill provides comprehensive validation for Claude Code skills according to multiple quality standards:

1. **Basic Validation** - Structure, frontmatter, SKILL.md existence
2. **Quality Validation** - Description quality, token budget, path formats
3. **Strict Mode** - Nixtla required sections, voice checks
4. **OpenSpec Mode** - AgentSkills strict field whitelist
5. **Pillars Mode** - 8+1 Pillars coverage for lang-*/convert-* skills

## Prerequisites

- Python 3.12+ with `pyyaml` installed
- The skill to validate must be a directory containing `SKILL.md`
- For Pillars validation, skill name must start with `lang-` or `convert-`

## Instructions

### 1. Run Basic Validation

```bash
uv run python scripts/validate_skill_comprehensive.py path/to/skill/
```

**Output**: Grade (A-F), issue counts, detailed results by category.

### 2. Run Verbose Validation (Show All Checks)

```bash
uv run python scripts/validate_skill_comprehensive.py path/to/skill/ --verbose
```

### 3. Run Strict Mode (Nixtla Quality Standards)

```bash
uv run python scripts/validate_skill_comprehensive.py path/to/skill/ --strict
```

**Additional checks**:
- Required sections: Overview, Prerequisites, Instructions, Output, Error Handling, Examples, Resources
- Description must include "Use when..." phrase
- Description must NOT use first/second person
- Instructions must have numbered step-by-step list

### 4. Run OpenSpec Mode (AgentSkills Strict Whitelist)

```bash
uv run python scripts/validate_skill_comprehensive.py path/to/skill/ --openspec
```

**Additional checks**:
- Only 6 fields allowed: name, description, license, allowed-tools, metadata, compatibility
- Name MUST match directory name
- Extra fields cause MAJOR errors (not warnings)

### 5. Run Pillars Validation (Language Skills)

```bash
uv run python scripts/validate_skill_comprehensive.py path/to/skill/ --pillars
```

**Validates 8+1 Pillars coverage**:
1. Module - import/export, namespacing
2. Error - error handling, Result types
3. Concurrency - async, threads, channels
4. Metaprogramming - macros, decorators
5. Zero/Default - null handling, Option types
6. Serialization - JSON, encoding/decoding
7. Build - package management
8. Testing - test frameworks, assertions
9. Dev Workflow/REPL (for REPL-centric languages)

### 6. Combine All Modes

```bash
uv run python scripts/validate_skill_comprehensive.py path/to/skill/ --strict --openspec --pillars --verbose
```

### 7. JSON Output (For CI/CD)

```bash
uv run python scripts/validate_skill_comprehensive.py path/to/skill/ --json
```

## Output

### Grade System (A-F)

| Grade | Score Range | Meaning | Action |
|-------|-------------|---------|--------|
| **A** | 90-100 | Production ready | Deploy |
| **B** | 80-89 | Good, minor improvements | Address minor issues |
| **C** | 70-79 | Acceptable | Plan improvements |
| **D** | 60-69 | Reject, regenerate | Major rework required |
| **F** | <60 | Fundamentally broken | Rebuild |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed (Grade A/B) |
| 1 | CRITICAL issues found (Grade F) |
| 2 | MAJOR issues found (Grade D) |
| 3 | MINOR issues found (Grade C) |
| 4 | NIT issues found (--strict mode only) |

> **Note**: WARNING severity never blocks validation and always results in exit code 0.

### Severity Levels

| Level | Definition | Action |
|-------|------------|--------|
| **CRITICAL** | Skill will not work | Must fix before use |
| **MAJOR** | Significant problems | Create bug issue |
| **MINOR** | May affect UX/quality | Create enhancement issue |
| **NIT** | Stylistic or pedantic issues | Fix in --strict mode only |
| **WARNING** | Non-blocking advisory notice | Review recommended, never blocks |
| **INFO** | Suggestions | Optional improvement |
| **PASSED** | Check passed | No action needed |

### Validation Checklist

Copy this checklist and track your progress:

- [ ] Run basic validation: `uv run python scripts/validate_skill_comprehensive.py path/to/skill/`
- [ ] Fix all CRITICAL issues (Grade F blockers)
- [ ] Fix all MAJOR issues (Grade D issues)
- [ ] Run strict mode: add `--strict` flag
- [ ] Address required sections (Overview, Prerequisites, etc.)
- [ ] For lang-*/convert-* skills: run with `--pillars` flag
- [ ] Verify 8+1 Pillars coverage (50%+ recommended)
- [ ] Run with `--verbose` to review all checks
- [ ] Achieve Grade B or higher before deployment
- [ ] Document any intentionally skipped checks

## Error Handling

### Common Errors and Solutions

**"SKILL.md not found"**
- Ensure the path points to a skill directory (not a file)
- Check file is named `SKILL.md` (uppercase preferred)

**"Malformed YAML frontmatter"**
- Ensure frontmatter starts and ends with `---`
- Validate YAML syntax (check for missing quotes, colons)

**"Skill name must be lowercase"**
- Use kebab-case: `my-skill-name` not `MySkillName`

**"Directory name must match skill name"**
- Rename directory or update `name` field to match

**"Required section missing"**
- Add missing sections (only in --strict mode)
- Sections: Overview, Prerequisites, Instructions, Output, Error Handling, Examples, Resources

**"SKILL.md has X lines (max 500)"**
- Move detailed content to references/ subdirectory
- Use progressive disclosure pattern

## Examples

### Example 1: Validate a Single Skill

```bash
$ uv run python scripts/validate_skill_comprehensive.py ./skills/my-skill/

======================================================================
Skill Validation: ./skills/my-skill/
======================================================================

Grade: B (85.2/100)

Summary:
  CRITICAL: 0
  MAJOR:    0
  MINOR:    3

Details:
  [Description Quality]
    [MINOR] Description should include 'Use when ...' phrase

  [Token Budget]
    [MINOR] SKILL.md has 520 lines (recommended: under 500)

----------------------------------------------------------------------
✓ Skill validation passed (Grade B)
```

### Example 2: Validate with Strict Mode

```bash
$ uv run python scripts/validate_skill_comprehensive.py ./skills/lang-rust-dev/ --strict --pillars

======================================================================
Skill Validation: ./skills/lang-rust-dev/
======================================================================

Grade: A (94.5/100)

Pillars Coverage:
  ✓ Module: 1.0/1.0 - Full coverage with dedicated section
  ✓ Error: 1.0/1.0 - Full coverage (12 keyword occurrences)
  ~ Concurrency: 0.5/1.0 - Partial coverage (4 keyword occurrences)
  ✓ Metaprogramming: 1.0/1.0 - Full coverage with dedicated section
  ✓ Zero/Default: 1.0/1.0 - Full coverage (8 keyword occurrences)
  ✓ Serialization: 1.0/1.0 - Full coverage with dedicated section
  ✓ Build: 1.0/1.0 - Full coverage with dedicated section
  ✓ Testing: 1.0/1.0 - Full coverage (15 keyword occurrences)

----------------------------------------------------------------------
✓ Skill validation passed (Grade A)
```

### Example 3: JSON Output for CI/CD

```bash
$ uv run python scripts/validate_skill_comprehensive.py ./skills/my-skill/ --json

{
  "skill_path": "./skills/my-skill/",
  "exit_code": 0,
  "overall_score": 92.5,
  "grade": "A",
  "counts": {
    "critical": 0,
    "major": 0,
    "minor": 1,
    "info": 2,
    "passed": 25
  },
  "pillar_scores": [],
  "category_scores": {},
  "results": [...]
}
```

## Resources

### Official Documentation

- [AgentSkills OpenSpec](https://github.com/agentskills/agentskills) - Official skill specification
- [Claude Code Skills](https://code.claude.com/docs/en/skills.md) - Anthropic skill documentation
- [skills-ref Library](https://pypi.org/project/skills-ref/) - Reference validator library

### Reference Documents (in this skill)

- [Validation Rules](references/validation-rules.md) - Complete 190+ rules reference
  - 1. Structure Validation Rules (8 rules)
  - 2. Frontmatter Validation Rules (25 rules)
  - 3. Name Field Validation Rules (12 rules)
  - 4. Description Quality Rules (15 rules)
  - 5. Token Budget Rules (8 rules)
  - 6. Required Sections Rules (9 rules)
  - 7. Path Format Rules (6 rules)
  - 8. Resource Reference Rules (8 rules)
  - 9. Allowed-Tools Rules (10 rules)
  - 10. 8+1 Pillars Rules (18 rules)
  - 11. Progressive Disclosure Rules (12 rules)
  - 12. Content Quality Rules (15 rules)
  - 13. Agent-Specific Rules (22 rules)

- [Frontmatter Schema](references/frontmatter-schema.md) - Field requirements
  - 1. Required Fields
  - 2. Optional Fields (Claude Code)
  - 3. Enterprise Fields
  - 4. Field Validation Details
  - 5. Field Whitelist Modes
  - 6. Examples

- [Pillars Coverage](references/pillars-coverage.md) - 8+1 Pillars validation guide
  - 1. When to Apply Pillars Validation
  - 2. The 8 Core Pillars
  - 3. The 9th Pillar (REPL/Workflow)
  - 4. Scoring System
  - 5. Coverage Thresholds
  - 6. Gap Mitigation Strategies
  - 7. Example Evaluation

- [Scoring System](references/scoring-system.md) - Grading and scoring details
  - 1. Multi-Scale Criterion Scoring (0-3)
  - 2. Letter Grade System (A-F)
  - 3. Severity Levels
  - 4. Category Weighting
  - 5. Overall Score Calculation
  - 6. Exit Codes
  - 7. Interpreting Results

### Related Skills

- `plugin-validation-skill` - Plugin-level validation
- `setup-github-marketplace` - Marketplace validation

## See Also

- `/cpv-validate-skill` command - Invoke this validation
- `skill-validation-agent` - Agent for automated skill audits
