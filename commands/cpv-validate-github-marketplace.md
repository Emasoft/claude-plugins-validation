---
name: cpv-validate-github-marketplace
description: Validate a Claude Code marketplace from a GitHub repository without registering it (optional --audit for security scan)
user-invocable: true
---

Validate a marketplace hosted on GitHub by cloning it to a temporary directory and running the CPV marketplace validator.

## ONE-LINER (use this — do not invent your own bash)

**Validation only:**
```bash
CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  github --marketplace "$REPO" --report "$REPORT_FILE"
```

**Validation + security audit (every plugin in marketplace gets scanned, v2.48 tree-scan-once architecture):**
```bash
CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  github --marketplace "$REPO" --audit --report "$REPORT_FILE"
```

**Alternative — direct security scan of all plugins via `--marketplace` flag (v2.48+):**
```bash
CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  security --marketplace "github:$REPO" --report "$REPORT_FILE"
```

The v2.48 `validate_security.py --marketplace` mode stages all plugins to a single corpus, runs fclones cross-plugin dedup (saves 20-40% on typical marketplaces by deduplicating shared READMEs/templates), then runs all 5 external scanners (cc-audit, tirith, trufflehog, semgrep, Cisco AI Defense skill-scanner) in tree-scan-once mode with per-plugin bucketing.

The `--audit` flag adds a security scan via `skill-audit` on top of the standard marketplace validation.

The user provides either:
- A GitHub URL: `https://github.com/owner/repo`
- A shorthand: `owner/repo`

Report: marketplace manifest validity, number of plugins, source reference integrity, deduplication summary, and any validation or security findings (bucketed per plugin).

## Report path resolution

```bash
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  MAIN_ROOT="$(git worktree list | head -n1 | awk '{print $1}')"
else
  MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
fi
REPORT_DIR="$MAIN_ROOT/reports/validate_github_marketplace"
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
│ 1 │ Fix ALL issues (incl. WARNING)                  │ Dispatch the marketplace-fixer agent on every finding                 │ CRITICAL+MAJOR+MINOR+NIT+WARNING │
│ 2 │ Fix NIT and higher                              │ Skip WARNING-only findings                                            │ CRITICAL+MAJOR+MINOR+NIT         │
│ 3 │ Fix MINOR and higher                            │ Skip NIT and WARNING                                                  │ CRITICAL+MAJOR+MINOR             │
│ 4 │ Fix MAJOR and higher                            │ Only fix the publish-blockers (and CRITICALs)                         │ CRITICAL+MAJOR                   │
│ 5 │ Fix CRITICAL only                               │ Strictest mode — fix the loaders/security blockers and nothing else   │ CRITICAL                         │
│ 0 │ End                                             │ Done — exit without running the fixer                                 │ —                                │
└───┴─────────────────────────────────────────────────┴───────────────────────────────────────────────────────────────────────┴──────────────────────────────────┘
Type a number to choose:
```

On `0` → reply `Done.` and stop.

On `1`-`5` → dispatch the **marketplace-fixer** agent with the report path
and the chosen `min_severity`. NOTE: since the temp clone has been cleaned
up, mechanical marketplace fixes can be applied only after the user
re-clones the marketplace locally; the fixer can also handle architectural
migrations (Layout A↔B↔C) on a local clone.

| Row | min_severity | Agent prompt template                                                  |
|-----|--------------|------------------------------------------------------------------------|
| 1   | `WARNING`    | `Fix every finding in <REPORT_PATH>. min_severity=WARNING.`            |
| 2   | `NIT`        | `Fix findings in <REPORT_PATH>. min_severity=NIT.`                     |
| 3   | `MINOR`      | `Fix findings in <REPORT_PATH>. min_severity=MINOR.`                   |
| 4   | `MAJOR`      | `Fix findings in <REPORT_PATH>. min_severity=MAJOR.`                   |
| 5   | `CRITICAL`   | `Fix findings in <REPORT_PATH>. min_severity=CRITICAL.`                |

After the agent returns, reply `Done.` and stop.
