---
name: standardize-plugin
description: >
  Audit and standardize existing Claude Code plugin or marketplace repositories to match
  CPV standards. Triggers when user mentions standardizing, auditing repo structure,
  fixing plugin infrastructure, or enforcing standards on existing plugins.
---

# Standardize Plugin / Marketplace

## Audit a Plugin Repository

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <plugin-path> [--report report.md]
```

Checks: validation rules (190+), pipeline readiness (hooks, workflows, publish.py, cliff.toml), file inventory vs standard template, .gitignore completeness, README badge markers.

## Fix a Plugin Repository

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <plugin-path> --fix [--dry-run]
```

Generates missing files without modifying existing code: adds workflows, hooks, cliff.toml, .python-version, badge markers. Does NOT touch plugin.json, pyproject.toml versions, or existing source code.

## Audit a Marketplace

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_marketplace.py" <marketplace-path> [--report report.md]
```

Validates marketplace.json, checks all plugin sources point to external GitHub repos (flags local paths), verifies CI/CD workflows.

## Fix a Marketplace

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_marketplace.py" <marketplace-path> --fix [--dry-run]
```

## Standard Plugin Files

Every plugin repo SHOULD have:
- `.claude-plugin/plugin.json` — manifest (REQUIRED)
- `pyproject.toml` — Python project config
- `.python-version` — reproducible builds
- `.gitignore` — includes .claude/, .tldr/, llm_externalizer_output/
- `README.md` — with `<!--BADGES-START-->` / `<!--BADGES-END-->`
- `cliff.toml` — changelog generation
- `scripts/publish.py` — release automation
- `.githooks/pre-push` — quality gate
- `.github/workflows/ci.yml` — lint + validate + test
- `.github/workflows/release.yml` — tagged releases
- `.github/workflows/validate.yml` — plugin validation
- `.github/workflows/notify-marketplace.yml` — marketplace notification

## TOKEN OPTIMIZATION
Use `mcp__plugin_llm-externalizer_llm-externalizer__*` tools for bounded analysis. Pass file paths via `input_files_paths`.
