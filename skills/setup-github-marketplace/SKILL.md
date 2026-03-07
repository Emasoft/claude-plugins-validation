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

See [Marketplace Setup Guide](references/marketplace-setup-guide.md) for detailed commands.

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
  > Sections: Placeholder Reference · sync_marketplace_versions.py
- [README Template](references/readme-template.md)
  > Sections: Template Content · Architecture
- [Troubleshooting Guide](references/troubleshooting.md)
  > Sections: Authentication Issues · Token Missing Required Scopes

## Token Optimization

Load reference files on-demand as each phase is reached, rather than reading all upfront.
