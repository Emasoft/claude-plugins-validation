# Quality Scoring — Validation Issues and Fixes

## Table of Contents

- [1. How Scoring Works](#1-how-scoring-works)
- [2. Category Definitions](#2-category-definitions)
- [3. Status Thresholds](#3-status-thresholds)
- [4. Sub-Validator Crash Messages](#4-sub-validator-crash-messages)
- [5. Recommendation Messages](#5-recommendation-messages)
- [6. How to Improve Each Category Score](#6-how-to-improve-each-category-score)

---

Comprehensive remediation guide for all issues detected by `validate_scoring.py`.

## Checklist

- [ ] Identify which sub-validator crashed (message will say e.g. "validate_skill.py crashed")
- [ ] Re-run that sub-validator directly with `--verbose` to see the traceback
- [ ] Fix the underlying cause (usually a malformed input file)
- [ ] Re-run scoring to confirm no crash-class CRITICALs remain
- [ ] Re-validate

## Overview

`validate_scoring.py` is the quality aggregator — it runs ALL other validators and computes a weighted quality score (0–100) broken into seven categories. It does not generate its own validation messages beyond the error messages produced when a sub-validator crashes. The messages you will see from scoring are:

1. **Sub-validator crash messages** (CRITICAL): When a validator raises an unexpected exception
2. **Recommendation strings**: Generated from category scores (not fix messages, but action guidance)
3. **Category scoring**: Weighted formula that converts CRITICAL/MAJOR/MINOR counts into scores

This reference explains:
- What each validator crash message means and how to fix it
- How the scoring system works
- How to interpret and act on recommendations
- How to achieve PASS status

---

## 1. How Scoring Works

The scoring formula is:

- Start each category at **10 points**
- Deduct **3.0 points** per CRITICAL issue
- Deduct **1.5 points** per MAJOR issue
- Deduct **0.5 points** per MINOR issue
- Minimum category score: **0**

The overall score (0–100) is a weighted sum:

| Category | Weight |
|---|---|
| `security` | 25% |
| `schema_compliance` | 20% |
| `matcher_validity` | 15% |
| `script_existence` | 15% |
| `hook_types` | 10% |
| `documentation` | 8% |
| `maintainability` | 7% |

**Rating scale (per category):**
- 9–10: Excellent
- 7–8: Good
- 5–6: Fair
- 0–4: Poor

---

## 2. Category Definitions

The validator categorizes issues from all sub-validators into these seven categories:

### schema_compliance
Issues related to: JSON structure, manifest format, `plugin.json` validity, required fields, schema validation, kebab-case naming.
Minimum threshold: **8/10**

### security
Issues related to: secrets, credentials, injection, path traversal, dangerous patterns, unsafe code. Also includes ALL results from `validate_security.py`.
Minimum threshold: **8/10**

### matcher_validity
Issues related to: hook matchers, regex patterns, invalid pattern syntax, tool name patterns, wildcards.
Minimum threshold: **7/10**

### script_existence
Issues related to: missing scripts, non-executable files, missing shebangs, chmod requirements, command not found.
Minimum threshold: **7/10**

### hook_types
Issues related to: hook type validation, event types, `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`.
Minimum threshold: **9/10**

### documentation
Issues related to: README, description fields, documentation quality, missing docstrings.
Minimum threshold: **5/10**

### maintainability
Issues related to: version fields, structure, duplicate definitions, unused elements, deprecated patterns, lint/format issues.
Minimum threshold: **6/10**

---

## 3. Status Thresholds

| Status | Condition |
|---|---|
| **PASS** | Overall score ≥ 80 AND all categories meet their thresholds AND no critical failures |
| **CONDITIONAL_PASS** | No critical failures AND overall score between 60–79 OR some categories below threshold |
| **FAIL** | Any critical failure present OR overall score < 60 |

**Syntactic Tiers** are derived from the overall score (0–100): PASS (80+), CONDITIONAL_PASS (60-79), FAIL (<60).

> **Note**: Letter grades (A-F) are used exclusively by the **Semantic Validation** system (`/cpv-semantic-validation`), not by the syntactic scoring.

---

## 4. Sub-Validator Crash Messages

These CRITICAL messages appear when a sub-validator raises an unexpected exception during the scoring run. They indicate a bug in the plugin structure (not in the scoring script itself).

### [CRITICAL] Plugin validation failed: {error}
**Source**: `validate_scoring.py` — `run_all_validators()`
**What it means**: `validate_plugin.py` raised an unhandled exception. This typically means the plugin directory has a severely malformed structure that prevents even basic parsing.
**How to fix**:
1. Run `validate_plugin.py` directly to see the full error:
   ```bash
   uv run python scripts/validate_plugin.py path/to/plugin --verbose
   ```
2. Common causes:
   - `plugin.json` is missing or contains invalid JSON
   - The plugin directory has unexpected structure (missing required subdirectories)
   - File permissions prevent reading the plugin structure
3. Fix the reported structural issue, then re-run the scorer.

---

### [CRITICAL] Security validation failed: {error}
**Source**: `validate_scoring.py` — `run_all_validators()`
**What it means**: `validate_security.py` raised an unhandled exception.
**How to fix**:
1. Run security validation directly:
   ```bash
   uv run python scripts/validate_security.py path/to/plugin --verbose
   ```
2. Common causes:
   - File permissions preventing scanning
   - Binary files incorrectly classified as text
   - Very large files causing memory issues
3. Fix the reported issue and re-run.

---

### [CRITICAL] Hook validation failed: {error}
**Source**: `validate_scoring.py` — `run_all_validators()`
**What it means**: `validate_hook.py` raised an unhandled exception while processing `hooks/hooks.json`.
**How to fix**:
1. Run hook validation directly:
   ```bash
   uv run python scripts/validate_hook.py path/to/plugin/hooks/hooks.json --verbose
   ```
2. Common causes:
   - `hooks.json` is invalid JSON
   - Unexpected data types inside the hooks configuration
3. Fix `hooks/hooks.json` and re-run.

---

### [CRITICAL] MCP validation failed: {error}
**Source**: `validate_scoring.py` — `run_all_validators()`
**What it means**: `validate_mcp.py` raised an unhandled exception while processing `.mcp.json`.
**How to fix**:
1. Run MCP validation directly:
   ```bash
   uv run python scripts/validate_mcp.py path/to/plugin --verbose
   ```
2. Common causes:
   - `.mcp.json` is invalid JSON
   - Unexpected structure in MCP server definitions
3. Fix `.mcp.json` and re-run.

---

### [CRITICAL] Agent validation failed for {agent_file}: {error}
**Source**: `validate_scoring.py` — `run_all_validators()`
**What it means**: `validate_agent.py` raised an unhandled exception while processing an agent `.md` file.
**How to fix**:
1. Run agent validation directly:
   ```bash
   uv run python scripts/validate_agent.py path/to/plugin/agents/my-agent.md --verbose
   ```
2. Common causes:
   - Agent `.md` file has encoding issues (not valid UTF-8)
   - YAML frontmatter is malformed
3. Fix the agent file and re-run.

---

### [CRITICAL] Skill validation failed for {skill_dir}: {error}
**Source**: `validate_scoring.py` — `run_all_validators()`
**What it means**: `validate_skill.py` raised an unhandled exception while processing a skill directory.
**How to fix**:
1. Run skill validation directly:
   ```bash
   uv run python scripts/validate_skill.py path/to/plugin/skills/my-skill --verbose
   ```
2. Common causes:
   - `SKILL.md` has encoding issues
   - YAML frontmatter is malformed
3. Fix the skill directory and re-run.

---

### [CRITICAL] Command validation failed for {command_file}: {error}
**Source**: `validate_scoring.py` — `run_all_validators()`
**What it means**: `validate_command.py` raised an unhandled exception while processing a command `.md` file.
**How to fix**:
1. Run command validation directly:
   ```bash
   uv run python scripts/validate_command.py path/to/plugin/commands/my-command.md --verbose
   ```
2. Common causes:
   - Command `.md` file has encoding issues
   - YAML frontmatter is malformed
3. Fix the command file and re-run.

---

## 5. Recommendation Messages

Recommendations are automatically generated from category scores. They appear in the `Recommendations` section of the quality report. Here is how to interpret each recommendation prefix:

### [CRITICAL] {Category}: Fix {N} critical issue(s) immediately
**What it means**: One or more CRITICAL issues were found in this category. The overall status is FAIL.
**How to fix**:
1. Run the relevant validator directly with `--verbose` to see the exact critical messages.
2. Use the dedicated fix reference for that validator:
   - `security` → `security-fixes.md`
   - `schema_compliance` → `plugin-structure-fixes.md`
   - `matcher_validity` → `hook-fixes.md`
   - `script_existence` → `hook-fixes.md`
   - `hook_types` → `hook-fixes.md`
   - `documentation` → `plugin-structure-fixes.md`
   - `maintainability` → `plugin-structure-fixes.md`
3. Fix all CRITICAL issues before re-running.

---

### [REQUIRED] {Category}: Score {X}/10 is below minimum {Y}/10 (need +{Z} points)
**What it means**: The category score is below its minimum threshold. The overall status is CONDITIONAL_PASS or FAIL.
**How to fix**:
1. Each point requires fixing a certain number of issues (MAJOR = 1.5 pts deducted, MINOR = 0.5 pts).
2. To gain +Z points, fix:
   - For +1.5 pts: Fix 1 MAJOR issue
   - For +0.5 pts: Fix 1 MINOR issue
3. Run the relevant validator with `--verbose` to identify and prioritize the issues to fix.

---

### [RECOMMENDED] {Category}: Address {N} major issue(s) to improve quality
**What it means**: The category passes its threshold but has MAJOR issues that reduce the score. Fixing them is recommended but not required for a passing grade.
**How to fix**:
1. Run the relevant validator to see the specific MAJOR messages.
2. Prioritize fixes from highest-weight categories: `security` (25%), `schema_compliance` (20%), `matcher_validity` (15%), `script_existence` (15%).

---

### [OPTIONAL] {Category}: Consider fixing {N} minor issue(s)
**What it means**: The category passes and has no MAJOR issues, but has MINOR issues that slightly reduce the score.
**How to fix**:
1. These are optional improvements. Fix them to move from Good to Excellent ratings.
2. Run the relevant validator with `--verbose` to see the MINOR messages.

---

## 6. How to Improve Each Category Score

### Improving schema_compliance (weight: 20%)
Fix issues related to:
- `plugin.json` structure and required fields → see `plugin-structure-fixes.md`
- Skill and agent frontmatter → see `enterprise-fixes.md`
- Manifest JSON validity → ensure `plugin.json` parses correctly

### Improving security (weight: 25%)
Security is the highest-weighted category. Any CRITICAL security issue causes FAIL status.
- Never commit credentials, API keys, or tokens
- Avoid shell injection in command strings
- No hardcoded absolute paths with sensitive information
- No `eval()` or dangerous shell constructs in scripts

### Improving matcher_validity (weight: 15%)
Fix issues in `hooks/hooks.json`:
- Invalid regex patterns in hook matchers → see `hook-fixes.md`
- Incorrect tool name wildcards
- Malformed event matcher patterns

### Improving script_existence (weight: 15%)
- Ensure all scripts referenced in `hooks.json` exist: `hooks/<script.sh>`
- Ensure shell scripts have execute permissions: `chmod +x hooks/my-hook.sh`
- Ensure scripts have a shebang line: `#!/usr/bin/env bash`

### Improving hook_types (weight: 10%)
- Use only valid event types: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `Stop`, etc.
- See `hook-fixes.md` for the full list of 27 valid event types

### Improving documentation (weight: 8%)
- Add or improve `README.md`
- Add `description` fields to `plugin.json`, skill frontmatters, agent frontmatters
- Document all commands, skills, and hooks

### Improving maintainability (weight: 7%)
- Add `version` field to `plugin.json`
- Remove duplicate hook definitions
- Remove unused/deprecated configuration
- Fix lint/format issues in scripts
