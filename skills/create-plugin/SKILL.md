---
name: create-plugin
description: >
  Create Claude Code plugin or marketplace repos with full CI/CD.
  Use when scaffolding a new plugin or marketplace. Loaded by plugin-creator agent.
user-invocable: false
---

# Create Plugin / Marketplace

## Overview

Scaffolds complete Claude Code plugin or marketplace repositories with standard files, CI/CD workflows, git hooks, and release pipeline.

## Marketplace layout

CPV supports two layouts — **Layout A (hub-and-spoke)** with one repo per plugin, or **Layout B (nested)** with plugins as subfolders inside the marketplace repo. Both work with Claude Code. Default to A for new marketplaces; follow the user's preference without argument. Full guide in [marketplace-layouts.md](references/marketplace-layouts.md).

## Prerequisites

- `git`, `uv`, `gh` CLI on PATH
- CPV plugin installed
- GitHub account (for publishing)

## Instructions

1. **Create a Plugin Repository**:
   ```bash
   uv run "${CLAUDE_PLUGIN_ROOT}/scripts/generate_plugin_repo.py" <target-dir> \
     --name <plugin-name> --description "<description>" \
     --author "<author>" --author-email "<email>" \
     --github-owner <github-username> [--marketplace <name>]
   ```

2. **Create a Marketplace Hub** (HUBS ONLY — no plugin code inside):
   ```bash
   uv run "${CLAUDE_PLUGIN_ROOT}/scripts/generate_marketplace_repo.py" <target-dir> \
     --name <marketplace-name> --owner-name "<display-name>" \
     --description "<description>" --github-owner <github-username>
   ```

3. **After generation**:
   - Validate: `uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_plugin.py" <target-dir> --strict`
   - Fix ALL issues (CRITICAL, MAJOR, MINOR, NIT) — only WARNINGs may remain
   - Init git: `cd <target-dir> && git init && git add -A && git commit -m "Initial scaffold"`
   - Create repo: `gh repo create <owner>/<name> --public --source . --push`
   - Configure hooks: `git config core.hooksPath git-hooks`

Copy this checklist and track your progress:
- [ ] Repository generated
- [ ] Validation passed
- [ ] All issues fixed
- [ ] Git initialized and committed
- [ ] GitHub repo created
- [ ] Hooks configured

4. **Standardize existing repos**:
   ```bash
   uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <path> [--fix]
   uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_marketplace.py" <path> [--fix]
   ```

## Output

- Complete plugin/marketplace directory with all standard files
- CI/CD workflows, pre-push hook, publish.py, cliff.toml
- README with badge markers and component tables

## Error Handling

| Error | Resolution |
|-------|------------|
| Target directory exists | Choose a different name or remove the existing directory |
| Missing required arguments | Provide all mandatory flags (`--name`, `--github-owner`) |
| Validation fails after generation | Run `standardize --fix`, then fix remaining issues manually |
| `ModuleNotFoundError: yaml` | Use `uv run --with pyyaml python` when outside CPV venv |

## Examples

**Create plugin:**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/generate_plugin_repo.py" /tmp/my-plugin \
  --name my-plugin --description "My awesome plugin" \
  --author "Me" --author-email "me@example.com" --github-owner MyGitHub
```

**Create marketplace:**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/generate_marketplace_repo.py" /tmp/my-mkt \
  --name my-marketplace --owner-name "My Org" --github-owner MyGitHub
```

## Resources

- [Pipeline Rules](references/pipeline-rules.md) — mandatory rules for all plugin operations
  > Pre-Push Hook · Fix-All Mandate · Running CPV Scripts · Processing Validation Output · GitHub Secrets · CI Workflow Dependencies · Marketplace Notification · All Scripts Are Python · Binary Plugins · README Requirements · Pre-Publish Local Dry-Run · Post-Push CI Verification · Mega-Linter Configuration · Common Fixes Reference
- [v2.1.80+ Plugin Features](references/v2-1-80-features.md)
  > Monitor tool · userConfig · channels · CLAUDE_PLUGIN_OPTION_<KEY> · Inline marketplace · managed-settings.d/ · Plugin skill `name` field
- [Marketplace Layouts](references/marketplace-layouts.md)
  > Overview · Layout A — Hub-and-Spoke (separate repos) · Layout B — Nested single-repo (monorepo) · How Claude Code updates plugins in each layout · When to choose which · Rich metadata fields (author, homepage, license, category) · Why CPV does not use git-subdir · Encountering a non-CPV marketplace · Refactoring between layouts · Agent behavior summary

## MCP Server Bundling

Place bundled MCP executables in **`servers/`** (docs convention), reference as `${CLAUDE_PLUGIN_ROOT}/servers/<name>`. Server names unique across sources. See `skills/fix-validation/references/empirical-loading-bugs.md` for 5 silent footguns CPV catches.

## Token Optimization

Use `mcp__plugin_llm-externalizer_llm-externalizer__*` for bounded analysis. Pass paths via `input_files_paths`.
