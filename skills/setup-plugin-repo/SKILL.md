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

Creates a Claude Code plugin GitHub repo with CI/CD, git hooks, and marketplace notification — ready for development with automated validation on push and release.

## Prerequisites

- `gh` CLI authenticated, `git` and `uv` on PATH
- CPV validator at `scripts/validate_plugin.py`
- GitHub PAT with `repo` scope (optional, for marketplace)

## Instructions

1. **Create GitHub repo**: `gh repo create <owner>/<name> --public --clone`, then `cd` into it
2. **Initialize plugin structure** from `references/plugin-repo-templates.md`: create `plugin.json`, `pyproject.toml`, `.gitignore`, `README.md` — fill all `{{PLACEHOLDER}}` values
3. **Install CI/CD workflows** from `references/plugin-workflows.md`: copy `ci.yml`, `release.yml`, `validate.yml`, `notify-marketplace.yml` into `.github/workflows/`
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
| Unfilled placeholders | Re-read templates, fill `{{...}}` |
| INVALID validation | Fix CRITICAL/MAJOR, re-validate |
| No PAT | Skip marketplace, warn user |

## Examples

**Input:**
```
Plugin: my-code-formatter | Owner: Emasoft | Desc: Custom style formatter
```

**Output:**
```
[DONE] setup-plugin-repo
  Repo: https://github.com/Emasoft/my-code-formatter
  Validation: VALID (0 critical, 0 major, 1 minor)
  Workflows: ci.yml, release.yml, validate.yml, notify-marketplace.yml
  Hooks: pre-push | Marketplace: configured
```

## Resources

- [`references/plugin-repo-templates.md`](references/plugin-repo-templates.md)
  > plugin.json Template · pyproject.toml Template · .gitignore Template · README.md Template · Placeholder Reference

- [`references/plugin-workflows.md`](references/plugin-workflows.md)
  > ci.yml · release.yml · validate.yml · notify-marketplace.yml · Placeholder Reference · Setup Instructions

- [`references/plugin-hooks-and-scripts.md`](references/plugin-hooks-and-scripts.md)
  > pre-push Hook Template · publish.py Pipeline Template · setup-hooks.py Template · Placeholder Reference

## Token Optimization

- Read only the needed template section from each reference file
- Fill placeholders in memory, single write per file
- Validate once at the end, not per file
