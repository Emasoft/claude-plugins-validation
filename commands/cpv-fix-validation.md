---
name: cpv-fix-validation
description: Fix issues from a PLUGIN validation report file (marketplace reports use /cpv-fix-marketplace-validation)
allowed-tools: Bash, Read, Agent
argument-hint: "<report_file_path>"
model: haiku
user-invocable: true
---

# /cpv-fix-validation

`/cpv-fix-validation` runs in the **main session** (haiku for cheap menu rendering). You pick a report, the main session dispatches the **plugin-fixer** (opus) work agent via the Agent tool to do the validate → fix → re-validate loop, and the result returns here.

Marketplace validation reports go to `/cpv-fix-marketplace-validation` instead — this command is plugin-only.

## You are the menu orchestrator

You — the model running THIS turn — render the menu via `scripts/format_menu.py`, parse the user's pick, dispatch the opus work agent. You do NOT fix anything yourself.

**Critical rules**:

- **NEVER use `AskUserQuestion`.** Print Unicode-bordered tables via `format_menu.py`; ask plain text.
- **NEVER hand-render menu tables.** ALWAYS call `${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py menu <json>` so cell widths use display columns (box-drawing/emoji/Asian-wide chars handled correctly) and disabled rows are dropped + remaining renumbered.
- **NEVER auto-pick a menu option.**

## Step 1 — if the user passed a path argument, skip the menu

If `/cpv-fix-validation <path>` was invoked with a path argument, jump straight to Step 4 dispatch with the provided path. Otherwise continue with Step 2.

## Step 2 — auto-discover recent reports + render the menu

Print this banner:

```text
Session model: <whatever the current session model is>. Menu rendering is currently haiku (this turn only). For cheaper navigation across every menu step, run /model haiku once.
```

Auto-discover recent plugin-relevant reports:

```bash
MAIN_ROOT="$(git worktree list 2>/dev/null | head -n1 | awk '{print $1}')"
[ -z "$MAIN_ROOT" ] && MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
REPORTS=$(find "$MAIN_ROOT/reports" -maxdepth 2 -type d \
  \( -name 'validate_plugin' -o -name 'validate_skill' -o -name 'validate_security' \
     -o -name 'validate_cache' -o -name 'validate_hook' -o -name 'validate_agent' \
     -o -name 'validate_command' -o -name 'validate_mcp' -o -name 'validate_lsp' \
     -o -name 'validate_rules' -o -name 'validate_xref' -o -name 'validate_documentation' \
     -o -name 'validate_encoding' -o -name 'validate_enterprise' -o -name 'validate_scoring' \
     -o -name 'validate_local_scope' -o -name 'validate_project_scope' \
     -o -name 'validate_settings_marketplace' -o -name 'validate_github_plugin' \
     -o -name 'plugin-diagnoser' \) \
  -print 2>/dev/null | xargs -I{} find {} -maxdepth 1 -type f -name '*.md' 2>/dev/null \
  | sort -r | head -n 8)
```

Build the row list (one entry per discovered report + fixed rows for manual entry / cancel), then call `format_menu.py menu`. Mark report rows with `disabled: true` if `find` returned fewer than 8 — the helper drops them and renumbers the rest:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" menu "$(cat <<EOF
{
  "header": "Recent validation report — what to fix?",
  "rows": [
    {"key": "1", "action_id": "report_1", "label": "<relative-path of report 1> (<age>)", "disabled": <true if not present>},
    ...
    {"key": "8", "action_id": "report_8", "label": "<relative-path of report 8> (<age>)", "disabled": <true if not present>},
    {"key": "9", "action_id": "manual",   "label": "Provide a different path (report .md file OR plugin/skill folder to validate fresh)"},
    {"key": "0", "action_id": "cancel",   "label": "Cancel / Exit"}
  ],
  "footer": "Type a number to choose:"
}
EOF
)" 2>/tmp/cpv_fix_validation_action_map.json
```

If no reports are found, fall back to the plain-text prompt:

> **Which plugin should I fix?** Reply with a path (plugin folder OR pre-existing validation report). Reply `0` to cancel.

## Step 3 — route the user's reply

Look up `action_id` from `/tmp/cpv_fix_validation_action_map.json`:

| action_id | Action |
|---|---|
| `cancel` | Reply EXACTLY: `Cancelled — no actions taken.` and stop. |
| `report_N` | Resolve to the report path from the earlier `find` output; dispatch the work agent with that path. |
| `manual` | Ask plain-text: `Path to a report file or plugin/skill folder?`. Wait. Dispatch with that path. |
| any plain-text path | Treat as the work agent's input — dispatch directly. |
| anything else | Re-print the menu and ask once more. |

## Step 4 — dispatch the work agent

```
Use the Agent tool with:
  subagent_type: plugin-fixer
  description: "Plugin fixer dispatched from /cpv-fix-validation"
  prompt: |
    <context>
    source: /cpv-fix-validation main-session menu
    user_choice: <the integer the user picked, OR "manual" / "argument">
    target_path: <absolute path to the report .md OR plugin folder>
    optional_min_severity: <if the orchestrator passed `min_severity=...`, forward it verbatim>
    </context>

    Validate, fix, re-validate the target until clean per your loop algorithm.
    Return a one-line summary plus the report path:

      `Fix loop: <N> issues addressed, <verdict> after re-validate (fix log: <abs-path>)`

    DO NOT render any menu yourself.
```

## Step 5 — render the post-fix summary table + post-fix menu

Parse the work agent's return into the relevant counts. If the fixer returned a re-validate verdict with finding counts, render the summary table first:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" summary "$(cat <<EOF
{
  "title": "Post-fix re-validate",
  "counts": {"critical": <C>, "major": <M>, "minor": <n>, "nit": <t>, "warning": <w>},
  "verdict": "<VALID|INVALID>",
  "report_path": "<fix-log-abs-path>"
}
EOF
)"
```

Then render the post-fix menu (drop options that no longer apply via `disabled: true`):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" menu "$(cat <<EOF
{
  "header": "What now?",
  "rows": [
    {"key": "1", "action_id": "revalidate",    "label": "Re-validate now (run /cpv-validate-plugin on the same target)"},
    {"key": "2", "action_id": "another_report","label": "Fix another report"},
    {"key": "3", "action_id": "open_log",      "label": "Open the fix log in your editor (returns the path)"},
    {"key": "4", "action_id": "post_issue",    "label": "Post a GitHub issue about the remaining findings", "disabled": <true if no remaining findings else false>},
    {"key": "5", "action_id": "done",          "label": "Skip / done"},
    {"key": "0", "action_id": "exit",          "label": "Exit"}
  ],
  "footer": "Type a number to choose:"
}
EOF
)" 2>/tmp/cpv_fix_validation_postfix_map.json
```

| action_id | Action |
|---|---|
| `revalidate` | Suggest the user invoke `/cpv-validate-plugin <target>`. Reply with the suggested command and stop. |
| `another_report` | Re-enter Step 2 (re-print the auto-discovery menu). |
| `open_log` | Reply with the absolute path of the fix log. Re-print the post-fix menu. |
| `post_issue` | Use Bash: `gh issue create --repo Emasoft/claude-plugins-validation --title "<short>" --body-file <fix-log>`. Show URL. Re-print menu. |
| `done` | Reply `Done.` Stop. |
| `exit` | Reply `Exit.` Stop. |

## Output

- Fix log saved to `$MAIN_ROOT/reports/plugin-fixer/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md` at the **main-repo root** (never a linked worktree). Both `reports/` and `reports_dev/` are gitignored.

## Related

- `/cpv-validate-plugin` — Generate a plugin validation report (read-only)
- `/cpv-fix-marketplace-validation` — Fix issues in a MARKETPLACE validation report
- `/cpv-semantic-validation` — Deep semantic analysis

## Architecture (v2.89.0 / v2.89.3)

Per TRDD-bcbceeed (v2.89.0): the menu orchestrator runs in the main session — subagents cannot spawn other subagents per the Claude Code spec, so only the main session can dispatch `plugin-fixer` (opus) via the Agent tool.

Per TRDD-81e7fa34 (v2.89.3): menu/summary rendering is centralised in `scripts/format_menu.py`. The command body never embeds Unicode tables — it hands rows to the helper, which uses display-column widths (box-drawing/emoji/Asian-wide chars correct), drops disabled rows, renumbers the rest, and emits the action_id map on stderr.
