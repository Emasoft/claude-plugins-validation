---
name: cpv-batch-fix
description: "Parallel fix for one OR many plugins. Accepts local paths, GitHub URLs, marketplaces, lists, and @listfile shapes. Single-plugin input → per-shard fan-out (v2.91.0 protocol). Marketplace/list input → per-plugin fan-out (one plugin-fixer per plugin, internal sharding when needed). Use when applying validation fixes across many plugins. Trigger with /cpv-batch-fix."
user-invocable: true
argument-hint: "<plugin-or-marketplace-or-list> [--shard-size N] [--max-parallel N] [--min-severity LEVEL]"
---

# cpv-batch-fix

## Overview

Parallel fix skill for one OR many plugins. Two dispatch shapes:

- **Single plugin** (default — backward-compatible with v2.91.0):
  shards the plugin's findings into ~30-finding groups and
  dispatches one `plugin-fixer` per shard, all in parallel. This
  is the original `/cpv-batch-fix` behaviour and still applies.

- **Marketplace / list** (NEW in v2.101.0): resolves the spec via
  `scripts/cpv_marketplace_input.py`, then dispatches one
  `plugin-fixer` per plugin from a single main-session message.
  Each per-plugin agent runs the standard fix loop on its
  assigned plugin — including its own internal sharding if its
  finding count justifies it.

The orchestrator body lives in this plugin's
`commands/cpv-batch-fix.md` slash-command file.

## Prerequisites

- `claude-plugins-validation` plugin installed (provides
  `scripts/cpv_batch_planner.py`, `scripts/cpv_batch_aggregator.py`,
  `scripts/cpv_marketplace_input.py`,
  `scripts/cpv_batch_orchestrator.py`, and the `plugin-fixer`
  agent).
- For URL inputs: `git` on PATH and network access to `github.com`.
- Write access to every plugin's tree — this skill MUTATES source
  files in place.
- Each plugin should be a clean git working tree (committed or
  stashed) before the run so the per-plugin fixer's commits are
  inspectable.

## Inputs

| Shape | Behaviour |
|---|---|
| Single plugin (local) | Per-shard fan-out (existing v2.91.0 path) |
| Single plugin (URL) | Clone + per-shard fan-out |
| Marketplace (local) | Per-plugin fan-out (NEW) |
| Marketplace (URL) | Clone every plugin + per-plugin fan-out |
| List (CLI / `@listfile` / comma-separated) | Per-plugin fan-out |

Knobs (forwarded to the orchestrator):

- `--shard-size N` (default 30) — per-plugin shard size
- `--max-parallel N` (default 8, cap 16) — max concurrent agents
- `--min-severity LEVEL` (default `minor`) — drop findings below

## Instructions

1. Determine whether the user's spec is a single plugin or a
   marketplace/list. The command body does this via the
   resolver; agents can dispatch the command directly.
2. Invoke the slash command body:
   ```text
   /cpv-batch-fix <user's spec> [--shard-size N] [--max-parallel N] [--min-severity LEVEL]
   ```
3. For single-plugin inputs, the orchestrator runs the existing
   per-shard protocol (TRDD-71e68ab5).
4. For marketplace/list inputs, the orchestrator runs the
   per-plugin protocol (TRDD-3dcbb37c) — one fixer per plugin in
   `batch_per_plugin` mode applied to the WHOLE plugin (each
   fixer's internal sharding kicks in when finding-count
   justifies it).
5. The user gets a status table + final summary.

## Output

- Unicode-bordered status table (one row per plugin).
- One-line DONE summary —
  `DONE: plugins=N clean=X fixed=Y partial=Z failed=W. Reports under <session_dir>/.`
- Per-plugin final validation reports under
  `$MAIN_ROOT/reports/validate_plugin/<ts±tz>-<plugin>.md`.
- Per-plugin status JSONs carrying `before` and `after` counts.
- Per-plugin commit batches in each plugin's git tree.

## Token contract

Main-session cost: ~3-4K tokens for a 17-plugin marketplace
(O(N) one-line returns + status table renders). Per-plugin subagent
work happens in its own context window.

## Error Handling

See [error-handling](references/error-handling.md) for the full
per-condition matrix and worked examples.

## Examples

See [error-handling](references/error-handling.md) §Examples.

## Resources

- TRDD-71e68ab5 — per-shard fix protocol (single plugin)
- TRDD-3dcbb37c — per-plugin fix protocol (marketplace input)
- `commands/cpv-batch-fix.md` — orchestrator body (in this plugin)
- `agents/plugin-fixer.md` — fix agent (`batch_shard` and `batch_per_plugin` modes)
- Sibling batch skills: `cpv-batch-validate`,
  `cpv-batch-security-audit`, `cpv-batch-caching-audit`,
  `cpv-batch-caching-optimize`
