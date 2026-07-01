---
name: batch-fix-protocol
description: "Schema reference for the /cpv-batch-fix parallel-shard fix protocol — manifest format, status format, planner/aggregator contracts. Use when implementing a new consumer of the batch protocol or extending the planner/aggregator. Used dynamically via the-skills-menu (TRDD-478d9687) — any CPV agent can invoke."
user-invocable: false
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

## Fork vs fresh worker — the token-honest dispatch choice (TRDD-GVMOKJBB)

Fan-out is a LAST resort, not a default (cost ≈ turns × per-turn-context):

1. **One plugin that fits one context → NO fan-out.** Sequential fix-as-you-go in ONE lean
   fixer (read each file once, fix in the same turn) is cheapest — a single warm context reused.
   Forking merely for parallelism here costs N× the context (a wall-clock tradeoff, NOT a token
   saving).
2. **A plugin that EXCEEDS one context → fan out**, and pick the worker by the dispatcher:
   - **`subagent_type:"fork"` (Agent tool) — ONLY from a LEAN dispatcher** that already loaded the
     exact context the worker needs (the fix skill + the ledger). A fork inherits the dispatcher's
     WHOLE conversation at cache-read rate, so it pays off only when that conversation is small and
     relevant. NEVER fork the bloated main session (it carries all of main's skills/MCP/CLAUDE.md
     forward).
   - **Fresh lean worker + on-disk slice — the correct choice from a bloated/cold context.** The
     `/cpv-batch-fix` path spawns N `plugin-fixer`s from the MAIN session, so this is the batch
     default: hand each worker its shard manifest / ledger slice ON DISK (a file path, not context
     inheritance) with a curated tool surface (no MCP).
3. **NEVER a skill `context: fork`.** That runs a skill in an ISOLATED fresh subagent with no
   conversation history (docs: skills#run-skills-in-a-subagent) → it COLD-writes cache = wastes
   tokens. It is the OPPOSITE of the Agent-tool fork despite the shared word.

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
- [agent-modes](references/agent-modes.md) — plugin-fixer per-mode workflow + the two agent-side status shapes (`batch_per_plugin`, `batch_same_turn`)
  > 1. Batch-shard mode (`mode: batch_shard`, TRDD-71e68ab5) · 2. Batch-per-plugin mode (`mode: batch_per_plugin`, TRDD-3dcbb37c) · 3. Batch same-turn modes (`batch_same_turn_validate_fix` / `batch_same_turn_full`, TRDD-3dcbb37c §3)
- TRDD-71e68ab5 — design (`design/tasks/`)
- `scripts/cpv_batch_planner.py` — planner source
- `scripts/cpv_batch_aggregator.py` — aggregator source
- `agents/plugin-fixer.md` — `batch_shard` mode contract
- `commands/cpv-batch-fix.md` — orchestrator slash command
