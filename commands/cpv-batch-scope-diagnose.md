---
name: cpv-batch-scope-diagnose
description: Read-only diagnostic across a fleet of project folders. One cpv-doctor-agent per project diagnoses the requested scope — user (~/.claude/), project (<project>/.claude/), local (<project>/.claude/settings.local.json), or full (all three + cross-scope conflict checker). LOCAL paths only — URL inputs are CRITICAL errors because the doctor needs ~/.claude/ filesystem access. Default 8 parallel agents per main-session message, cap 16.
argument-hint: "[project-folder-or-list] [--scope full|user|project|local] [--max-parallel N]"
user-invocable: true
---

# /cpv-batch-scope-diagnose — Read-only scope-aware diagnostic

The doctor's diagnostic surface includes three filesystem-anchored
extension scopes:

| Scope | Surface |
|---|---|
| `user` | `~/.claude/` — global agents/skills/MCP/hooks/output-styles/etc. |
| `project` | `<project>/.claude/` — git-tracked entries |
| `local` | `<project>/.claude/settings.local.json` — gitignored local entries |
| `full` | All of the above + cross-scope conflict checker |

For users who operate across many project folders (a fleet), this
command runs the doctor in **read-only** mode on every one, in
parallel, and aggregates the per-project findings into a single
status table. URL inputs are forbidden — the doctor needs
`~/.claude/` and `<project>/.claude/` filesystem state.

## You are the orchestrator

You — the model running THIS turn — drive the batch. You do NOT
diagnose anything yourself.

## Step 0 — Resolve arguments

If no project folder list was given, default to `$PWD`. If any
input looks URL-shaped, surface the canonical CRITICAL message:

```text
ERROR: cpv-batch-scope-* skills require LOCAL project paths.
       A valid Claude installation (~/.claude/) is necessary to
       diagnose user/project/local-scope extensions. URL inputs
       cannot reach the filesystem state of a Claude installation.
       Use cpv-batch-validate or cpv-batch-doctor for source-tree
       scans of remote plugins.
```

…and stop.

## Step 1 — Build the batch plan

```bash
BATCH_SPEC="${1:-$PWD}"
SCOPE="full"           # or --scope override
MAX_PARALLEL=8

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" plan \
  "$BATCH_SPEC" \
  --agent cpv-doctor-agent \
  --mode batch_scope_diagnose \
  --no-url \
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

## Step 2 — Dispatch one doctor per project, in groups of max_parallel

```yaml
for plugin_index in group:
    plugin = plan.plugins[plugin_index]
    Agent(
      subagent_type: "cpv-doctor-agent",
      description: "Batch-scope-diagnose {plugin.display_name}",
      prompt: |
        <context>
        source: /cpv-batch-scope-diagnose
        mode: batch_scope_diagnose
        scope: {scope}   # user | project | local | full
        plugin_index: {plugin.plugin_index}
        target_path: {plugin.abs_path}
        display_name: {plugin.display_name}
        session_dir: {plan.session_dir}
        status_path: {plan.session_dir}/plugin-{plugin.plugin_index}.status.json
        </context>

        Run the doctor's scope-aware recipes for the requested
        scope on `target_path`:

        - `user`: scan ~/.claude/ only.
        - `project`: scan target_path/.claude/ only (git-tracked entries).
        - `local`: scan target_path/.claude/settings.local.json only.
        - `full`: all three PLUS the cross-scope conflict checker
                 (TRDD-a175f78d §3).

        Write per-project status JSON to `status_path`:

          {
            "schema_version": 1,
            "plugin_index": <int>,
            "scope": <scope>,
            "status_symbol": "✓" | "✗" | "⚠",
            "status_label": "clean" | "findings" | "warning-only",
            "counts": {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "conflicts": <int>,   # cross-scope conflicts (full mode only)
            "report_path": "<abs-path-to-scope-doctor-report>",
            "notes": "<short summary>"
          }

        Return ONE line exactly:

          [project-{plugin.plugin_index}] {label}: <C>/<M>/<m>/<n>/<w> conflicts=<X> (status: {status_path})

        Do NOT apply fixes (this is read-only mode). Do NOT render
        menus.
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

1. Queue the final status table:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" \
     emit-status "$SESSION_DIR/plan.json"
   ```

2. Print a one-line summary inline (text, not a menu):

   ```text
   DONE: projects=N clean=X findings=Y warning-only=Z. Conflicts total: C. Reports under {session_dir}/.
   ```

3. If any project has findings or conflicts, append the fix-prompt
   inline:

   ```text
   Run /cpv-batch-scope-fix --scope <same scope> <same target> to apply fixes.
   ```

End the turn. The CMS Stop hook emits the final table via systemMessage.

## Fixed key→action map

`/cpv-batch-scope-diagnose` is a read-only fleet scope diagnostic; the
status table is informational only. No numbered or lettered action
rows — the user's next move is the text suggestion above
(`/cpv-batch-scope-fix`). The slug ``batch-cpv-doctor-agent-status``
is reserved for cpv-doctor-driven batch commands. The fixed
key→action map is empty by design; future post-scan menus extend this
contract with letter→action rows.

## See also

- TRDD-a175f78d — full design
- `agents/cpv-doctor-agent.md` — `batch_scope_diagnose` mode contract
- `commands/cpv-batch-scope-fix.md` — sibling fix-mode command
- `commands/cpv-batch-scope-diagnose-and-fix.md` — same-turn variant
