---
name: cpv-publish-as-github-repo
description: "End-to-end: validate, standardize, create GitHub repo, push, and configure full CI/CD pipeline for a plugin (replaces cpv-setup-plugin-repo)"
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
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_plugin.py" <plugin-folder> --verbose --strict 2>&1 | sed 's/\x1b\[[0-9;]*m//g'
```
**IMPORTANT**: Always use `--with pyyaml` when running CPV scripts from outside the CPV project venv. Always strip ANSI codes with `sed` when processing output. Use `grep -oE` (NOT `-oP`) for macOS compatibility.

### Phase 1b: FIX ALL ISSUES (mandatory)
**DO NOT skip this phase. DO NOT proceed to Phase 3+ with ANY non-WARNING issues.**

The pre-push hook runs `--strict` and blocks on CRITICAL, MAJOR, MINOR, and NIT. If you don't fix them now, the push will be blocked later.

For each issue found in Phase 1:
1. **CRITICAL/MAJOR/MINOR/NIT** → YOU MUST FIX THEM. Read the offending file, understand the issue, apply the fix.
2. **WARNING** → These are advisory only. Note them but do not block on them.

Common fixes:
- **SKILL.md missing Nixtla sections** (Overview, Prerequisites, Output, Error Handling, Examples, Resources) → Add the missing `## Section` headings with appropriate content to the SKILL.md file
- **SKILL.md missing numbered step-by-step** → Add numbered list under `## Instructions`
- **Missing .gitignore entries** → Append the missing patterns to .gitignore
- **Missing README badges** → Add `<!--BADGES-START-->` / `<!--BADGES-END-->` with badge markdown
- **Missing LICENSE** → Create MIT LICENSE file
- **Shell script not executable** → `chmod +x <script>`
- **Absolute paths in scripts** → Replace with `${CLAUDE_PLUGIN_ROOT}` or relative paths (or document as intentional for system binaries like `/usr/bin/docker`)
- **Missing author.email** → Add `"email": "nnn+user@users.noreply.github.com"` to plugin.json author object
- **Ruff lint errors** → Run `uv run ruff check --fix scripts/` then manually fix remaining
- **Broken backtick paths** → Fix or remove the reference

After fixing, re-run validation. Repeat until ONLY warnings remain:
```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_plugin.py" <plugin-folder> --strict 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | tail -5
```
The last line must say `✔ All checks passed` or `WARNING issues only`. If it says CRITICAL/MAJOR/MINOR, keep fixing.

### Phase 2: Standardize the plugin repo structure
```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <plugin-folder> --fix
```
This adds any missing standard files: .gitignore, .python-version, cliff.toml, .githooks/pre-push, .github/workflows/{ci,release,validate,notify-marketplace}.yml, scripts/publish.py, scripts/setup_git_hooks.py. It does NOT modify existing plugin code.

After standardize, fix any remaining .gitignore gaps (standardize warns but does not auto-add all entries — you must append them manually).

### Phase 2b: Generate README component tables
Scan the plugin directory for components and fill the README placeholders:
- Scan `commands/*.md` frontmatter → generate `| Command | Description |` table
- Scan `agents/*.md` frontmatter → generate `| Agent | Description |` table
- Scan `skills/*/SKILL.md` frontmatter → generate `| Skill | Description |` table
- Read `hooks/hooks.json` (if present) → list hook events and their purpose
- Write actual usage examples for each command

### Phase 2c: Re-validate after all fixes
```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_plugin.py" <plugin-folder> --strict 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | tail -5
```
**GATE CHECK**: Only proceed to Phase 3 if NO CRITICAL, MAJOR, MINOR, or NIT issues remain. Warnings are OK.

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
2. Update `notify-marketplace.yml` — edit `MARKETPLACE_OWNER` and `MARKETPLACE_REPO` env vars to match the `--marketplace` argument (e.g., `Emasoft` and `emasoft-plugins`)
3. Check if `$MARKETPLACE_PAT` env var is already set: `test -n "$MARKETPLACE_PAT" && echo "PAT found" || echo "PAT not set"`
   - If set: use it directly
   - If not set: ask user for a GitHub PAT with `repo` scope (or fine-grained with Contents R/W on marketplace repo)
4. Set secret: `gh secret set MARKETPLACE_PAT --repo <owner>/<plugin-name> --body "$MARKETPLACE_PAT"` (MUST use `--body` flag — piping does NOT work reliably)
4. Verify notify-marketplace.yml exists in .github/workflows/ (it should, from Phase 2)

### Phase 7: Final validation
```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_plugin.py" <plugin-folder> --verbose --strict 2>&1 | sed 's/\x1b\[[0-9;]*m//g'
```
**This MUST show only WARNINGs or all-clear.** If any CRITICAL/MAJOR/MINOR/NIT remain, go back and fix them before considering the publish complete. The pre-push hook will block future pushes on these same issues.

### Phase 8: Report results
Report to the user:
- Severity counts table (PASSED, CRITICAL, MAJOR, MINOR, NIT, WARNING)
- List of any remaining WARNINGs (advisory only)
- GitHub repo URL
- Marketplace notification status

## Checklist (copy and track progress)

- [ ] Plugin validated with --strict (zero CRITICAL/MAJOR/MINOR/NIT)
- [ ] ALL issues fixed (SKILL.md sections, .gitignore, badges, LICENSE, lint)
- [ ] Standard files added (standardize_plugin.py --fix)
- [ ] README has Components table, Install/Uninstall, Troubleshooting
- [ ] Re-validated clean (only WARNINGs remain)
- [ ] Git initialized and committed
- [ ] GitHub repo created and pushed
- [ ] CI/CD workflows present (ci.yml, release.yml, validate.yml, notify-marketplace.yml)
- [ ] Git hooks configured (pre-push with --strict)
- [ ] notify-marketplace.yml MARKETPLACE_OWNER/MARKETPLACE_REPO updated
- [ ] Marketplace PAT secret set (if marketplace specified)
- [ ] Final validation passed (--strict, only WARNINGs)

## Binary Plugins (Rust, Go, C/C++)

For plugins with compiled binaries (follows the perfect-skill-suggester pattern):
1. Check for Cargo.toml, go.mod, Makefile, CMakeLists.txt
2. Sources MUST live in `src/<component>/` (e.g., `src/skill-suggester/`)
3. Pre-compiled binaries go in `src/<component>/bin/<name>-<platform>-<arch>`
4. Compilation happens **locally** via `publish.py` — NOT on GitHub CI
5. Add `.github/workflows/build-binaries.yml` as a **fallback only** for CI-only environments
6. Binaries are committed alongside version bumps and pushed with the code

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
