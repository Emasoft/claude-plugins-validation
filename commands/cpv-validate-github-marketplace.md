---
description: Validate a Claude Code marketplace from a GitHub repository without registering it
---

Validate a marketplace hosted on GitHub by cloning it to a temporary directory and running the CPV marketplace validator.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_github_validate.py" --marketplace <owner/repo>
```

The user provides either:
- A GitHub URL: `https://github.com/owner/repo`
- A shorthand: `owner/repo`

Report: marketplace manifest validity, number of plugins, source reference integrity, and any validation findings.
