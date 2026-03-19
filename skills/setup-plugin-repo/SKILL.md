---
name: setup-plugin-repo
description: >
  Create and configure Claude Code plugin GitHub repositories with CI/CD, hooks, and marketplace notification.
  Use when setting up a new plugin repo. Trigger with /cpv-publish-as-github-repo.
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
2. **Initialize plugin**: from plugin-repo-templates (see Resources) create `plugin.json`, `pyproject.toml`, `.gitignore`, `README.md`. Use `${CLAUDE_PLUGIN_ROOT}` for scripts, `${CLAUDE_PLUGIN_DATA}` for persistent deps/caches. Scan for compiled components — if found, add build phases per plugin-binary-builds
3. **Install workflows**: from plugin-workflows (see Resources) copy `ci.yml`, `release.yml`, `validate.yml`, `notify-marketplace.yml` to `.github/workflows/`. For compiled binaries, add `build-binaries.yml` per plugin-binary-builds (see Resources)
4. **Install git hooks** from plugin-hooks-and-scripts (see Resources): copy `pre-push` to `.githooks/`, add `publish.py` and `setup-hooks.py` to `scripts/`, run `git config core.hooksPath .githooks`. The pre-push hook is the quality gate — it runs 4 gates (version bump, lint, validate --strict, tests) and blocks pushes with ANY non-WARNING issue.
5. **Configure marketplace notification**: ask user for `MARKETPLACE_PAT`. Check env var first: `test -n "$MARKETPLACE_PAT"`. Run `gh secret set MARKETPLACE_PAT --repo <owner>/<repo> --body "$MARKETPLACE_PAT"` (skip if declined)
6. **Validate**: run `uv run --with pyyaml python scripts/validate_plugin.py .` — fix ALL issues (CRITICAL, MAJOR, MINOR, NIT) — only WARNINGs may remain. Re-validate until clean.
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
- [Pipeline Rules](../canonical-pipeline/references/pipeline-rules.md)

## Token Optimization

Read needed sections; fill `<placeholder-for-...>` with user values; verify with `grep -r 'placeholder-for-'`.
Prefer LLM Externalizer MCP for template analysis to save context tokens.
