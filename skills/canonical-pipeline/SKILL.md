---
name: canonical-pipeline
description: >
  Standard files, CI/CD, hooks, and release pipeline for Emasoft Claude Code plugins.
  Use when creating or auditing plugin repos. Loaded by plugin-creator, plugin-fixer, and marketplace-fixer agents.
user-invocable: false
---

# Canonical Plugin Pipeline Standard

## Overview

Defines the standard files, workflows, hooks, and release pipeline that every Emasoft Claude Code plugin repository MUST have. Covers Python, JavaScript/TypeScript, Rust, Go, and Shell plugins.

## Prerequisites

- `git`, `uv`, `gh` CLI on PATH
- CPV plugin installed (`claude-plugins-validation`)
- GitHub account with `repo` scope PAT (for marketplace notification)

## Instructions

1. **Create plugin repo**: Run `generate_plugin_repo.py` or use `/cpv-create`
2. **Verify standard files**: Check all required files exist per [Detailed Standard](references/detailed-standard.md#standard-plugin-files)
3. **Install CI/CD workflows**: Ensure `ci.yml` (consolidated lint + validate + test), `release.yml`, `notify-marketplace.yml` in `.github/workflows/`. `validate.yml` was merged into `ci.yml` in v2.12.32.
4. **Install pre-push hook**: `uv run python scripts/publish.py --install-hook`
5. **Validate** (from any directory — fetches CPV from GitHub, no local vendoring):
   `uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-remote-validate plugin . --strict`
6. **Fix ALL issues**: CRITICAL, MAJOR, MINOR, NIT must be resolved — only WARNINGs may remain
7. **Commit and push**: The pre-push hook enforces 4 gates (version bump, lint, validate, tests)

Copy this checklist and track your progress:
- [ ] Plugin repo created
- [ ] Standard files verified
- [ ] CI/CD workflows installed
- [ ] Pre-push hook installed
- [ ] Validation passed
- [ ] All issues fixed
- [ ] Committed and pushed

## Output

A fully configured plugin repository with:
- Plugin manifest (`plugin.json`) and project config
- CI/CD workflows for lint, validate, test, release, and marketplace notification
- Pre-push hook running `publish.py --gate` (4-gate quality enforcement)
- 11-stage release pipeline via `publish.py` (auto-bump via git-cliff; `--patch`/`--minor`/`--major` override the auto-detection)

## Error Handling

| Error | Resolution |
|-------|------------|
| Missing required file | Run `standardize_plugin.py --fix` to generate missing files |
| Pre-push hook not blocking | Run `publish.py --install-hook` to reinstall |
| CI failing on `uv sync` | Use `uv sync --extra dev` for ruff/pytest/mypy |
| Marketplace notification fails | Check `MARKETPLACE_PAT` secret and owner/repo placeholders |

## Examples

**Create and publish:**
```
/cpv-create
> Choose: Create a new plugin → then Publish to GitHub
```

**Standardize existing:**
```
/cpv-create
> Choose: Standardize an existing plugin
```

## Resources

- [Detailed Standard](references/detailed-standard.md) — complete tables for files, workflows, hooks, pipeline stages, marketplace, and language-specific additions
  > Standard Plugin Files · Standard CI/CD Workflows · Git Hooks · Release Pipeline (`scripts/publish.py`) · Marketplace Standard · Language-Specific Additions
- [Pipeline Rules](references/pipeline-rules.md) — mandatory rules for all plugin operations
  > Pre-Push Hook: The Quality Gate · Fix-All Mandate · Running CPV Scripts · Processing Validation Output · GitHub Secrets · CI Workflow Dependencies · Marketplace Notification · All Scripts Are Python · Binary Plugins · README Requirements · Pre-Publish Local Dry-Run · Post-Push CI Verification · Mega-Linter Configuration · Common Fixes Reference
- [v2.1.80+ Plugin Features](references/v2-1-80-features.md)
  > Monitor tool · userConfig (plugin.json) · channels (plugin.json) · CLAUDE_PLUGIN_OPTION_<KEY> env vars · Inline marketplace (settings.json) · managed-settings.d/ drop-in directory · Plugin skill `name` field (v2.1.98)
