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

Automates the creation and configuration of a GitHub-based marketplace repository for Claude Code plugins. Handles CI/CD pipeline setup, batch plugin linking, and cross-marketplace migration using a hub-and-spoke architecture with repository_dispatch notifications.

## Prerequisites

- **GitHub CLI** (`gh`) authenticated with appropriate scopes
- **Personal Access Token** (PAT) with `repo`, `workflow`, and `admin:repo_hook` permissions
- **Repository permissions**: admin access on both marketplace and plugin repos
- **CPV validator** installed for pre-push validation hooks

## Instructions

1. Create the marketplace repo and initialize `marketplace.json`
2. Install CI/CD workflow templates and sync scripts
3. Link plugins by fetching metadata and installing notification workflows
4. Validate the full setup with `validate_marketplace.py`

See [Marketplace Setup Guide](references/marketplace-setup-guide.md) for detailed step-by-step commands and the completion checklist.

## Quick Start

1. **Create marketplace repo** -- `gh repo create`, clone, initialize `.claude-plugin/marketplace.json` with empty plugins array.
2. **Install infrastructure** -- Copy workflow templates (`update-submodules.yml`, `validate-marketplace.yml`) and sync scripts. Set `MARKETPLACE_PAT` secret.
3. **Link plugins (batch)** -- For each plugin: fetch `plugin.json` via `gh api`, add to `marketplace.json`, install `notify-marketplace.yml` on the plugin repo.
4. **Validate** -- Run `validate_marketplace.py --verbose`, test notification chain, verify CI runs.

See [Marketplace Setup Guide](references/marketplace-setup-guide.md) for complete step-by-step instructions, bash commands, examples, error handling, and the completion checklist.

## Output

- Marketplace repo with `marketplace.json`, CI/CD workflows, sync scripts, auto-generated README
- `notify-marketplace.yml` installed on each linked plugin repo
- Pre-push git hooks running cpv validation

## Reference

| Document | Content |
|----------|---------|
| [Marketplace Setup Guide](references/marketplace-setup-guide.md) | Full phases 1-5, arguments, examples, error handling, completion checklist |
| [Marketplace Architecture](references/marketplace-architecture.md) | Hub-and-spoke design, schemas, notification flow, directory structure |
| [Workflow Templates](references/workflow-templates.md) | All GitHub Actions workflow YAML templates |
| [Script Templates](references/script-templates.md) | Python sync/generate/hooks scripts |
| [Plugin Linking Guide](references/plugin-linking-guide.md) | Add/remove/migrate plugins, batch operations, PAT configuration |
| [README Template](references/readme-template.md) | Auto-generated README template and customization |
| [Troubleshooting Guide](references/troubleshooting.md) | Common errors, debug commands, resolution steps |

## Error Handling

- **Authentication failures**: Verify PAT scopes include `repo` and `workflow`
- **Dispatch not received**: Check `notify-marketplace.yml` is on the plugin's default branch
- **Validation errors**: Run `validate_marketplace.py --verbose` for detailed diagnostics

See [Troubleshooting Guide](references/troubleshooting.md) for full error catalog and resolution steps.

## Examples

- `setup-github-marketplace` -- Create a new marketplace from scratch with default settings
- `setup-github-marketplace --link org/my-plugin` -- Link an existing plugin to the marketplace
- `setup-github-marketplace --migrate source-market target-market` -- Migrate plugins between marketplaces

## Resources

- [Marketplace Setup Guide](references/marketplace-setup-guide.md) -- Complete setup walkthrough
- [Marketplace Architecture](references/marketplace-architecture.md) -- Design and schemas
- [Troubleshooting Guide](references/troubleshooting.md) -- Error resolution
- [Plugin Linking Guide](references/plugin-linking-guide.md) -- Add, remove, migrate plugins

## Token Optimization

This skill uses reference files to keep the main SKILL.md small. The agent should load reference files on-demand as each phase is reached, rather than reading all references upfront.
