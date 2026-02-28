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

## Notes

- The agent runs in fork mode and follows the setup-github-marketplace skill instructions
- Uses the plugin-validator agent which has full knowledge of marketplace validation rules
- Run `/cpv-validate-marketplace` afterwards to verify the setup
