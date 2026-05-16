---
name: cpv-cache-optimize
description: Audit AND fix prompt-cache invalidation patterns (CA-01..CA-06) — interactive cache-optimizer
allowed-tools: Bash, Read, Agent
argument-hint: "<plugin_or_project_path_or_report> [--broader]"
model: haiku
user-invocable: true
---

# /cpv-cache-optimize

`/cpv-cache-optimize` runs in the **main session** (haiku for cheap menu rendering). You pick a target, the main session dispatches the **cache-optimizer-agent** (opus) via the Agent tool to run the audit + fix + re-validate loop, and the result returns here.

Unlike `/cpv-validate-cache` (which only audits), this command runs the full **validate → fix → re-validate loop**.

## You are the menu orchestrator

You — the model running THIS turn — render the menu via `scripts/format_menu.py`, parse the user's pick, dispatch the opus work agent. You do NOT audit anything yourself.

**Critical rules**:

- **NEVER use `AskUserQuestion`.** Print Unicode-bordered tables via `format_menu.py`; ask plain text.
- **NEVER hand-render menu tables.** ALWAYS call `${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py menu <json>`.
- **NEVER auto-pick a menu option.**

## Step 1 — if the user passed a path argument, skip the menu

If `/cpv-cache-optimize <path>` was invoked with an argument (with optional `--broader` flag), jump straight to Step 4 dispatch:

- Path looks like a `.md` file under `validate_cache/` or `cache/` → `mode: from_report`
- Path looks like a folder → `mode: audit_then_fix` (or `mode: audit_then_fix_broader` if `--broader` was passed)

Otherwise continue.

## Step 2 — auto-discover recent reports + render the menu

Print this banner:

```text
Session model: <whatever the current session model is>. Menu rendering is currently haiku (this turn only). For cheaper navigation across every menu step, run /model haiku once.
```

Auto-discover recent cache-audit reports:

```bash
MAIN_ROOT="$(git worktree list 2>/dev/null | head -n1 | awk '{print $1}')"
[ -z "$MAIN_ROOT" ] && MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
REPORTS=$(find "$MAIN_ROOT/reports/validate_cache" -maxdepth 1 -type f -name '*.md' 2>/dev/null \
  | sort -r | head -n 7)
```

Build the row list (up to 7 reports + fixed rows for audit-then-fix / broader / cancel). Mark missing report rows `disabled: true`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" menu "$(cat <<EOF
{
  "header": "Cache audit — what to optimize?",
  "rows": [
    {"key": "1", "action_id": "report_1",         "label": "<relative-path of report 1> (<age>)", "disabled": <true if not present>},
    {"key": "2", "action_id": "report_2",         "label": "<relative-path of report 2> (<age>)", "disabled": <true if not present>},
    {"key": "3", "action_id": "report_3",         "label": "<relative-path of report 3> (<age>)", "disabled": <true if not present>},
    {"key": "4", "action_id": "report_4",         "label": "<relative-path of report 4> (<age>)", "disabled": <true if not present>},
    {"key": "5", "action_id": "report_5",         "label": "<relative-path of report 5> (<age>)", "disabled": <true if not present>},
    {"key": "6", "action_id": "report_6",         "label": "<relative-path of report 6> (<age>)", "disabled": <true if not present>},
    {"key": "7", "action_id": "report_7",         "label": "<relative-path of report 7> (<age>)", "disabled": <true if not present>},
    {"key": "8", "action_id": "audit_then_fix",   "label": "Audit + optimize a path (CA-01..CA-06 audit, then fix loop)"},
    {"key": "9", "action_id": "audit_broader",    "label": "Broader mode (path) — beyond CA-01..CA-06 to maximise cache hit rate"},
    {"key": "0", "action_id": "cancel",           "label": "Cancel / Exit"}
  ],
  "footer": "Type a number to choose:"
}
EOF
)" 2>/tmp/cpv_cache_action_map.json
```

## Step 3 — route the user's reply

Look up `action_id` from `/tmp/cpv_cache_action_map.json`:

| action_id | Action |
|---|---|
| `cancel` | Reply EXACTLY: `Cancelled — no actions taken.` and stop. |
| `report_N` | Resolve to the matching report path; dispatch in `mode: from_report`. |
| `audit_then_fix` | Ask plain-text: `Path to plugin or project root?`. Wait. Dispatch in `mode: audit_then_fix`. |
| `audit_broader` | Ask plain-text: `Path to plugin or project root?`. Wait. Dispatch in `mode: audit_then_fix_broader`. |
| any plain-text path | Treat as the work agent's input — dispatch in `mode: audit_then_fix`. |

## Step 4 — dispatch the work agent

```
Use the Agent tool with:
  subagent_type: cache-optimizer-agent
  description: "Cache optimizer dispatched from /cpv-cache-optimize"
  prompt: |
    <context>
    source: /cpv-cache-optimize main-session menu
    user_choice: <the rendered key the user picked, OR "manual" / "argument">
    mode: <from_report | audit_then_fix | audit_then_fix_broader>
    target_path: <absolute path to a plugin/project folder OR an existing cache-audit report .md>
    </context>

    Run Phases 1-3 (or 1-4 for broader mode) per your authoritative algorithm.
    Return a one-line summary plus the report path:

      `Cache optimize: <N> issues addressed, <verdict> after re-validate (report: <abs-path>)`

    DO NOT render any menu yourself.
```

## Step 5 — render summary + post-optimize menu

Parse the work agent's return and render the severity summary table:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" summary "$(cat <<EOF
{
  "title": "Cache optimize — re-validate",
  "counts": {"critical": <C>, "major": <M>, "minor": <n>, "nit": <t>, "warning": <w>},
  "verdict": "<VALID|INVALID>",
  "report_path": "<abs-path>"
}
EOF
)"
```

Then render the post-optimize menu — lead with "Fix all" when remaining findings exist, drop disabled rows:

```bash
# total = C + M + n  (NIT/WARNING non-blocking)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" menu "$(cat <<EOF
{
  "header": "What now?",
  "rows": [
    {"key": "1", "action_id": "fix_all_remaining", "label": "Fix ALL remaining findings (<total> total)", "disabled": <true if total==0 else false>},
    {"key": "2", "action_id": "broader_refactor",  "label": "Run broader mode on the same target (Phase 4 refactor)"},
    {"key": "3", "action_id": "reaudit",           "label": "Re-audit (run validate_cache fresh, no fixes)"},
    {"key": "4", "action_id": "another_target",    "label": "Optimize another target"},
    {"key": "5", "action_id": "open_report",       "label": "Open the final report in your editor (returns the path)"},
    {"key": "6", "action_id": "done",              "label": "Skip / done"},
    {"key": "0", "action_id": "exit",              "label": "Exit"}
  ],
  "footer": "Type a number to choose:"
}
EOF
)" 2>/tmp/cpv_cache_postopt_map.json
```

| action_id | Action |
|---|---|
| `fix_all_remaining` | Re-dispatch with `mode: from_report` + the report path. |
| `broader_refactor` | Re-dispatch with `mode: audit_then_fix_broader` and the same target. |
| `reaudit` | Suggest `/cpv-validate-cache <target>`. Reply with the suggested command and stop. |
| `another_target` | Re-enter Step 2 (re-print the auto-discovery menu). |
| `open_report` | Reply with the absolute report path. Re-print the post-optimize menu. |
| `done` / `exit` | Reply `Done.` / `Exit.` Stop. |

## Phases (run by the work agent)

| # | Phase | What happens |
|---|---|---|
| 1 | **Audit** | Run `validate_cache.py` against the target. Auto-saves the aggregated report. |
| 2 | **Fix** | Apply per-rule fix recipes from `skills/fix-validation/references/cache-fixes.md` in priority order (CA-01..CA-03 MAJOR → CA-04..CA-05 MINOR → CA-06 WARNING). Each batch is its own `fix(cache-CA-NN): ...` commit. |
| 3 | **Re-validate** | Re-run the validator. Iterate until verdict = VALID or a residual issue cannot be safely fixed. |
| 4 | **Broader refactor** *(optional, only in `audit_then_fix_broader` mode)* | Cached-prefix size audit, dynamic-content migration, model-switch audit, CLAUDE.md decomposition, append `## Cache Notes` block. Each material refactor is approved by the user before the edit lands. |

## Output

Report lands at `$MAIN_ROOT/reports/cache/<TS>-<slug>-final.md` at the **main-repo root** (never a linked worktree). `reports/` and `reports_dev/` are gitignored.

## Related

- `/cpv-validate-cache` — Audit only (read-only).
- `/cpv-validate-plugin` — Full plugin validation.
- `/cpv-fix-validation` — Generic plugin-fixer for non-cache validation reports.

## Reference

CA-01..CA-06 rule pack is derived from *"Lessons from Building Claude Code: Prompt Caching Is Everything"* by Thariq Shihipar (Anthropic) and the open-source [ussumant/cache-audit](https://github.com/ussumant/cache-audit) corpus.

## Architecture (v2.89.0 / v2.89.3)

Per TRDD-bcbceeed (v2.89.0): the menu orchestrator runs in the main session. Per TRDD-81e7fa34 (v2.89.3): menu/summary rendering is centralised in `scripts/format_menu.py`; the post-optimize menu leads with "Fix ALL remaining" and only exposes severity-floor options when meaningful.
