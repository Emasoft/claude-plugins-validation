---
name: publish-to-marketplace
description: >
  Use when publishing a plugin to a GitHub-hosted marketplace.
  Trigger with "publish plugin", "push to marketplace", "marketplace sync".
tags:
  - marketplace
  - publish
  - ci-cd
  - plugin
allowed-tools: Read, Bash(git:*,gh:*,uv:*), Write, Edit, Glob, Grep, AskUserQuestion
agent: plugin-validator
context: fork
user-invocable: false
---

# Publish Plugin to Marketplace

## Overview

Publishes a validated Claude Code plugin to a GitHub-hosted marketplace repo. Configures the notification workflow, PAT secret, and publish pipeline so that every `git push` automatically syncs the marketplace.

## Prerequisites

- Plugin repo with valid `.claude-plugin/plugin.json` (name, version, description)
- `gh` CLI authenticated (`gh auth status`)
- Marketplace repo exists with `marketplace.json` (see `setup-github-marketplace` skill)
- `uv` on PATH, plugin has `pyproject.toml`

## Instructions

### Phase 0: Discover Marketplace

Ask the user for their marketplace repo coordinates (`<owner>/<marketplace-repo>`). Verify it exists: `gh repo view <owner>/<marketplace-repo> --json name`. All subsequent placeholders use these values.

### Phase 1: Configure Notification Pipeline

1. **Create PAT**: Ask user for a GitHub PAT with `repo` scope (or fine-grained with Contents R/W on marketplace repo). See publish-pipeline-guide (Resources) Section 1
2. **Set secret**: `gh secret set MARKETPLACE_PAT --repo <owner>/<plugin-repo>`
3. **Install notify-marketplace.yml**: Copy from publish-pipeline-guide (Resources) Section 2 into `.github/workflows/`. Fill `MARKETPLACE_OWNER` and `MARKETPLACE_REPO`
4. **Verify CI workflows**: Ensure `ci.yml`, `validate.yml`, `release.yml` exist (from `setup-plugin-repo` skill)

### Phase 2: Configure Publish Pipeline

5. **Install publish.py**: Should already exist from standardize. Verify with `test -f scripts/publish.py`
6. **Install pre-push hook**: `uv run python scripts/publish.py --install-hook` (PSS pattern)
7. **Verify hooks**: `git config core.hooksPath` should show `git-hooks`

### Phase 3: Publish

8. **Run gate check**: `uv run python scripts/publish.py --gate` (verify quality gates pass)
9. **Run publish**: `uv run python scripts/publish.py --patch` (or `--minor`/`--major`)
10. **Verify dispatch**: Check marketplace repo Actions tab — `update-submodules.yml` should trigger within 30s
11. **Verify marketplace.json**: Plugin version should update in marketplace repo

Copy this checklist and track your progress:
- [ ] PAT created and secret set
- [ ] notify-marketplace.yml installed
- [ ] CI workflows present
- [ ] publish.py + pre-push hook installed
- [ ] First publish successful
- [ ] Marketplace sync verified

## Output

Report: plugin name, old/new version, push status, marketplace dispatch status (triggered/failed/not_configured). On failure, report which phase failed with error.

## Error Handling

| Error | Resolution |
|-------|------------|
| `MARKETPLACE_PAT` missing | `gh secret set MARKETPLACE_PAT` |
| Dispatch not received | Check PAT scope, verify workflow is on default branch |
| Pre-push blocks | Fix validation/lint issues, bump version |
| Version mismatch | Run `publish.py` which auto-bumps all sources |
| Push rejected | Check branch protection; PAT owner must be admin |

## Examples

**Input:** `publish my-plugin to marketplace`
**Output:**
```
[DONE] Plugin: my-plugin 1.0.0->1.0.1 | Push: ok | Dispatch: triggered
```

## Resources

- [Publish Pipeline Guide](references/publish-pipeline-guide.md)
  > Section 1: PAT Setup · Section 2: notify-marketplace.yml · Section 3: The Dispatch Chain · Section 4: publish.py Pipeline · Section 5: Pre-Push Hook Gates · Section 6: marketplace.json Entry Format · Section 7: Troubleshooting
- `setup-plugin-repo` skill — publish.py, pre-push hook, CI workflows
- `setup-github-marketplace` skill — marketplace repo, sync workflows

## Token Optimization

Prefer LLM Externalizer MCP for bounded file analysis to save context tokens.
