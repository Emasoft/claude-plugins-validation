---
description: Create a new Claude Code plugin repository from scratch with all standard files
---

Scaffold a complete plugin repository using the generator script.

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
3. Suggest `gh repo create <owner>/<name> --public --source . --push`
4. Suggest registering in a marketplace
