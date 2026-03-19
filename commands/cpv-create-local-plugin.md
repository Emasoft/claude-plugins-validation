---
description: Create a new Claude Code plugin locally with all standard files (local only — no GitHub repo creation)
---

Scaffold a complete plugin repository **locally** using the generator script. This command creates the directory structure and files on disk only — it does NOT create a GitHub repository or push anything.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/generate_plugin_repo.py" <target-dir> \
  --name <plugin-name> \
  --description "<description>" \
  --author "<author-name>" \
  --author-email "<email>" \
  --github-owner <github-username> \
  --marketplace <marketplace-name> \
  [--license MIT] \
  [--python-version 3.12] \
  [--dry-run]
```

Parse the user's request to determine the plugin name, description, and other parameters. Ask for any missing required parameters (name, description, author, github-owner).

After generation:
1. Run `validate_plugin.py` on the result to verify it passes
2. Suggest `git init && git add -A && git commit -m "Initial scaffold"`
3. To publish on GitHub, use `/cpv-publish-as-github-repo <target-dir>`
4. To register in a marketplace, use `/cpv-publish-plugin-to-marketplace`
