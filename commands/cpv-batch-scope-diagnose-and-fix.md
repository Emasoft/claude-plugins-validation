---
name: cpv-batch-scope-diagnose-and-fix
description: Same-turn diagnose + fix across a fleet of project folders. One cpv-doctor-agent per project scans the requested scope + verifies findings + applies obvious fixes inline (NIT and CRITICAL auto; MAJOR / MINOR auto when the recipe is safe). LOCAL paths only — URL inputs are CRITICAL errors. Default 8 parallel agents per main-session message, cap 16.
argument-hint: "[project-folder-or-list] [--scope full|user|project|local] [--max-parallel N]"
user-invocable: true
---

# /cpv-batch-scope-diagnose-and-fix — Same-turn scope-aware sweep

Combines `/cpv-batch-scope-diagnose` and `/cpv-batch-scope-fix`
into one per-project agent turn. Each cpv-doctor-agent reads each
scope-anchored file ONCE, classifies findings, applies the
obvious mechanical fixes inline, and writes the per-project
status JSON.

Per TRDD-a175f78d §3, the same-turn variant auto-applies:

- NIT (duplicate-no-effect) → silent
- CRITICAL (misplaced local-scope entry) → silent
- MAJOR / MINOR with a SAFE recipe (e.g. the fix is purely deleting
  a duplicate that has identical content modulo whitespace) → silent
- MAJOR / MINOR with UNSAFE recipes (content-differing duplicates,
  untracked refs that the user might WANT preserved) → reported in
  `pending_fixes[]`, not applied

LOCAL paths only — URL inputs are CRITICAL errors.

## You are the orchestrator

You — the model running THIS turn — drive the batch. You do NOT
diagnose or fix anything yourself.

## Step 0 — Resolve arguments

URL inputs → canonical CRITICAL message (see
`/cpv-batch-scope-diagnose`). Empty input → `$PWD`.

## Step 1 — Build the batch plan

```bash
BATCH_SPEC="${1:-$PWD}"
SCOPE="full"
MAX_PARALLEL=8

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" plan \
  "$BATCH_SPEC" \
  --agent cpv-doctor-agent \
  --mode batch_scope_same_turn \
  --no-url \
  --max-parallel "$MAX_PARALLEL"
```

Print the initial status table.

## Step 2 — Dispatch one doctor per project, same-turn

```yaml
for plugin_index in group:
    plugin = plan.plugins[plugin_index]
    Agent(
      subagent_type: "cpv-doctor-agent",
      description: "Batch-scope-diagnose-and-fix {plugin.display_name}",
      prompt: |
        <context>
        source: /cpv-batch-scope-diagnose-and-fix
        mode: batch_scope_same_turn
        scope: {scope}
        plugin_index: {plugin.plugin_index}
        target_path: {plugin.abs_path}
        display_name: {plugin.display_name}
        session_dir: {plan.session_dir}
        status_path: {plan.session_dir}/plugin-{plugin.plugin_index}.status.json
        </context>

        Run the doctor's scope-aware diagnostic + apply obvious
        fixes in ONE pass per TRDD-a175f78d §3:

        * Scan each scope-anchored file ONCE (no separate validate
          → fix → re-validate cycle).
        * For NIT / CRITICAL / safe-MAJOR / safe-MINOR findings:
          apply inline.
        * For unsafe-MAJOR / unsafe-MINOR: record in
          `pending_fixes[]` without applying.
        * Run ONE final clean-room re-check after every applied
          fix.

        Write per-project status JSON to `status_path`:

          {
            "schema_version": 1,
            "plugin_index": <int>,
            "scope": <scope>,
            "status_symbol": "✓" | "✗" | "⚠",
            "status_label": "clean" | "fixed" | "partial" | "failed",
            "before": {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "after":  {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "pending_fixes": [<list>],
            "report_path": "<abs-path-to-final-report>",
            "notes": "<short summary>"
          }

        Return ONE line exactly:

          [project-{plugin.plugin_index}] {label}: fixed=X pending=Y (status: {status_path})

        Do NOT render menus.
      run_in_background: false
    )
```

## Step 3 — Mid-batch status refresh + Step 4 — Final summary

Same as the read-only diagnose command. After every project
returns, list any pending fixes per project so the user can
choose which to apply.

## Why a same-turn variant?

Because the doctor's diagnostic surface is the LOCAL filesystem
(not a remote repo), each scope-anchored file is fast to read but
the diagnostic has many recipes per file. Reading each file once
and applying all in-scope fixes inline cuts per-project token
cost without sacrificing accuracy.

## See also

- TRDD-a175f78d — full design
- `agents/cpv-doctor-agent.md` — `batch_scope_same_turn` mode contract
- `commands/cpv-batch-scope-diagnose.md` — read-only variant
- `commands/cpv-batch-scope-fix.md` — separate-pass fix command
