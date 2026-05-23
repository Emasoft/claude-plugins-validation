---
name: cpv-batch-validate-and-fix
description: Same-turn validate-and-fix across a marketplace / list / single plugin. One plugin-fixer per plugin scans + verifies false positives + fixes ALL findings in the same turn — each source file is READ ONCE, not the three times the separate validate → fix → re-validate cycle requires. Cuts per-plugin token cost ~3×. Default 8 parallel agents per main-session message, cap 16.
argument-hint: "<plugin-or-marketplace-or-list> [--max-parallel N]"
user-invocable: true
---

# /cpv-batch-validate-and-fix — Same-turn validate + verify + fix

For fleet operators who want to apply validation fixes across a
marketplace WITHOUT paying the token cost of the standard
`/cpv-batch-validate` → `/cpv-batch-fix` pipeline (which reads every
plugin's source files THREE times — once for validate, once for
fix, once for re-validate), this command runs both phases in **one
agent turn per plugin**. Each plugin-fixer subagent reads each
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
  --agent plugin-fixer \
  --mode batch_same_turn_validate_fix \
  --max-parallel "$MAX_PARALLEL"
```

Print the initial status table:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" status_table "$(cat "$STATUS_TABLE")"
```

## Step 2 — Dispatch one fixer per plugin, in groups of max_parallel

For each `dispatch_groups[i]`, emit one Agent call per plugin in a
single main-session message:

```yaml
for plugin_index in group:
    plugin = plan.plugins[plugin_index]
    Agent(
      subagent_type: "plugin-fixer",
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

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" status \
  "$SESSION_DIR/plan.json" \
| python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" status_table /dev/stdin
```

## Step 4 — Final summary

After every plugin has reported, print the final status table +
one-line summary:

```text
DONE: plugins=N clean=X fixed=Y partial=Z failed=W. Total FPs verified: F. Reports under {session_dir}/.
```

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
- `agents/plugin-fixer.md` — `batch_same_turn_validate_fix` mode contract
