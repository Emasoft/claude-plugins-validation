---
description: Security audit a Claude Code plugin from a GitHub repository (prompt injection, secrets, shellcheck, semgrep)
---

Clone a plugin from GitHub and run both a security audit and full validation without installing it.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_github_validate.py" --audit-plugin <owner/repo>
```

The user provides either:
- A GitHub URL: `https://github.com/owner/repo`
- A shorthand: `owner/repo`

This runs `skill-audit` for security scanning and then `validate_plugin.py` for the full 190+ rule validation. Report the security audit results first (4 scanners), then the validation results. Flag any prompt injection findings, leaked secrets, or dangerous code patterns.
