---
name: plugin-validation-skill
description: Validates Claude Code plugins for structural correctness, quality, and marketplace readiness. Use when validating a plugin. Loaded by plugin-validator, plugin-creator, and plugin-fixer agents.
tags: [validation, plugins, marketplace, hooks, skills, mcp, quality-assurance]
user-invocable: false
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep, Write
---

# Plugin Validation Skill

## Overview

Validates Claude Code plugins against 190+ structural and quality rules covering manifests, hooks, skills, MCP servers, marketplace configs, and agents. Produces a severity-graded report with actionable fix guidance.

## Prerequisites

- Python 3.12+ with `pyyaml`, `uv` package manager
- Plugin directory with valid structure (`.claude-plugin/plugin.json`)

## Instructions

1. Run via the launcher (see [launcher-invocation.md](references/launcher-invocation.md) for the canonical one-liner — NEVER call `validate_plugin.py` directly).
   > Why the launcher is mandatory · The one-liner (use this verbatim) · Full alias table · Direct invocation (development only)
2. Review compact summary (always use `--report` to save details to file).
3. Fix issues: CRITICAL > MAJOR > MINOR (use `/cpv-fix-validation <report_path>`).
4. Re-run until exit code 0.

## Output

- **Syntactic Score**: 0-100 numeric with tier (PASS / CONDITIONAL_PASS / FAIL)
- **Exit Code**: 0 (pass), 1 (CRITICAL), 2 (MAJOR), 3 (MINOR), 4 (NIT, --strict only). WARNING never blocks.
- **Summary**: Issue counts by severity level
- **Report File**: `$MAIN_ROOT/reports/validate_plugin/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md` at the **main-repo root** (first entry of `git worktree list`) — never a linked worktree. Per-component subfolder + local-time+GMT-offset timestamp mandatory. Both `reports/` and `reports_dev/` gitignored.

> For **Semantic Quality Grading** (A-F letter grades), use `/cpv-semantic-validation`.

## Error Handling

- **Non-zero exit**: Report severity and failing checks. Do NOT publish until MAJOR/CRITICAL resolved.
- **Missing deps**: `uv pip install ruff mypy` or `brew install shellcheck`.
- **Invalid JSON/YAML**: Show parse error with path and line number.

## Examples

See [launcher-invocation.md](references/launcher-invocation.md) for the one-liner and full alias table (25+ aliases).
> Why the launcher is mandatory · The one-liner (use this verbatim) · Full alias table · Direct invocation (development only)

Example 1:
- Input: `... plugin ~/Code/my-plugin/ --report ...`
- Output: `Plugin Validation: PASS. CRITICAL=0 MAJOR=0 MINOR=2 PASSED=155`

Example 2:
- Input: `... skill ./skills/my-skill/ --strict --report ...`
- Output: `Skill Validation: PASS. Score 85/100`

Example 3:
- Input: `... cache ~/Code/my-plugin/ --report ...`
- Output: `Cache Audit: VALID (no CA-NN findings)`

## Resources

- [Validation Checklist](references/validation-checklist.md) - Master checklist for pre-release
  - 1. Plugin Manifest Checklist
  - 2. Plugin Structure Checklist
  - 3. Hook Configuration Checklist
  - 4. Skill Validation Checklist
  - 5. MCP Server Checklist
  - 6. Marketplace Checklist
  - 7. Agent Checklist
  - 8. LSP Server Checklist
  - 9. Script and Code Quality Checklist
  - 10. Pre-Release Final Checklist
  - 11. Validation Commands
- [Plugin Structure](references/plugin-structure.md) - Required plugin directory layout
  - 1. Directory Structure
  - 2. Plugin Manifest (plugin.json)
  - 3. Component Placement Rules
  - 4. Path Variables
  - 5. Common Structure Errors
  - 6. Validation Checklist
- [Hook Validation](references/hook-validation.md) - Hook configuration reference
  - 1. Hook Configuration File
  - 2. Valid Hook Events
  - 3. Matcher Syntax
  - 4. Hook Types
  - 5. Hook Input/Output Format
  - 6. Script Requirements
  - 7. Common Hook Errors
  - 8. Validation Checklist
- [Troubleshooting](references/troubleshooting-python-scripts.md)
  > Bash Arithmetic Exit Codes · Unused Variable Warnings - Pyright/ruff · Missing Python Dependencies - ModuleNotFoundError · Git Hook Not Running · Plugin JSON Missing Required Fields · Ruff Linting - Unused Variable Error · Marketplace Plugin Source Format · Version Consistency Between Plugins and Marketplace · Git Tag Already Exists Error · subprocess.run Output Truncation · Best Practices Summary · Quick Diagnostic Commands

- [Security Validator Contract](references/security-validator-contract.md) - `validate_security.py` I/O contract, five external scanners, pre-scan dedup pipeline, self-scan filter parity, env knobs
  - Path-only stdout by default
  - Aggregated reporting
  - Five external scanners always run
  - Pre-scan dedup pipeline (v2.48)
  - Self-scan filter parity
  - Env knobs

## Token Optimization

Always `--report <path>`. Use LLM Externalizer for report analysis.

## Checklist
Copy this checklist and track your progress:
- [ ] Run launcher with `--verbose --report`
- [ ] Fix CRITICAL > MAJOR > MINOR
- [ ] Re-run until exit 0
