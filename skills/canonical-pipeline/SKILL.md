---
name: canonical-pipeline
description: >
  Documents the canonical file structure, CI/CD workflows, git hooks, and release pipeline
  standard for all Emasoft Claude Code plugins and marketplaces. Use as a reference when
  creating, standardizing, or auditing plugin repositories.
---

# Canonical Plugin Pipeline Standard

This skill defines the standard files, workflows, and hooks that every Emasoft Claude Code plugin repository MUST have.

## Standard Plugin Files

| File | Required | Purpose |
|------|----------|---------|
| `.claude-plugin/plugin.json` | YES | Plugin manifest (name, version, description, components) |
| `pyproject.toml` | Python | Python project config (or `package.json` for JS/TS) |
| `.python-version` | Python | Runtime version pinning (e.g., `3.12`) |
| `.node-version` | JS/TS | Runtime version pinning (e.g., `22`) |
| `.gitignore` | YES | Must include `.claude/`, `.tldr/`, `llm_externalizer_output/` |
| `README.md` | YES | Must have `<!--BADGES-START-->` / `<!--BADGES-END-->` markers |
| `cliff.toml` | YES | git-cliff changelog configuration |
| `scripts/publish.py` | YES | 10-stage release pipeline + --gate mode + --install-hook |
| `git-hooks/pre-push` | YES | Thin bash delegator to `publish.py --gate` |
| `CHANGELOG.md` | YES | Auto-generated changelog |
| `LICENSE` | YES | License file (MIT recommended) |

## Standard CI/CD Workflows

| Workflow | Required | Triggers | Purpose |
|----------|----------|----------|---------|
| `.github/workflows/ci.yml` | YES | push, PR | Lint + validate + test |
| `.github/workflows/release.yml` | YES | version tag (`v*`) | Create GitHub Release |
| `.github/workflows/validate.yml` | YES | push, PR | Run CPV validation |
| `.github/workflows/notify-marketplace.yml` | Marketplace | release published | Notify marketplace via repository_dispatch |

### Binary Plugins (Rust, Go, C/C++)

Binary plugins keep sources in `src/<component>/` and pre-compiled binaries in `src/<component>/bin/`:
- Compilation happens **locally** via `publish.py` (NOT on GitHub CI)
- Binaries for all 5 platforms are committed alongside version bumps
- `build-binaries.yml` exists as a **fallback only** for CI-only environments

| Workflow | Triggers | Purpose |
|----------|----------|---------|
| `.github/workflows/build-binaries.yml` | manual / source changes | FALLBACK cross-compilation (prefer local builds) |

## Git Hooks

### pre-push (`git-hooks/pre-push`)

Thin bash delegator that calls `publish.py --gate` (follows PSS pattern — one script, two modes):

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"
uv run python scripts/publish.py --gate
```

The `--gate` mode runs 4 quality gates:
1. **Version bump check**: blocks if local version matches remote (forces semver bump)
2. **Lint**: `uv run ruff check scripts/` (Python) or `npx eslint src/` (JS/TS)
3. **Validate plugin**: `uv run scripts/validate_plugin.py . --strict` (blocks on CRITICAL/MAJOR/MINOR/NIT)
4. **Tests**: `uv run pytest tests/ -q` (Python) or `npx jest` (JS/TS)

If ANY gate fails, the push is blocked. WARNINGs are allowed through.

### Setup

```bash
# Self-install via publish.py (preferred):
uv run python scripts/publish.py --install-hook
# Or manually:
git config core.hooksPath git-hooks
```

## Release Pipeline (`scripts/publish.py`)

The publish script supports three modes:

### Gate mode (`--gate`)
Called by the pre-push hook. Runs quality checks only, no version bump or push:
- **G1**: Version bump check (local vs remote)
- **G2**: Lint (`ruff check scripts/`)
- **G3**: Validate (`--strict`, blocks on CRITICAL/MAJOR/MINOR/NIT)
- **G4**: Tests (`pytest tests/ -x -q`)

### Install mode (`--install-hook`)
Self-installs `git-hooks/pre-push` into `.git/hooks/` and sets `core.hooksPath`.

### Publish mode (`--patch`/`--minor`/`--major`)
The 10-stage release pipeline (follows PSS `pss_ship.py` pattern):

1. **Pre-flight checks**: Clean working tree
2. **Lint**: `uv run ruff check scripts/`
3. **Validate plugin**: `--strict` (blocks on CRITICAL/MAJOR/MINOR/NIT)
4. **Run tests**: `uv run pytest tests/ -q`
5. **Version consistency**: Check all version sources match (plugin.json, pyproject.toml)
6. **Bump version**: Update plugin.json, pyproject.toml, `__version__` vars
7. **Update README badge**: Replace `version-X.Y.Z-blue` with new version
8. **Generate changelog**: `git-cliff -o CHANGELOG.md` (if git-cliff installed)
9. **Commit, tag, push**: `git commit`, `git tag vX.Y.Z`, `git push --tags`
10. **GitHub release**: `gh release create vX.Y.Z` (if gh CLI installed)

After push, GitHub handles:
- **GitHub Release**: Triggered by tag via `release.yml`
- **Marketplace notification**: Triggered by release via `notify-marketplace.yml`

## Marketplace Standard

Marketplaces follow the **hub-only architecture**:
- NO plugin code inside the marketplace repo
- `marketplace.json` with GitHub source pointers: `{"source": "github", "repo": "owner/name"}`
- Each plugin lives in its OWN GitHub repo

### Marketplace Files

| File | Required | Purpose |
|------|----------|---------|
| `.claude-plugin/marketplace.json` | YES | Plugin registry (GitHub source pointers only) |
| `README.md` | YES | Auto-generated plugin catalog |
| `scripts/update_catalog.py` | YES | Regenerate README from marketplace.json |
| `.github/workflows/validate.yml` | YES | Validate marketplace on push/PR |
| `.github/workflows/update-catalog.yml` | YES | Auto-update README when marketplace.json changes |
| `.githooks/pre-push` | YES | Quality gate |
| `cliff.toml` | YES | Changelog configuration |

## Language-Specific Additions

### Python Plugins
- `pyproject.toml` with `[project]` metadata and `[tool.ruff]` config
- `.python-version` (e.g., `3.12`)
- CI: `ruff check`, `ruff format --check`, `mypy`, `pytest`

### JavaScript/TypeScript Plugins
- `package.json` with metadata and scripts
- `.node-version` (e.g., `22`)
- CI: `eslint`, `prettier --check`, `tsc --noEmit`, `jest`/`vitest`

### Rust Plugins
- `Cargo.toml` with metadata
- CI: `cargo clippy`, `cargo fmt --check`, `cargo test`
- `build-binaries.yml` for cross-compilation

### Go Plugins
- `go.mod` with module path
- CI: `go vet`, `staticcheck`, `go test`
- `build-binaries.yml` for cross-compilation

### Shell Plugins
- Scripts in `scripts/` or `bin/`
- CI: `shellcheck` on all `.sh` files

## Pipeline Rules

See [Pipeline Rules](references/pipeline-rules.md) for the full set of mandatory rules covering:
- Pre-push hook 4-gate enforcement (version bump, lint, validate --strict, tests)
- Fix-all mandate (only WARNINGs may remain before publishing)
- Running CPV scripts (`uv run --with pyyaml python`)
- Processing validation output (strip ANSI, use `grep -oE`)
- GitHub secrets (`--body` flag mandatory)
- Marketplace notification configuration
- README requirements (badges, components, install/uninstall, troubleshooting)
- Common fixes reference table

## Quick Reference

### Create a new plugin (local)
```
/cpv-create-local-plugin
```

### Publish plugin to GitHub
```
/cpv-publish-a-plugin-as-github-repo ./my-plugin --owner MyGitHub
```

### Standardize an existing repo
```
/cpv-standardize ./my-plugin --fix
```

### Create a marketplace
```
/cpv-create-github-marketplace MyGitHub/my-marketplace
```

### Publish to marketplace
```
/cpv-publish-plugin-to-marketplace MyGitHub/my-plugin --marketplace MyGitHub/my-marketplace
```
