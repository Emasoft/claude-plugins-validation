---
description: Audit an existing plugin repo and fix it to match CPV standards
---

Audit and optionally fix an existing plugin repository to match CPV's standard file structure.

**Audit only (report gaps):**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <plugin-path>
```

**Audit and fix (add missing files):**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <plugin-path> --fix
```

**Preview fixes without applying:**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_plugin.py" <plugin-path> --fix --dry-run
```

The standardizer checks for missing: .githooks/pre-push, scripts/publish.py, cliff.toml, .github/workflows/{ci,release,validate,notify-marketplace}.yml, .python-version, pyproject.toml, badge markers in README.md, .gitignore entries.

With `--fix`, it generates missing files using standard templates WITHOUT modifying existing code, manifests, or versions.
