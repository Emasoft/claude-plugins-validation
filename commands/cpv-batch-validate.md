---
name: cpv-batch-validate
description: Fan out cpv-plugin-validator-agent agents across every plugin in a marketplace, a list of plugins, or a single plugin. Accepts local paths and GitHub URLs. One validator agent per plugin, dispatched in parallel from a single main-session message (default 8 at a time, cap 16). Returns one consolidated severity table covering every scanned plugin.
argument-hint: "<plugin-or-marketplace-or-list> [--max-parallel N]"
user-invocable: true
---

# /cpv-batch-validate — Validate every plugin in a marketplace, in parallel

For users who manage a fleet of plugins (a marketplace, a list of
project folders, or a Git org), `/cpv-validate-plugin` on each one
in turn is slow AND quietly burns context every time the user
re-loads the same handful of validators. `/cpv-batch-validate`
delegates each plugin to its own `cpv-plugin-validator-agent` subagent in
**parallel** — one fresh haiku context per plugin, all dispatched
from a single main-session message. Total main-session cost is
~3-4K tokens for any batch size; per-plugin work happens out-of-band
in the subagent contexts.

Input grammar (TRDD-3dcbb37c §1):

| Shape | Example |
|---|---|
| Single plugin (local) | `./my-plugin` |
| Single plugin (URL) | `https://github.com/owner/plugin` or `owner/plugin` |
| Marketplace (local) | `./marketplace-root` |
| Marketplace (URL) | `https://github.com/owner/marketplace-repo` |
| List on CLI | `./a ./b ./c` (whitespace-separated) |
| List in file | `@/tmp/inputs.txt` (one spec per line, `#` comments OK) |
| Comma-separated | `./a,./b,./c` |

URL inputs are cloned into `${TMPDIR}/cpv-batch-input-<uuid>/`;
marketplace inputs enumerate `.claude-plugin/marketplace.json` and
clone every plugin referenced. All temp clones are cleaned up
automatically when the batch finishes.

## You are the orchestrator

You — the model running THIS turn — orchestrate the batch from the
main session. You do NOT validate anything yourself. You delegate to
N parallel `cpv-plugin-validator-agent` subagents via the Agent tool, each in
`batch_validate` mode.

**Critical rules**:

- **NEVER scan plugin contents yourself.** Even if the resolver
  surfaces 17 plugins, you read ZERO source files.
- **NEVER print findings inline.** The agents write per-plugin
  status JSONs; the orchestrator just aggregates them into a status
  table.
- **NEVER use `AskUserQuestion`.** Plain-text prompts only.

## Step 0 — Resolve arguments

The user supplies:

- A target spec (required; first positional argument — accepts every
  shape from the table above).
- Optional `--max-parallel N` (default 8, cap 16).

If no spec was given, ask the user plain-text:

```text
What should I batch-validate? Provide an absolute path, a GitHub URL (https://github.com/owner/repo or owner/repo), a marketplace, or a list file like @/tmp/plugins.txt.
```

## Step 1 — Build the batch plan

Resolve `$CLAUDE_PLUGIN_ROOT` and run the orchestrator's plan subcommand:

```bash
# Separate the positional target specs from the --max-parallel flag in a
# single pass. We must NOT collapse to "$1": the input grammar above lists
# a whitespace-separated LIST (`./a ./b ./c`) as a valid shape, and the
# orchestrator's `plan` subcommand declares its inputs as `nargs="+"`, so
# every positional must be forwarded — using only "$1" would silently
# validate the first plugin and drop the rest.
#
# Both --max-parallel N (two tokens) and --max-parallel=N (one token) are
# handled, because argparse accepts the `=` form and a user may type it;
# the old `grep -A1 | tail` parse returned the literal `--max-parallel=N`
# for the `=` form and crashed argparse's type=int. Default 8; the
# orchestrator re-caps at 16, so a larger N is safe to pass.
BATCH_SPECS=()
MAX_PARALLEL=8
while [ "$#" -gt 0 ]; do
  case "$1" in
    --max-parallel)
      MAX_PARALLEL="$2"
      shift 2
      ;;
    --max-parallel=*)
      MAX_PARALLEL="${1#--max-parallel=}"
      shift
      ;;
    *)
      BATCH_SPECS+=("$1")
      shift
      ;;
  esac
done

if [ "${#BATCH_SPECS[@]}" -eq 0 ]; then
  echo "ERROR: no target spec given to /cpv-batch-validate" >&2
  exit 1
fi

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" plan \
  "${BATCH_SPECS[@]}" \
  --agent cpv-plugin-validator-agent \
  --mode batch_validate \
  --max-parallel "$MAX_PARALLEL"
```

Capture the orchestrator's stdout. It looks like:

```text
PLAN: /tmp/cpv-batch/<ts>-plugin-validator/plan.json
STATUS_TABLE: /tmp/cpv-batch/<ts>-plugin-validator/status_table.json
SESSION_DIR: /tmp/cpv-batch/<ts>-plugin-validator
PLUGIN_COUNT: <N>
DISPATCH_GROUPS: <G>
```

If `PLUGIN_COUNT` is `0`, reply plain-text:

```text
The input resolved to zero plugins. Nothing to validate. ✓
```

…and stop.

Otherwise queue the initial status table for the claude-menu-system
Stop hook (emitted post-turn via ``systemMessage`` — zero token cost,
NEVER printed inline by the orchestrator):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" "$STATUS_TABLE"
```

NEVER print menu inline; the CMS Stop hook emits via systemMessage at turn end.
End the turn after this call. Routing of the user's reply (if any) is
resolved purely from the fixed letter→action map in §"Fixed key→action map"
below — never by reading back the rendered menu.

## Step 2 — Dispatch one validator per plugin, in groups of `max_parallel`

Read `plan.json` and iterate `dispatch_groups[]`. For each group, in a
**single main-session message**, emit one Agent tool call per plugin
in the group:

```yaml
# Pseudocode — emit |group| Agent tool calls in ONE assistant message
for plugin_index in group:
    plugin = plan.plugins[plugin_index]
    Agent(
      subagent_type: "cpv-plugin-validator-agent",
      description: "Batch-validate plugin {plugin.display_name}",
      prompt: |
        <context>
        source: /cpv-batch-validate
        mode: batch_validate
        plugin_index: {plugin.plugin_index}
        plugin_path: {plugin.abs_path}
        source_url: {plugin.source_url or "—"}
        display_name: {plugin.display_name}
        session_dir: {plan.session_dir}
        status_path: {plan.session_dir}/plugin-{plugin.plugin_index}.status.json
        </context>

        Run `validate_plugin` on the plugin and write a per-plugin
        status JSON to `status_path` with these keys exactly:

          {
            "status_symbol": "✓" | "✗" | "⚠",
            "status_label": "valid" | "invalid" | "warning-only",
            "verdict": "VALID" | "INVALID",
            "counts": {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "report_path": "<abs-path-to-validation-report>",
            "notes": "<short summary, e.g. 0/0/0/0 + 1 WARNING>"
          }

        Return ONE line exactly:

          [plugin-{plugin.plugin_index}] {verdict}: <C>/<M>/<m>/<n>/<w> (status: {status_path})

        Do NOT render any menu. Do NOT recommend follow-up actions.
        The /cpv-batch-validate orchestrator handles aggregation.
      run_in_background: false
    )
```

Per the Anthropic spec, **multiple Agent tool calls in a single
assistant message execute in parallel**. After every Agent call
returns its one-liner, move to the next dispatch group.

## Step 3 — Refresh the status table after each wave

After each group's agents have returned, queue the live status table
via the orchestrator's ``emit-status`` subcommand (one shot —
aggregates every per-plugin status JSON and hands the CMS spec to
``print_menu`` for emission via the Stop hook at turn end):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" \
  emit-status "$SESSION_DIR/plan.json"
```

NEVER print menu inline; the CMS Stop hook emits via systemMessage at
turn end. End the turn after this call. The user sees the running
progress (one row per plugin: ✓ valid / ✗ invalid / ⚠ warning-only /
○ queued; CPV symbols are translated to the CMS enum
``ok``/``missing``/``buggy``/``pending`` by the orchestrator).

## Step 4 — Final summary

After ALL dispatch groups have finished:

1. Queue the final status table one more time (CMS Stop hook emits at
   turn end — same call as Step 3):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" \
     emit-status "$SESSION_DIR/plan.json"
   ```

2. Print a one-line summary inline (text, not a menu):

   ```text
   DONE: plugins=N valid=X invalid=Y warning-only=Z. Reports under {session_dir}/.
   ```

3. If any plugin is INVALID, append the fix-prompt inline:

   ```text
   Run `/cpv-batch-fix {original-target}` to fan out cpv-plugin-fixer-agent
   agents across the invalid plugins.
   ```

End the turn. The CMS Stop hook emits the final table via systemMessage.

## Fixed key→action map

`/cpv-batch-validate` is a one-shot fleet scan; the status table is
informational only. There are no numbered or lettered action rows in
its menu surface — the user's next move is to run the fix command
shown in the text summary. The slug ``batch-plugin-validator-status``
is reserved for this command's status table and used to derive the
queue path. The fixed key→action map is empty by design; future
post-scan menus (Phase 2+) extend this contract with letter→action
rows.

## Token-cost guarantee

| Item | Tokens |
|------|--------|
| Orchestrator plan output | ~500 |
| Initial status table | ~300 |
| Per-plugin one-line returns | ~80 × N |
| Mid-batch status refreshes | ~300 × ceil(N/8) |
| Final status table + summary | ~400 |
| **Total (N=17 plugins)** | **~3-4K tokens** |

No per-plugin report body ever crosses the main-session context.

## See also

- TRDD-3dcbb37c §1-5 — full design
- `scripts/cpv_marketplace_input.py` — input resolver
- `scripts/cpv_batch_orchestrator.py` — plan / status helper
- `agents/cpv-plugin-validator-agent.md` — `batch_validate` mode contract
- `commands/cpv-batch-security-audit.md`, `commands/cpv-batch-caching-audit.md`, `commands/cpv-batch-caching-optimize.md` — sibling batch skills
