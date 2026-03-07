---
name: setup-plugin-repo
description: >
  Create and configure Claude Code plugin GitHub repositories with CI/CD, hooks, and marketplace notification.
  Use when setting up a new plugin repo. Trigger with /cpv-setup-plugin-repo.
tags:
  - plugin
  - github
  - setup
  - ci-cd
allowed-tools: Read, Bash(git:*,gh:*,uv:*), Write, Edit, Glob, Grep, AskUserQuestion
user-invocable: false
---

# Setup Plugin Repository

## Overview

Creates a Claude Code plugin GitHub repo with CI/CD, git hooks, and marketplace notification.

## Prerequisites

- `gh` CLI authenticated, `git` and `uv` on PATH
- CPV validator at `scripts/validate_plugin.py`
- GitHub PAT with `repo` scope (optional, for marketplace)

## Instructions

1. **Create GitHub repo**: `gh repo create <owner>/<name> --public --clone`, then `cd` into it
2. **Initialize plugin**: from `references/plugin-repo-templates.md` create `plugin.json`, `pyproject.toml`, `.gitignore`, `README.md`. Scan for compiled components — if found, add build phases per `references/plugin-binary-builds.md`
3. **Install workflows**: from `references/plugin-workflows.md` copy `ci.yml`, `release.yml`, `validate.yml`, `notify-marketplace.yml` to `.github/workflows/`. For compiled binaries, add `build-binaries.yml` per `references/plugin-binary-builds.md`
4. **Install git hooks** from `references/plugin-hooks-and-scripts.md`: copy `pre-push` to `.githooks/`, add `publish.py` and `setup-hooks.py` to `scripts/`, run `git config core.hooksPath .githooks`
5. **Configure marketplace notification**: ask user for `MARKETPLACE_PAT`, run `gh secret set MARKETPLACE_PAT` (skip if declined)
6. **Validate**: run `uv run scripts/validate_plugin.py .` — fix CRITICAL/MAJOR issues
7. **Commit and push**: stage all, commit "Initial plugin scaffold", push to `main`

Copy this checklist and track your progress:

- [ ] Create GitHub repo
- [ ] Initialize plugin structure
- [ ] Install CI/CD workflows
- [ ] Install git hooks
- [ ] Configure marketplace notification
- [ ] Validate
- [ ] Commit and push

## Output

Report: repo URL, validation result (VALID/INVALID + severity counts), installed workflows/hooks, skipped steps. On failure, report which step failed with error.

## Error Handling

| Error | Resolution |
|-------|------------|
| `gh auth` fails | `gh auth login` and retry |
| Repo exists | Ask user: clone or new name |
| Unfilled placeholders | Re-read templates, fill `<placeholder-for-...>` values |
| INVALID validation | Fix CRITICAL/MAJOR, re-validate |
| No PAT | Skip marketplace, warn user |

## Examples

**Input:**
```
Plugin: <placeholder-for-plugin-name> | Owner: <placeholder-for-github-repo-owner> | Desc: <placeholder-for-plugin-description>
```

**Output:**
```
[DONE] setup-plugin-repo
  Repo: https://github.com/<placeholder-for-github-repo-owner>/<placeholder-for-plugin-name>
  Validation: VALID (0 critical, 0 major, 1 minor)
  Workflows: ci.yml, release.yml, validate.yml, notify-marketplace.yml
  Hooks: pre-push | Marketplace: configured
```

## Resources

- [`references/plugin-repo-templates.md`](references/plugin-repo-templates.md) — plugin.json, pyproject.toml, .gitignore, README templates
- [`references/plugin-workflows.md`](references/plugin-workflows.md) — ci.yml, release.yml, validate.yml, notify-marketplace.yml
- [`references/plugin-hooks-and-scripts.md`](references/plugin-hooks-and-scripts.md) — pre-push hook, publish.py, setup-hooks.py
- [`references/plugin-binary-builds.md`](references/plugin-binary-builds.md) — build-binaries.yml, binary distribution, platform detection

## Compiling Templates

Replace `<placeholder-for-...>` tokens with user values. Run `grep -r 'placeholder-for-'` to verify none remain. Each plugin MUST have its own repo — never embed plugins inside the marketplace.

## Token Optimization

- Read only the needed template section, fill in memory, single write per file
