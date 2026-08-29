---
name: cpv-validate-plugin-folder
description: "READ-ONLY full scan of ONE plugin or skill folder — structure and rules, the full security pipeline, secret and sensitive-data leaks, and prompt-cache CA-01..CA-07 findings in a single pass. Nothing in the target is modified. Prints a severity table, the report path, and the exact agent that fixes each finding class. Use when validating, auditing, checking or scanning a plugin folder, a skill folder, or this project before publishing or installing. Trigger with a local path, a GitHub or GitLab URL, or an owner/repo slug; with no argument it scans the current project root."
tags: [validation, security, cache, leaks, read-only, plugins, skills]
user-invocable: true
argument-hint: "[folder path, GitHub/GitLab URL, or owner/repo — defaults to the current project root]"
---

# cpv-validate-plugin-folder

Read-only. Runs one script and reports what it printed.

## Overview

One command, full coverage, for one folder shape at a time. The skill
composes CPV's existing validators — `plugin --strict`, `marketplace`,
`skill --strict`, and the full `security` scan — behind a single entry
point (`cpv_validate_plugin_folder.py`) so a caller never has to know in
advance which validator applies. It never implements a rule of its own:
every finding traces back to an existing CPV validator, and the skill's
only job is detecting the folder's shape, dispatching the right
validator combination, merging the results, and reporting a single
severity table with the report path and the fixer to run next.

## Prerequisites

- A Python 3 interpreter on `PATH` (the script is invoked as
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_validate_plugin_folder.py"`).
- `git` on `PATH` when the target is a remote URL or an `owner/repo`
  slug — the script clones it into a temp sandbox before scanning.
- No other setup: the script requires no network access for a local
  path, no installation step, and never runs the plugin/skill/repo it
  scans.

## Instructions

1. Run exactly this, substituting the user's argument when they gave one and
   omitting it entirely when they did not — the script resolves the default
   itself, so do not guess a path on its behalf:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_validate_plugin_folder.py" [folder-path-or-repo-url]
   ```

2. Relay the script's output verbatim in substance: the severity table, the
   report path, and the suggested fixer for each failing scan.

3. Stop there. Do not summarise findings away, do not re-run individual
   validators, and do not fix anything — this skill is the read-only half.
   Fixing is a separate, explicit user decision, which is why the script names
   the fixer instead of dispatching it.

The skill is done when the table, the report path and any fixer suggestions
have been relayed; a non-zero exit is a verdict to report, not an error to
retry.

## Remote repositories

A `github.com` or `gitlab.com` URL (including GitLab subgroups and `/-/tree/`
web-view forms) or a bare `owner/repo` slug is cloned into a temp sandbox,
scanned there, and the clone is deleted on EVERY exit path.

A cloned repo MUST be a plugin (`.claude-plugin/plugin.json`) or a skill
(`SKILL.md`) at its root; anything else exits with an error naming what was
missing. That gate binds remote input ONLY — a LOCAL folder still scans
whatever shape it has, so you can point this at a work-in-progress tree.
Scanning an arbitrary repository would otherwise report security findings about
code that was never a Claude Code component, which reads as a verdict on
something it is not.

A local path that EXISTS always wins over the remote interpretation, so a
directory named like a slug is never mistaken for a repo.

## What it covers

The script composes existing validators; it implements no rules of its own.
Coverage follows the detected folder shape:

| # | Shape | Scans |
|---|-------|-------|
| 1 | plugin (`.claude-plugin/plugin.json`) | `plugin --strict` (structure, components, docs, encoding, rules, MCP/LSP, **plus the CA-01..CA-07 cache audit and the execution-class security gate**) + full `security` |
| 2 | marketplace (`.claude-plugin/marketplace.json`) | `marketplace` + full `security` |
| 3 | skill (bare `SKILL.md`) | `skill --strict` + full `security` |
| 4 | unknown | full `security` |

`cache` mode is deliberately NEVER run, because both branches are wrong: it is
plugin-scoped and CRITICALs on any folder without a `plugin.json`, so on a
skill or unknown folder it reports an inapplicability as a finding; and where a
`plugin.json` does exist, `plugin` mode already runs the same CA-01..CA-07
audit in-process, so a separate pass would duplicate it.

The full `security` mode runs for every shape — it is where secret and
sensitive-data leak detection lives, and it is the one scan whose absence is
dangerous rather than merely incomplete.

## Exit codes

`0` clean · `1` CRITICAL · `2` MAJOR · `3` MINOR · `4` NIT. Worst scan wins.
A code outside `0..4` means a scanner **could not run** — the script reports
that explicitly as UNKNOWN rather than folding it into a pass.

## Read-only guarantee

No file inside the target is created, modified or deleted. The merged report is
written to the CALLER's `reports/cpv-validate-plugin-folder/` — the
conventional gitignored report location — never inside the scanned folder when
it is a different tree.

## Output

On stdout: a severity table (CRITICAL/MAJOR/MINOR/NIT counts per scan
that ran), the path to the merged report file under
`reports/cpv-validate-plugin-folder/`, and — for every scan that did not
come back clean — the name of the agent that fixes that finding class
(for example `cpv-plugin-fixer-agent` for structural findings or
`cpv-plugin-leaks-preventer-agent` for a leaked secret). The exit code
is the worst of the four verdicts across every scan that ran: `0` clean,
`1` CRITICAL, `2` MAJOR, `3` MINOR, `4` NIT.

## Error Handling

A scanner that could not run at all (missing dependency, crash, or any
exit code outside `0..4`) is reported explicitly as UNKNOWN in the
table rather than folded into a pass — a scan that never happened must
never read as "clean". A remote URL that fails to clone, or a cloned
repo that is neither a plugin nor a skill at its root, exits with an
error naming what was missing; the temp clone is deleted on that path
too, since cleanup runs on every exit, not only the success path.

## Examples

```bash
# Scan the current project root (no argument).
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_validate_plugin_folder.py"

# Scan a specific local plugin folder before publishing it.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_validate_plugin_folder.py" ./my-plugin

# Scan a GitHub repo by URL without cloning it yourself.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_validate_plugin_folder.py" \
  https://github.com/owner/repo

# Scan by owner/repo slug.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_validate_plugin_folder.py" owner/repo
```

## Resources

- `scripts/cpv_validate_plugin_folder.py` — the single script this
  skill runs; it composes `remote_validation.py`'s `plugin`,
  `marketplace`, `skill`, and `security` modes based on the detected
  folder shape (see "What it covers" above).
