---
description: Add, remove, list, or update GitHub plugin marketplaces
---

Manage marketplace registrations. Parse the user's request and run the appropriate subcommand.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" add <owner/repo>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" remove <name>
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" list [--json]
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_marketplace.py" update [name]
```

Accepts any GitHub URL format for `add` (full URLs, SSH, git://, owner/repo). After changes, remind the user to run `/reload-plugins`.

If `add` fails, verify the repo exists and contains `.claude-plugin/marketplace.json`. Use `/cpv-validate-github-marketplace` to check a repo before adding.
