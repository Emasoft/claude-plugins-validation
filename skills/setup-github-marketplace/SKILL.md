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
agent: plugin-creator
context: fork
user-invocable: false
---

# Setup GitHub Marketplace

## Overview

Automates creation and configuration of a GitHub-based marketplace repository for Claude Code plugins. Handles CI/CD pipeline setup, batch plugin linking, and cross-marketplace migration.

## Marketplace layout

CPV supports TWO marketplace layouts and the agent must be fluent in both:

- **Layout A (hub-and-spoke)**: separate repos per plugin, marketplace holds only `marketplace.json`. Entries: `{"source": "github", "repo": "owner/name"}`. Preferred default.
- **Layout B (nested single-repo)**: plugins as subdirectories inside the marketplace repo. Entries: `"./plugins/<name>"`. Also fully supported.

Both layouts make plugins installable and updatable by Claude Code. The agent defaults to suggesting Layout A for new marketplaces but must follow the user's preference without argument. Full guide, version-update flows for each layout, refactor procedures in both directions, and agent behavior matrix in [marketplace-layouts.md](references/marketplace-layouts.md).

## Prerequisites

- **GitHub CLI** (`gh`) authenticated with `repo`, `workflow`, `admin:repo_hook` scopes
- **CPV validator** installed for pre-push validation hooks

## Instructions

1. Create the marketplace repo and initialize `marketplace.json`
2. Install CI/CD workflow templates and sync scripts
3. Link plugins by fetching metadata and installing notification workflows
4. Validate the full setup with `validate_marketplace.py`

Copy this checklist and track your progress:

- [ ] Create marketplace repo
- [ ] Install CI/CD workflows
- [ ] Link plugins
- [ ] Run validation

See [Marketplace Setup Guide](references/marketplace-setup-guide.md) for detailed commands:
  > Overview · Prerequisites · Arguments · Phase 1: Create Marketplace Repository · Phase 2: Install Infrastructure · Phase 3: Link Plugin Repos · Phase 4: Plugin Management · Phase 5: Validate and Verify · Error Handling · Examples

## Output

- Marketplace repo with `marketplace.json`, CI/CD workflows, sync scripts
- `notify-marketplace.yml` installed on each linked plugin repo

## Error Handling

- **Authentication failures**: Verify PAT scopes include `repo` and `workflow`
- **Dispatch not received**: Check `notify-marketplace.yml` is on the plugin's default branch
- **Validation errors**: Run `validate_marketplace.py --verbose`

## Examples

**Input:** `setup-github-marketplace` with a new org
**Output:** Creates marketplace repo, installs CI/CD, generates README

**Input:** `setup-github-marketplace --link org/my-plugin`
**Output:** Fetches plugin metadata, adds to `marketplace.json`, installs notification workflow

## Resources

- [Marketplace Layouts](references/marketplace-layouts.md)
  > Overview · Layout A — Hub-and-Spoke (separate repos) · Layout B — Nested single-repo (monorepo) · How Claude Code updates plugins in each layout · When to choose which · Rich metadata fields (author, homepage, license, category) · Why CPV does not use git-subdir · Encountering a non-CPV marketplace · Refactoring between layouts · Agent behavior summary
- [Workflow Templates](references/workflow-templates.md)
  > Placeholder Reference · validate.yml (Marketplace CI) · update-submodules.yml (Dispatch Receiver) · notify-marketplace.yml.template (Plugin Side)
- [Script Templates](references/script-templates.md)
  > Placeholder Reference · sync_marketplace_versions.py · pre-commit-hook.py · pre-push-hook.py · setup-hooks.py · push-plugins.sh · generate-readme.py
- [README Template](references/readme-template.md)
  > Template Content · Placeholder Reference · Auto-Generation · Customization Guide
- [Troubleshooting Guide](references/troubleshooting.md)
  > Authentication Issues · Repository Creation Failures · CI/CD Pipeline Issues · Notification Chain Failures · Validation Failures · Secret Configuration Issues · Common Error Messages · Debug Commands

## Compiling Templates

Replace `<placeholder-for-...>` tokens with user values; verify with `grep -r 'placeholder-for-'`. For README, use generate-readme.py (script-templates — see Resources).

## Token Optimization

Load reference files on-demand per phase. Prefer LLM Externalizer MCP for file analysis.
