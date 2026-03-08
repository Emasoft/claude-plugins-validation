---
name: setup-github-marketplace
description: >
  Use when creating a plugin marketplace or linking plugins to one.
  Trigger with "set up marketplace" or "create plugin marketplace".
tags:
  - marketplace
  - github
  - ci-cd
  - automation
  - setup
allowed-tools: Read, Bash(git:*,gh:*,python:*,uv:*), Write, Edit, Glob, Grep, AskUserQuestion
agent: plugin-validator
context: fork
user-invocable: false
---

# Setup GitHub Marketplace

## Overview

Automates creation and configuration of a GitHub-based marketplace repository for Claude Code plugins. Handles CI/CD pipeline setup, batch plugin linking, and cross-marketplace migration.

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

- [Workflow Templates](references/workflow-templates.md)
  > Sections: Placeholder Reference · notify-marketplace.yml · Required Secrets · Setup Instructions
- [Script Templates](references/script-templates.md)
  > Sections: Placeholder Reference · sync_marketplace_versions.py · generate-readme.py · setup-hooks.py · push-plugins.sh
- [README Template](references/readme-template.md)
  > Sections: Template Content · Architecture
- [Troubleshooting Guide](references/troubleshooting.md)
  > Sections: Authentication Issues · Token Missing Required Scopes

## Compiling Templates

Replace all `<placeholder-for-...>` tokens with user values. Use `grep -r 'placeholder-for-' <file>` to verify none remain. For the marketplace README, use `generate-readme.py` (in `references/script-templates.md`) to automate replacement and plugin table generation.

Only the hub-and-spoke architecture (1 marketplace repo + N independent plugin repos) is supported. Each plugin MUST have its own GitHub repo because: plugins version independently from the marketplace; contributors can fork/clone a single plugin without pulling the whole marketplace; PRs stay isolated to each plugin's repo; and embedding plugins via Git subtrees/worktrees creates merge conflicts and scaling problems. Decline alternative structures politely and explain the rationale.

## Token Optimization

Load reference files on-demand as each phase is reached, rather than reading all upfront.
