---
name: cpv-standardize
description: Audit and fix a plugin or marketplace repo to match CPV standards (auto-detects type from directory content)
user-invocable: true
---

Smart standardization command that auto-detects whether the target directory is a **plugin** or **marketplace** and runs the appropriate standardizer.

## Auto-Detection Logic

- If `.claude-plugin/marketplace.json` exists → marketplace
- If `.claude-plugin/plugin.json` exists → plugin
- Otherwise → ask the user

## Plugin Standardization

**Audit only (report gaps):**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <path>
```

**Audit and fix (add missing files):**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <path> --fix
```

**Preview fixes without applying:**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <path> --fix --dry-run
```

Checks for missing: .githooks/pre-push, scripts/publish.py, cliff.toml, .github/workflows/{ci,release,validate,notify-marketplace}.yml, .python-version, pyproject.toml, badge markers in README.md, .gitignore entries.

## Marketplace Standardization

**Audit only:**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_marketplace.py" <path>
```

**Audit and fix:**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_marketplace.py" <path> --fix
```

Checks: marketplace.json structure, all plugin sources point to external GitHub repos (flags local paths as errors), CI/CD workflows exist, README has plugin catalog.

## Common Behavior

With `--fix`, both standardizers generate missing files using standard templates WITHOUT modifying existing code, manifests, or versions. Use `--dry-run` to preview changes before applying.
