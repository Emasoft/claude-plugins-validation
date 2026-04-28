---
name: setup-plugin-repo
description: >
  Create and configure Claude Code plugin GitHub repositories with CI/CD, hooks, and marketplace notification.
  Use when setting up a new plugin repo. Loaded by plugin-creator agent.
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

- `gh` CLI authenticated, `git`, `uv`, and `uvx` on PATH
- GitHub PAT with `repo` scope (optional, for marketplace)

Note: the CPV validator is fetched remotely from GitHub via `uvx`. Downstream
plugins do NOT vendor `scripts/validate_plugin.py` — the canonical pipeline
runs `cpv-remote-validate plugin . --strict` which pulls the current CPV
release automatically.

## Instructions

1. **Create GitHub repo**: `gh repo create <owner>/<name> --public --clone`, then `cd` into it
2. **Initialize plugin**: create standard files from plugin-repo-templates (see Resources)
3. **(Layout C only) Add self-marketplace**: if the user picked Layout C, also create `.claude-plugin/marketplace.json` with a single self-entry pointing to `"./"`. Both manifests must share the same `name` and `version`.
4. **Install workflows**: from plugin-workflows (see Resources) copy `ci.yml` (consolidated lint + validate + test) and `release.yml` to `.github/workflows/`. **Skip `notify-marketplace.yml` for Layout C** — there is no separate marketplace repo to notify (both manifests live in this repo).
5. **Install git hooks**: run `uv run python scripts/publish.py --install-hook` (sets `core.hooksPath` to `git-hooks`)
6. **Configure marketplace** (Layout A only): `uv run python scripts/set_marketplace_pat.py <owner>/<repo>` (skip if declined — see setup-marketplace-auto-notification). **Skip for Layout B and Layout C** — both manifests are already in the same repo.
7. **Validate** (remote CPV from GitHub): `uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-remote-validate plugin . --strict` — fix ALL non-WARNING issues. Layout C also requires `validate_marketplace.py --strict` to clear name/version sync.
8. **Commit and push**: stage all, commit "Initial plugin scaffold", push to `main`

Copy this checklist and track your progress:

- [ ] Create GitHub repo
- [ ] Initialize plugin structure
- [ ] (Layout C) Add `.claude-plugin/marketplace.json` self-entry, name+version aligned with plugin.json
- [ ] Install CI/CD workflows (skip notify-marketplace.yml for B/C)
- [ ] Install git hooks
- [ ] Configure marketplace notification (Layout A only)
- [ ] Validate (plugin + marketplace for Layout C)
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
  > ci.yml -- Consolidated CI (lint + validate + test) · release.yml -- GitHub Release on Tag · notify-marketplace.yml -- Marketplace Notification · Placeholder Reference · Setup Instructions
- [Plugin Hooks and Scripts](references/plugin-hooks-and-scripts.md)
  > pre-push Hook Template · publish.py Pipeline Template · setup-hooks.py Template · Placeholder Reference
- [Plugin Binary Builds](references/plugin-binary-builds.md)
  > When to Add a Build Phase · build-binaries.yml — Cross-Platform Compilation Workflow · Binary Distribution Pattern · Platform Detection Wrapper · Extending the Python Pre-Push Hook · Extending publish.py for Binary Builds · Extending ci.yml for Binary Builds · Cargo Release Profile (Rust Optimization)
- [Pipeline Rules](references/pipeline-rules.md)
  > Pre-Push Hook: The Quality Gate · Fix-All Mandate · Running CPV Scripts · Processing Validation Output · GitHub Secrets · CI Workflow Dependencies · Marketplace Notification · All Scripts Are Python · Binary Plugins · README Requirements · Pre-Publish Local Dry-Run · Post-Push CI Verification · Mega-Linter Configuration · Common Fixes Reference
- [v2.1.80+ Plugin Features](references/v2-1-80-features.md)
  > Monitor tool · userConfig (plugin.json) · channels (plugin.json) · CLAUDE_PLUGIN_OPTION_<KEY> env vars · Inline marketplace (settings.json) · managed-settings.d/ drop-in directory · Plugin skill `name` field (v2.1.98)

## Token Optimization

Use LLM Externalizer MCP for template analysis to save context tokens.
