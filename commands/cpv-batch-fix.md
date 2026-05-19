---
name: cpv-batch-fix
description: Shard a plugin's validation findings into parallel-fix batches. Dispatches N plugin-fixer agents from the main session — one per shard, each with its own fresh context window (size depends on plugin-fixer's `model:` frontmatter — opus/sonnet default 200K, the [1m] variants 1M, future models may differ). For plugins with many findings where a single fixer agent would exhaust its context window above the 50% performance-drop threshold.
allowed-tools: Read, Bash, Glob, Grep
argument-hint: "<plugin-path> [--shard-size N] [--max-parallel N] [--min-severity LEVEL]"
user-invocable: true
---

# /cpv-batch-fix — Parallel batch fix for large plugins

For plugins where the total set of findings can't comfortably fit
in a single `plugin-fixer` agent's working context (and stay under
the ~50% utilisation level beyond which model quality begins to
degrade), the single-agent fix loop will silently exhaust its
context window mid-way and exit without finishing the job. The
"safe size" is per-model: bare `opus` / `sonnet` give 200K tokens
(~20-30 findings safe), the `opus[1m]` / `sonnet[1m]` variants
give 1M (~100-150 findings safe). Per the Anthropic subagent spec,
**subagents cannot spawn other subagents**, so `plugin-fixer`
cannot parallelise itself.

This command does the parallelisation **from the main session**, which
is the only place the Agent tool can fan out:

1. Runs `scripts/cpv_batch_planner.py` to slice the findings into
   shards of ~30 (zero LLM cost). The planner uses **scope-based
   grouping**: every finding inside `skills/<name>/` belongs to the
   `skills/<name>/` scope (with refactor rights), every other finding
   belongs to a single-file scope.
2. Dispatches N `plugin-fixer` agents in a SINGLE main-session
   message, each given its own shard manifest. Each agent starts
   with a fresh context window (size determined by `plugin-fixer`'s
   `model:` frontmatter at dispatch time) and refactor rights
   inside `skill_dir` scopes — including the right to split an
   oversized SKILL.md into multiple smaller focused skills,
   externalise content into `references/*.md`, etc.
3. Runs `scripts/cpv_batch_aggregator.py` once the shards have all
   returned, producing one consolidated report (zero LLM cost).
4. Main-session token cost: ~3-4K total (just paths, no report
   bodies).

See TRDD-71e68ab5 (`design/tasks/TRDD-20260519_114050+0200-71e68ab5-batch-fix-parallel-sharding.md`)
for the full design.

## You are the orchestrator

You — the model running THIS turn — orchestrate the slash command from
the main session. You do NOT do any fixing yourself. You delegate the
actual fix work to N parallel `plugin-fixer` agents via the Agent tool,
each in `batch_shard` mode.

## Step 0 — Resolve arguments

The user supplies:

- A plugin path (required — first positional argument)
- Optional `--shard-size N` (default 30)
- Optional `--max-parallel N` (default 8, cap 16)
- Optional `--min-severity LEVEL` (default `minor` — CRITICAL/MAJOR/MINOR all in scope; raise to `major` or `critical` to skip MINOR fixes)

If no plugin path was given, ask the user plain-text:

```text
Which plugin should I batch-fix? Provide an absolute path.
```

## Step 1 — Plan the batch

Resolve `$CLAUDE_PLUGIN_ROOT` (the cached plugin folder for `claude-plugins-validation`) and run:

```bash
PLUGIN_PATH="<user-supplied-path>"
SHARD_SIZE=30   # or user override
MAX_PARALLEL=8  # or user override
MIN_SEVERITY=minor  # or user override

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_planner.py" \
  "$PLUGIN_PATH" \
  --shard-size "$SHARD_SIZE" \
  --max-parallel "$MAX_PARALLEL" \
  --min-severity "$MIN_SEVERITY"
```

The planner prints a JSON summary to stdout. Capture it. Read the
``index_path`` and ``shard_count`` fields.

If ``shard_count`` is `0`, the plugin has no actionable findings — reply
plain-text:

```text
No actionable findings at min-severity=<level>. Nothing to fix. ✓
```

…and stop.

If ``shard_count > 8`` AND ``max_parallel`` was left at default 8: tell
the user the batch will dispatch 8 at a time, with later shards queued
serially. The planner already caps the index's ``max_parallel`` field.

Print the per-shard table (so the user sees what will run) using
`format_menu.py` status_table mode:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" status_table "$(cat <<EOF
{
  "title": "Batch plan",
  "header": ["#", "Shard", "Files", "Findings", "Manifest"],
  "rows": [
    /* one row per shard from index.json */
  ]
}
EOF
)"
```

## Step 2 — Dispatch shards in parallel

Read ``shards[]`` from `index.json`. Cap at ``max_parallel`` per the
index's resolved value. In a **single message**, dispatch one Agent
tool call per shard (up to the cap):

```yaml
# Pseudocode — emit N Agent tool calls in one assistant message
for shard in index.shards[:max_parallel]:
    Agent(
      subagent_type: "plugin-fixer",
      description: "Batch-fix shard {shard.shard_id}/{index.shard_count}",
      prompt: |
        <context>
        source: /cpv-batch-fix
        mode: batch_shard
        shard_manifest: {shard.manifest_path}
        shard_id: {shard.shard_id}
        shard_of: {index.shard_count}
        status_path: {shard.status_path}
        plugin_path: {index.plugin_path}
        </context>

        Run in batch_shard mode per agents/plugin-fixer.md.
        Read the shard manifest, fix only its findings, write the
        status JSON to status_path, then return ONE line exactly:

          [shard-{shard.shard_id}] done: <fixed>/<total> fixed, <remaining> remaining (status: <status_path>)

        Do NOT touch files outside this shard's manifest.
        Do NOT browse the plugin or read other shards.
        Do NOT render menus — the /cpv-batch-fix orchestrator handles aggregation.
      run_in_background: false
    )
```

Multiple Agent tool calls in a single assistant message **execute in
parallel** per the Claude Code spec. Each agent gets its own fresh
context window (size determined by `plugin-fixer.model` — defaults to
opus 200K, can be `opus[1m]` or `sonnet[1m]` for 1M). If there are
more shards than ``max_parallel``, dispatch in waves (collect the
first wave's one-line summaries, then
dispatch the next wave).

## Step 3 — Aggregate

After every shard has returned its one-line summary, run the aggregator:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_aggregator.py" "$SESSION_DIR"
```

The aggregator's stdout is one line:

```text
DONE: shards=N fixed=X failed=Y remaining=Z. Report: <abs-path>
```

Forward this line verbatim to the user. If `failed > 0` or `remaining > 0`, append a hint:

```text
Some shards did not complete cleanly. Inspect the report for per-shard error details. You can re-run /cpv-batch-fix to retry the remaining findings.
```

## Step 4 — Done

Do not enter a post-action menu. The user picked this command knowing
exactly what they wanted; spam back-to-menu friction is not helpful here.

If they want to revalidate, they can run `/cpv-validate-plugin` or
`/cpv-doctor` directly.

## Token-cost guarantee

Main-session cost (this turn):

| # | Item | Tokens |
|---|------|--------|
| 1 | Planner JSON output | ~400 |
| 2 | Per-shard table | ~300 |
| 3 | N shard dispatches × one-line return | ~50 × N (max 800 at N=16) |
| 4 | Aggregator summary line | ~150 |
| 5 | **Total** | **~2-3K tokens** |

No report body ever crosses the main-session context.

## Configuration

| # | Knob | Default | Notes |
|---|------|---------|-------|
| 1 | `--shard-size` | 30 | Choose dynamically based on `plugin-fixer.model`'s context window AND the rule that model quality degrades above ~50% context utilisation. Safe ceiling ≈ `(model_context / 2) / 3-5K-tokens-per-finding`. opus/sonnet 200K → ~20-30 findings/shard; opus[1m]/sonnet[1m] → ~100-150 findings/shard; future models may differ. Raise for trivial mechanical findings, lower for complex ones. No hard cap — the orchestrator decides. |
| 2 | `--max-parallel` | 8 | Hard cap 16 — beyond that, Anthropic's API rate-limits kick in and dispatches fail. The 16 ceiling is a safety against rate-limit failure, not a behavioral limit. |
| 3 | Findings per file | unlimited | A single-file shard with more findings than `--shard-size` gets its own oversized shard with a stderr warning |
| 4 | Plugin size | unlimited | Tested up to 500+ findings; larger plugins simply spawn more shards |
| 5 | Per-shard iterations | unlimited | The shard agent fixes until convergence (or oscillation, see plugin-fixer §Rules). NO hardcoded iteration ceiling — bigger shards need more iterations |
| 6 | Per-shard wall-clock | unlimited | Bounded only by the agent's `maxTurns` (set in `agents/plugin-fixer.md` frontmatter, currently 200). Time per turn is not capped — fixes can take as long as they need |

## See also

- TRDD-71e68ab5 — full design
- `agents/plugin-fixer.md` — `batch_shard` mode contract
- `skills/batch-fix-protocol/SKILL.md` — schema reference
- `scripts/cpv_batch_planner.py` — planner source
- `scripts/cpv_batch_aggregator.py` — aggregator source
