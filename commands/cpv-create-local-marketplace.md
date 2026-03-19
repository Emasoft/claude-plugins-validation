---
name: cpv-create-local-marketplace
description: Create a new marketplace hub locally for Claude Code plugins (local only — no GitHub repo creation)
user-invocable: true
---

Scaffold a marketplace hub repository **locally**. This command creates the directory structure and files on disk only — it does NOT create a GitHub repository or push anything.

Marketplaces are HUBS ONLY — they contain pointers to external plugin repos, never plugin code.

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

After generation:
1. Suggest `git init && git add -A && git commit -m "Initial marketplace scaffold"`
2. To publish on GitHub, use `/cpv-create-github-marketplace`
