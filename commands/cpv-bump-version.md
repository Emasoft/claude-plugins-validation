---
name: cpv-bump-version
description: Bump plugin version in plugin.json and pyproject.toml
agent: plugin-manager
user-invocable: true
---

Bump the plugin version:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" --patch   # 1.2.0 -> 1.2.1
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" --minor   # 1.2.0 -> 1.3.0
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" --major   # 1.2.0 -> 2.0.0
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" --set 2.0.0  # explicit version
```

Updates both `plugin.json` and `pyproject.toml` (if present).

See the **plugin-management** skill for version bump details.
