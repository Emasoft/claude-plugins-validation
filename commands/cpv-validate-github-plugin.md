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

## Post-validate fix prompt (mandatory)

After printing the validation summary, print the following 6-row Unicode
table verbatim and wait for the user's number. Do NOT skip — even on
PASS / VALID, the user always gets the explicit "fix N or end" choice.
NEVER ask "what's next?" generically.

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Action                                          ┃ What it does                                                          ┃ Severities the fixer will touch ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Fix ALL issues (incl. WARNING)                  │ Dispatch the plugin-fixer agent on every finding in the report        │ CRITICAL+MAJOR+MINOR+NIT+WARNING │
│ 2 │ Fix NIT and higher                              │ Skip WARNING-only findings                                            │ CRITICAL+MAJOR+MINOR+NIT         │
│ 3 │ Fix MINOR and higher                            │ Skip NIT and WARNING                                                  │ CRITICAL+MAJOR+MINOR             │
│ 4 │ Fix MAJOR and higher                            │ Only fix the publish-blockers (and CRITICALs)                         │ CRITICAL+MAJOR                   │
│ 5 │ Fix CRITICAL only                               │ Strictest mode — fix the loaders/security blockers and nothing else   │ CRITICAL                         │
│ 0 │ End                                             │ Done — exit without running the fixer                                 │ —                                │
└───┴─────────────────────────────────────────────────┴───────────────────────────────────────────────────────────────────────┴──────────────────────────────────┘
Type a number to choose:
```

On `0` → reply `Done.` and stop.

On `1`-`5` → the report lives on the user's local disk (the temp clone is
gone). Dispatch the **plugin-fixer** agent with the report path and the
chosen `min_severity`. NOTE: since the original GitHub clone has been
cleaned up, the fixer can fix only the report's structural findings —
to apply fixes to the actual source you must clone the repo locally
first and re-validate from that path.

| Row | min_severity | Agent prompt template                                                  |
|-----|--------------|------------------------------------------------------------------------|
| 1   | `WARNING`    | `Fix every finding in <REPORT_PATH>. min_severity=WARNING.`            |
| 2   | `NIT`        | `Fix findings in <REPORT_PATH>. min_severity=NIT.`                     |
| 3   | `MINOR`      | `Fix findings in <REPORT_PATH>. min_severity=MINOR.`                   |
| 4   | `MAJOR`      | `Fix findings in <REPORT_PATH>. min_severity=MAJOR.`                   |
| 5   | `CRITICAL`   | `Fix findings in <REPORT_PATH>. min_severity=CRITICAL.`                |

After the fixer agent returns, reply `Done.` and stop.
