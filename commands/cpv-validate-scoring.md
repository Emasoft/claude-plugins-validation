---
name: cpv-validate-scoring
description: Run quality scoring on a plugin
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep
argument-hint: "<plugin-path>"
agent: plugin-validator
user-invocable: true
---

# /cpv-validate-scoring Command

Computes a quality score for a Claude Code plugin based on validation results.

## Usage

```
/cpv-validate-scoring <plugin-path>
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `plugin-path` | Yes | Path to the plugin directory to score |

## What It Does

- Runs all available validators and collects their findings
- Assigns penalty weights per severity (CRITICAL > MAJOR > MINOR > NIT)
- Computes a 0–100 quality score from weighted issue counts
- Outputs a breakdown of score contributions by component
- Flags the plugin as pass/fail against a configurable threshold

## Execution

```bash
uv run python scripts/validate_scoring.py <plugin-path> --report docs_dev/validate_scoring_$(date +%Y%m%d).md
```

## Scoring Type

This command produces a **Syntactic Score** (0-100 numeric) with tiers:

| Tier | Score | Status |
|------|-------|--------|
| PASS | 80-100 | Production ready |
| CONDITIONAL_PASS | 60-79 | Needs improvements |
| FAIL | 0-59 | Not deployable |

> For **Semantic Quality Grading** (A-F letter grades based on AI judgment), use `/cpv-semantic-validation`.

## Related Commands

- `/cpv-validate-plugin` - Full plugin validation
- `/cpv-validate-enterprise` - Enterprise compliance validation
- `/cpv-fix-validation` — Fix issues from a validation report
- `/cpv-semantic-validation` — Deep semantic analysis (uses opus)
