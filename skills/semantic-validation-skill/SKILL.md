---
name: semantic-validation-skill
description: "Deep AI semantic validation for skills/agents. Use when checking triggering, clarity, examples. Trigger with /cpv-semantic-validation. 10x token cost."
tags:
  - validation
  - semantic
  - quality
  - skills
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

This produces a **Semantic Grade (A-F)**, complementary to the **Syntactic Score (0-100)** from script validation. The two systems are independent — run syntactic validation first (cheap), then semantic validation only when needed.

## Prerequisites

- Python 3.12+ with `pyyaml` installed
- `uv` package manager
- Skill or agent directory to analyze

## Instructions

1. Navigate to the claude-plugins-validation directory
2. Run the command: `/cpv-semantic-validation <skill_or_agent_path>`
3. The agent runs cheap script validation first as a baseline:
   ```bash
   uv run python scripts/validate_skill_comprehensive.py "<skill_path>" --strict --report docs_dev/validate_semantic_baseline_YYYYMMDD.md
   ```
4. The agent reads the actual SKILL.md and agent .md files
5. The agent evaluates 7 semantic criteria that require AI judgment
6. Review the grade (A-F) and report at `docs_dev/semantic_validation_YYYYMMDD.md`

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

## Token Optimization

- **Explicit opt-in only** — never run automatically. Uses opus (10x cost).
- **Run script baseline first** — the cheap syntactic check catches 90% of issues.
- **Read only the target SKILL.md/agent .md** — not the entire plugin tree.
- **Write full report to file** — return only grade + filepath.

## Resources

- Semantic Validation Criteria — see `skills/fix-validation/references/skill-semantic-validation.md` for full criteria, rubrics, report format
- `skill-validation-skill` — Script-based validation (cheap, fast)
- `plugin-validation-skill` — Full plugin validation

## Validation Checklist
Copy this checklist and track your progress:
- [ ] Confirm explicit user opt-in
- [ ] Run semantic validation on target skill
- [ ] Review A-F grade and per-criterion scores
- [ ] Address failing criteria
