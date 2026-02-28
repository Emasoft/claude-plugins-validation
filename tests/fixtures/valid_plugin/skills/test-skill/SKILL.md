---
name: test-skill
description: A test skill for validation testing
triggers:
  - when user asks about testing
  - when user mentions validation
  - when user needs to verify something
---

# Test Skill

This skill teaches how to perform testing and validation tasks.

## When to Use

Use this skill when:
- You need to test validation logic
- You want to verify plugin configurations
- You need to check fixture validity

## Instructions

Follow these steps for testing:

1. Identify what needs to be tested
2. Create appropriate test fixtures
3. Run the validation
4. Check the results

## Examples

### Example 1: Basic Validation

```python
from cpv_validation_common import ValidationReport

report = ValidationReport()
report.passed("Test passed successfully")
```

### Example 2: Checking Errors

```python
report = ValidationReport()
report.critical("Missing required field: name")
assert report.has_critical
```
