# Scoring System Reference

This document explains the multi-scale scoring (0-3) and letter grading (A-F) system.

## Table of Contents

- [1. Multi-Scale Criterion Scoring (0-3)](#1-multi-scale-criterion-scoring-0-3)
- [2. Letter Grade System (A-F)](#2-letter-grade-system-a-f)
- [3. Severity Levels](#3-severity-levels)
- [4. Category Weighting](#4-category-weighting)
- [5. Overall Score Calculation](#5-overall-score-calculation)
- [6. Exit Codes](#6-exit-codes)
- [7. Interpreting Results](#7-interpreting-results)

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

## 2. Letter Grade System (A-F)

### Grade Definitions

| Grade | Score Range | Status | Action Required |
|-------|-------------|--------|-----------------|
| **A** | 90-100 | Production Ready | Deploy with confidence |
| **B** | 80-89 | Good | Minor improvements optional |
| **C** | 70-79 | Acceptable | Plan improvements |
| **D** | 60-69 | Reject | Major rework required |
| **F** | < 60 | Broken | Rebuild or retire |

### Grade Criteria

#### Grade A (90-100)

- No CRITICAL issues
- No MAJOR issues
- ≤ 2 MINOR issues
- All required sections present
- Description quality excellent
- Token budget compliant

#### Grade B (80-89)

- No CRITICAL issues
- ≤ 2 MAJOR issues
- Minor issues acceptable
- Most best practices followed
- Good discoverability

#### Grade C (70-79)

- No CRITICAL issues
- Some MAJOR issues
- Multiple MINOR issues
- Basic functionality works
- Needs quality improvements

#### Grade D (60-69)

- No CRITICAL issues (barely)
- Multiple MAJOR issues
- Many MINOR issues
- Significant problems
- Should not be deployed

#### Grade F (< 60)

- CRITICAL issues present
- Fundamental problems
- Skill may not function
- Requires complete rework

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
Grade: A
```

---

## 6. Exit Codes

| Code | Meaning | Grade Range |
|------|---------|-------------|
| **0** | All checks passed (including WARNING) | A, B |
| **1** | CRITICAL issues found | F |
| **2** | MAJOR issues found | D, F |
| **3** | MINOR issues found | C |
| **4** | NIT issues found (--strict mode only) | B, C |

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

Grade: B (85.2/100)

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
✓ Skill validation passed (Grade B)
```

### Priority Actions

**Grade A/B**:
- Optional: Address MINOR issues
- Deploy when ready

**Grade C**:
1. Address all MAJOR issues first
2. Address MINOR issues that affect discoverability
3. Re-validate before deployment

**Grade D**:
1. Create GitHub issue for each MAJOR problem
2. Major rework required
3. Do NOT deploy

**Grade F**:
1. Fix CRITICAL issues immediately
2. Consider complete rewrite
3. Validate incrementally during rebuild

### Common Improvement Paths

| From | To | Actions |
|------|----|---------|
| F → D | Fix CRITICAL issues (SKILL.md, frontmatter) |
| D → C | Fix MAJOR issues (name format, references) |
| C → B | Fix most MINOR issues (description, budget) |
| B → A | Fix remaining MINOR, add optional elements |

---

## Summary

| Aspect | Values |
|--------|--------|
| **Criterion Scale** | 0 (missing) to 3 (excellent) |
| **Letter Grades** | A (90+), B (80-89), C (70-79), D (60-69), F (<60) |
| **Severities** | CRITICAL, MAJOR, MINOR, NIT, WARNING, INFO, PASSED |
| **Exit Codes** | 0 (pass/warning), 1 (CRITICAL), 2 (MAJOR), 3 (MINOR), 4 (NIT, strict only) |
| **Calculation** | Weighted average: CRIT=0, MAJ=1, MIN=2, PASS=3 |
