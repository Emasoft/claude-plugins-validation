---
name: cpv-batch-validate
description: "Fleet-wide parallel validation. Accepts local paths, GitHub URLs, marketplaces, lists, and @listfile shapes. Dispatches one plugin-validator per plugin (default 8 parallel, cap 16). Use when validating many plugins at once — e.g. every plugin in a marketplace. Trigger with /cpv-batch-validate or 'validate every plugin in X'."
user-invocable: true
argument-hint: "<plugin-or-marketplace-or-list> [--max-parallel N]"
---

# cpv-batch-validate

## Overview

Parallel-validation skill for a fleet of Claude Code plugins.
Resolves the user's input via `scripts/cpv_marketplace_input.py`
(every shape from §Inputs), builds a batch plan via
`scripts/cpv_batch_orchestrator.py`, dispatches one
`plugin-validator` subagent per plugin in `batch_validate` mode,
and aggregates per-plugin status JSONs into a single Unicode-bordered
status table.

The full orchestrator body lives in the plugin's
`commands/cpv-batch-validate.md` slash-command file. This SKILL.md
is the canonical entry point when the skill is invoked from
`the-skills-menu` (CPV agents) or from the `/cpv-batch-validate`
slash command directly.

## Prerequisites

- `claude-plugins-validation` plugin installed (provides
  `scripts/cpv_marketplace_input.py`,
  `scripts/cpv_batch_orchestrator.py`, `scripts/format_menu.py`,
  and the `plugin-validator` agent).
- For URL inputs: `git` on PATH and network access to
  `github.com` so the resolver can `git clone --depth 1` each
  plugin/marketplace into `${TMPDIR}/cpv-batch-input-<uuid>/`.
- For marketplace inputs: the marketplace must ship a parseable
  `.claude-plugin/marketplace.json` at its root listing one or
  more plugins.

## Inputs

| Shape | Example |
|---|---|
| Single plugin (local) | the absolute path of the plugin root |
| Single plugin (URL) | `https://github.com/owner/plugin` or `owner/plugin` |
| Single skill (local) | a folder whose root holds a `SKILL`-shaped file |
| Single skill (URL) | a github repo whose root is a skill |
| Skill pack (local) | a folder containing many skill subfolders (Anthropic-style `./skills/<name>/SKILL.md` or flat `./<name>/SKILL.md`) |
| Skill pack (URL) | a github repo containing many skill folders |
| Marketplace (local) | the absolute path of the marketplace root |
| Marketplace (URL) | `https://github.com/owner/marketplace-repo` |
| List (CLI) | multiple whitespace-separated entries |
| List (file) | a path prefixed with `@`, e.g. an inputs list file |
| Comma-separated | a single string of entries joined with commas |

The resolver handles MIXED inputs — a marketplace can list both
plugins and skill folders; a list can contain plugin paths +
skill paths + URLs. Each entry is classified independently by
its on-disk shape.

## Instructions

1. Confirm the user wants the full validation pipeline (not just
   security or caching — those have dedicated batch skills).
2. Invoke the slash command body:
   ```text
   /cpv-batch-validate <user's spec> [--max-parallel N]
   ```
3. The command resolves the spec, plans the batch, fans out N
   `plugin-validator` subagents in parallel (one per plugin), and
   prints the per-plugin status table after every dispatch wave.
4. The user gets the final status table + a one-line summary
   (`DONE: plugins=N valid=X invalid=Y warning-only=Z`).
5. If any plugin is INVALID, suggest `/cpv-batch-fix <same spec>`
   to apply fixes across the marketplace.

## Output

Two artefacts:

1. **Unicode-bordered status table** printed to the user (rendered
   by `scripts/format_menu.py status_table`). One row per plugin
   with `✓` / `✗` / `⚠` / `○` (queued) symbols.
2. **One-line DONE summary** —
   `DONE: plugins=N valid=X invalid=Y warning-only=Z. Reports under <session_dir>/.`

Per-plugin validation reports live under
`$MAIN_ROOT/reports/validate_plugin/<ts±tz>-<plugin>.md` (written
by the dispatched subagents, not by this skill). Per-plugin status
JSONs live under
`<session_dir>/plugin-<index>.status.json`.

## Token contract

Main-session cost is bounded by `O(N)` one-line returns, NOT the
size of any per-plugin report. Typical 17-plugin run: ~3-4K tokens.

## Error Handling

See [error-handling](references/error-handling.md) for the full
per-condition matrix and worked examples.

## Examples

See [error-handling](references/error-handling.md) §Examples.

## Resources

- TRDD-3dcbb37c — full design (in this plugin's `design/tasks/`)
- `commands/cpv-batch-validate.md` — orchestrator body
- `scripts/cpv_marketplace_input.py` — universal input resolver
- `scripts/cpv_batch_orchestrator.py` — plan / status helper
- `agents/plugin-validator.md` — `batch_validate` mode contract
- Sibling batch skills (this plugin): `cpv-batch-security-audit`,
  `cpv-batch-caching-audit`, `cpv-batch-caching-optimize`,
  `cpv-batch-fix`
