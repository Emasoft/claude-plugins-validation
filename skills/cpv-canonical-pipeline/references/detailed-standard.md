# Canonical Pipeline — Detailed Standard

## Table of Contents

- [Standard Plugin Files](#standard-plugin-files)
- [Standard CI/CD Workflows](#standard-cicd-workflows)
- [Git Hooks](#git-hooks)
- [Release Pipeline](#release-pipeline-scriptspublishpy)
- [Marketplace Standard](#marketplace-standard)
- [Language-Specific Additions](#language-specific-additions)

## Checklist

- [ ] Standard files present (plugin.json, README.md, LICENSE, CHANGELOG.md, .gitignore, pyproject.toml, cliff.toml)
- [ ] CI/CD workflows present (`.github/workflows/ci.yml` with lint + validate + test jobs)
- [ ] Pre-push git hook installed (`publish.py --install-hook`)
- [ ] `scripts/publish.py` present and executable
- [ ] Marketplace-side pieces configured if applicable
- [ ] Language-specific additions applied for the detected stack

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
| `.commitlintrc.json` | YES | Sets `"body-max-line-length": [0]` (v2.157.0). WITHOUT it, commitlint falls back to `@commitlint/config-conventional` (limit 100) and **every Dependabot PR fails CI forever** — its machine-generated body always exceeds 100 chars. The `type-enum` rule stays ON: a *human's* badly-typed commit must still fail. Detected by CIP-7. |
| `.cspell.json` | YES | Project dictionary (v2.157.0). Mega-Linter ships SPELL enabled, so WITHOUT a dictionary CI hard-errors on the plugin's own proper nouns (agent/skill names). **AUTHOR-OWNED**: `standardize --fix` augments, never clobbers — so it is deliberately NOT in `_FORCE_TEMPLATE_FILES`. It is a dictionary, not a mute button: a real typo must still fail. |
| `scripts/publish.py` | YES | 15-stage release pipeline + --gate mode + --install-hook. Stage 8/15 is the **CI-parity preflight**, run BEFORE the bump/commit/tag/push so a parity defect cannot strand a half-published repo — it runs only in the full pipeline, never in `--gate` mode. A missing tool degrades to WARNING and never false-blocks. |
| `git-hooks/pre-push` | YES | Thin bash delegator to `publish.py --gate` |
| `CHANGELOG.md` | YES | Auto-generated changelog |
| `LICENSE` | YES | License file (MIT recommended) |

## Standard CI/CD Workflows

| Workflow | Required | Triggers | Purpose |
|----------|----------|----------|---------|
| `.github/workflows/ci.yml` | YES | push, PR, merge_group | Consolidated: `lint` + `validate` + `test` jobs. Check-run names: `Lint`, `Validate`, `Test` (bare job display names — not `workflow / job` format). |
| `.github/workflows/release.yml` | YES | version tag (`v*`) | Create GitHub Release |
| `.github/workflows/notify-marketplace.yml` | Marketplace | release published | Notify marketplace via repository_dispatch |

> **v2.12.32**: `validate.yml` was merged into `ci.yml`. Plugins are expected to have ONE workflow file (`ci.yml`) with three jobs named `Lint`, `Validate`, `Test`. The `cpv-setup-branch-rules` CLI depends on those three bare job display names to build the required-status-checks rule.

### Binary Plugins (Rust, Go, C/C++)

Binary plugins keep sources in `src/<component>/` and pre-compiled binaries in `src/<component>/bin/`:
- Compilation happens **locally** via `publish.py` (NOT on GitHub CI)
- Binaries for all 5 platforms are committed alongside version bumps
- `build-binaries.yml` exists as a **fallback only** for CI-only environments

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
3. **Validate plugin** (remote CPV from GitHub, no local vendoring): `uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-remote-validate plugin . --strict` (blocks on CRITICAL/MAJOR/MINOR/NIT)
4. **Tests**: `uv run pytest tests/ -q` (Python) or `npx jest` (JS/TS)

### Setup

```bash
# Self-install via publish.py (preferred):
uv run python scripts/publish.py --install-hook
# Or manually:
git config core.hooksPath git-hooks
```

## Release Pipeline (`scripts/publish.py`)

### Gate mode (`--gate`)
Runs quality checks only, no version bump or push. Works **standalone** (a local
pre-release check) AND as the pre-push hook's delegate — the two contexts differ
only in what G0/G1 enforce (issue cpv#192):
- **G0**: Orchestrator check — applies **only while a push is in flight**
  (git-push ancestry). A standalone run pushes nothing, so there is nothing for
  G0 to protect and it is skipped with a note; a direct `git push` still
  carries push ancestry and is still BLOCKED. Undeterminable ancestry fails
  CLOSED (treated as a push).
- **G1**: Version bump check (local vs remote) — same principle: blocks in push
  context, informational note when standalone (pre-bump is the normal state
  before a release; the pipeline bumps later).
- **G2**: Lint (`ruff check scripts/`)
- **G3**: Validate (`--strict`, blocks on CRITICAL/MAJOR/MINOR/NIT)
- **G4**: Tests (`pytest tests/ -x -q`)

### Install mode (`--install-hook`)
Self-installs `git-hooks/pre-push` into `.git/hooks/` and sets `core.hooksPath`.

### Publish mode (`--patch`/`--minor`/`--major`, or no flag for auto-bump)
The 15-stage release pipeline (all fail-fast — any non-zero exit aborts),
plus 2 unnumbered post-release steps that run after the release is public
and can never abort it (`_PIPELINE_STAGES` in `generate_plugin_repo.py` is
the single source of truth — this list is derived from it, never retyped):

1. **Bypass guard**: reject `CPV_SKIP_*` / `SKIP_*` / `NO_VERIFY` env vars
2. **Check working tree is clean**: `git status --porcelain`
3. **Lint + type-check**: `uv run ruff check scripts/` + mypy
4. **Run tests**: `uv run pytest tests/ -q`
5. **Validate plugin (remote CPV)**: `uvx cpv-remote-validate plugin . --strict` (blocks on CRITICAL/MAJOR/MINOR/NIT)
6. **Secret scan**: trufflehog
7. **Linux fork-parity probe**: re-runs the suite forced to fork, the way Linux does it
8. **CI-parity preflight (remote CPV)**: `uvx cpv-remote-validate ci-preflight .` — runs ONLY in this full pipeline, never in `--gate` mode
9. **Marketplace-registration check**: Layout A — notify workflow + PAT secret + remote registration; Layout B — run from marketplace root + nested plugin listed
10. **Check version consistency**: all version sources must match (plugin.json, pyproject.toml)
11. **Bump version**: update plugin.json, pyproject.toml, `__version__` vars
12. **Update README version badge**: replace `version-X.Y.Z-blue` with new version
13. **Generate changelog**: `git-cliff -o CHANGELOG.md` (if git-cliff installed)
14. **Commit, tag, push**: `git commit`, `git tag vX.Y.Z`, `git push --tags`
15. **Create GitHub release**: `gh release create vX.Y.Z` (if gh CLI installed)

Post-release (unnumbered — never abort the publish):
- Verify CI is green on the released commit
- Prove the release installs

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
- CI: `ruff check`, `mypy`, `pytest` (linters only — CANON: no formatter, so no `ruff format`/`ruff format --check`; `ruff check --fix` is fine locally)

### JavaScript/TypeScript Plugins
- `package.json` with metadata and scripts
- `.node-version` (e.g., `22`)
- CI: `eslint`, `tsc --noEmit`, `jest`/`vitest` (linters only — CANON: no formatter, so no `prettier`/`prettier --check`; `eslint --fix` is fine locally)

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

## MCP Server Bundling

When a plugin bundles MCP server executables/scripts (referenced by the `command:` field in `.mcp.json` or inline `mcpServers`), place them in **`servers/`** at the plugin root.

This matches the official docs example (https://code.claude.com/docs/en/plugins-reference#mcp-servers):
```json
{
  "mcpServers": {
    "database-tools": {
      "command": "${CLAUDE_PLUGIN_ROOT}/servers/db-server",
      "args": ["--config", "${CLAUDE_PLUGIN_ROOT}/config.json"]
    }
  }
}
```

Rules:
- A plugin may declare MCP servers in any of: (A) `.mcp.json` at plugin root, (B) inline `mcpServers: {...}` in `plugin.json`, or (C) path-string `mcpServers: "./path/to/config.json"` in `plugin.json`. Multiple sources can coexist as long as **every server name is unique across ALL sources** (verified empirically 2026-04-18 — sources are loaded ADDITIVELY at runtime). Defining the same server name in two sources (e.g. `database-tools` declared in both `.mcp.json` and inline `plugin.json mcpServers`) causes silent inline-wins shadowing — CPV emits MAJOR per duplicate name.
- DO NOT set `mcpServers: "./.mcp.json"` (override pointing at default file). It is redundant — CC auto-loads `.mcp.json` automatically. CPV emits MINOR.
- The **server executables** referenced by `command:` live in `servers/` — `servers/db-server`, `servers/api-server.py`, `servers/index.js`, etc.
- Always reference them with `${CLAUDE_PLUGIN_ROOT}/servers/<name>` — never with bare relative paths.
- This is a **soft convention** for new plugins. Existing plugins with a different working layout (e.g. `bin/`, `src/servers/`) are not required to migrate.

## Empirical Validation Rules — DO NOT VIOLATE (verified 2026-04-18)

These five rules catch silent-failure modes in CC's plugin loader that `claude plugin validate` does NOT catch. CPV catches them all. Plugin authors should treat them as hard constraints.

| # | Rule | What CC does if violated | CPV severity |
|---|------|-------------------------|--------------|
| 1 | `agents` field never contains folder paths — only `.md` file paths | Validate-time: rejects with cryptic `Invalid input`. If validate skipped: silently drops the agents at runtime, no error in `--debug` | MAJOR |
| 2 | `hooks` override never points at `./hooks/hooks.json` (the default) | Validate passes silently. Runtime: `Duplicate hooks file detected` AND **disables the plugin's MCP servers** with `hook-load-failed` | MAJOR |
| 3 | MCP server names unique across `.mcp.json` + inline `plugin.json:mcpServers` | Silent inline-wins shadow at runtime. The `.mcp.json` declaration is dropped without warning | MAJOR per duplicate |
| 4 | LSP server names unique across `.lsp.json` + inline `plugin.json:lspServers` | Silent inline-wins shadow at runtime (verified via flag-touch probe) | MAJOR per duplicate |
| 5 | `mcpServers` field never points at `./.mcp.json` (the default) | Harmless single load (no cascade like hooks), but redundant and confusing | MINOR |

For full empirical evidence and 13 test plugin scenarios, see `skills/cpv-fix-validation/references/empirical-loading-bugs.md`.
