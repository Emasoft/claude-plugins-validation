---
name: setup-github-marketplace
description: |
  Set up a GitHub marketplace for Claude Code plugins with automated CI/CD.
  Use when creating a new plugin marketplace or linking plugins to one.
tags:
  - marketplace
  - github
  - ci-cd
  - automation
  - setup
allowed-tools: Read, Bash, Write, Edit, Glob, Grep, AskUserQuestion
agent: plugin-validator
context: fork
user-invocable: false
---

# Setup GitHub Marketplace

Set up a complete GitHub marketplace repository for Claude Code plugins with automated CI/CD pipelines.

## Overview

This skill walks through creating and configuring a **3-repo architecture** for distributing Claude Code plugins:

1. **Plugin Repo** -- individual plugin source code, validated by cpv scripts
2. **Marketplace Repo** -- central registry that aggregates plugins via git submodules
3. **Consumer** -- end-user who installs plugins from the marketplace

Key components created during setup:
- **marketplace.json** -- plugin registry with metadata, versions, and source configuration
- **GitHub Actions workflows** -- automated validation, submodule sync, and notification chain
- **Sync scripts** -- version synchronization, README generation, hook installation
- **Auto-updating README** -- plugin table and architecture diagram regenerated on every update

## Prerequisites

Before running this skill, ensure:

- `gh` CLI is installed and authenticated (`gh auth status` must succeed)
- Git is configured with `user.name` and `user.email`
- A GitHub account with permission to create repositories
- A **MARKETPLACE_PAT** personal access token with these scopes:
  - `repo` -- full control of private repositories
  - `workflow` -- update GitHub Action workflows
- At least one Claude Code plugin repo to link (optional but recommended)

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `<marketplace-name>` | Yes | Repository name in kebab-case (e.g. `my-claude-plugins`) |
| `--owner <github-username>` | No | GitHub owner/org. Defaults to `gh api user -q .login` |
| `--plugin <plugin-repo>` | No | Link a plugin repo immediately during setup |

## Phase 1: Create Marketplace Repository

### Step 1: Resolve owner

```bash
OWNER={{owner}}
if [ -z "$OWNER" ]; then
  OWNER=$(gh api user -q .login)
fi
```

### Step 2: Check if repo already exists

```bash
gh repo view "$OWNER/{{marketplace-name}}" --json name 2>/dev/null
```

If the repo exists, skip creation and proceed to Phase 2.

### Step 3: Create the repository

```bash
gh repo create "$OWNER/{{marketplace-name}}" --public \
  --description "Claude Code plugin marketplace"
```

### Step 4: Clone and initialize structure

```bash
gh repo clone "$OWNER/{{marketplace-name}}"
cd "{{marketplace-name}}"
mkdir -p .claude-plugin .github/workflows scripts
```

### Step 5: Create marketplace.json skeleton

Write `.claude-plugin/marketplace.json`:
```json
{
  "name": "{{marketplace-name}}",
  "version": "1.0.0",
  "description": "Claude Code plugin marketplace",
  "owner": "{{owner}}",
  "plugins": []
}
```

### Step 6: Commit initial structure

```bash
git add -A && git commit -m "Initialize marketplace structure" && git push
```

Reference: [Marketplace Architecture](references/marketplace-architecture.md)

## Phase 2: Install Infrastructure

### Step 1: Install GitHub Actions workflows

Copy and customize these workflow files into `.github/workflows/`:

- **update-submodules.yml** -- triggered by repository_dispatch from plugin repos; pulls latest submodule commits and regenerates the README
- **validate-marketplace.yml** -- runs on push/PR; validates marketplace.json schema, checks all plugin entries, runs cpv validation

Replace placeholders in each template:
- `{{MARKETPLACE_OWNER}}` with the resolved owner
- `{{MARKETPLACE_REPO}}` with the marketplace name

Reference: [Workflow Templates](references/workflow-templates.md)
  - Placeholder Reference
  - notify-marketplace.yml (Plugin Side)
  - update-submodules.yml (Marketplace Side)
  - validate-marketplace.yml (Marketplace CI)
  - Plugin CI Workflow (Optional)

### Step 2: Install sync scripts

Copy these scripts into `scripts/`:

- **sync_marketplace_versions.py** -- reads each submodule's plugin.json, updates marketplace.json version fields
- **generate-readme.py** -- generates README.md with plugin table, architecture diagram, and install instructions
- **setup-hooks.py** -- installs git pre-push hooks that run cpv validation before pushing

```bash
chmod +x scripts/*.py
```

Reference: [Script Templates](references/script-templates.md)
  - Placeholder Reference
  - sync_marketplace_versions.py
  - generate-readme.py
  - setup-hooks.py
  - pre-push-hook.py
  - push-plugins.sh

### Step 3: Generate initial README

```bash
uv run python scripts/generate-readme.py --marketplace-dir .
```

### Step 4: Commit infrastructure

```bash
git add -A && git commit -m "Install CI/CD infrastructure" && git push
```

## Phase 3: Link Plugin Repos

For each plugin repo to link:

### Step 1: Add as git submodule

```bash
git submodule add "https://github.com/{{owner}}/{{plugin-repo}}.git" "plugins/{{plugin-repo}}"
```

### Step 2: Install notification workflow in plugin repo

Clone the plugin repo and install `.github/workflows/notify-marketplace.yml`. This workflow triggers a `repository_dispatch` event on the marketplace repo whenever the plugin is pushed.

### Step 3: Configure MARKETPLACE_PAT secret

```bash
gh secret set MARKETPLACE_PAT --repo "{{owner}}/{{plugin-repo}}"
```

The user will be prompted to paste the PAT value.

### Step 4: Add plugin entry to marketplace.json

Add an entry to the `plugins` array:
```json
{
  "name": "{{plugin-repo}}",
  "path": "plugins/{{plugin-repo}}",
  "source": {
    "type": "github",
    "owner": "{{owner}}",
    "repo": "{{plugin-repo}}"
  },
  "version": "0.0.0"
}
```

### Step 5: Test notification chain

```bash
gh workflow run notify-marketplace.yml --repo "{{owner}}/{{plugin-repo}}"
```

Verify the marketplace repo receives the dispatch and updates.

### Step 6: Commit changes

```bash
git add -A && git commit -m "Link plugin: {{plugin-repo}}" && git push
```

Reference: [Plugin Linking Guide](references/plugin-linking-guide.md)
  - Adding a Plugin to the Marketplace
  - Removing a Plugin from the Marketplace
  - Configuring MARKETPLACE_PAT Secret
  - Installing Notification Workflow
  - Testing the Notification Chain
  - Batch Operations

## Phase 4: Plugin Management

### Add a plugin

1. Add git submodule for the plugin repo
2. Install `notify-marketplace.yml` in the plugin's `.github/workflows/`
3. Set `MARKETPLACE_PAT` secret on the plugin repo
4. Add entry to `marketplace.json`
5. Run `uv run python scripts/sync_marketplace_versions.py`

### Remove a plugin

1. Remove the plugin entry from `marketplace.json`
2. Run `git submodule deinit plugins/{{plugin-repo}} && git rm plugins/{{plugin-repo}}`
3. Delete `notify-marketplace.yml` from the plugin repo
4. Run `uv run python scripts/sync_marketplace_versions.py`

### Update a plugin

Automatic via the notification chain:
1. Developer pushes to plugin repo
2. `notify-marketplace.yml` fires `repository_dispatch`
3. Marketplace's `update-submodules.yml` pulls latest and regenerates README

Reference: [Plugin Linking Guide](references/plugin-linking-guide.md)
  - Adding a Plugin to the Marketplace
  - Removing a Plugin from the Marketplace
  - Updating a Plugin Version

## Phase 5: Validate and Verify

### Step 1: Run marketplace validation

```bash
uv run python scripts/validate_marketplace.py {{marketplace-path}} --verbose
```

Confirm: marketplace.json is valid, all plugin entries have source config, workflows are installed.

### Step 2: Test end-to-end notification chain

Make a trivial commit to a linked plugin repo and push. Verify:
- Plugin's `notify-marketplace.yml` triggers
- Marketplace's `update-submodules.yml` runs
- Submodule is updated and README regenerated

### Step 3: Install git hooks

```bash
uv run python scripts/setup-hooks.py --marketplace-dir {{marketplace-path}}
```

This installs a pre-push hook that runs cpv validation before every push.

### Step 4: Verify CI workflows

```bash
gh run list --repo "{{owner}}/{{marketplace-name}}" --limit 5
```

Confirm recent workflow runs completed successfully.

## Completion Checklist

Copy this checklist and track your progress as you complete each step.

### Repository Setup
- [ ] Marketplace GitHub repo created and public
- [ ] .claude-plugin/marketplace.json created with valid schema
- [ ] README.md generated with plugin table and architecture diagram
- [ ] LICENSE file added (MIT recommended)
- [ ] .gitignore configured (Python, Node, OS files)

### CI/CD Workflows
- [ ] update-submodules.yml installed in .github/workflows/
- [ ] validate-marketplace.yml installed in .github/workflows/
- [ ] MARKETPLACE_PAT secret configured on marketplace repo
- [ ] Workflows tested with manual dispatch (`gh workflow run`)
- [ ] Workflow permissions set (contents: write for submodule updates)

### Scripts
- [ ] scripts/sync_marketplace_versions.py installed
- [ ] scripts/generate-readme.py installed
- [ ] scripts/setup-hooks.py installed
- [ ] All scripts are executable (`chmod +x`)
- [ ] Scripts run successfully with `uv run`

### Plugin Linking (repeat per plugin)
- [ ] Plugin added as git submodule under plugins/
- [ ] notify-marketplace.yml installed in plugin .github/workflows/
- [ ] MARKETPLACE_PAT secret set on plugin repo
- [ ] Plugin entry added to marketplace.json with correct source config
- [ ] Notification chain tested (plugin push triggers marketplace update)
- [ ] Plugin passes `validate_plugin.py` validation

### Validation
- [ ] validate_marketplace.py passes with --verbose on marketplace repo
- [ ] validate_plugin.py passes on each linked plugin
- [ ] Git pre-push hooks installed and functional
- [ ] CI validation workflow runs on PR and push events
- [ ] All validation results show zero major findings

### Documentation
- [ ] README.md has architecture diagram (Mermaid)
- [ ] README.md has plugin table (auto-generated from marketplace.json)
- [ ] README.md has installation instructions for consumers
- [ ] README.md has developer setup guide
- [ ] README.md has marketplace maintenance section
- [ ] CONTRIBUTING.md added with plugin submission guidelines

### Security
- [ ] MARKETPLACE_PAT has minimum required scopes (repo + workflow only)
- [ ] No secrets committed to repository (checked with git log search)
- [ ] No private filesystem paths in any committed files
- [ ] .gitignore covers .env, *.pem, credentials files
- [ ] Branch protection enabled on main branch

## Troubleshooting

Common issues and solutions are documented in the troubleshooting guide. Check it if you encounter errors during setup or if the notification chain does not fire.

Reference: [Troubleshooting Guide](references/troubleshooting.md)

## Resources

### [Marketplace Architecture](references/marketplace-architecture.md)
- 3-Repo Architecture Pattern
- Notification Flow
- marketplace.json Schema
- Plugin Entry Schema
- MARKETPLACE_PAT Configuration
- Directory Structure
- Event Types
- Validation Pipeline

### [Workflow Templates](references/workflow-templates.md)
- Placeholder Reference
- notify-marketplace.yml (Plugin Side)
- update-submodules.yml (Marketplace Side)
- validate-marketplace.yml (Marketplace CI)
- Plugin CI Workflow (Optional)

### [Script Templates](references/script-templates.md)
- Placeholder Reference
- sync_marketplace_versions.py
- generate-readme.py
- setup-hooks.py
- pre-push-hook.py
- push-plugins.sh

### [Plugin Linking Guide](references/plugin-linking-guide.md)
- Adding a Plugin to the Marketplace
- Removing a Plugin from the Marketplace
- Updating a Plugin Version
- Configuring MARKETPLACE_PAT Secret
- Installing Notification Workflow
- Testing the Notification Chain
- Batch Operations

### [README Template](references/readme-template.md)
- Template Content
- Placeholder Reference
- Auto-Generation
- Customization Guide

### [Troubleshooting Guide](references/troubleshooting.md)
- Authentication Issues
- Repository Creation Failures
- CI/CD Pipeline Issues
- Notification Chain Failures
- Validation Failures
- Secret Configuration Issues
- Common Error Messages
- Debug Commands
