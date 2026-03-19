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
| `scripts/publish.py` | YES | 9-stage release pipeline script |
| `.githooks/pre-push` | YES | Quality gate: validate + lint + test before push |
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

### pre-push (`.githooks/pre-push`)

Quality gate that runs before every push (follows PSS pattern — delegates to `publish.py --gate`):
1. Version bump check: blocks if local version matches remote (forces semver bump)
2. Lint: `uv run ruff check scripts/ tests/` (Python) or `npx eslint src/` (JS/TS)
3. Validate plugin: `uv run scripts/validate_plugin.py . --strict` (blocks on CRITICAL/MAJOR/MINOR)
4. Test: `uv run pytest tests/ -q` (Python) or `npx jest` (JS/TS)

If ANY step fails, the push is blocked. NITs are warnings only.

### Setup

```bash
git config core.hooksPath .githooks
# Or use the setup script if present:
uv run python scripts/setup_git_hooks.py
```

## Release Pipeline (`scripts/publish.py`)

The 12-stage release pipeline (follows PSS `pss_ship.py` pattern):

1. **Pre-flight checks**: Clean working tree, on main branch
2. **Lint**: `uv run ruff check scripts/ tests/`
3. **Validate plugin**: `uv run scripts/validate_plugin.py . --strict` (blocks on CRITICAL/MAJOR/MINOR)
4. **Run tests**: `uv run pytest tests/ -q`
5. **Version consistency**: Check all version sources match (plugin.json, pyproject.toml)
6. **Bump version**: Update plugin.json, pyproject.toml, `__version__` vars
7. **Update README badge**: Replace `version-X.Y.Z-blue` with new version
8. **Generate changelog**: `git-cliff --tag vX.Y.Z -o CHANGELOG.md` (if git-cliff installed)
9. **Build binaries**: Compile for current platform (if compiled sources exist, skip if no changes)
10. **Git commit**: `git commit -am "bump: version X.Y.Z → X.Y.Z"`
11. **Git tag**: `git tag vX.Y.Z`
12. **Push**: `git push && git push --tags`

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
/cpv-publish-as-github-repo ./my-plugin --owner MyGitHub
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
