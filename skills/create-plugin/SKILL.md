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

Generated files: plugin.json, pyproject.toml, .python-version, .gitignore, README.md (with badge markers), LICENSE, cliff.toml, scripts/publish.py (with --gate/--install-hook modes), scripts/setup-hooks.py, git-hooks/pre-push (thin bash delegator), .github/workflows/{ci,release,validate,notify-marketplace}.yml, empty commands/agents/skills/tests/ directories.

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

1. Validate: `uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_plugin.py" <target-dir> --strict`
1b. **FIX ALL issues** — CRITICAL, MAJOR, MINOR, NIT must all be fixed. Only WARNINGs may remain. The pre-push hook will block publishing otherwise.
2. Init git: `cd <target-dir> && git init && git add -A && git commit -m "Initial scaffold"`
3. Create repo: `gh repo create <owner>/<name> --public --source . --push` Then configure hooks: `git config core.hooksPath git-hooks`
4. Register in marketplace (if applicable)

## Standardize Existing Repos

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <path> [--fix] [--dry-run]
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_marketplace.py" <path> [--fix] [--dry-run]
```

## Pipeline Rules

See [Pipeline Rules](../canonical-pipeline/references/pipeline-rules.md) for mandatory rules on pre-push hooks, fix-all mandate, running CPV scripts, and GitHub secrets.

## TOKEN OPTIMIZATION
Use `mcp__plugin_llm-externalizer_llm-externalizer__*` tools for bounded analysis. Pass file paths via `input_files_paths`.
