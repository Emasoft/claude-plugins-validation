---
name: cpv-validate-github-marketplace
description: Validate a Claude Code marketplace from a GitHub repository without registering it (optional --audit for security scan)
user-invocable: true
---

Validate a marketplace hosted on GitHub by cloning it to a temporary directory and running the CPV marketplace validator.

**Validation only:**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_github_validate.py" --marketplace <owner/repo>
```

**Validation + security audit** (replaces the former `/cpv-audit-security` for marketplaces):
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_github_validate.py" --marketplace <owner/repo> --audit
```

The `--audit` flag adds a security scan via `skill-audit` on top of the standard marketplace validation.

The user provides either:
- A GitHub URL: `https://github.com/owner/repo`
- A shorthand: `owner/repo`

Report: marketplace manifest validity, number of plugins, source reference integrity, and any validation or security findings.
