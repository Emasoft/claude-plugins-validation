---
name: cpv-batch-caching-optimize
description: Fan out cpv-cache-optimizer-agent across every plugin in a marketplace, a list of plugins, or a single plugin and APPLY the cache-invalidation fixes (CA-01..CA-07). Accepts local paths and GitHub URLs. One cpv-cache-optimizer-agent per plugin in batch_fix mode (Phase 1 audit + Phase 2 fix + Phase 3 re-validate; Phase 4 broader refactor SKIPPED in batch mode to avoid per-plugin user prompts). Parallel main-session dispatch (default 8, cap 16).
argument-hint: "<plugin-or-marketplace-or-list> [--max-parallel N]"
user-invocable: true
---

# /cpv-batch-caching-optimize — Apply cache-invalidation fixes across many plugins

For users who maintain a marketplace and want to apply the
cache-invalidation pattern fixes (CA-01..CA-07) across every
plugin in one pass, this command fans out one `cpv-cache-optimizer-agent`
per plugin in **`batch_fix`** mode. Each agent runs Phase 1 (Audit)
→ Phase 2 (Fix) → Phase 3 (Re-validate) on its assigned plugin and
writes its result back as the per-plugin status JSON.

Phase 4 (Broader cache-aware refactor) is **deliberately skipped**
in batch mode: Phase 4 changes require interactive per-step
approval and that doesn't compose with a parallel dispatch
(each agent would block waiting for the orchestrator's permission).
Run the interactive cache flow from `/cpv-main-menu` (menu-tree §3.3.3
"Audit + broader refactoring") on a single plugin if you want Phase 4.

Same input grammar as the rest of the batch family.

## You are the orchestrator

You — the model running THIS turn — drive the batch. You do NOT
optimize anything yourself.

## Step 0 — Resolve arguments

If no target was given, ask plain-text:

```text
Which marketplace, plugin, or list should I cache-optimize? Provide a path, URL, or @listfile.
```

## Step 1 — Build the batch plan

```bash
BATCH_SPEC="$1"
MAX_PARALLEL=8

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" plan \
  "$BATCH_SPEC" \
  --agent cpv-cache-optimizer-agent \
  --mode batch_fix \
  --max-parallel "$MAX_PARALLEL"
```

Capture the orchestrator's stdout (``PLAN``, ``STATUS_TABLE``,
``SESSION_DIR``, ``PLUGIN_COUNT``, ``DISPATCH_GROUPS``). Queue the
initial status table for the claude-menu-system Stop hook (emitted
post-turn via ``systemMessage`` — zero token cost, NEVER printed
inline by the orchestrator):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/print_menu.py" "$STATUS_TABLE"
```

NEVER print menu inline; the CMS Stop hook emits via systemMessage at
turn end.

## Step 2 — Dispatch optimize agents in parallel

```yaml
for plugin_index in group:
    plugin = plan.plugins[plugin_index]
    Agent(
      subagent_type: "cpv-cache-optimizer-agent",
      description: "Batch-cache-optimize {plugin.display_name}",
      prompt: |
        <context>
        source: /cpv-batch-caching-optimize
        mode: batch_fix
        plugin_index: {plugin.plugin_index}
        plugin_path: {plugin.abs_path}
        source_url: {plugin.source_url or "—"}
        display_name: {plugin.display_name}
        session_dir: {plan.session_dir}
        status_path: {plan.session_dir}/plugin-{plugin.plugin_index}.status.json
        </context>

        Run Phase 1 (Audit) → Phase 2 (Fix) → Phase 3 (Re-validate)
        on your standard cache workflow. SKIP Phase 4 (Broader
        refactor) — batch mode does not allow per-step user
        prompts. Write per-plugin status JSON to `status_path` with:

          {
            "status_symbol": "✓" | "✗" | "⚠",
            "status_label": "clean" | "fixed" | "partial" | "failed",
            "before": {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "after":  {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "report_path": "<abs-path-to-final-cache-report>",
            "notes": "<short summary, e.g. fixed 5/5, 0 remaining>"
          }

        Return ONE line exactly:

          [plugin-{plugin.plugin_index}] {label}: fixed=X remaining=Y (status: {status_path})

        Do NOT render menus. Do NOT prompt the user for Phase 4 —
        this is non-interactive batch mode.
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
   DONE: plugins=N clean=X fixed=Y partial=Z failed=W. Reports under {session_dir}/.
   ```

3. If any plugin is `partial` or `failed`, append the retry-prompt
   inline:

   ```text
   Some plugins still have remaining CA-* findings. Inspect their
   reports for per-plugin error details and re-run /cpv-batch-caching-optimize
   to retry; or run the interactive cache flow from /cpv-main-menu
   (menu-tree §3.3.3 "Audit + broader refactoring") on a single plugin
   for Phase 4 (broader refactor).
   ```

End the turn. The CMS Stop hook emits the final table via systemMessage.

## Fixed key→action map

`/cpv-batch-caching-optimize` is a one-shot fleet cache fixer; the
status table is informational only. No numbered or lettered action
rows — the user's next move is the text suggestion above (re-run, or
switch to the interactive cache flow via `/cpv-main-menu` →
menu-tree §3.3.3 "Audit + broader refactoring" for Phase 4). The slug
``batch-cache-optimizer-agent-status`` is shared with
`/cpv-batch-caching-audit` (same agent type — intentional). The fixed
key→action map is empty by design; future post-scan menus extend this
contract with letter→action rows.

## Why batch mode skips Phase 4

Phase 4 changes (e.g. extracting shared cache headers into a
common skill, restructuring agent contexts to share prefixes) need
the user to approve each refactor individually because they
affect the plugin's user-facing structure. In batch mode the
orchestrator can't surface 17 different "should I refactor this?"
prompts mid-dispatch — every agent would block. Phase 4 stays
opt-in via the interactive single-plugin cache flow under
`/cpv-main-menu` (menu-tree §3.3.3 "Audit + broader refactoring").

## See also

- TRDD-3dcbb37c §1-5 — full design
- `agents/cpv-cache-optimizer-agent.md` — `batch_fix` mode contract
- `skills/cpv-cache-validation-skill/SKILL.md` — CA-01..CA-07 pattern catalog
- `commands/cpv-batch-caching-audit.md` — sibling read-only audit command
