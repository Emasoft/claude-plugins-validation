# Syntactic Scoring System Reference

This document explains the **Syntactic Validation Score** — a 0-100 numeric score computed by scripts based on structural and mechanical checks.

> **Note**: For **Semantic Quality Grading** (A-F letter grades based on AI judgment), see `/cpv-semantic-validation`. The two scoring systems are independent and complementary.

## Table of Contents

- [1. Multi-Scale Criterion Scoring (0-3)](#1-multi-scale-criterion-scoring-0-3)
- [2. Tier System (PASS / CONDITIONAL_PASS / FAIL)](#2-tier-system)
- [3. Severity Levels](#3-severity-levels)
- [4. Category Weighting](#4-category-weighting)
- [5. Overall Score Calculation](#5-overall-score-calculation)
- [6. Exit Codes](#6-exit-codes)
- [7. Interpreting Results](#7-interpreting-results)
- [8. Two Scoring Systems](#8-two-scoring-systems)

---

## 1. Multi-Scale Criterion Scoring (0-3)

Each validation check receives a score from 0-3:

| Score | Status | Definition | Example |
|-------|--------|------------|---------|
| **0** | Missing/Absent | Required element is completely missing or fundamentally broken | No SKILL.md file |
| **1** | Present but Inadequate | Element exists but doesn't meet requirements | Description < 20 chars |
| **2** | Adequate | Element meets basic requirements | Valid frontmatter |
| **3** | Excellent | Element exceeds requirements | Comprehensive description with triggers |

### Score Mapping to Severity

| Score | Severity Level |
|-------|----------------|
| 0 | CRITICAL or MAJOR |
| 1 | MAJOR or MINOR |
| 2 | MINOR, NIT, or WARNING |
| 3 | PASSED |

---

## 2. Tier System

The Syntactic Score (0-100) maps to three tiers:

| Tier | Score Range | Status | Action Required |
|------|-------------|--------|-----------------|
| **PASS** | 80-100 | Production ready | Deploy with confidence |
| **CONDITIONAL_PASS** | 60-79 | Needs improvements | Fix MAJOR issues before deployment |
| **FAIL** | 0-59 | Not deployable | Fix CRITICAL/MAJOR issues, major rework needed |

### Tier Criteria

#### PASS (80-100)

- No CRITICAL issues
- No or very few MAJOR issues (≤2)
- MINOR issues acceptable
- All required sections present
- Token budget compliant

#### CONDITIONAL_PASS (60-79)

- No CRITICAL issues
- Some MAJOR issues present
- Multiple MINOR issues
- Basic functionality works but needs quality improvements
- Should not be deployed without fixes

#### FAIL (0-59)

- CRITICAL issues present, OR
- Many MAJOR issues
- Fundamental structural problems
- Skill may not function correctly
- Requires significant rework

---

## 3. Severity Levels

### CRITICAL

**Definition**: Skill will not function at all.

**Causes**:
- SKILL.md missing
- Malformed YAML frontmatter
- Invalid `context` value
- Invalid boolean fields
- Path not a directory

**Action**: Must fix before any use.

**Exit Code**: 1

### MAJOR

**Definition**: Significant problems that impact usability.

**Causes**:
- Name format invalid
- Description too long (>1024 chars)
- Token budget exceeded (>800 lines)
- Required sections missing (strict mode)
- Referenced files not found
- First/second person in description (strict mode)

**Action**: Create bug issue, fix before deployment.

**Exit Code**: 2

### MINOR

**Definition**: Quality issues that may affect user experience.

**Causes**:
- Description too short (<20 chars)
- Line count warning (500-800 lines)
- Missing "Use when..." phrase
- Name differs from directory
- Over-permissioning (>6 tools)
- Pillar coverage gaps

**Action**: Create enhancement issue, plan improvements.

**Exit Code**: 3

### NIT

**Definition**: Stylistic or pedantic issues that only matter under strict quality review.

**Causes**:
- Inconsistent formatting or whitespace
- Non-preferred naming conventions
- Stylistic deviations from best practices

**Action**: Fix when running in --strict mode. Ignored in normal mode.

**Exit Code**: 4 (--strict mode only, otherwise 0)

### WARNING

**Definition**: Non-blocking advisory notices that highlight potential concerns.

**Causes**:
- Deprecated patterns still functional
- Approaching threshold limits (e.g., line count nearing budget)
- Compatibility notes for future spec changes

**Action**: Review recommended. WARNING never blocks validation.

**Exit Code**: 0 (never blocks)

### INFO

**Definition**: Suggestions and informational notes.

**Causes**:
- Unknown frontmatter fields
- Missing optional directories
- Discoverability improvements
- Best practice suggestions

**Action**: Optional improvements.

**Exit Code**: 0

### PASSED

**Definition**: Check passed successfully.

**Action**: No action needed.

**Exit Code**: 0

---

## 4. Category Weighting

Validation results are grouped into categories with different weights:

| Category | Weight | Description |
|----------|--------|-------------|
| **Structure** | 15% | Directory structure, SKILL.md existence |
| **Frontmatter** | 25% | Field validation, schema compliance |
| **Description Quality** | 15% | Triggers, voice, length |
| **Token Budget** | 10% | Line count, word count |
| **Required Sections** | 10% | Overview, Instructions, etc. (strict mode) |
| **Resource References** | 10% | Script/file existence |
| **Content Quality** | 10% | Path formats, terminology |
| **Pillars Coverage** | 5% | 8+1 Pillars (when applicable) |

### Weight Redistribution

When certain categories don't apply:
- Non-strict mode: Required Sections weight redistributes to Frontmatter
- Non-lang skills: Pillars Coverage weight redistributes to Content Quality

---

## 5. Overall Score Calculation

### Basic Algorithm

```python
def calculate_score(results: list[ValidationResult]) -> float:
    # Count by level
    critical = sum(1 for r in results if r.level == "CRITICAL")
    major = sum(1 for r in results if r.level == "MAJOR")
    minor = sum(1 for r in results if r.level == "MINOR")
    passed = sum(1 for r in results if r.level == "PASSED")

    total = critical + major + minor + passed

    if total == 0:
        return 0.0

    # Weighted scoring
    # CRITICAL = 0 points, MAJOR = 1 point, MINOR = 2 points, PASSED = 3 points
    weighted_score = (
        critical * 0 +
        major * 1 +
        minor * 2 +
        passed * 3
    )

    max_possible = total * 3
    return (weighted_score / max_possible) * 100
```

### Example Calculation

**Results**:
- CRITICAL: 0
- MAJOR: 2
- MINOR: 3
- PASSED: 20

**Calculation**:
```
Total checks: 0 + 2 + 3 + 20 = 25
Weighted score: (0*0) + (2*1) + (3*2) + (20*3) = 0 + 2 + 6 + 60 = 68
Max possible: 25 * 3 = 75
Percentage: (68/75) * 100 = 90.67%
Tier: PASS
```

---

## 6. Exit Codes

| Code | Meaning | Tier Range |
|------|---------|------------|
| **0** | All checks passed (including WARNING) | PASS |
| **1** | CRITICAL issues found | FAIL |
| **2** | MAJOR issues found | CONDITIONAL_PASS, FAIL |
| **3** | MINOR issues found | PASS, CONDITIONAL_PASS |
| **4** | NIT issues found (--strict mode only) | PASS, CONDITIONAL_PASS |

> **Note**: WARNING severity never produces a non-zero exit code. WARNING results always map to exit code 0.

### Exit Code Priority

If multiple severity levels are present:
1. CRITICAL → Exit 1
2. MAJOR → Exit 2
3. MINOR → Exit 3
4. NIT → Exit 4 (--strict mode only; ignored in normal mode)
5. WARNING → Exit 0 (never blocks)
6. All PASSED → Exit 0

---

## 7. Interpreting Results

### Validation Report Structure

```
======================================================================
Skill Validation: ./skills/my-skill/
======================================================================

Syntactic Score: 90.67/100 (PASS)

Summary:
  CRITICAL: 0
  MAJOR:    1
  MINOR:    3
  INFO:     2
  PASSED:   20

Pillars Coverage:
  ✓ Module: 1.0/1.0 - Full coverage with dedicated section
  ~ Error: 0.5/1.0 - Partial coverage
  ...

Details:
  [Frontmatter]
    [PASSED] 'name' field present: my-skill
    [PASSED] 'description' field present
    [MAJOR] 'allowed-tools' uses unscoped Bash

  [Token Budget]
    [MINOR] SKILL.md has 520 lines (recommended: under 500)

  [Description Quality]
    [MINOR] Description should include 'Use when ...' phrase

----------------------------------------------------------------------
✓ Skill validation passed (Syntactic Score: 90.67/100 — PASS)
```

### Priority Actions

**PASS (80-100)**:
- Optional: Address MINOR issues
- Deploy when ready

**CONDITIONAL_PASS (60-79)**:
1. Address all MAJOR issues first
2. Address MINOR issues that affect discoverability
3. Re-validate before deployment

**FAIL (0-59)**:
1. Fix CRITICAL issues immediately
2. Fix MAJOR issues
3. Consider significant rework
4. Validate incrementally during rebuild

### Common Improvement Paths

| From | To | Actions |
|------|----|---------|
| FAIL → CONDITIONAL_PASS | Fix CRITICAL issues (SKILL.md, frontmatter) |
| CONDITIONAL_PASS → PASS | Fix MAJOR issues (name format, references) |
| Low PASS → High PASS | Fix remaining MINOR, add optional elements |

---

## 8. Two Scoring Systems

This plugin validation suite uses **two independent scoring systems**:

| System | Scale | Computed By | What It Measures |
|--------|-------|-------------|------------------|
| **Syntactic Score** | 0-100 numeric (PASS/CONDITIONAL_PASS/FAIL) | Scripts (`validate_*.py`) | Structural correctness, schema compliance, mechanical checks |
| **Semantic Grade** | A-F letter grade | AI agent (`semantic-validator`, opus) | Description effectiveness, instruction clarity, example quality, workflow completeness |

- Syntactic validation is **cheap** (sonnet model, ~2K tokens) — run it always
- Semantic validation is **expensive** (opus model, ~50K tokens) — run it only via `/cpv-semantic-validation`
- The two scores are **complementary**: a plugin can have a perfect Syntactic Score (100) but a poor Semantic Grade (D) if descriptions are vague and examples are toy-like

---

## Summary

| Aspect | Values |
|--------|--------|
| **Criterion Scale** | 0 (missing) to 3 (excellent) |
| **Syntactic Tiers** | PASS (80+), CONDITIONAL_PASS (60-79), FAIL (<60) |
| **Severities** | CRITICAL, MAJOR, MINOR, NIT, WARNING, INFO, PASSED |
| **Exit Codes** | 0 (pass/warning), 1 (CRITICAL), 2 (MAJOR), 3 (MINOR), 4 (NIT, strict only) |
| **Calculation** | Weighted average: CRIT=0, MAJ=1, MIN=2, PASS=3 |
| **Semantic Grade** | A-F (via `/cpv-semantic-validation` only) |
