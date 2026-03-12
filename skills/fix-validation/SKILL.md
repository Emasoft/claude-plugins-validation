---
name: fix-validation
description: >
  Maps CPV validation errors to fix reference files. Use when looking up remediation steps.
  Trigger with /cpv-fix-validation.
allowed-tools: Read, Edit, Write, Glob, Grep
user-invocable: false
---

# Fix Validation — Error-to-Fix Index

## Overview

Central lookup for the fix agent. Given a validation error, find the reference file with fix instructions. All fix guides are in `references/`.

## Prerequisites

- A validation report from `/cpv-validate-plugin` or `/cpv-validate-skill`
- Access to the `references/` directory

## Instructions

1. Read the validation report for error messages with severity and file path
2. Match the error to a validator in the [Error-to-Fix Index](references/error-index.md)
3. Open the corresponding fix guide file
4. Use the guide's TOC to find the exact section for the error

Copy this checklist and track your progress:

- [ ] Read validation report
- [ ] Match errors to validators
- [ ] Open fix guide and apply fixes
- [ ] Re-run validation to confirm clean

## Output

The fix agent uses this index to locate the correct fix guide, then applies the fix and logs it to `docs_dev/fix-log_<name>_YYYYMMDD.md`.

## Error Handling

If no matching section is found in the reference file, search by error message keywords. If the reference file is missing, report the gap.

## Examples

**Input:** `[MAJOR] Missing plugin.json`
**Output:** Error Index → validate_plugin.py → plugin-structure-fixes §1 → apply fix

**Input:** `[MAJOR] Description too short`
**Output:** Error Index → validate_skill_comprehensive.py → skill-fixes §4 → apply fix

## Resources

- [Error-to-Fix Index](references/error-index.md)
  > Plugin Structure · Skill Structure · Hooks · Security · Encoding · MCP · Enterprise · Rules · LSP · Cross-References · Scoring · Marketplace · Documentation

## Token Optimization

Prefer LLM Externalizer MCP for bounded file analysis to save context tokens.
