---
name: setup-github-marketplace
description: >
  Use when creating a plugin marketplace or linking plugins to one.
  Loaded by plugin-creator agent.
tags:
  - marketplace
  - github
  - ci-cd
  - automation
  - setup
allowed-tools: Read, Bash(git:*,gh:*,python:*,uv:*), Write, Edit, Glob, Grep, AskUserQuestion
user-invocable: false
---

# Setup GitHub Marketplace

## Overview

Automates creation and configuration of a GitHub-based marketplace repository for Claude Code plugins. Handles CI/CD pipeline setup, batch plugin linking, and cross-marketplace migration.

## Marketplace layout

Two supported layouts: **A (hub-and-spoke)** — separate repos per plugin, entries `{"source":"github","repo":"owner/name"}`, preferred default; **B (nested monorepo)** — plugins as subdirs, entries `"./plugins/<name>"`. Agent defaults to suggesting A but follows user preference. Details: [marketplace-layouts.md](references/marketplace-layouts.md).

## Prerequisites

- **GitHub CLI** (`gh`) authenticated with `repo`, `workflow`, `admin:repo_hook` scopes
- **CPV validator** installed for pre-push validation hooks

## Instructions

1. Create the marketplace repo and initialize `marketplace.json`
2. Install CI/CD workflow templates and sync scripts
3. Link plugins by fetching metadata and installing notification workflows
4. Validate with `uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-remote-validate marketplace . --strict` (remote CPV — no vendoring, no drift)

Copy this checklist and track your progress:

- [ ] Create marketplace repo
- [ ] Install CI/CD workflows
- [ ] Link plugins
- [ ] Run validation

See [Marketplace Setup Guide](references/marketplace-setup-guide.md) for detailed commands:
  > Overview · Prerequisites · Arguments · Phase 1: Create Marketplace Repository · Phase 2: Install Infrastructure · Phase 3: Link Plugin Repos · Phase 4: Plugin Management · Phase 5: Validate and Verify · Error Handling · Examples

## Output

Marketplace repo with `marketplace.json` + CI/CD + sync scripts; `notify-marketplace.yml` on each linked plugin repo.

## Error Handling

PAT needs `repo`+`workflow`. Dispatch missing: confirm `notify-marketplace.yml` on plugin default branch. Run `validate_marketplace.py --verbose`. Full troubleshooting: below.

## Examples

**Input:** new marketplace → **Output:** creates repo, installs CI/CD, generates README.
**Input:** `--link org/my-plugin` → **Output:** fetches metadata, adds entry, installs `notify-marketplace.yml`.

## Resources

- [Marketplace Layouts](references/marketplace-layouts.md)
  > Overview · Layout A — Hub-and-Spoke (separate repos) · Layout B — Nested single-repo (monorepo) · How Claude Code updates plugins in each layout · When to choose which · Rich metadata fields (author, homepage, license, category) · Why CPV does not use git-subdir · Encountering a non-CPV marketplace · Refactoring between layouts · Agent behavior summary
- [Workflow Templates](references/workflow-templates.md)
  > Placeholder Ref · validate.yml (CI) · update-submodules.yml (dispatch) · notify-marketplace.yml.template (plugin side)
- [Script Templates](references/script-templates.md)
  > Placeholder Ref · sync_marketplace_versions.py · pre-commit/pre-push hooks · setup-hooks.py · push-plugins.sh · generate-readme.py
- [README Template](references/readme-template.md)
  > Content · Placeholder Ref · Auto-Generation · Customization
- [Troubleshooting Guide](references/troubleshooting.md)
  > Authentication · Repo Creation · CI/CD · Notification Chain · Validation · Secret Config · Common Errors · Debug Commands
- [Local → GitHub Migration](references/local-to-github-migration.md)
  > Scenario · Detect the starting state · Four migration paths · Path 1: Lift-and-shift the whole marketplace to GitHub as Layout B · Path 2: Split every plugin into its own repo + create a Layout A hub from the local folder · Path 3: Ship ONE plugin to its own GitHub repo + keep the local marketplace for dev · Path 4: Ship ONE plugin to an EXISTING third-party GitHub marketplace · Gotchas · Post-migration verification · User instructions template
- [Orphan Plugin Onboarding](references/orphan-plugin-onboarding.md)
  > Scenario · The marketplace requirement — explain it first · Detect the scenario · Ask the user which path fits · Path A: plugin came from an existing marketplace · Path B: host in a NEW local marketplace · Path C: host in a NEW GitHub marketplace (user's own) · Path D: host in an EXISTING GitHub marketplace the user owns · Full pipeline is mandatory · Final user instructions

## Compiling Templates

Replace `<placeholder-for-...>` tokens with user values; verify with `grep -r 'placeholder-for-'`. For README, use generate-readme.py (script-templates — see Resources).

## Token Optimization

Load reference files on-demand per phase. Prefer LLM Externalizer MCP for file analysis.
