---
name: show-version
description: Show the CPV management tools version
when_to_use: When the cpv-main-menu user picks Help → Show CPV version, or any flow needs to print the CPV management CLI version string
user-invocable: false
allowed-tools: Bash(uv:*)
---

# show-version

Show the management CLI version:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --version
```
