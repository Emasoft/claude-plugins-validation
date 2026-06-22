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

**Profile-aware.** The standardize flow first resolves the plugin's pipeline profile (`scripts/cpv_pipeline_profile.py`'s `resolve_pipeline_profile()` — manifest `cpv.pipeline_profile` overrides; fails safe to `standard`), then generates/expects the PROFILE-APPROPRIATE templates. Do NOT migrate a non-standard plugin to the plain standard shape: a **remote-validation** plugin keeps its remote `cpv-remote-validate` gate (no re-vendored validators), a **submodule-build** plugin keeps its build-source submodule + shipped `bin/` and submodule-aware `publish.py`, and a **binary-release** plugin keeps its SHA-pinned, least-privilege build/release workflow (SHA256SUMS + build matrix). The profile is a SELECTOR not a SUPPRESSOR — drift findings still fire against the profile's canon.

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
   - **Under `--force-templates`** (the canon UPGRADE verb, NOT plain `--fix`), ALL agents are migrated to the-skills-menu: each agent's frontmatter `skills:` is rewritten to `[the-skills-menu]` (every other field preserved) and the mandatory dynamic-loading instruction is inserted into its body; a per-plugin `skills/the-skills-menu/SKILL.md` catalog is created if absent (reusing the scaffold generator so it is byte-identical). The migration is idempotent (re-running is a no-op) and skips + reports any agent file lacking YAML frontmatter — see the `the-skills-menu-create` skill for the canonical rewrite rules. Plain `--fix` never touches an agent.
   - **Markdown-poison guardrail**: after editing any `.md`, reword a line-start `#` / `+ ` / `* ` prose continuation (markdownlint MD018/MD004 NIT blocks `--strict`).
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
- [ ] **For `--force-templates` runs**: every agent migrated to the-skills-menu (frontmatter `skills:` → `[the-skills-menu]` + dynamic-load body instruction) and `skills/the-skills-menu/SKILL.md` catalog present — see the `the-skills-menu-create` skill for the canonical rewrite rules
- [ ] Remaining issues fixed manually
- [ ] Re-validation passed
- [ ] **For migration runs (`/cpv-upgrade-plugin`)**: the 87-check matrix in the repo-root `references/canonical-pipeline-migration-checklist.md` (NOT inside this skill dir) returns exit 0 (every BLOCKER + MAJOR passes), AND a real `publish.py --patch` + `gh run watch --exit-status` returned green CI on the resulting tag. See the repo-root `agents/plugin-fixer.md` "Pre-completion verification (REQUIRED)". Closes [issue #21 ask #1](https://github.com/Emasoft/claude-plugins-validation/issues/21).

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
  > Pre-Push Hook: The Quality Gate · Fix-All Mandate · Running CPV Scripts · Processing Validation Output · GitHub Secrets · CI Workflow Dependencies · Superseded validate.yml Removal · Marketplace Notification · All Scripts Are Python · Binary Plugins · README Requirements · Pre-Publish Local Dry-Run · Post-Push CI Verification · Generated-Pipeline Reliability Contract (v2.134.0 — CPV ref pinned, integrity-skip + timeout, `Test` aggregate gate, notify no-op without secret, "done" = green CI) · Mega-Linter Configuration · Common Fixes Reference
- [Parallel scanning (v2.103.0+)](../canonical-pipeline/references/parallelism.md) — `standardize-plugin` re-runs validators after every fix; the v2.103.0+ rewrite makes each `validate_plugin` pass ~11.6× faster, so multi-iteration audits cost a fraction of pre-rewrite
  > Table of contents · Performance summary · Environment knobs (disable selectively for debugging) · Scaffolded plugins (created via `create-plugin` / `setup-plugin-repo`) · Batch commands (`cpv-batch-*`) · Remote validation (`cpv` remote-mode + scaffolded `publish.py`) · When to disable parallelism · See also

## Token Optimization

Use `mcp__plugin_llm-externalizer_llm-externalizer__*` tools for bounded analysis. Pass file paths via `input_files_paths`.
