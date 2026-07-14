---
name: cpv-batch-caching-audit
description: Fan out cache-optimizer-agent across every plugin in a marketplace, a list of plugins, or a single plugin. Accepts local paths and GitHub URLs. One cache-optimizer-agent per plugin running in batch_audit mode (read-only — Phase 1 of the cache pipeline). Detects the seven prompt-cache-invalidation patterns (CA-01..CA-07) per plugin without applying fixes. Parallel main-session dispatch (default 8, cap 16).
argument-hint: "<plugin-or-marketplace-or-list> [--max-parallel N]"
user-invocable: true
---

# /cpv-batch-caching-audit — Read-only cache audit across many plugins

The cache-optimizer-agent's full workflow (audit → fix → re-validate
→ optional Phase-4 broader refactor) is expensive — each Phase-4 step
requires interactive confirmation. For a fleet-wide "which plugins
have cache-invalidation findings?" snapshot, this command runs only
**Phase 1** (audit) per plugin and returns the per-plugin severity
table. Apply fixes later with `/cpv-batch-caching-optimize`.

Same input grammar as the rest of the batch family — single plugin
local/URL, marketplace local/URL, list, `@listfile`, or
comma-separated.

## You are the orchestrator

You — the model running THIS turn — drive the batch from the main
session. You do NOT audit anything yourself.

## Step 0 — Resolve arguments

If no target was given, ask plain-text:

```text
Which marketplace, plugin, or list should I cache-audit? Provide a path, URL, or @listfile.
```

## Step 1 — Build the batch plan

```bash
BATCH_SPEC="$1"
MAX_PARALLEL=8

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" plan \
  "$BATCH_SPEC" \
  --agent cache-optimizer-agent \
  --mode batch_audit \
  --max-parallel "$MAX_PARALLEL"
```

Capture the orchestrator's stdout (``PLAN``, ``STATUS_TABLE``,
``SESSION_DIR``, ``PLUGIN_COUNT``, ``DISPATCH_GROUPS``). Queue the
initial status table for the claude-menu-system Stop hook (emitted
post-turn via ``systemMessage`` — zero token cost, NEVER printed
inline by the orchestrator):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_menu.py" "$STATUS_TABLE"
```

NEVER print menu inline; the CMS Stop hook emits via systemMessage at
turn end.

## Step 2 — Dispatch audit agents in parallel

```yaml
for plugin_index in group:
    plugin = plan.plugins[plugin_index]
    Agent(
      subagent_type: "cache-optimizer-agent",
      description: "Batch-cache-audit {plugin.display_name}",
      prompt: |
        <context>
        source: /cpv-batch-caching-audit
        mode: batch_audit
        plugin_index: {plugin.plugin_index}
        plugin_path: {plugin.abs_path}
        source_url: {plugin.source_url or "—"}
        display_name: {plugin.display_name}
        session_dir: {plan.session_dir}
        status_path: {plan.session_dir}/plugin-{plugin.plugin_index}.status.json
        </context>

        Run Phase 1 (Audit) of your standard cache workflow ONLY —
        skip Phase 2 (Fix), Phase 3 (Re-validate), and Phase 4
        (Broader refactor). Write per-plugin status JSON to
        `status_path` with the canonical batch_audit schema (see
        agents/cache-optimizer-agent.md). Audit-only, so set `after`
        equal to `before`:

          {
            "status_symbol": "✓" | "✗" | "⚠",
            "status_label": "clean" | "findings" | "warning-only",
            "before": {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "after":  {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "report_path": "<abs-path-to-cache-audit-report>",
            "notes": "<short summary, e.g. 0 CA-01, 2 CA-04>"
          }

        Return ONE line exactly:

          [plugin-{plugin.plugin_index}] {label}: <C>/<M>/<m>/<n>/<w> (status: {status_path})

        Do NOT render menus. Do NOT apply fixes. Do NOT ask the user
        whether to proceed — this is read-only audit mode.
      run_in_background: false
    )
```

## Step 3 — Mid-batch status refresh

Queue the live status table via the orchestrator's ``emit-status``
subcommand (aggregates every per-plugin status JSON, hands the CMS
spec to ``cpv_menu`` — Stop hook emits at turn end):

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
   DONE: plugins=N clean=X findings=Y warning-only=Z. Reports under {session_dir}/.
   ```

3. If any plugin has findings, append the optimise-prompt inline:

   ```text
   Run `/cpv-batch-caching-optimize {target}` to dispatch optimization
   agents that will fix the CA-01..CA-07 findings.
   ```

End the turn. The CMS Stop hook emits the final table via systemMessage.

## Fixed key→action map

`/cpv-batch-caching-audit` is a read-only fleet cache scan; the status
table is informational only. No numbered or lettered action rows —
the user's next move is to run `/cpv-batch-caching-optimize` (text
summary). The slug ``batch-cache-optimizer-agent-status`` is reserved
for this command's status table. The fixed key→action map is empty by
design; future post-scan menus extend this contract with letter→action
rows.

## See also

- TRDD-3dcbb37c §1-5 — full design
- `agents/cache-optimizer-agent.md` — `batch_audit` mode contract
- `skills/cache-validation-skill/SKILL.md` — CA-01..CA-07 pattern catalog
- `commands/cpv-batch-caching-optimize.md` — sibling fix-mode command
