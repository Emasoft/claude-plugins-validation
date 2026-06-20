---
name: canonical-pipeline
description: >
  Standard files, CI/CD, hooks, and release pipeline for Emasoft Claude Code plugins.
  Use when creating or auditing plugin repos. Used dynamically via the-skills-menu — any CPV agent can invoke at runtime (TRDD-478d9687).
user-invocable: false
---

# Canonical Plugin Pipeline Standard

## Overview

Defines the standard files, workflows, hooks, and release pipeline that every Emasoft Claude Code plugin repository MUST have. Covers Python, JavaScript/TypeScript, Rust, Go, and Shell plugins. Pipeline supports all three CPV layouts (A: separate plugin and marketplace repos; B: nested monorepo; C: marketplace-in-plugin self-referential single repo).

### Profile-aware

The pipeline comes in four PROFILES, auto-detected from repo shape (or set authoritatively via the manifest `cpv.pipeline_profile`; detection fails safe to `standard`) by `scripts/cpv_pipeline_profile.py`'s `resolve_pipeline_profile()`. The profile is a SELECTOR (which canon a file is compared against), never a SUPPRESSOR — every drift finding still fires:

- **standard** — vendors the CPV validators and ships the standard `gen_*` workflows.
- **remote-validation** — de-vendors the local validators; validation is ONLY the remote `cpv-remote-validate --strict` gate (its absence is the point).
- **submodule-build** — build sources live in a git submodule, pre-built binaries are committed to `bin/`, and `publish.py` is submodule-aware.
- **binary-release** — ships compiled binaries as RELEASE assets via a build matrix + stage script + CI smoke job + `SHA256SUMS`.

### Layout C specifics

Layout C (marketplace-in-plugin) needs a different release pipeline:
- `publish.py` bumps THREE version slots atomically: `plugin.json::version`, `marketplace.json::metadata.version`, AND the self-entry's `version` in `marketplace.json::plugins[]`.
- `notify-marketplace.yml` is NOT installed (no separate marketplace repo).
- A single tag `vX.Y.Z` covers both manifest changes.
- `validate_marketplace.py --strict` runs alongside `validate_plugin.py --strict` in CI.

## Prerequisites

- `git`, `uv`, `gh` CLI on PATH
- CPV plugin installed (`claude-plugins-validation`)
- GitHub account with `repo` scope PAT (for marketplace notification)

## Instructions

1. **Create plugin repo**: Run `generate_plugin_repo.py` or use `/cpv-main-menu` → Create
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
/cpv-main-menu
> Choose: 6 — Create → Create a new plugin → then Publish to GitHub
```

**Standardize existing:**
```
/cpv-main-menu
> Choose: 8 — Manage → Standardize plugin
```

## Resources

- [Detailed Standard](references/detailed-standard.md) — complete tables for files, workflows, hooks, pipeline stages, marketplace, and language-specific additions
  > Standard Plugin Files · Standard CI/CD Workflows · Git Hooks · Release Pipeline (`scripts/publish.py`) · Marketplace Standard · Language-Specific Additions
- [Pipeline Rules](references/pipeline-rules.md) — mandatory rules for all plugin operations
  > Pre-Push Hook: The Quality Gate · Fix-All Mandate · Running CPV Scripts · Processing Validation Output · GitHub Secrets · CI Workflow Dependencies · Marketplace Notification · All Scripts Are Python · Binary Plugins · README Requirements · Pre-Publish Local Dry-Run · Post-Push CI Verification · Generated-Pipeline Reliability Contract (v2.134.0 — CPV ref pinned, integrity-skip + timeout, `Test` aggregate gate, notify no-op without secret, "done" = green CI) · Mega-Linter Configuration · Common Fixes Reference
- [Pipeline Standards (current)](references/pipeline-standards.md) — the standards every newly-scaffolded plugin ships with
  > Overview · Whole-repo lint via cpv_lint_engine · Idempotent publish.py · validate_pipeline_script_refs rule · Cross-platform scripts — no bash, no jq/sed/awk · Input sanitization — every script parameter · Hooks MUST persist state in CLAUDE_PLUGIN_DATA, never CLAUDE_PLUGIN_ROOT · Hook commands MUST be cross-platform (Python-delegated) · PEP 723 scripts MUST be invoked via uv run · Migrating a legacy plugin
- [v2.1.80+ Plugin Features](references/v2-1-80-features.md)
  > Monitor tool · userConfig (plugin.json) · channels (plugin.json) · CLAUDE_PLUGIN_OPTION_<KEY> env vars · Inline marketplace (settings.json) · managed-settings.d/ drop-in directory · Plugin skill `name` field (v2.1.98)
- [Parallel scanning (v2.103.0+)](references/parallelism.md) — every CPV validator runs file scans in parallel by default; scaffolded plugins inherit ~11.6× speedup automatically via `uvx --from git+...` remote-mode
  > Table of contents · Performance summary · Environment knobs (disable selectively for debugging) · Scaffolded plugins (created via `create-plugin` / `setup-plugin-repo`) · Batch commands (`cpv-batch-*`) · Remote validation (`cpv` remote-mode + scaffolded `publish.py`) · When to disable parallelism · See also
- [Scan cache (v2.104.0+)](references/scan-cache.md) — content-hash-keyed SQLite cache of skillaudit findings; ~50× speedup on repeat runs; 5-level storage path resolution; survives plugin updates via `$CLAUDE_PLUGIN_DATA`
  > Table of contents · Overview · Storage path resolution chain · SQLite schema · Security invariants · Env-vars · GitHub Actions integration · Pruning (age + LRU) · Introspection helpers · When to invalidate manually · What the cache does NOT cache · Performance characteristics · See also
- [Binary scanning (v2.104.0+)](references/binary-scanning.md) — binary-aware STRATEGY replacing the dangerous "skip binaries" anti-pattern; runs the full skillaudit catalog on ASCII / UTF-16 strings + recursive decode chain (base64 / hex / gzip / zlib); never skips a file
  > Table of contents · The security principle: NEVER skip a file · Detection · Scanning pipeline · Edge cases (ALL logged, NEVER silent) · Env-var · What this scanner does NOT do · Coverage gain vs current text-only behavior · Performance characteristics · Future work
- Migration exit gate: the repo-root `references/canonical-pipeline-migration-checklist.md` (CPV ships it at the repo root, NOT inside this skill dir)
