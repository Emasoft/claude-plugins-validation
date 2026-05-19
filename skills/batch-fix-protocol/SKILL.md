---
name: batch-fix-protocol
description: "Schema reference for the /cpv-batch-fix parallel-shard fix protocol — manifest format, status format, planner/aggregator contracts. Use when implementing a new consumer of the batch protocol or extending the planner/aggregator. Used dynamically via the-skills-menu (TRDD-478d9687) — any CPV agent can invoke."
user-invocable: false
allowed-tools: Read
---

# batch-fix-protocol

## Overview

`/cpv-batch-fix` slices a plugin's validation findings into parallel-fix
shards and dispatches N `plugin-fixer` agents from the main session —
one per shard, each with a fresh context window whose size depends on
`plugin-fixer.model` (per-model — never assume a fixed limit). This
skill documents the data contract that ties the planner, the shard
agents, and the aggregator together. Loaded by `plugin-fixer` (when it
sees `mode: batch_shard` in its context block) and by the
`/cpv-batch-fix` slash command body.

See [JSON schemas](references/json-schemas.md) for the three file
formats and TRDD-71e68ab5 (`design/tasks/TRDD-20260519_114050+0200-71e68ab5-batch-fix-parallel-sharding.md`)
for the design rationale.

> 1. Top-level index (`index.json`) · 2. Per-shard manifest (`shard-K.json`) · 3. Per-shard status (`shard-K.status.json`) · Schema version bumps

## Prerequisites

- A plugin with validation findings (typically from `validate_plugin.py --json`)
- Python 3.10+ (planner + aggregator are pure Python, no LLM)
- Adequate `$TMPDIR` for the session directory

## Instructions

1. Read the JSON-schema reference: [json-schemas](references/json-schemas.md).
   > 1. Top-level index (`index.json`) · 2. Per-shard manifest (`shard-K.json`) · 3. Per-shard status (`shard-K.status.json`) · Schema version bumps
2. For consumers of the batch protocol: open the planner CLI help via `uv run python scripts/cpv_batch_planner.py --help` to see the available flags.
3. For consumers of the batch protocol: open the aggregator CLI help via `uv run python scripts/cpv_batch_aggregator.py --help` to see the available flags.
4. For schema changes: bump `SCHEMA_VERSION` in BOTH `scripts/cpv_batch_planner.py` and `scripts/cpv_batch_aggregator.py` to the new version.
5. For schema changes: update the corresponding tests in `tests/test_cpv_batch_planner.py` and `tests/test_cpv_batch_aggregator.py`.
6. For new consumers (e.g. an alternative orchestrator): write a manifest reading function that mirrors the schema in step 1; do NOT write a parallel format.

## Output

The protocol writes three JSON file types into a single session
directory. See [json-schemas](references/json-schemas.md)
for the full shape of each.

> 1. Top-level index (`index.json`) · 2. Per-shard manifest (`shard-K.json`) · 3. Per-shard status (`shard-K.status.json`) · Schema version bumps

Briefly:

- `index.json` — top-level batch manifest written by the planner.
- `shard-K.json` — per-shard work-list manifest written by the planner. Schema v2 uses **scope-based ownership** (`scope_path` + `scope_kind`); each scope is either a whole skill directory (`skill_dir` — the agent may refactor freely, including creating sibling skills and `references/` files) or a single file.
- `shard-K.status.json` — per-shard outcome status written by each shard fixer.

## Error Handling

| # | Error | Resolution |
|---|-------|------------|
| 1 | Planner can't parse `validate_plugin.py --json` output | Re-run with `--report <pre-existing.json>` |
| 2 | Shard agent dies before writing status | Aggregator marks `agent_exit_reason: missing`; re-run `/cpv-batch-fix` for residual |
| 3 | Two shards share a scope (planner bug) | Aggregator keeps the most recent `fixed_count`; report flags as duplicate |
| 4 | Schema version mismatch | Aggregator refuses with explicit version error |

## Examples

```bash
python3 scripts/cpv_batch_planner.py /path/to/big-plugin --shard-size 30
# Then main session fans out N plugin-fixer Agents in one message
python3 scripts/cpv_batch_aggregator.py /tmp/cpv-batch/<ts>
# → DONE: shards=6 fixed=145 failed=1 remaining=1. Report: <abs-path>
```

Re-run `/cpv-batch-fix` to retry the residual — the planner re-validates and re-shards.

## Resources

- [json-schemas](references/json-schemas.md) — full schema reference
  > 1. Top-level index (`index.json`) · 2. Per-shard manifest (`shard-K.json`) · 3. Per-shard status (`shard-K.status.json`) · Schema version bumps
- TRDD-71e68ab5 — design (`design/tasks/`)
- `scripts/cpv_batch_planner.py` — planner source
- `scripts/cpv_batch_aggregator.py` — aggregator source
- `agents/plugin-fixer.md` — `batch_shard` mode contract
- `commands/cpv-batch-fix.md` — orchestrator slash command
