---
name: setup-plugin-repo
description: >
  Create and configure Claude Code plugin GitHub repositories with CI/CD, hooks, and marketplace notification.
  Use when setting up a new plugin repo. Trigger with /cpv-publish-a-plugin-as-github-repo.
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
2. **Initialize plugin**: create standard files from plugin-repo-templates (see Resources)
3. **Install workflows**: from plugin-workflows (see Resources) copy `ci.yml`, `release.yml`, `validate.yml`, `notify-marketplace.yml` to `.github/workflows/`
4. **Install git hooks**: run `uv run python scripts/publish.py --install-hook` (sets `core.hooksPath` to `git-hooks`)
5. **Configure marketplace**: `gh secret set MARKETPLACE_PAT --repo <owner>/<repo> --body "$PAT"` (skip if declined)
6. **Validate**: `uv run --with pyyaml python scripts/validate_plugin.py .` — fix ALL non-WARNING issues
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

Report: repo URL, VALID/INVALID + severity counts, installed workflows/hooks, skipped steps.

## Error Handling

| Error | Resolution |
|-------|------------|
| `gh auth` fails | `gh auth login` and retry |
| Repo exists | Ask user: clone or new name |
| Unfilled placeholders | Re-read templates, fill `<placeholder-for-...>` values |
| INVALID validation | Fix ALL issues (CRITICAL, MAJOR, MINOR, NIT), re-validate |
| No PAT | Skip marketplace, warn user |

## Examples

**Input:** `Plugin: my-plugin | Owner: my-org`
**Output:** `[DONE] Repo: github.com/my-org/my-plugin | VALID | Hooks: pre-push | Marketplace: ok`

## Resources

- [Plugin Repo Templates](references/plugin-repo-templates.md)
  > plugin.json Template · pyproject.toml Template · .gitignore Template · README.md Template · Placeholder Reference
- [Plugin Workflows](references/plugin-workflows.md)
  > ci.yml -- Continuous Integration · release.yml -- GitHub Release on Tag · validate.yml -- Plugin Validation · notify-marketplace.yml -- Marketplace Notification · Placeholder Reference · Setup Instructions
- [Plugin Hooks and Scripts](references/plugin-hooks-and-scripts.md)
  > pre-push Hook Template · publish.py Pipeline Template · setup-hooks.py Template · Placeholder Reference
- [Plugin Binary Builds](references/plugin-binary-builds.md)
  > When to Add a Build Phase · build-binaries.yml — Cross-Platform Compilation Workflow · Binary Distribution Pattern · Platform Detection Wrapper · Extending the Python Pre-Push Hook · Extending publish.py for Binary Builds · Extending ci.yml for Binary Builds · Cargo Release Profile (Rust Optimization)
- [Pipeline Rules](references/pipeline-rules.md)
  > Pre-Push Hook: The Quality Gate · Fix-All Mandate · Running CPV Scripts · Processing Validation Output · GitHub Secrets · CI Workflow Dependencies · Marketplace Notification · All Scripts Are Python · Binary Plugins · README Requirements · Pre-Publish Local Dry-Run · Post-Push CI Verification · Mega-Linter Configuration · Common Fixes Reference

## Token Optimization

Use LLM Externalizer MCP for template analysis to save context tokens.
