---
description: Validate a Claude Code plugin from a GitHub repository without installing it
---

Validate a plugin hosted on GitHub by cloning it to a temporary directory and running the full CPV validation suite (190+ rules).

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_github_validate.py" --plugin <owner/repo>
```

The user provides either:
- A GitHub URL: `https://github.com/owner/repo`
- A shorthand: `owner/repo`

The script clones with `--depth 1`, runs `validate_plugin.py`, reports results, and cleans up the temp directory.

If the validation reports errors or warnings, summarize them clearly. If the repo doesn't contain `.claude-plugin/plugin.json`, report that it's not a valid plugin.
