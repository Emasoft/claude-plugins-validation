---
description: Create a new marketplace hub repository for Claude Code plugins
---

Scaffold a marketplace hub repository. Marketplaces are HUBS ONLY — they contain pointers to external plugin repos, never plugin code.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/generate_marketplace_repo.py" <target-dir> \
  --name <marketplace-name> \
  --owner-name "<display-name>" \
  --description "<description>" \
  --github-owner <github-username> \
  [--add-plugin owner/repo]... \
  [--dry-run]
```

Parse the user's request for marketplace name, description, and any initial plugins to include. Each plugin must be specified as a GitHub owner/repo (e.g., `Emasoft/my-plugin`).

After generation, suggest `git init && git add -A && git commit -m "Initial marketplace scaffold"` and `gh repo create`.
