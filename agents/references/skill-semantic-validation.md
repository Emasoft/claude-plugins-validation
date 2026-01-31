# Skill Semantic Validation Guide

This reference contains detailed semantic validation criteria that require AI judgment and cannot be performed by scripts.

## Table of Contents

1. [Description Quality](#1-description-clarity--specificity)
2. [Instructions Quality](#3-instructions-conciseness)
3. [Example Quality](#5-example-quality)
4. [Workflow Validation](#6-workflow-completeness)
5. [Technical Quality](#8-script-error-handling)
6. [Output Patterns](#14-timestamped-report-files-pattern)
7. [Report Format](#semantic-validation-report-format)

---

## 1. Description Clarity & Specificity

**Check**: Is the description specific enough for Claude to match this skill to user intent?

**Signs of Poor Description**:
- Vague statements like "helps with X tasks"
- No concrete trigger scenarios
- Missing domain-specific keywords
- Too abstract to match real queries

**Example Assessment**:
```
❌ BAD: "A utility skill for helping with code"
✅ GOOD: "Enforces test-driven development workflow. Use when implementing
   features, fixing bugs, or refactoring code."
```

## 2. Description Keyword Richness

**Check**: Does the description contain domain-specific keywords that users would naturally use?

**Evaluate**:
- Technical terms relevant to the skill's domain
- Action verbs that describe what the skill does
- Synonyms that cover different ways users might phrase requests

## 3. Instructions Conciseness

**Check**: Are instructions clear and actionable without being verbose?

**Signs of Bloat**:
- Repeating the same concept in different words
- Over-explaining basic concepts
- Missing progressive disclosure

## 4. Degrees of Freedom Appropriateness

**Check**: Does the skill provide appropriate flexibility vs. strict guidance?

| Skill Type | Flexibility |
|------------|-------------|
| User-facing | Should be flexible, allow user choices |
| Internal | Can be more prescriptive |
| Critical ops | Strict with confirmation steps |

## 5. Example Quality

**Good Examples Have**:
- Realistic input (not "foo", "bar", "test")
- Expected output showing actual behavior
- Edge cases and error scenarios
- Progressive complexity (simple → advanced)

## 6. Workflow Completeness

**Verify**:
- Entry points (what triggers the workflow)
- Decision points (branching logic)
- Exit points (completion criteria)
- Error recovery paths

## 7. Error Handling Guidance

**Should Include**:
- Common failure modes
- Recovery procedures
- When to escalate vs. retry
- User notification requirements

## 8. Script Error Handling

**Review For**:
- Exit codes meaningful (0 = success, non-zero = specific errors)
- Error messages actionable
- Input validation before processing
- Cleanup on failure

## 9. Magic Constants Detection

**Flag**:
- Numeric thresholds without explanation
- File paths without justification
- Timeout values without rationale

## 10. Terminology Consistency

**Look For**:
- Same concept referred to with different names
- Mixed terminology (e.g., "error" vs "exception" vs "failure")

**Action**: When you find synonyms, recommend unifying to a single term.

## 11. Conditional Workflows Validation

**Pattern**: `**Creating new content?** → Follow "Creation workflow" below`

**Verify**:
- Each arrow (→) points to an existing section
- All decision branches are covered
- No orphan targets

## 12. Feedback Loops Logic

**Verify**:
- Every loop has an explicit exit condition
- Exit conditions are achievable
- No infinite loop risks

**Indicators of Risk**:
```
❌ "Keep trying until it works" - no limit
✅ "Only proceed when validation passes" - clear exit
✅ "After 3 attempts, escalate to user" - bounded
```

## 13. Progressive Disclosure Effectiveness

**Evaluate**:
- SKILL.md under 500 lines
- Detailed content in references/ files
- TOC in files > 100 lines
- References one level deep

## 14. Timestamped Report Files Pattern

**Why This Matters**: Sub-agents returning verbose output consume the orchestrator's context.

**Verify**:
- Results saved to timestamped .md files
- Instructions say "return only the file path"
- Output directory specified

---

## Semantic Validation Report Format

```markdown
## Semantic Validation Results

### Description Quality
- **Clarity**: PASS/PARTIAL/FAIL - [Notes]
- **Keywords**: PASS/PARTIAL/FAIL - [Notes]

### Instructions Quality
- **Conciseness**: PASS/PARTIAL/FAIL - [Notes]
- **Degrees of Freedom**: PASS/PARTIAL/FAIL - [Notes]

### Content Quality
- **Examples**: PASS/PARTIAL/FAIL - [Notes]
- **Workflow Completeness**: PASS/PARTIAL/FAIL - [Notes]
- **Error Handling**: PASS/PARTIAL/FAIL - [Notes]

### Technical Quality
- **Script Quality**: PASS/PARTIAL/FAIL/N/A - [Notes]
- **Magic Constants**: PASS/PARTIAL/FAIL - [Notes]
- **Terminology**: PASS/PARTIAL/FAIL - [Notes]

### Workflow Logic
- **Conditional Workflows**: PASS/PARTIAL/FAIL - [Notes]
- **Feedback Loops**: PASS/PARTIAL/FAIL - [Notes]

### Output Patterns
- **Progressive Disclosure**: PASS/PARTIAL/FAIL - [Notes]
- **Timestamped Reports**: PASS/PARTIAL/FAIL/N/A - [Notes]

### Overall Semantic Score: X/14 passing
```

---

## Direct File Reading Workflow

When script validation passes but semantic analysis is required:

1. **Read SKILL.md** - Evaluate description, instructions, examples
2. **Read References** - Check TOC, content relevance, nesting
3. **Read Scripts** - Check error handling, exit codes, documentation
4. **Cross-Reference Check** - Verify terminology and path consistency
