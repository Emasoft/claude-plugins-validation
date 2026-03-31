---
name: cpv-validate-github-plugin
description: Validate a Claude Code plugin from a GitHub repository without installing it (optional --audit for security scan)
user-invocable: true
---

Validate a plugin hosted on GitHub by cloning it to a temporary directory and running the full CPV validation suite (190+ rules).

**Validation only:**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_github_validate.py" --plugin <owner/repo>
```

**Validation + security audit**:
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_github_validate.py" --plugin <owner/repo> --audit
```

The `--audit` flag adds a security scan via `skill-audit` (prompt injection, secrets, shellcheck, semgrep) on top of the standard 190+ rule validation.

The user provides either:
- A GitHub URL: `https://github.com/owner/repo`
- A shorthand: `owner/repo`

The script clones with `--depth 1`, runs validation (and optionally security audit), reports results, and cleans up the temp directory.

If the validation reports errors or warnings, summarize them clearly. If the repo doesn't contain `.claude-plugin/plugin.json`, report that it's not a valid plugin.
