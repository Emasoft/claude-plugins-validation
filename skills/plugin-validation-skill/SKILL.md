---
name: plugin-validation-skill
description: Validates Claude Code plugins for structural correctness, quality compliance, and marketplace readiness. Use when validating a plugin. Trigger with /cpv-validate-plugin.
tags: [validation, plugins, marketplace, hooks, skills, mcp, quality-assurance]
user-invocable: true
allowed-tools: Read, Bash(uv*), Bash(python*), Glob, Grep, Write
---

# Plugin Validation Skill

## Overview

This skill runs automated validation on Claude Code plugins, checking manifests, hooks, skills, MCP servers, and marketplace configs against 190+ structural and quality rules. It produces a severity-graded report with actionable fix guidance so you can resolve issues before publishing.

Validates Claude Code plugins and components for quality and compliance. Checks:
- Plugin manifest (`plugin.json`) structure/fields
- Hook configs (`hooks.json`) and scripts
- Skill frontmatter and content (190+ rules)
- MCP server configs (`.mcp.json`)
- Marketplace configs and git submodules
- Agent definitions and system prompts

## Prerequisites

- Python 3.12+ with `pyyaml`, `uv` package manager
- Plugin directory with valid structure (`.claude-plugin/plugin.json`)

## Instructions

1. Set `CLAUDE_PRIVATE_USERNAMES="your_username"` if needed (usually auto-detected)
2. Run the validator:
   ```bash
   uv run python scripts/validate_plugin.py /path/to/plugin --report docs_dev/validate_plugin_YYYYMMDD.md
   ```
3. Review compact summary (always use `--report` to save details to file)
4. Fix issues: CRITICAL > MAJOR > MINOR (use `/cpv-fix-validation <report_path>`)
5. Re-run until exit code 0

## Output

- **Syntactic Score**: 0-100 numeric with tier (PASS / CONDITIONAL_PASS / FAIL)
- **Exit Code**: 0 (pass), 1 (CRITICAL), 2 (MAJOR), 3 (MINOR), 4 (NIT, --strict only). WARNING never blocks.
- **Summary**: Issue counts by severity level
- **Report File**: Full output saved to `docs_dev/validate_<plugin-name>_<date>.md`

> For **Semantic Quality Grading** (A-F letter grades), use `/cpv-semantic-validation`.

## Error Handling

- **Non-zero exit**: Report severity and failing checks. Do NOT publish until MAJOR/CRITICAL resolved.
- **Missing deps**: `uv pip install ruff mypy` or `brew install shellcheck`.
- **Invalid JSON/YAML**: Show parse error with path and line number.

## Examples

### Example 1: Validate a Plugin

```bash
uv run python scripts/validate_plugin.py /path/to/my-plugin --verbose --report docs_dev/validate_plugin_YYYYMMDD.md
```

### Example 2: Validate a Skill Only

```bash
uv run python scripts/validate_skill_comprehensive.py /path/to/skill-dir --strict --report docs_dev/validate_skill_YYYYMMDD.md
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
- [Troubleshooting](references/troubleshooting-python-scripts.md) - Common issues and fixes

## Token Optimization

- **ALWAYS use `--report <path>`** — #1 token saver; without it, verbose output floods context.
- **NEVER read the report file** — just provide the path to the user.
- **NEVER read plugin source files** — scripts handle reading internally.
- **Subagents DON'T inherit skills** — saves ~50K tokens per agent.
- **One script = one validation** — don't chain scripts.

## Related Commands

- `/cpv-fix-validation` — Fix issues from a validation report
- `/cpv-semantic-validation` — Deep semantic analysis (uses opus)
- `/cpv-validate-skill` — Single skill validation
- `/cpv-validate-hooks` — Hook-only validation

## Validation Checklist
Copy this checklist and track your progress:
- [ ] Run validate_plugin.py with --verbose
- [ ] Review CRITICAL and MAJOR issues
- [ ] Fix all blocking issues
- [ ] Re-run validation to confirm clean
