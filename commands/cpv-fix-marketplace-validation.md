---
name: cpv-fix-marketplace-validation
description: Fix issues from a marketplace validation report file (or migrate marketplace layout)
allowed-tools: Bash, Read, Agent
argument-hint: "<report_file_path>"
model: haiku
user-invocable: true
---

# /cpv-fix-marketplace-validation

`/cpv-fix-marketplace-validation` runs in the **main session** (haiku for cheap menu rendering). You pick a report (or an architectural-migration / pipeline-standardization mode), the main session dispatches the **marketplace-fixer** (opus) work agent to do validate → fix → re-validate (or run the migration playbook), and the result returns here.

Plugin validation reports → `/cpv-fix-validation`; this command is marketplace-only.

## You are the menu orchestrator

You — the model running THIS turn — render the menu via `scripts/format_menu.py`, parse the user's pick, dispatch the opus work agent. You do NOT fix anything yourself.

**Critical rules**:

- **NEVER use `AskUserQuestion`.** Print Unicode-bordered tables via `format_menu.py`; ask plain text.
- **NEVER hand-render menu tables.** ALWAYS call `${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py menu <json>`.
- **NEVER auto-pick a menu option.**

## Step 1 — if the user passed a path argument, skip the menu

If `/cpv-fix-marketplace-validation <path>` was invoked with an argument, jump straight to Step 4 dispatch with `mode: auto` and the provided path. Otherwise continue.

## Step 2 — auto-discover recent reports + render the menu

Print this banner:

```text
Session model: <whatever the current session model is>. Menu rendering is currently haiku (this turn only). For cheaper navigation across every menu step, run /model haiku once.
```

Auto-discover recent marketplace-relevant reports:

```bash
MAIN_ROOT="$(git worktree list 2>/dev/null | head -n1 | awk '{print $1}')"
[ -z "$MAIN_ROOT" ] && MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
REPORTS=$(find "$MAIN_ROOT/reports" -maxdepth 2 -type d \
  \( -name 'validate_marketplace' -o -name 'validate_github_marketplace' \
     -o -name 'validate_settings_marketplace' \) \
  -print 2>/dev/null | xargs -I{} find {} -maxdepth 1 -type f -name '*.md' 2>/dev/null \
  | sort -r | head -n 6)
```

Build the row list (up to 6 discovered reports + fixed rows for architecture migration / pipeline standardization / manual / cancel), then call `format_menu.py menu`. Mark missing report rows `disabled: true`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" menu "$(cat <<EOF
{
  "header": "Recent marketplace report — what to fix?",
  "rows": [
    {"key": "1", "action_id": "report_1",       "label": "<relative-path of report 1> (<age>)", "disabled": <true if not present>},
    {"key": "2", "action_id": "report_2",       "label": "<relative-path of report 2> (<age>)", "disabled": <true if not present>},
    {"key": "3", "action_id": "report_3",       "label": "<relative-path of report 3> (<age>)", "disabled": <true if not present>},
    {"key": "4", "action_id": "report_4",       "label": "<relative-path of report 4> (<age>)", "disabled": <true if not present>},
    {"key": "5", "action_id": "report_5",       "label": "<relative-path of report 5> (<age>)", "disabled": <true if not present>},
    {"key": "6", "action_id": "report_6",       "label": "<relative-path of report 6> (<age>)", "disabled": <true if not present>},
    {"key": "7", "action_id": "migrate_layout", "label": "Marketplace architecture migration (Layout A↔B↔C, non-CPV → CPV conversion)"},
    {"key": "8", "action_id": "fix_pipeline",   "label": "Pipeline standardization (add/repair publish.py / cliff.toml / CI / CHANGELOG)"},
    {"key": "9", "action_id": "manual",         "label": "Provide a different path (report .md OR marketplace folder/owner-repo slug)"},
    {"key": "0", "action_id": "cancel",         "label": "Cancel / Exit"}
  ],
  "footer": "Type a number to choose:"
}
EOF
)" 2>/tmp/cpv_fix_marketplace_action_map.json
```

If no reports are found, fall back to the plain-text prompt:

> **What would you like me to do with your marketplace?** Reply with a path (report OR marketplace folder OR owner/repo slug). Reply `0` to cancel.

## Step 3 — route the user's reply

Look up `action_id` from `/tmp/cpv_fix_marketplace_action_map.json`:

| action_id | Action |
|---|---|
| `cancel` | Reply EXACTLY: `Cancelled — no actions taken.` and stop. |
| `report_N` | Resolve to the matching report path; dispatch in `mode: mechanical_or_architectural`. |
| `migrate_layout` | Ask plain-text: `Marketplace folder/repo or owner/repo slug?`. Dispatch in `mode: architectural_migration`. |
| `fix_pipeline` | Ask plain-text: `Marketplace folder/repo path?`. Dispatch in `mode: pipeline_standardization`. |
| `manual` | Ask plain-text: `Path to a report file or marketplace folder/owner-repo slug?`. Dispatch with `mode: auto`. |
| any plain-text path | Treat as the work agent's input — dispatch directly in `mode: auto`. |

## Step 4 — dispatch the work agent

```
Use the Agent tool with:
  subagent_type: marketplace-fixer
  description: "Marketplace fixer dispatched from /cpv-fix-marketplace-validation"
  prompt: |
    <context>
    source: /cpv-fix-marketplace-validation main-session menu
    user_choice: <the integer the user picked, OR "manual" / "argument">
    mode: <mechanical_or_architectural | architectural_migration | pipeline_standardization | auto>
    target_path: <absolute path to the report .md OR marketplace folder OR owner/repo slug>
    </context>

    Validate, fix, re-validate the marketplace until clean per your loop algorithm.
    Return a one-line summary plus the report path:

      `Marketplace fix: <N> issues addressed, <verdict> after re-validate (fix log: <abs-path>)`

    DO NOT render any menu yourself.
```

## Step 5 — render summary + post-fix menu

Parse the work agent's return and render the severity summary first:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" summary "$(cat <<EOF
{
  "title": "Marketplace fix — post-fix re-validate",
  "counts": {"critical": <C>, "major": <M>, "minor": <n>, "nit": <t>, "warning": <w>},
  "verdict": "<VALID|INVALID>",
  "report_path": "<fix-log-abs-path>"
}
EOF
)"
```

Then render the post-fix menu (lead with "Fix all remaining" when applicable; drop empty severity rows):

```bash
# total = C + M + n  (NIT/WARNING are non-blocking, not counted for "Fix all")
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" menu "$(cat <<EOF
{
  "header": "What now?",
  "rows": [
    {"key": "1", "action_id": "fix_all_remaining", "label": "Fix ALL remaining findings (<total> total)", "disabled": <true if total==0 else false>},
    {"key": "2", "action_id": "fix_at_critical",   "label": "Fix only CRITICAL (<C> findings)", "disabled": <true if C==0 or (M==0 and n==0) else false>},
    {"key": "3", "action_id": "fix_at_major",      "label": "Fix at-or-above MAJOR (<C+M> findings)", "disabled": <true if M==0 or n==0 else false>},
    {"key": "4", "action_id": "revalidate",        "label": "Re-validate the marketplace"},
    {"key": "5", "action_id": "another_report",    "label": "Fix another marketplace"},
    {"key": "6", "action_id": "open_log",          "label": "Open the fix log in your editor (returns the path)"},
    {"key": "7", "action_id": "post_issue",        "label": "Post a GitHub issue about the remaining findings", "disabled": <true if total==0 else false>},
    {"key": "8", "action_id": "done",              "label": "Skip / done"},
    {"key": "0", "action_id": "exit",              "label": "Exit"}
  ],
  "footer": "Type a number to choose:"
}
EOF
)" 2>/tmp/cpv_fix_marketplace_postfix_map.json
```

| action_id | Action |
|---|---|
| `fix_all_remaining` | Re-dispatch the marketplace-fixer with `min_severity: minor` (= every remaining finding). |
| `fix_at_critical` / `fix_at_major` | Re-dispatch with the matching `min_severity`. |
| `revalidate` | Suggest `/cpv-validate-github-marketplace <target>`. Reply with the suggested command and stop. |
| `another_report` | Re-enter Step 2. |
| `open_log` | Reply with the absolute log path. Re-print the post-fix menu. |
| `post_issue` | Use Bash: `gh issue create ... --body-file <fix-log>`. Show URL. Re-print menu. |
| `done` / `exit` | Reply `Done.` / `Exit.` Stop. |

## Output

All outputs land at the **main-repo root** (never a linked worktree). Resolve via `git worktree list | head -n1`. Both `reports/` and `reports_dev/` are gitignored.

- Fix log: `$MAIN_ROOT/reports/marketplace-fixer/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md`
- Migration log (when `mode: architectural_migration`): `$MAIN_ROOT/reports/migrate-marketplace-architecture/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md`

## Related

- `/cpv-validate-github-marketplace` — Generate a marketplace validation report (required input)
- `/cpv-fix-validation` — Fix plugin validation reports
- `/cpv-create` — Create a new marketplace scaffold

## Architecture (v2.89.0 / v2.89.3)

Per TRDD-bcbceeed (v2.89.0): the menu orchestrator runs in the main session. Per TRDD-81e7fa34 (v2.89.3): menu/summary rendering goes through `scripts/format_menu.py`; post-fix menu leads with "Fix ALL remaining" and only exposes severity-floor options when they would yield a different result.
