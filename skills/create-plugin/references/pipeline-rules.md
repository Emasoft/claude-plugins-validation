# Pipeline Rules — Mandatory for ALL Plugin Operations

## Table of Contents

- [Pre-Push Hook: The Quality Gate](#pre-push-hook-the-quality-gate)
- [Fix-All Mandate](#fix-all-mandate)
- [Running CPV Scripts](#running-cpv-scripts)
- [Processing Validation Output](#processing-validation-output)
- [GitHub Secrets](#github-secrets)
- [CI Workflow Dependencies](#ci-workflow-dependencies)
- [Marketplace Notification](#marketplace-notification)
- [All Scripts Are Python](#all-scripts-are-python)
- [Binary Plugins](#binary-plugins)
- [README Requirements](#readme-requirements)
- [Pre-Publish Local Dry-Run](#pre-publish-local-dry-run)

## Checklist

- [ ] Pre-push hook installed and enforcing --strict
- [ ] All findings above WARNING fixed before push
- [ ] CPV scripts invoked with `uv run --with pyyaml python`
- [ ] GitHub secrets set via `set_marketplace_pat.py` (never pipe to `gh secret set`)
- [ ] Plugin scripts are all Python (no ad-hoc shell)
- [ ] README has install/update/uninstall sections
- [ ] Dry-run passes before the first real publish
- [Post-Push CI Verification](#post-push-ci-verification)
- [Mega-Linter Configuration](#mega-linter-configuration)
- [Common Fixes Reference](#common-fixes-reference)

These rules MUST be followed by every agent, command, and skill that creates, publishes, standardizes, or fixes a plugin repository.

## Pre-Push Hook: The Quality Gate

The pre-push hook is the **keystone of the entire pipeline**. It runs 4 gates in sequence and blocks the push if ANY gate fails:

| Gate | What It Does | Blocks On |
|------|-------------|-----------|
| 1. Version bump | Compares local vs remote version | Same version (must bump) |
| 2. Lint | `ruff check scripts/ tests/` | Any lint error |
| 3. Validate | `validate_plugin.py . --strict` | CRITICAL, MAJOR, MINOR, NIT (exit codes 1-4) |
| 4. Tests | `pytest tests/ -q` | Any test failure |

**Only WARNINGs (exit code 5+) pass through.** Everything else blocks.

## Fix-All Mandate

Before publishing or pushing a plugin, ALL CRITICAL, MAJOR, MINOR, and NIT issues MUST be fixed. The workflow is:

```
validate --strict → fix issues → re-validate → repeat until only WARNINGs remain → then proceed
```

Never skip this loop. Never publish with unfixed issues. The pre-push hook will block you anyway.

## Running CPV Scripts

Always use this exact command form when running CPV scripts from outside the CPV project:
```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/<script>.py" <args>
```
Without `--with pyyaml`, you get `ModuleNotFoundError: No module named 'yaml'`.

## Processing Validation Output

Always strip ANSI color codes and use macOS-compatible grep:
```bash
... 2>&1 | sed 's/\x1b\[[0-9;]*m//g'
```
Use `grep -oE` (extended regex), NOT `grep -oP` (Perl regex — unavailable on macOS).

## GitHub Secrets

Always use `--body` flag:
```bash
gh secret set MARKETPLACE_PAT --repo <owner>/<repo> --body "$MARKETPLACE_PAT"
```
Piping via `echo | gh secret set` does NOT work reliably.

Check if the env var exists first: `test -n "$MARKETPLACE_PAT"`

## CI Workflow Dependencies

CI workflows MUST use `uv sync --extra dev` (NOT just `uv sync`).
ruff, pytest, mypy, pyyaml are in `[project.optional-dependencies] dev`.
Without `--extra dev`, ALL CI runs fail with "Failed to spawn: ruff".

## Marketplace Notification

After `standardize_plugin.py --fix` generates `notify-marketplace.yml`, ALWAYS update:
- `MARKETPLACE_OWNER` → actual marketplace owner (e.g., `Emasoft`)
- `MARKETPLACE_REPO` → actual marketplace repo name (e.g., `emasoft-plugins`)

The generated values are placeholders that will NOT work.

## All Scripts Are Python

The plugin repo pipeline is always Python:
- Pre-push hook: Python script
- publish.py: Python script
- setup_git_hooks.py: Python script
- All validation scripts: Python

GitHub YAML workflows ONLY:
- Run read-only checks (lint, validate, test)
- Create releases (attach artifacts)
- Send notifications (repository_dispatch)
- NEVER commit code, create PRs, or modify the repo

## Binary Plugins

Compiled sources live in `src/<component>/`. Pre-compiled binaries in `src/<component>/bin/`.
Compilation happens **locally** via `publish.py` — NOT on GitHub CI.
`build-binaries.yml` is a FALLBACK only for CI-only environments.

## README Requirements

README MUST include:
- Badge markers: `<!--BADGES-START-->` / `<!--BADGES-END-->`
- Components table (auto-generated from commands/agents/skills/hooks)
- Install section (marketplace + GitHub + manual)
- Uninstall section
- Update section
- Troubleshooting section (3 required topics: hook path not found, old version after update, restart required)

## Pre-Publish Local Dry-Run

Before the first push, ALWAYS verify the generated pipeline works locally:
1. Run the pre-push hook: `echo "" | uv run python git-hooks/pre-push`
2. Run publish.py dry-run: `uv run python scripts/publish.py --dry-run`
Both must complete without crashes. This catches import errors, missing deps, and template bugs.

## Post-Push CI Verification

After every first push to a new GitHub repo, ALWAYS verify CI passes:
```bash
sleep 30 && gh run list --repo <owner>/<plugin-name> --limit 5
```
If any workflow failed, investigate with `gh run view <id> --log-failed | head -30`.
Fix and push again. Do NOT leave failing CI as the final state.

## Mega-Linter Configuration

The `.mega-linter.yml` config must include:
- `COPYPASTE_JSCPD_ARGUMENTS: "--threshold 5"` — 0% is too strict for plugin repos
- `REPOSITORY_CHECKOV_ARGUMENTS: "--skip-check CKV2_GHA_1"` — flags missing top-level workflow permissions, but we set permissions per-job
- `.gitignore` must include `megalinter-reports/` and `mega-linter.log`

## Common Fixes Reference

| Issue | Fix |
|-------|-----|
| SKILL.md missing sections | Add: Overview, Prerequisites, Instructions (numbered), Output, Error Handling, Examples, Resources |
| .gitignore gaps | Append missing patterns: __pycache__/, .venv/, .env, dist/, build/, .coverage, .pytest_cache/, .ruff_cache/, node_modules/, *_dev/ |
| Missing README badges | Add `<!--BADGES-START-->` block with CI, Version, License, Validation badges |
| Missing LICENSE | Create MIT LICENSE file |
| Script not executable | `chmod +x <script>` |
| Ruff lint errors | `uv run ruff check --fix scripts/` then manually fix remaining |
| Missing author.email | Add `"email": "nnn+user@users.noreply.github.com"` to plugin.json |
| Absolute paths | Replace with `${CLAUDE_PLUGIN_ROOT}` or document as intentional system binaries |
