---
name: cpv-plugin-validation-skill
description: Validates Claude Code plugins for structural correctness, quality, and marketplace readiness. Use when validating, fixing, migrating, upgrading, or scaffolding a plugin. Used dynamically via cpv-the-skills-menu (TRDD-478d9687). Embeds canonical plugins-reference.md.
tags: [validation, plugins, marketplace, hooks, skills, mcp, quality-assurance]
user-invocable: false
---

# Plugin Validation Skill

## Phase 0 — plugin-shape detection (MANDATORY before any other action)

Before validating, fixing, migrating, upgrading, or scaffolding ANY directory, the agent MUST run [shape-detection](references/shape-detection.md) to verify the directory IS a plugin. If `.claude-plugin/plugin.json` is missing, the agent MUST refuse to scaffold a plugin around a SKILL.md / single agent / loose commands / unknown folder, and instead ABORT and ask the user whether to "wrap into a NEW plugin" or "ADD to an existing plugin". Hard refusal protocol + detection table in the reference.

The canonical plugin layout, manifest schema, env vars, caching rules, and CLI commands are EMBEDDED verbatim from the official doc at [plugins-reference](references/plugins-reference.md). Always read it BEFORE deciding any plugin shape question.

## Overview

Validates Claude Code plugins against 190+ structural and quality rules covering manifests, hooks, skills, MCP servers, marketplace configs, and agents. Produces a severity-graded report with actionable fix guidance.

## Prerequisites

- Python 3.12+ with `pyyaml`, `uv` package manager
- Plugin directory with valid structure (`.claude-plugin/plugin.json` — see Phase 0)

## Instructions

1. Run [Phase 0 plugin-shape detection](references/shape-detection.md). If NOT a plugin → ABORT and ask the user.
2. Run via the launcher (see [launcher-invocation](references/launcher-invocation.md) — NEVER call `validate_plugin.py` directly).
   > Why the launcher is mandatory · The one-liner (use this verbatim) · Full alias table · Direct invocation (development only)
3. Review compact summary; always use `--report` to save details.
4. Fix issues CRITICAL > MAJOR > MINOR (`/cpv-fix-validation <report_path>`).
5. Re-run until exit code 0.

## Output

- Exit codes: 0 (pass), 1 (CRITICAL), 2 (MAJOR), 3 (MINOR), 4 (NIT, --strict). WARNING never blocks.
- Report at `$MAIN_ROOT/reports/validate_plugin/<TS>-<slug>.md`.
- For A-F semantic grades use `/cpv-semantic-validation`.

## Error Handling

- Non-zero exit → fix before publish.
- Missing deps → `uv pip install ruff mypy` or `brew install shellcheck`.
- Invalid JSON/YAML → show parse error with path and line number.

## Examples

- Input: `... plugin ~/Code/my-plugin/ --report ...` → `PASS. CRITICAL=0 MAJOR=0`
- Input: `... skill ./skills/my-skill/ --strict --report ...` → `PASS. Score 85/100`

## Resources

- [Plugins Reference](references/plugins-reference.md) — official doc embedded verbatim
  > Plugin components reference · Plugin installation scopes · Plugin manifest schema · Plugin caching and file resolution · Plugin directory structure · CLI commands reference · Debugging and development tools · Distribution and versioning reference · See also
- [Skills Reference](references/skills-reference.md) — official skills doc; consult when Phase 0 detects "this is a SKILL"
- [Shape Detection](references/shape-detection.md) — Phase 0 detection table + hard-refusal protocol
  > Why this rule exists · Detection table — root-folder signals to verdict · Hard refusal protocol · Standard plugin layout · Path-variable rules — ${CLAUDE_PLUGIN_ROOT} vs ${CLAUDE_PLUGIN_DATA} · Custom non-standard root entries · Common mis-classification patterns · Verifier: ten checks before marking as plugin
- [Validation Checklist](references/validation-checklist.md)
  > 1. Plugin Manifest Checklist · 2. Plugin Structure Checklist · 3. Hook Configuration Checklist · 4. Skill Validation Checklist · 5. MCP Server Checklist · 6. Marketplace Checklist · 7. Agent Checklist · 8. LSP Server Checklist · 9. Script and Code Quality Checklist · 10. Pre-Release Final Checklist · 11. Validation Commands
- [Plugin Structure](references/plugin-structure.md)
  > 1. Directory Structure · 2. Plugin Manifest (plugin.json) · 3. Component Placement Rules · 4. Path Variables · 5. Common Structure Errors · 6. Validation Checklist
- [Hook Validation](references/hook-validation.md)
  > 1. Hook Configuration File · 2. Valid Hook Events · 3. Matcher Syntax · 4. Hook Types · 5. Hook Input/Output Format · 6. Script Requirements · 7. Common Hook Errors · 8. Validation Checklist

## Checklist

Copy this checklist and track your progress:

- [ ] Phase 0: confirm `.claude-plugin/plugin.json` exists (else ABORT)
- [ ] Run launcher with `--verbose --report`
- [ ] Fix CRITICAL > MAJOR > MINOR
- [ ] Re-run until exit 0
