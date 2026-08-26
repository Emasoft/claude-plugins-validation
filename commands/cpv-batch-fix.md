---
name: cpv-batch-fix
description: Parallel fix for one OR many plugins. Single-plugin inputs run the existing per-shard protocol (one cpv-plugin-fixer-agent per shard of findings). Marketplace / list / URL inputs run a per-plugin fan-out (one cpv-plugin-fixer-agent per plugin; each fixer may internally shard its own findings). Accepts local paths, GitHub URLs, marketplace URLs/folders, comma-separated lists, and @listfile shapes. Default 8 parallel agents per main-session message, cap 16. Each agent starts with a fresh context window (size determined by `cpv-plugin-fixer-agent.model`).
argument-hint: "<plugin-or-marketplace-or-list> [--shard-size N] [--max-parallel N] [--min-severity LEVEL]"
user-invocable: true
---

# /cpv-batch-fix — Parallel batch fix for large plugins

For plugins where the total set of findings can't comfortably fit
in a single `cpv-plugin-fixer-agent` agent's working context (and stay under
the ~50% utilisation level beyond which model quality begins to
degrade), the single-agent fix loop will silently exhaust its
context window mid-way and exit without finishing the job. The
"safe size" is per-model: bare `opus` / `sonnet` give 200K tokens
(~20-30 findings safe), the `opus[1m]` / `sonnet[1m]` variants
give 1M (~100-150 findings safe). Per the Anthropic subagent spec,
**subagents cannot spawn other subagents**, so `cpv-plugin-fixer-agent`
cannot parallelise itself.

This command does the parallelisation **from the main session**, which
is the only place the Agent tool can fan out:

1. Runs `scripts/cpv_batch_planner.py` to slice the findings into
   shards of ~30 (zero LLM cost). The planner uses **scope-based
   grouping**: every finding inside `skills/<name>/` belongs to the
   `skills/<name>/` scope (with refactor rights), every other finding
   belongs to a single-file scope.
2. Dispatches N `cpv-plugin-fixer-agent` agents in a SINGLE main-session
   message, each given its own shard manifest. Each agent starts
   with a fresh context window (size determined by `cpv-plugin-fixer-agent`'s
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
actual fix work to N parallel `cpv-plugin-fixer-agent` agents via the Agent tool,
each in `batch_shard` mode.

## Step 0 — Resolve arguments

The user supplies (TRDD-3dcbb37c §1 extended input grammar):

- A target spec (required — first positional argument). Accepts:
  - Single plugin path (`./my-plugin`)
  - Single plugin URL (`https://github.com/owner/plugin` or `owner/plugin`)
  - Marketplace path (`./marketplace-root`)
  - Marketplace URL (`https://github.com/owner/marketplace-repo`)
  - List on CLI (multiple positional args)
  - List file (`@/tmp/inputs.txt`)
  - Comma-separated (`./a,./b,./c`)
- Optional `--shard-size N` (default 30) — per-plugin shard size
- Optional `--max-parallel N` (default 8, cap 16)
- Optional `--min-severity LEVEL` (default `minor` — CRITICAL/MAJOR/MINOR all in scope; raise to `major` or `critical` to skip MINOR fixes)

If no target was given, ask the user plain-text:

```text
What should I batch-fix? Provide an absolute path, a GitHub URL, a marketplace, or a list file like @/tmp/plugins.txt.
```

Classify the spec via the resolver. This single `plan` call BOTH
classifies the input AND builds the per-plugin `plan.json` the
fan-out path consumes — capture `SESSION_DIR` so the marketplace
branch reuses it instead of re-planning:

```bash
TARGET="<user-supplied-target>"
MAX_PARALLEL=8  # or user override (--max-parallel, cap 16)
RESOLVED_JSON="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" plan \
  "$TARGET" \
  --agent cpv-plugin-fixer-agent \
  --mode batch_per_plugin \
  --max-parallel "$MAX_PARALLEL")"
PLUGIN_COUNT=$(echo "$RESOLVED_JSON" | sed -n 's/^PLUGIN_COUNT: //p')
SESSION_DIR=$(echo "$RESOLVED_JSON" | sed -n 's/^SESSION_DIR: //p')
STATUS_TABLE=$(echo "$RESOLVED_JSON" | sed -n 's/^STATUS_TABLE: //p')
```

- If `PLUGIN_COUNT == 1` AND the resolved kind is `plugin` (single plugin), **fall through to Step 1 — Plan the batch** below using the existing per-shard protocol on that one plugin path. The per-plugin `plan.json` from this classification call is not used by the single-plugin path (Step 1 runs the separate per-SHARD planner `cpv_batch_planner.py`); it lives in `SESSION_DIR` and is harmlessly left behind under `$TMPDIR/cpv-batch/`.
- If `PLUGIN_COUNT > 1` OR the input is a marketplace, run the **per-plugin fan-out** instead — see "§Marketplace / list input — per-plugin fan-out" further down. That path REUSES the `SESSION_DIR` + `plan.json` already built here (Step M1 does NOT re-plan).

## Step 1 — Plan the batch

Resolve `$CLAUDE_PLUGIN_ROOT` (the cached plugin folder for `claude-plugins-validation`) and run:

```bash
PLUGIN_PATH="<user-supplied-path>"
SHARD_SIZE=30   # or user override
MAX_PARALLEL=8  # or user override
MIN_SEVERITY=minor  # or user override

PLANNER_JSON="$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_planner.py" \
  "$PLUGIN_PATH" \
  --shard-size "$SHARD_SIZE" \
  --max-parallel "$MAX_PARALLEL" \
  --min-severity "$MIN_SEVERITY")"

# CRITICAL: the per-shard planner creates its OWN session dir
# (${TMPDIR}/cpv-batch/<ts>/) holding index.json + shard-*.status.json —
# this is NOT the same dir as the orchestrator's Step-0 `plan` call
# (${TMPDIR}/cpv-batch/<ts>-<agent>/, which holds plan.json). Overwrite
# SESSION_DIR here with the PLANNER's session_dir so Step 3's aggregator
# (which requires index.json) reads the correct directory. Parse with a
# real JSON loader — the planner prints indent=2 pretty JSON, so grep/sed
# would be fragile.
SESSION_DIR=$(echo "$PLANNER_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_dir"])')
INDEX_PATH=$(echo "$PLANNER_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["index_path"])')
SHARD_COUNT=$(echo "$PLANNER_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["shard_count"])')
```

The planner prints a JSON summary to stdout. The capture above reads the
``session_dir``, ``index_path``, and ``shard_count`` fields (and resets
``SESSION_DIR`` to the planner's dir for Step 3's aggregator).

If ``shard_count`` is `0`, the plugin has no actionable findings — reply
plain-text:

```text
No actionable findings at min-severity=<level>. Nothing to fix. ✓
```

…and stop.

If ``shard_count > 8`` AND ``max_parallel`` was left at default 8: tell
the user the batch will dispatch 8 at a time, with later shards queued
serially. The planner already caps the index's ``max_parallel`` field.

Queue the per-shard table (so the user sees what will run) via the
claude-menu-system Stop hook. Build a CMS-shaped status_table spec
inline — one row per shard — and write it to a tempfile, then hand
the path to ``print_menu.py``:

Read the planner's index from ``$INDEX_PATH`` (captured in Step 1 — it
resolves to ``$SESSION_DIR/index.json``). Each entry in its ``shards[]``
array carries ``shard_id``, ``file_count``, ``finding_count``, and
``manifest_path`` (shape defined by ``scripts/cpv_batch_planner.py``).
Map one shard per CMS ``status_table`` row — ``status: pending`` is the
right enum for queued rows — then write the spec to a tempfile and hand
the path to ``print_menu.py``:

```bash
PLAN_SPEC="/tmp/cpv-batch-fix-shard-plan-$$.json"
cat > "$PLAN_SPEC" <<EOF
{
  "spec_version": 1,
  "mode": "status_table",
  "plugin": "cpv",
  "slug": "batch-fix-shard-plan",
  "title": "Batch plan",
  "row_header": "Shard",
  "rows": [
    /* one row per shard from \$INDEX_PATH (= \$SESSION_DIR/index.json):
       { "label": "shard-1 (5 files / 27 findings)",
         "status": "pending",
         "notes": "/tmp/.../shard-1.manifest.json" } */
  ]
}
EOF

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" "$PLAN_SPEC"
```

NEVER print menu inline; the CMS Stop hook emits via systemMessage at
turn end. End the turn after this call.

## Step 2 — Dispatch shards in parallel

Read ``shards[]`` from `index.json`. Cap at ``max_parallel`` per the
index's resolved value. In a **single message**, dispatch one Agent
tool call per shard (up to the cap):

```yaml
# Pseudocode — emit N Agent tool calls in one assistant message
for shard in index.shards[:max_parallel]:
    Agent(
      subagent_type: "cpv-plugin-fixer-agent",
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

        Run in batch_shard mode per agents/cpv-plugin-fixer-agent.md.
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
context window (size determined by `cpv-plugin-fixer-agent.model` — defaults to
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
| 1 | `--shard-size` | 30 | Choose dynamically based on `cpv-plugin-fixer-agent.model`'s context window AND the rule that model quality degrades above ~50% context utilisation. Safe ceiling ≈ `(model_context / 2) / 3-5K-tokens-per-finding`. opus/sonnet 200K → ~20-30 findings/shard; opus[1m]/sonnet[1m] → ~100-150 findings/shard; future models may differ. Raise for trivial mechanical findings, lower for complex ones. No hard cap — the orchestrator decides. |
| 2 | `--max-parallel` | 8 | Hard cap 16 — beyond that, Anthropic's API rate-limits kick in and dispatches fail. The 16 ceiling is a safety against rate-limit failure, not a behavioral limit. |
| 3 | Findings per file | unlimited | A single-file shard with more findings than `--shard-size` gets its own oversized shard with a stderr warning |
| 4 | Plugin size | unlimited | Tested up to 500+ findings; larger plugins simply spawn more shards |
| 5 | Per-shard iterations | unlimited | The shard agent fixes until convergence (or oscillation, see cpv-plugin-fixer-agent §Rules). NO hardcoded iteration ceiling — bigger shards need more iterations |
| 6 | Per-shard wall-clock | unlimited | Bounded only by the agent's `maxTurns` (set in `agents/cpv-plugin-fixer-agent.md` frontmatter, currently 200). Time per turn is not capped — fixes can take as long as they need |

## Marketplace / list input — per-plugin fan-out (TRDD-3dcbb37c)

When the user's target resolved to MORE THAN ONE plugin (a
marketplace, a list, or a multi-spec), the orchestrator switches
from per-shard parallelism to **per-plugin parallelism**: one
`cpv-plugin-fixer-agent` subagent per plugin, all dispatched from one
main-session message in groups of `--max-parallel` (default 8,
cap 16). Each per-plugin agent runs the standard fix loop on its
own plugin — including the agent's own internal `batch_shard`
logic when its finding count justifies it.

### Step M1 — Reuse the per-plugin plan from Step 0

Step 0 already ran `cpv_batch_orchestrator.py plan` (with the same
`--agent cpv-plugin-fixer-agent --mode batch_per_plugin --max-parallel`), which
both classified the input AND wrote `plan.json` + `status_table.json`
into `SESSION_DIR`. Do NOT re-plan — reuse the captured paths:
`SESSION_DIR`, `$SESSION_DIR/plan.json`, and `STATUS_TABLE`. Queue the
initial status table for the claude-menu-system Stop hook (emitted
post-turn via ``systemMessage`` — zero token cost, NEVER printed
inline by the orchestrator):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" "$STATUS_TABLE"
```

NEVER print menu inline; the CMS Stop hook emits via systemMessage at
turn end. End the turn after this call.

### Step M2 — Dispatch one cpv-plugin-fixer-agent per plugin, in groups of max_parallel

For each `dispatch_groups[i]`, emit one Agent call per plugin in a
single main-session message:

```yaml
for plugin_index in group:
    plugin = plan.plugins[plugin_index]
    Agent(
      subagent_type: "cpv-plugin-fixer-agent",
      description: "Batch-fix plugin {plugin.display_name}",
      prompt: |
        <context>
        source: /cpv-batch-fix (marketplace/list mode)
        mode: batch_per_plugin
        plugin_index: {plugin.plugin_index}
        plugin_path: {plugin.abs_path}
        source_url: {plugin.source_url or "—"}
        display_name: {plugin.display_name}
        session_dir: {plan.session_dir}
        status_path: {plan.session_dir}/plugin-{plugin.plugin_index}.status.json
        min_severity: {min_severity}
        shard_size: {shard_size}
        </context>

        Apply the standard fix loop to the plugin at `plugin_path`.
        When the plugin's finding count exceeds your context-safe
        ceiling (per your `model:` frontmatter), DELEGATE to your
        own internal `batch_shard` protocol by invoking
        `scripts/cpv_batch_planner.py` on that one plugin and
        consuming the resulting shard manifests sequentially — do
        NOT spawn parallel sub-subagents (Anthropic spec forbids).

        Write per-plugin status JSON to `status_path` with:

          {
            "status_symbol": "✓" | "✗" | "⚠",
            "status_label": "clean" | "fixed" | "partial" | "failed",
            "before": {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "after":  {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "report_path": "<abs-path-to-final-validation-report>",
            "notes": "<short summary, e.g. fixed 12/12, 0 remaining>"
          }

        Return ONE line exactly:

          [plugin-{plugin.plugin_index}] {label}: fixed=X remaining=Y (status: {status_path})

        Do NOT render menus. Do NOT recommend follow-ups.
      run_in_background: false
    )
```

### Step M3 — Refresh status table between waves

Queue the live status table via the orchestrator's ``emit-status``
subcommand (aggregates every per-plugin status JSON, hands the CMS
spec to ``print_menu`` — Stop hook emits at turn end):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" \
  emit-status "$SESSION_DIR/plan.json"
```

NEVER print menu inline; the CMS Stop hook emits via systemMessage at
turn end. End the turn after this call.

### Step M4 — Final summary

After every plugin has reported:

1. Queue the final status table:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" \
     emit-status "$SESSION_DIR/plan.json"
   ```

2. Print a one-line summary inline (text, not a menu):

   ```text
   DONE: plugins=N clean=X fixed=Y partial=Z failed=W. Reports under {session_dir}/.
   ```

3. If any plugin is `partial` or `failed`, append the doctor-prompt
   inline:

   ```text
   Some plugins still have remaining findings. Inspect their reports
   for per-plugin details and re-run /cpv-batch-fix to retry; or run
   /cpv-doctor <one plugin> for the deep design-correctness recipes
   that go beyond schema validation.
   ```

End the turn. The CMS Stop hook emits the final table via systemMessage.

## Fixed key→action map

`/cpv-batch-fix` is a one-shot fleet fixer; the per-shard and
per-plugin status tables are informational only. No numbered or
lettered action rows — the user's next move is the text suggestion
above (re-run, or `/cpv-doctor` for deeper recipes). Two slugs are
reserved by this command: ``batch-fix-shard-plan`` (per-shard mode,
single-plugin path) and ``batch-cpv-plugin-fixer-agent-status`` (per-plugin
mode, marketplace/list path; shared with
`/cpv-batch-validate-and-fix`, `/cpv-batch-full-scan-and-fix` — same
agent type). The fixed key→action map is empty by design; future
post-scan menus extend this contract with letter→action rows.

## See also

- TRDD-71e68ab5 — per-shard fix protocol (single plugin)
- TRDD-3dcbb37c — per-plugin fix protocol (marketplace / list input)
- `agents/cpv-plugin-fixer-agent.md` — `batch_shard` + `batch_per_plugin` mode contracts
- `skills/cpv-batch-fix-protocol/SKILL.md` — per-shard schema reference
- `scripts/cpv_batch_planner.py` — per-shard planner source
- `scripts/cpv_batch_aggregator.py` — per-shard aggregator source
- `scripts/cpv_marketplace_input.py` — universal input resolver (TRDD-3dcbb37c)
- `scripts/cpv_batch_orchestrator.py` — per-plugin plan / status helper (TRDD-3dcbb37c)
