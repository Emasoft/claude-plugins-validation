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

Print the initial status table.

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

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" status \
  "$SESSION_DIR/plan.json" \
| python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" status_table /dev/stdin
```

## Step 4 — Final summary

```text
DONE: projects=N clean=X findings=Y warning-only=Z. Conflicts total: C. Reports under {session_dir}/.
```

If any project has findings or conflicts, suggest:

```text
Run /cpv-batch-scope-fix --scope <same scope> <same target> to apply fixes.
```

## See also

- TRDD-a175f78d — full design
- `agents/cpv-doctor-agent.md` — `batch_scope_diagnose` mode contract
- `commands/cpv-batch-scope-fix.md` — sibling fix-mode command
- `commands/cpv-batch-scope-diagnose-and-fix.md` — same-turn variant
