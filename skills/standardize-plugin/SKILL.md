---
name: standardize-plugin
description: >
  Audit and fix plugin/marketplace repos to match CPV standards.
  Use when standardizing or auditing repo structure. Used dynamically via the-skills-menu (TRDD-478d9687).
user-invocable: false
---

# Standardize Plugin / Marketplace

## Overview

Audits existing plugin or marketplace repositories against CPV standards and auto-fixes missing files, workflows, and hooks.

## Prerequisites

- `uv` on PATH
- CPV plugin installed
- Target repository accessible on disk

## Instructions

1. **Audit a Plugin**:
   ```bash
   uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" standardize <plugin-path> [--report report.md]
   ```
   Checks: 190+ validation rules, pipeline readiness, file inventory, .gitignore, README badges.

2. **Fix a Plugin** (generates missing files, does NOT modify existing code):
   ```bash
   uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" standardize <plugin-path> --fix [--dry-run]
   ```

3. **After --fix**, manually fix remaining issues:
   - .gitignore gaps, SKILL.md missing Nixtla sections, README badges, MINOR/NIT issues
   - Pre-push hook blocks on CRITICAL, MAJOR, MINOR, NIT — only WARNINGs pass
   - **Empirical-loading-bugs MAJORs** (added 2026-04-18) need manual fixes — see `skills/canonical-pipeline/references/detailed-standard.md` "Empirical Validation Rules":
     - `agents` field with folder paths → list specific `.md` files instead
     - `hooks: "./hooks/hooks.json"` → remove the field (default file auto-loads) or point at non-default path
     - MCP/LSP same server name in 2 sources → consolidate into one source per name
   - **Layout C consistency findings** (when both `plugin.json` and `marketplace.json` exist at the repo root):
     - `plugin.json.name` ≠ marketplace self-entry name → align both
     - `plugin.json.version` ≠ marketplace self-entry version → align (publish.py should bump both atomically)
     - Self-entry source ≠ `"./"` → set to `"./"`
   - **Current pipeline standards** — load `fix-validation` skill's `pipeline-migration.md` for the full conversion recipes:
     - §1 dangling script refs · §2 lint engine consolidation · §3 cross-platform Python (bash → Python, os.path → pathlib, hook commands) · §4 idempotent publish.py · §5 input sanitization (no shell=True; regex-validate every CLI flag / env-var / JSON field at the boundary)

Copy this checklist and track your progress:
- [ ] Audit report reviewed
- [ ] `--fix` applied
- [ ] Remaining issues fixed manually
- [ ] Re-validation passed
- [ ] **For migration runs (`/cpv-upgrade-plugin`)**: the 82-check matrix in `references/canonical-pipeline-migration-checklist.md` returns exit 0 (every BLOCKER + MAJOR passes), AND a real `publish.py --patch` + `gh run watch --exit-status` returned green CI on the resulting tag. See `agents/plugin-fixer.md` "Pre-completion verification (REQUIRED)". Closes [issue #21 ask #1](https://github.com/Emasoft/claude-plugins-validation/issues/21).

4. **Audit/Fix a Marketplace**:
   ```bash
   uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" standardize_marketplace <path> [--fix] [--dry-run]
   ```

## Output

- Audit report listing all standard files: present, missing, or needs update
- After `--fix`: generated files (workflows, hooks, cliff.toml, .python-version, badge markers)
- Exit code 1 after `--fix` is expected if warnings remain

## Error Handling

| Error | Resolution |
|-------|------------|
| `standardize exit code 1` | Expected after --fix if warnings remain — fix manually |
| Missing `plugin.json` | Target is not a plugin — check path |
| `ModuleNotFoundError: yaml` | Use `uv run --with pyyaml python` |

## Examples

**Audit:**
```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" standardize ./my-plugin/
```

**Fix and re-validate:**
```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" standardize ./my-plugin/ --fix
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" plugin ./my-plugin/ --strict
```

## Resources

- [Pipeline Rules](references/pipeline-rules.md) — mandatory rules for all plugin operations
  > Pre-Push Hook: The Quality Gate · Fix-All Mandate · Running CPV Scripts · Processing Validation Output · GitHub Secrets · CI Workflow Dependencies · Marketplace Notification · All Scripts Are Python · Binary Plugins · README Requirements · Pre-Publish Local Dry-Run · Post-Push CI Verification · Mega-Linter Configuration · Common Fixes Reference
- [Parallel scanning (v2.103.0+)](../canonical-pipeline/references/parallelism.md) — `standardize-plugin` re-runs validators after every fix; the v2.103.0+ rewrite makes each `validate_plugin` pass ~11.6× faster, so multi-iteration audits cost a fraction of pre-rewrite
  > Table of contents · Performance summary · Environment knobs (disable selectively for debugging) · Scaffolded plugins (created via `create-plugin` / `setup-plugin-repo`) · Batch commands (`cpv-batch-*`) · Remote validation (`cpv` remote-mode + scaffolded `publish.py`) · When to disable parallelism · See also

## Token Optimization

Use `mcp__plugin_llm-externalizer_llm-externalizer__*` tools for bounded analysis. Pass file paths via `input_files_paths`.
