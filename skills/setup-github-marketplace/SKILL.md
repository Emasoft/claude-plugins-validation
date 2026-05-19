---
name: setup-github-marketplace
description: >
  Use when creating a plugin marketplace or linking plugins to one.
  Used dynamically via the-skills-menu (TRDD-478d9687).
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

Automates creation of a GitHub-hosted Claude Code plugin marketplace. Handles CI/CD setup, batch plugin linking, and cross-marketplace migration.

## Marketplace layout

Three layouts: **A** (hub-and-spoke, separate repos), **B** (nested monorepo, subdirs), **C** (marketplace-in-plugin, single repo with both manifests, source="./"). Suggest A for multi-plugin sets, C for single-plugin repos. Details: [marketplace-layouts.md](references/marketplace-layouts.md).

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
  > Overview · Layout A — Hub-and-Spoke (separate repos) · Layout B — Nested single-repo (monorepo) · Layout C — Marketplace-in-plugin (self-referential single repo) · How Claude Code updates plugins in each layout · When to choose which · Rich metadata fields (author, homepage, license, category) · Why CPV does not use git-subdir · Encountering a non-CPV marketplace · Refactoring between layouts · Agent behavior summary
- [Workflow Templates](references/workflow-templates.md)
  > Placeholder Reference · validate.yml (Marketplace CI) · update-submodules.yml (Dispatch Receiver) · notify-marketplace.yml.template (Plugin Side)
- [Script Templates](references/script-templates.md)
  > Placeholder Reference · sync_marketplace_versions.py · pre-commit-hook.py · pre-push-hook.py · setup-hooks.py · push-plugins.sh · generate-readme.py
- [README Template](references/readme-template.md)
  > Template Content · Placeholder Reference · Auto-Generation · Customization Guide
- [Troubleshooting Guide](references/troubleshooting.md)
  > Authentication Issues · Repository Creation Failures · CI/CD Pipeline Issues · Notification Chain Failures · Validation Failures · Secret Configuration Issues · Common Error Messages · Debug Commands
- [Local → GitHub Migration](references/local-to-github-migration.md)
  > Scenario · Detect the starting state · Four migration paths · Path 1: Lift-and-shift the whole marketplace to GitHub as Layout B · Path 2: Split every plugin into its own repo + create a Layout A hub from the local folder · Path 3: Ship ONE plugin to its own GitHub repo + keep the local marketplace for dev · Path 4: Ship ONE plugin to an EXISTING third-party GitHub marketplace · Gotchas · Post-migration verification · User instructions template
- [Orphan Plugin Onboarding](references/orphan-plugin-onboarding.md)
  > Scenario · The marketplace requirement — explain it first · Detect the scenario · Ask the user which path fits · Path A: plugin came from an existing marketplace · Path B: host in a NEW local marketplace · Path C: host in a NEW GitHub marketplace (user's own) · Path D: host in an EXISTING GitHub marketplace the user owns · Full pipeline is mandatory · Final user instructions

## Compiling Templates

Replace `<placeholder-for-...>` tokens; verify with grep. README: use generate-readme.py.

## Token Optimization

Load reference files on-demand. Prefer LLM Externalizer MCP for file analysis.
