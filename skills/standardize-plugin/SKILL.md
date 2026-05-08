---
name: standardize-plugin
description: >
  Audit and fix plugin/marketplace repos to match CPV standards.
  Use when standardizing or auditing repo structure. Loaded by plugin-creator agent.
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
   - **Current pipeline standards** — when the plugin has any of these:
     - `[MAJOR] Dangling reference to scripts/<name>.py` from validate_pipeline_script_refs → load fix-validation skill's pipeline-migration reference §1 (replace removed lint script with cpv_lint_engine in CI; drop from pre-push hook)
     - Legacy lint script still present → load pipeline-migration reference §2 (delete it; consolidate to cpv_lint_engine)
     - publish.py lacks `_read_remote_version` / `_infer_bump_type` / `_git_porcelain_clean` helpers → load pipeline-migration reference §3 (regenerate via gen_publish_py, or surgically add the 5 helpers + idempotent guards)

Copy this checklist and track your progress:
- [ ] Audit report reviewed
- [ ] `--fix` applied
- [ ] Remaining issues fixed manually
- [ ] Re-validation passed

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

## Token Optimization

Use `mcp__plugin_llm-externalizer_llm-externalizer__*` tools for bounded analysis. Pass file paths via `input_files_paths`.
