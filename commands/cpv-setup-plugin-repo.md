---
name: cpv-setup-plugin-repo
description: Create and configure a Claude Code plugin GitHub repository with CI/CD, hooks, and marketplace notification.
allowed-tools: Read, Bash, Write, Edit, Glob, Grep, AskUserQuestion
argument-hint: "<plugin-name> [--owner <github-username>]"
agent: plugin-validator
user-invocable: true
---

# /cpv-setup-plugin-repo Command

Set up a new Claude Code plugin repository with full CI/CD pipeline, git hooks, and marketplace notification.

## Usage

```
/cpv-setup-plugin-repo <plugin-name> [--owner <github-username>]
```

## What It Does

1. Creates a GitHub repo with proper plugin structure
2. Installs CI/CD workflows (ci.yml, release.yml, validate.yml, notify-marketplace.yml)
3. Configures git hooks (pre-push validation)
4. Optionally sets up marketplace notification

See the `setup-plugin-repo` skill for full details.
