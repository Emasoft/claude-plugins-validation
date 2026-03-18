---
name: create-plugin
description: >
  Create new Claude Code plugin or marketplace repositories from scratch with full CI/CD
  pipeline. Triggers when user mentions creating a plugin, scaffolding a repo, setting up
  a new marketplace, or bootstrapping a plugin project.
---

# Create Plugin / Marketplace

## Create a Plugin Repository

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/generate_plugin_repo.py" <target-dir> \
  --name <plugin-name> \
  --description "<description>" \
  --author "<author-name>" \
  --author-email "<email>" \
  --github-owner <github-username> \
  --marketplace <marketplace-name> \
  [--license MIT] \
  [--python-version 3.12] \
  [--dry-run]
```

Generated files: plugin.json, pyproject.toml, .python-version, .gitignore, README.md (with badge markers), LICENSE, cliff.toml, scripts/publish.py, scripts/setup_git_hooks.py, .githooks/pre-push, .github/workflows/{ci,release,validate,notify-marketplace}.yml, empty commands/agents/skills/tests/ directories.

## Create a Marketplace Hub

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/generate_marketplace_repo.py" <target-dir> \
  --name <marketplace-name> \
  --owner-name "<display-name>" \
  --description "<description>" \
  --github-owner <github-username> \
  [--add-plugin owner/repo]... \
  [--dry-run]
```

**CRITICAL**: Marketplaces are HUBS ONLY. Plugin sources use `{"source": "github", "repo": "owner/repo"}`. Never put plugin code in a marketplace repo.

## After Generation

1. Validate: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/validate_plugin.py" <target-dir>`
2. Init git: `cd <target-dir> && git init && git add -A && git commit -m "Initial scaffold"`
3. Create repo: `gh repo create <owner>/<name> --public --source . --push`
4. Register in marketplace (if applicable)

## Standardize Existing Repos

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <path> [--fix] [--dry-run]
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_marketplace.py" <path> [--fix] [--dry-run]
```

## TOKEN OPTIMIZATION
Use `mcp__plugin_llm-externalizer_llm-externalizer__*` tools for bounded analysis. Pass file paths via `input_files_paths`.
