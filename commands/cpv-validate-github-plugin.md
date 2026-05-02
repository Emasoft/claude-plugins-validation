---
name: cpv-validate-github-plugin
description: Validate a Claude Code plugin from a GitHub repository without installing it (optional --audit for security scan)
user-invocable: true
---

Validate a plugin hosted on GitHub by cloning it to a temporary directory and running the full CPV validation suite (190+ rules).

## ONE-LINER (use this — do not invent your own bash)

**Validation only:**
```bash
CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  github --plugin "$REPO" --report "$REPORT_FILE"
```

**Validation + security audit:**
```bash
CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  github --plugin "$REPO" --audit --report "$REPORT_FILE"
```

**Alternative — security-only scan via direct URL ingestion (v2.48+):**
```bash
CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  security "https://github.com/$REPO" --report "$REPORT_FILE"
```

The `validate_security.py` script (v2.48+) auto-detects GitHub URLs and clones them itself — no need for the `github` wrapper if you only need the security scan.

The `--audit` flag adds a security scan via `skill-audit` (prompt injection, secrets, shellcheck, semgrep) on top of the standard 190+ rule validation.

The user provides either:
- A GitHub URL: `https://github.com/owner/repo`
- A shorthand: `owner/repo`

The script clones with `--depth 1`, runs validation (and optionally security audit), reports results, and cleans up the temp directory.

If the validation reports errors or warnings, summarize them clearly. If the repo doesn't contain `.claude-plugin/plugin.json`, report that it's not a valid plugin.

## Report path resolution

```bash
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  MAIN_ROOT="$(git worktree list | head -n1 | awk '{print $1}')"
else
  MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
fi
REPORT_DIR="$MAIN_ROOT/reports/validate_github_plugin"
mkdir -p "$REPORT_DIR"
REPORT_FILE="$REPORT_DIR/$(date +%Y%m%d_%H%M%S%z)-$(echo "$REPO" | tr '/' '_').md"
```
