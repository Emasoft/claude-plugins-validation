---
name: cpv-batch-validate-and-fix
description: Same-turn validate-and-fix across a marketplace / list / single plugin. One cpv-plugin-fixer-agent per plugin scans + verifies false positives + fixes ALL findings in the same turn — each source file is READ ONCE, not the three times the separate validate → fix → re-validate cycle requires. Cuts per-plugin token cost ~3×. Default 8 parallel agents per main-session message, cap 16.
argument-hint: "<plugin-or-marketplace-or-list> [--max-parallel N]"
user-invocable: true
---

# /cpv-batch-validate-and-fix — Same-turn validate + verify + fix

For fleet operators who want to apply validation fixes across a
marketplace WITHOUT paying the token cost of the standard
`/cpv-batch-validate` → `/cpv-batch-fix` pipeline (which reads every
plugin's source files THREE times — once for validate, once for
fix, once for re-validate), this command runs both phases in **one
agent turn per plugin**. Each cpv-plugin-fixer-agent subagent reads each
source file ONCE, scans + verifies false positives (via the
v2.100.x AST/schema/markdown classifier) + applies fixes inline.
No intermediate JSON report is written; the per-plugin status JSON
carries the before/after counts.

Per the iron-rule: the same-turn mode is an OPTIMISATION, never a
shortcut. Every finding still goes through the full classifier
chain; FPs are verified via `llm-externalizer` with file-range
syntax (≤ 200 LOC per call) before being silenced.

Same input grammar as the other batch skills — single plugin /
plugin URL / marketplace local/URL / list / `@listfile` /
comma-separated.

## You are the orchestrator

You — the model running THIS turn — drive the batch. You do NOT
fix anything yourself.

## Step 0 — Resolve arguments

If no target was given, ask plain-text:

```text
What should I validate-and-fix in one pass? Provide an absolute path, a GitHub URL, a marketplace, or a list file like @/tmp/plugins.txt.
```

## Step 1 — Build the batch plan

```bash
BATCH_SPEC="$1"
MAX_PARALLEL=8

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" plan \
  "$BATCH_SPEC" \
  --agent cpv-plugin-fixer-agent \
  --mode batch_same_turn_validate_fix \
  --max-parallel "$MAX_PARALLEL"
```

Capture the orchestrator's stdout. It prints one `KEY: value` line each
for `PLAN`, `STATUS_TABLE`, `SESSION_DIR`, `PLUGIN_COUNT`, and
`DISPATCH_GROUPS`. Bind the two you need downstream:

```bash
STATUS_TABLE="$(... STATUS_TABLE line from the plan output ...)"
SESSION_DIR="$(...  SESSION_DIR  line from the plan output ...)"
```

If `PLUGIN_COUNT` is `0`, reply plain-text that there is nothing to
validate-and-fix and stop.

Queue the initial status table for the claude-menu-system Stop hook
(emitted post-turn via ``systemMessage`` — zero token cost, NEVER
printed inline by the orchestrator):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" "$STATUS_TABLE"
```

NEVER print menu inline; the CMS Stop hook emits via systemMessage at turn end.
End the turn after this call.

## Step 2 — Dispatch one fixer per plugin, in groups of max_parallel

For each `dispatch_groups[i]`, emit one Agent call per plugin in a
single main-session message:

```yaml
for plugin_index in group:
    plugin = plan.plugins[plugin_index]
    Agent(
      subagent_type: "cpv-plugin-fixer-agent",
      description: "Batch-validate-and-fix {plugin.display_name}",
      prompt: |
        <context>
        source: /cpv-batch-validate-and-fix
        mode: batch_same_turn_validate_fix
        plugin_index: {plugin.plugin_index}
        plugin_path: {plugin.abs_path}
        source_url: {plugin.source_url or "—"}
        display_name: {plugin.display_name}
        session_dir: {plan.session_dir}
        status_path: {plan.session_dir}/plugin-{plugin.plugin_index}.status.json
        </context>

        Apply the same-turn validate-and-fix loop to the plugin at
        `plugin_path`:

        1. Walk every source file ONCE (skills/*.md, agents/*.md,
           commands/*.md, scripts/*.py, plugin.json, etc.).
        2. For each file: scan it via the in-process validators,
           classify each finding via the v2.100.x context
           classifier (Python AST / JSON schema / Markdown fence /
           YAML workflow). For uncertain findings, invoke
           `llm-externalizer` with file-range syntax (≤ 200 LOC
           per call) — minimum-token FP verification.
        3. Apply confirmed-real fixes inline. Skip confirmed FPs.
        4. After every file is fixed, run ONE final
           `validate_plugin --strict` as the clean-room re-check.

        Write per-plugin status JSON to `status_path`:

          {
            "schema_version": 1,
            "plugin_index": <int>,
            "status_symbol": "✓" | "✗" | "⚠",
            "status_label": "clean" | "fixed" | "partial" | "failed",
            "before": {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "after":  {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "fps_verified": <int>,
            "report_path": "<abs-path-to-final-re-check-report>",
            "notes": "<short summary, e.g. fixed 12/12, 3 FPs verified>"
          }

        Return ONE line exactly:

          [plugin-{plugin.plugin_index}] {label}: fixed=X remaining=Y fps=Z (status: {status_path})

        Do NOT render menus. Do NOT recommend follow-ups.
      run_in_background: false
    )
```

## Step 3 — Mid-batch status refresh

Queue the live status table via the orchestrator's ``emit-status``
subcommand (aggregates every per-plugin status JSON, hands the CMS
spec to ``print_menu`` — Stop hook emits at turn end):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" \
  emit-status "$SESSION_DIR/plan.json"
```

NEVER print menu inline; the CMS Stop hook emits via systemMessage at
turn end. End the turn after this call.

## Step 4 — Final summary

After every plugin has reported:

1. Queue the final status table:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" \
     emit-status "$SESSION_DIR/plan.json"
   ```

2. Print a one-line summary inline (text, not a menu):

   ```text
   DONE: plugins=N clean=X fixed=Y partial=Z failed=W. Total FPs verified: F. Reports under {session_dir}/.
   ```

End the turn. The CMS Stop hook emits the final table via systemMessage.

## Fixed key→action map

`/cpv-batch-validate-and-fix` is a one-shot same-turn fleet fix; the
status table is informational only. No numbered or lettered action
rows. The slug ``batch-cpv-plugin-fixer-agent-status`` is reserved for this
command's status table (shared with `/cpv-batch-fix`,
`/cpv-batch-full-scan-and-fix` — same agent type). The fixed
key→action map is empty by design; future post-scan menus extend this
contract with letter→action rows.

## Why a separate same-turn variant?

Same-turn mode is an OPTIMISATION trade-off:

| Knob | Standard pipeline | Same-turn |
|------|-------------------|-----------|
| Per-file reads (worst case) | 3 (validate / fix / re-validate) | 1 |
| FP verification | post-hoc (in `/cpv-fix-validation`) | inline via llm-externalizer |
| Intermediate JSON reports | YES | NO (just status JSON) |
| Per-plugin token cost | high | ~3× lower |
| Visibility into FP decisions | high (separate reports) | medium (in status JSON) |

When in doubt, use `/cpv-batch-validate` + `/cpv-batch-fix`
separately (more visibility, slower). When you trust the
classifier chain and want a faster sweep, use this command.

## See also

- TRDD-3dcbb37c §3 — full design
- `/cpv-batch-validate` + `/cpv-batch-fix` — separate-pass equivalent
- `/cpv-batch-full-scan-and-fix` — same-turn but includes security + caching audit
- `agents/cpv-plugin-fixer-agent.md` — `batch_same_turn_validate_fix` mode contract
