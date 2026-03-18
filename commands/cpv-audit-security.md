---
description: Run a security audit on a plugin or marketplace (prompt injection, secrets, shellcheck, semgrep)
---

Run a dedicated security audit using `skill-audit`. This checks for:
- **Prompt injection** patterns in markdown files (commands, skills, agents, rules)
- **Leaked secrets** via TruffleHog (API keys, tokens, passwords)
- **Shell command issues** via ShellCheck (hooks, scripts)
- **Code vulnerabilities** via Semgrep (path traversal, injection, etc.)

The target can be:
- A directory path: `./my-plugin/`
- An installed plugin in canonical form: `<plugin-name>@<marketplace-name>` — resolve to `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`
- A registered marketplace by name — resolve to `~/.claude/plugins/marketplaces/<name>/`

**For a local directory:**
```bash
skill-audit ./my-plugin/ -v
```

**For a JSON report:**
```bash
skill-audit ./my-plugin/ -f json
```

Report each scanner's result (passed/failed/skipped) and list any findings with severity, message, and file location.

Note: `skill-audit` must be installed separately (`pip install skill-audit` or `uvx skill-audit`). If not found, suggest installation.
