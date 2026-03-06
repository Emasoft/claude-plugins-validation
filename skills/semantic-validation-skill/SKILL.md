---
name: semantic-validation-skill
description: |
  Deep AI-driven semantic validation for skills and agents. Evaluates description triggering,
  instruction clarity, example quality, and workflow completeness. Expensive (uses opus).
  Trigger with /cpv-semantic-validation. Explicit opt-in only.
tags:
  - validation
  - semantic
  - quality
  - skills
agent: semantic-validator
user-invocable: true
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep
---

# Semantic Validation Skill

Deep semantic analysis of skill and agent quality — things that automated scripts cannot check.

## Overview

This skill performs AI-driven evaluation of:
- Description triggering effectiveness (will it fire at the right time?)
- Instruction clarity and completeness
- Example quality (realistic, complete, covers failure paths)
- Workflow completeness (start/end, branches, exit conditions)
- Technical quality (exit codes, error handling, terminology)
- Progressive disclosure effectiveness
- Output pattern documentation

**Cost**: Uses opus model. Only invoke when you need deep quality analysis beyond what scripts provide.

## Prerequisites

- Python 3.12+ with `pyyaml` installed
- `uv` package manager
- Skill or agent directory to analyze

## Instructions

### Step 1: Run Script Validation First (Baseline)

The semantic validator always runs cheap script validation first to establish a baseline:

```bash
uv run python scripts/validate_skill_comprehensive.py "<skill_path>" --strict --report docs_dev/validate_semantic_baseline_YYYYMMDD.md
```

### Step 2: Deep Semantic Analysis

The agent reads the actual SKILL.md and agent .md files, evaluating 7 semantic criteria that require AI judgment.

### Step 3: Review Results

The agent produces a grade (A-F) and writes a detailed report to `docs_dev/semantic_validation_YYYYMMDD.md`.

## Output

- **Grade**: A-F letter grade based on semantic quality
- **Criteria Results**: Pass/Partial/Fail for each of 7 criteria
- **Report File**: Full analysis saved to `docs_dev/semantic_validation_YYYYMMDD.md`

## Error Handling

- If script validation fails with CRITICAL issues, semantic analysis is skipped — fix structural issues first.
- If the skill path is invalid, the agent reports the error and exits.

## Examples

### Example 1: Validate a Skill Semantically

```
/cpv-semantic-validation ./skills/my-skill/
```

### Example 2: Validate an Agent

```
/cpv-semantic-validation ./agents/my-agent.md
```

## Resources

- [Semantic Validation Criteria](../../agents/references/skill-semantic-validation.md) — Full criteria, rubrics, report format
- `skill-validation-skill` — Script-based validation (cheap, fast)
- `plugin-validation-skill` — Full plugin validation
