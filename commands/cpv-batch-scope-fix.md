---
name: cpv-batch-scope-fix
description: Apply fixes for scope-aware doctor findings across a fleet of project folders. One cpv-doctor-agent per project in batch_scope_fix mode handles the requested scope (user / project / local / full). LOCAL paths only — URL inputs are CRITICAL errors. Default 8 parallel agents per main-session message, cap 16.
argument-hint: "[project-folder-or-list] [--scope full|user|project|local] [--max-parallel N]"
user-invocable: true
---

# /cpv-batch-scope-fix — Apply scope-aware doctor fixes across a fleet

Counterpart to `/cpv-batch-scope-diagnose`. After the doctor has
diagnosed each project, this command dispatches one per project in
**fix** mode to apply the obvious mechanical fixes the diagnostic
flagged. The same scope semantics apply (`user` / `project` /
`local` / `full`).

Per TRDD-a175f78d §3:

- **NIT** findings (duplicates with identical content) →
  auto-applied silently.
- **CRITICAL** findings (misplaced settings.local.json entries) →
  auto-applied silently.
- **MAJOR** / **MINOR** findings (content-differing duplicates,
  untracked refs, etc.) → reported with a fix recipe but require
  explicit user approval before applying. The same-turn variant
  (`/cpv-batch-scope-diagnose-and-fix`) handles MAJOR/MINOR
  inline; this command requires a prior diagnose pass.

LOCAL paths only — URL inputs are CRITICAL errors (the doctor
needs `~/.claude/` filesystem access).

## You are the orchestrator

You — the model running THIS turn — drive the batch. You do NOT
fix anything yourself.

## Step 0 — Resolve arguments

If any input looks URL-shaped, surface the canonical CRITICAL
message (see `/cpv-batch-scope-diagnose` Step 0) and stop.

If no project list was given, default to `$PWD`.

## Step 1 — Build the batch plan

```bash
BATCH_SPEC="${1:-$PWD}"
SCOPE="full"
MAX_PARALLEL=8

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" plan \
  "$BATCH_SPEC" \
  --agent cpv-doctor-agent \
  --mode batch_scope_fix \
  --no-url \
  --max-parallel "$MAX_PARALLEL"
```

Capture the orchestrator's stdout (``PLAN``, ``STATUS_TABLE``,
``SESSION_DIR``, ``PLUGIN_COUNT``, ``DISPATCH_GROUPS``). `$SESSION_DIR`
is required by Steps 3 and 4 (`emit-status "$SESSION_DIR/plan.json"`),
so you MUST bind it here from the `SESSION_DIR:` line. Queue the
initial status table for the claude-menu-system Stop hook (emitted
post-turn via ``systemMessage`` — zero token cost, NEVER printed
inline by the orchestrator):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_menu.py" "$STATUS_TABLE"
```

NEVER print menu inline; the CMS Stop hook emits via systemMessage at
turn end.

## Step 2 — Dispatch one doctor per project

```yaml
for plugin_index in group:
    plugin = plan.plugins[plugin_index]
    Agent(
      subagent_type: "cpv-doctor-agent",
      description: "Batch-scope-fix {plugin.display_name}",
      prompt: |
        <context>
        source: /cpv-batch-scope-fix
        mode: batch_scope_fix
        scope: {scope}
        plugin_index: {plugin.plugin_index}
        target_path: {plugin.abs_path}
        display_name: {plugin.display_name}
        session_dir: {plan.session_dir}
        status_path: {plan.session_dir}/plugin-{plugin.plugin_index}.status.json
        </context>

        Apply scope-aware fixes per TRDD-a175f78d §3:

        * NIT (duplicate-no-effect): auto-delete the duplicate.
        * CRITICAL (misplaced local-scope entry referencing a
          non-existent or off-tree file): auto-move into the
          correct .claude/ tree.
        * MAJOR / MINOR (content-differing duplicates, untracked
          refs, etc.): DO NOT auto-apply. Record the fix recipe in
          status JSON `pending_fixes[]` so the orchestrator can
          escalate to the user.

        Write per-project status JSON to `status_path`:

          {
            "schema_version": 1,
            "plugin_index": <int>,
            "scope": <scope>,
            "status_symbol": "✓" | "✗" | "⚠",
            "status_label": "clean" | "fixed" | "partial" | "failed",
            "before": {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "after":  {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "pending_fixes": [<list of fix recipes the user must approve>],
            "report_path": "<abs-path-to-scope-doctor-fix-report>",
            "notes": "<short summary>"
          }

        Return ONE line exactly:

          [project-{plugin.plugin_index}] {label}: fixed=X pending=Y (status: {status_path})

        Do NOT render menus. Do NOT silently mutate ~/.claude/
        beyond the iron-rule fix categories.
      run_in_background: false
    )
```

## Step 3 — Mid-batch status refresh

Queue the live status table via the orchestrator's ``emit-status``
subcommand (aggregates every per-project status JSON, hands the CMS
spec to ``cpv_menu`` — Stop hook emits at turn end):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" \
  emit-status "$SESSION_DIR/plan.json"
```

NEVER print menu inline; the CMS Stop hook emits via systemMessage at
turn end. End the turn after this call.

## Step 4 — Final summary

After every project has reported:

1. Queue the final status table (same call as Step 3).

2. Print a one-line summary inline (text, not a menu). This is a
   **fix** command, so the summary uses the fix vocabulary
   (`fixed` / `partial` / `failed`) that matches the per-project
   `status_label` above — NOT the diagnose-mode `clean` / `findings`
   / `warning-only` shape:

   ```text
   DONE: projects=N clean=X fixed=Y partial=Z failed=W. Pending fixes: P. Reports under {session_dir}/.
   ```

3. If any project has `pending_fixes`, list them so the user can pick
   which to apply (via `/cpv-batch-scope-diagnose-and-fix` for the
   same-turn variant or the doctor's per-project interactive flow).

End the turn. The CMS Stop hook emits the final table via systemMessage.

## See also

- TRDD-a175f78d — full design
- `agents/cpv-doctor-agent.md` — `batch_scope_fix` mode contract
- `commands/cpv-batch-scope-diagnose.md` — read-only sibling
- `commands/cpv-batch-scope-diagnose-and-fix.md` — same-turn variant
