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
2. Match the error to a validator in the index below
3. Open the corresponding fix guide file in `references/`
4. Use the guide's TOC to find the exact section for the error

Copy this checklist and track your progress:

- [ ] Read validation report
- [ ] Match errors to validators
- [ ] Open fix guide and apply fixes
- [ ] Re-run validation to confirm clean

## Error Index by Validator

**validate_plugin.py** → `references/plugin-structure-fixes.md`
- Manifest (plugin.json) → §1 · Directories → §2 · Commands → §3 · Agents → §4
- Hooks → §5 · MCP → §6 · Scripts → §7 · Cross-platform → §8 · README/LICENSE → §10

**validate_skill_comprehensive.py** → `references/skill-fixes.md`
- Structure → §1 · Frontmatter → §2 · Name → §3 · Description → §4
- Token budget → §5 · Required sections (strict) → §6 · Reference files → §7

**validate_hook.py** → `references/hook-fixes.md`
- JSON structure → §1 · Event types → §2 · Matchers → §3 · Hook types → §4
- Command/Prompt/Agent hooks → §5-7 · Timeouts → §8 · Scripts → §9-10

**validate_security.py** → `references/security-fixes.md`
- Injection → §2 · Path traversal → §3 · Secrets → §4 · Paths → §5 · Permissions → §7

**validate_encoding.py** → `references/encoding-fixes.md`
- UTF-8 → §2 · BOM → §3 · JSON unicode → §4 · Line endings → §6-8

**validate_mcp.py** → `references/mcp-fixes.md`
- Config → §1 · Servers → §2 · Transport → §3-5 · Env vars → §6 · OAuth → §11

**validate_enterprise.py** → `references/enterprise-fixes.md`
- Plugin/path → §1 · Skills → §2 · Metadata → §3 · Author/License → §4-5

**validate_rules.py** → `references/rules-fixes.md`
- Directory → §1 · Encoding → §2 · Content → §3 · Frontmatter → §4

**validate_lsp.py** → `references/lsp-fixes.md`
- Config → §1 · Structure → §2 · Command → §4 · Filetypes → §7

**validate_xref.py** → `references/xref-fixes.md`
- Agent refs → §2 · Subagent_type → §3 · Version sync → §4 · Skill refs → §6

**validate_scoring.py** → `references/scoring-fixes.md`
- Crash messages → §4 · Low scores → §6

**validate_marketplace.py** → `references/marketplace-fixes.md`
- marketplace.json → §1 · Plugin entries → §2 · Pipeline → §5

**validate_documentation.py** → `references/documentation-fixes.md`
- README → §1-2 · Links → §3 · CHANGELOG → §4 · Headings → §5

## Output

The fix agent uses this index to locate the correct fix guide, then applies the fix and logs it to `docs_dev/fix-log_<name>_YYYYMMDD.md`.

## Error Handling

If no matching section is found in the reference file, search by error message keywords. If the reference file is missing, report the gap.

## Examples

**Input:** A validation report containing `[MAJOR] Missing plugin.json`
**Output:** Opens `references/plugin-structure-fixes.md` → §1. Plugin Manifest Issues → applies fix

**Input:** A validation report containing `[MAJOR] Description too short`
**Output:** Opens `references/skill-fixes.md` → §4. Description Quality Issues → applies fix

## Resources

All fix guides: `references/*-fixes.md`
