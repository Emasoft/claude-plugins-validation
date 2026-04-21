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

1. Set `CLAUDE_PRIVATE_USERNAMES="your_username"` if needed (usually auto-detected)
2. Run the validator:
   ```bash
   uv run python scripts/validate_plugin.py /path/to/plugin --report reports/validate_plugin_YYYYMMDD.md
   ```
3. Review compact summary (always use `--report` to save details to file)
4. Fix issues: CRITICAL > MAJOR > MINOR (use `/cpv-fix-validation <report_path>` for plugin reports, `/cpv-fix-marketplace-validation <report_path>` for marketplace reports)
5. Re-run until exit code 0

## Output

- **Syntactic Score**: 0-100 numeric with tier (PASS / CONDITIONAL_PASS / FAIL)
- **Exit Code**: 0 (pass), 1 (CRITICAL), 2 (MAJOR), 3 (MINOR), 4 (NIT, --strict only). WARNING never blocks.
- **Summary**: Issue counts by severity level
- **Report File**: Full output saved to `reports/validate_<plugin-name>_<date>.md` at the **project root** — worktree-aware. The folder is gitignored by convention; reports often contain private data (full paths, source snippets, validation output). Resolve the root with `${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}` when the caller runs inside a worktree.

> For **Semantic Quality Grading** (A-F letter grades), use `/cpv-semantic-validation`.

## Error Handling

- **Non-zero exit**: Report severity and failing checks. Do NOT publish until MAJOR/CRITICAL resolved.
- **Missing deps**: `uv pip install ruff mypy` or `brew install shellcheck`.
- **Invalid JSON/YAML**: Show parse error with path and line number.

## Examples

```bash
uv run python scripts/validate_plugin.py /path/to/plugin --verbose --report reports/report.md
uv run python scripts/validate_skill_comprehensive.py /path/to/skill/ --strict --report reports/report.md
```

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

## Token Optimization

Always `--report <path>` — share path, don't read. One script per run.
Prefer LLM Externalizer MCP for report analysis to save context tokens.

## Checklist
Copy this checklist and track your progress:
- [ ] Run validate_plugin.py --verbose --report
- [ ] Fix CRITICAL > MAJOR > MINOR
- [ ] Re-run until exit 0
