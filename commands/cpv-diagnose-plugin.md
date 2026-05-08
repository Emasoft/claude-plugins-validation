---
name: cpv-diagnose-plugin
description: Deep diagnostic audit of an existing plugin — pipeline staleness, security (with all 5 external scanners), cross-platform issues, marketplace registration, cached-vs-github sync. Returns a structured report + offers a follow-up menu.
argument-hint: <plugin-path>
user-invocable: true
---

# /cpv-diagnose-plugin

Deep diagnostic audit of an existing Claude Code plugin. Goes beyond
`/cpv-validate-plugin` (which only checks structure) by ALSO checking:

- **Pipeline staleness** — does `publish.py` have idempotency helpers?
  Are there shipped `.sh` scripts? Hook commands using bash-only
  constructs (`set -euo pipefail`, `[[ ]]`, etc.)? `os.path` instead of
  `pathlib`? Hooks writing to `${CLAUDE_PLUGIN_ROOT}` (state lost on
  every plugin update)?
- **Security** — runs all 5 external scanners (cc-audit, tirith,
  trufflehog, semgrep, Cisco AI Defense skill-scanner) plus the
  in-process security validator.
- **Cross-platform compliance** — flags every shell script, jq/sed/awk
  usage, hardcoded `/tmp/`, `shell=True`, `os.geteuid()`, anything that
  breaks on Windows.
- **Marketplace registration** — is the plugin registered in any
  marketplace's `marketplace.json`? Does the marketplace's notify
  workflow exist? Is `MARKETPLACE_PAT` configured?
- **Cached-vs-GitHub sync** — when the plugin is installed via cache,
  is the cache version current with the latest GitHub tag?
- **Missing/duplicated parts** — same skill name in two folders, same
  MCP server in `.mcp.json` AND inline plugin.json, etc.

After the report, the agent prints a follow-up numbered-table menu
offering: full upgrade to current standards, CRITICAL fixes only,
register/create marketplace, sync cache from GitHub, or end.

## Usage

```bash
# Dispatch the plugin-diagnoser agent
/cpv-diagnose-plugin /path/to/my-plugin
```

The agent writes the diagnostic report to
`$MAIN_ROOT/reports/plugin-diagnoser/<ts±tz>-<plugin-name>.md` and
prints a 5-line summary plus the follow-up menu.

## Behavior

- ALWAYS runs the 5 external scanners (~1–3 min). To skip them and
  return faster, use `/cpv-validate-plugin` instead — that's structure-only.
- NEVER mutates the plugin (read-only). Mutations require user
  confirmation via the follow-up menu.
- Cross-platform checks treat **Python and Node.js/TypeScript** as the
  only fully cross-platform stacks. Anything else (bash, jq, sed, awk,
  POSIX-only tools) is flagged.
- Marketplace check: scans `~/.claude/plugins/known_marketplaces.json`
  AND probes `gh api repos/<owner>/<plugin>` for related marketplaces.
- Sync check: compares the cached install's `plugin.json.version`
  against `gh api repos/<owner>/<plugin>/releases/latest`.

## When to use

- Auditing a plugin you're considering installing.
- Auditing your own plugin before publishing a release.
- Onboarding an inherited plugin (find every issue at once).
- After a CPV major version bump (verify the plugin still meets current standards).
