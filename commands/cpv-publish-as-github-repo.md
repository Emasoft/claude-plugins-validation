---
name: cpv-publish-as-github-repo
description: End-to-end: validate, standardize, create GitHub repo, push, and configure full CI/CD pipeline for a plugin (replaces cpv-setup-plugin-repo)
allowed-tools: Read, Bash(git:*,gh:*,uv:*), Write, Edit, Glob, Grep, AskUserQuestion
argument-hint: "<plugin-folder> [--owner <github-username>] [--marketplace <owner/marketplace-repo>]"
agent: plugin-creator
user-invocable: true
---

# /cpv-publish-as-github-repo

End-to-end command that takes a local plugin folder and publishes it as a complete GitHub repository with full CI/CD pipeline, ready for marketplace publishing.

> **Note:** This command replaces the former `/cpv-setup-plugin-repo`. It performs all the same operations (CI/CD setup, git hooks, marketplace notification) plus validation and standardization.

## Usage

```
/cpv-publish-as-github-repo ./my-plugin
/cpv-publish-as-github-repo ./my-plugin --owner MyGitHub
/cpv-publish-as-github-repo ./my-plugin --owner MyGitHub --marketplace MyGitHub/my-marketplace
```

## What It Does

### Phase 1: Validate the plugin
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/validate_plugin.py" <plugin-folder> --verbose
```
If CRITICAL or MAJOR issues exist, report them and ask the user to fix before continuing. Do NOT proceed with a broken plugin.

### Phase 2: Standardize the plugin repo structure
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <plugin-folder> --fix
```
This adds any missing standard files: .gitignore, .python-version, cliff.toml, .githooks/pre-push, .github/workflows/{ci,release,validate,notify-marketplace}.yml, scripts/publish.py, scripts/setup_git_hooks.py. It does NOT modify existing plugin code.

### Phase 3: Initialize git (if not already a git repo)
```bash
cd <plugin-folder>
git init
git add -A
git commit -m "Initial plugin scaffold"
```

### Phase 4: Create GitHub repository
Determine the owner (from --owner flag, or ask user, or from `gh api user -q .login`).
```bash
gh repo create <owner>/<plugin-name> --public --source . --push
```
If the repo already exists, ask the user whether to push to the existing repo.

### Phase 5: Configure CI/CD pipeline
```bash
# Set up git hooks (plugin may use either script name)
cd <plugin-folder>
if [ -f scripts/setup_git_hooks.py ]; then
  uv run python scripts/setup_git_hooks.py
elif [ -f scripts/setup-hooks.py ]; then
  uv run python scripts/setup-hooks.py
else
  echo "No hook setup script found — configure git hooks manually"
fi
```

### Phase 6: Configure marketplace notification (optional)
If --marketplace is provided:
1. Verify the marketplace repo exists: `gh repo view <marketplace-repo> --json name`
2. Ask user for a GitHub PAT with `repo` scope (or fine-grained with Contents R/W on marketplace repo)
3. Set secret: `gh secret set MARKETPLACE_PAT --repo <owner>/<plugin-name>`
4. Verify notify-marketplace.yml exists in .github/workflows/ (it should, from Phase 2)

### Phase 7: Final validation
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/validate_plugin.py" <plugin-folder> --verbose
```

### Phase 8: Report results

## Checklist (copy and track progress)

- [ ] Plugin validated (no CRITICAL/MAJOR issues)
- [ ] Standard files added (standardize_plugin.py --fix)
- [ ] Git initialized and committed
- [ ] GitHub repo created and pushed
- [ ] CI/CD workflows present (ci.yml, release.yml, validate.yml, notify-marketplace.yml)
- [ ] Git hooks configured (pre-push)
- [ ] Marketplace PAT secret set (if marketplace specified)
- [ ] Final validation passed

## Binary Plugins (Rust, Go, C/C++)

For plugins with compiled binaries (like perfect-skill-suggester):
1. Check for Cargo.toml, go.mod, Makefile, CMakeLists.txt
2. If found, add `.github/workflows/build-binaries.yml` from the plugin-binary-builds reference
3. The build workflow compiles for macOS (x86_64 + arm64), Linux (x86_64 + arm64), and Windows (x86_64)
4. Binaries are attached to GitHub releases

## Multi-Language Plugins

The command works for ALL plugin languages. The standardize script adds:
- Python: pyproject.toml, .python-version, ruff/mypy in CI
- JavaScript/TypeScript: Detected by package.json, eslint in CI
- Rust: Detected by Cargo.toml, clippy in CI
- Shell: Detected by *.sh scripts, shellcheck in CI
- Go: Detected by go.mod, staticcheck in CI

## Error Handling

| Error | Resolution |
|-------|------------|
| Plugin has CRITICAL issues | Report issues, ask user to fix |
| `gh auth` fails | `gh auth login` |
| Repo already exists | Ask: push to existing or rename? |
| PAT not provided | Skip marketplace, warn user |
| Push fails | Check branch protection, permissions |
