---
name: cpv-setup-github-marketplace
description: Set up a GitHub marketplace for Claude Code plugins.
allowed-tools: Read, Bash, Write, Edit, Glob, Grep, AskUserQuestion
argument-hint: "<marketplace-name> [--owner <github-username>] [--plugin <plugin-repo>]"
agent: plugin-validator
user-invocable: true
---

# Setup GitHub Marketplace

Set up a complete GitHub marketplace for Claude Code plugins with automated CI/CD pipeline.

## Usage

```
/cpv-setup-github-marketplace my-plugins-marketplace
/cpv-setup-github-marketplace my-plugins-marketplace --owner MyGitHubUser
/cpv-setup-github-marketplace my-plugins-marketplace --plugin my-awesome-plugin
```

## What It Does

1. Creates a GitHub repository for the marketplace (if it doesn't exist)
2. Installs CI/CD workflows for automated plugin updates
3. Sets up validation scripts and git hooks
4. Generates an auto-updating README with plugin table
5. Links plugin repos via notification workflows
6. Validates the entire setup

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `<marketplace-name>` | Yes | Name for the marketplace repo (kebab-case) |
| `--owner <username>` | No | GitHub username/org (defaults to authenticated user) |
| `--plugin <repo>` | No | Link a plugin repo immediately after setup |

## Prerequisites

- `gh` CLI installed and authenticated (`gh auth status`)
- Git configured with user.name and user.email
- GitHub Personal Access Token with `repo` + `workflow` scopes

## Execution

This command delegates to the `plugin-validator` agent using the `setup-github-marketplace` skill.
The agent orchestrates multiple scripts and GitHub API calls in phases:

```bash
# Phase 1: Create marketplace repo structure
gh repo create <marketplace-name> --public

# Phase 2: Install validation and automation scripts
uv run python scripts/setup_marketplace_automation.py --marketplace-dir <path> --full

# Phase 3: Validate the setup
uv run python scripts/validate_marketplace.py <path> --verbose --report docs_dev/validate_marketplace_$(date +%Y%m%d).md
```

## Notes

- The agent follows the `setup-github-marketplace` skill instructions for the full multi-phase workflow
- Uses the `plugin-validator` agent which has full knowledge of marketplace validation rules
- The underlying script is `scripts/setup_marketplace_automation.py` (accepts `--marketplace-dir`, `--dry-run`, `--full`, `--status`)
- Run `/cpv-validate-marketplace` afterwards to verify the setup
