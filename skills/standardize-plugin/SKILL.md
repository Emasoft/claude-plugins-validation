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
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <plugin-path> [--report report.md]
```

Checks: validation rules (190+), pipeline readiness (hooks, workflows, publish.py, cliff.toml), file inventory vs standard template, .gitignore completeness, README badge markers.

## Fix a Plugin Repository

```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <plugin-path> --fix [--dry-run]
```

Generates missing files without modifying existing code: adds workflows, hooks, cliff.toml, .python-version, badge markers. Does NOT touch plugin.json, pyproject.toml versions, or existing source code.

After standardize --fix, you MUST still fix remaining issues manually:
- .gitignore gaps (standardize warns but does not auto-add all entries)
- SKILL.md missing Nixtla sections (Overview, Prerequisites, Output, Error Handling, Examples, Resources)
- README badge markers and component tables
- Any MINOR or NIT issues
The pre-push hook blocks on CRITICAL, MAJOR, MINOR, and NIT. Only WARNINGs pass.

## Audit a Marketplace

```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_marketplace.py" <marketplace-path> [--report report.md]
```

Validates marketplace.json, checks all plugin sources point to external GitHub repos (flags local paths), verifies CI/CD workflows.

## Fix a Marketplace

```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_marketplace.py" <marketplace-path> --fix [--dry-run]
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
- `git-hooks/pre-push` — thin bash delegator to `publish.py --gate`
  → Runs 4 gates: version bump, lint, validate --strict, tests. Blocks ALL except WARNINGs.
- `.github/workflows/ci.yml` — lint + validate + test
- `.github/workflows/release.yml` — tagged releases
- `.github/workflows/validate.yml` — plugin validation
- `.github/workflows/notify-marketplace.yml` — marketplace notification

## Pipeline Rules

See [Pipeline Rules](../canonical-pipeline/references/pipeline-rules.md) for the full set of mandatory rules.

## TOKEN OPTIMIZATION
Use `mcp__plugin_llm-externalizer_llm-externalizer__*` tools for bounded analysis. Pass file paths via `input_files_paths`.
