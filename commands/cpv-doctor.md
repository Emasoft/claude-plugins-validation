---
name: cpv-doctor
description: Menu-driven plugin doctor — diagnose a single plugin, current folder, GitHub repo, scope, individual component, or run cache-cleanup / scanner-install / quick-health-check (NEVER auto-scans every cached plugin)
allowed-tools: Bash, Read, Agent
model: haiku
user-invocable: true
---

# /cpv-doctor

`/cpv-doctor` opens a menu in the **main session** (haiku for cheap menu rendering). You pick a row, the main session dispatches the **cpv-doctor-agent** (opus) work agent via the Agent tool to do the actual diagnostic, the work returns to the main session, and the menu cycle continues until you exit.

The doctor NEVER scans every cached plugin by default — the wholesale "scan all installed plugins" path is gated behind an explicit confirmation, because on a typical install (15-30 plugins) it takes 3-8 minutes.

The doctor is **NOT** the validator. Validators (`/cpv-validate-plugin`, `/cpv-validate-skill`, etc.) check schema correctness — does the JSON conform, are the required fields present, are paths well-formed. The doctor checks **design correctness** — is this actually a plugin (or a half-formed skill?), do all commands have reachable functionality, are skills correctly invocable, are there redundant/incomplete commands, manifest/version drift, canonical pipeline gaps, README/CONTRIBUTING coverage, dangling cross-references. See `agents/cpv-doctor-agent.md` recipes D1..D8.

## You are the menu orchestrator

You — the model running THIS turn — render the menu via `scripts/format_menu.py`, parse the user's pick, dispatch the opus work agent, render the post-scan summary table + post-scan menu when the work returns. You do NOT scan anything yourself. You do NOT analyse reports. Every diagnostic recipe lives in `agents/cpv-doctor-agent.md`; you only orchestrate.

**Critical rules**:

- **NEVER use `AskUserQuestion`.** Print Unicode-bordered tables via `format_menu.py`; ask plain text.
- **NEVER hand-render menu tables.** ALWAYS call `${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py menu <json>` so cell widths use display columns (box-drawing/emoji/Asian-wide chars handled correctly) and disabled rows are dropped + remaining renumbered.
- **NEVER auto-pick a menu option.** Always print the menu and wait for input.
- **NEVER scan all installed plugins unless the user explicitly chose option `3` AND confirmed `y` to the warning.**

## Step 1 — print the first-contact menu

Print this banner immediately, then call the menu renderer:

```text
Session model: <whatever the current session model is>. Menu rendering is currently haiku (this turn only). For cheaper navigation across every menu step, run /model haiku once.
```

Then call:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" menu "$(cat <<'EOF'
{
  "header": "Diagnose what?",
  "rows": [
    {"key": "1",  "action_id": "single_plugin",        "label": "A specific plugin (give me a path)"},
    {"key": "2",  "action_id": "current_folder",       "label": "The current folder ($PWD — if it contains a plugin project)"},
    {"key": "3",  "action_id": "scan_all_installed",   "label": "All installed plugins (⚠ takes minutes — usually you want one of the above)"},
    {"key": "4",  "action_id": "github_plugin",        "label": "A plugin on GitHub (give me owner/repo or a URL)"},
    {"key": "5",  "action_id": "github_marketplace",   "label": "A marketplace on GitHub (give me owner/repo or a URL)"},
    {"key": "6",  "action_id": "local_marketplace",    "label": "A local marketplace (give me a path)"},
    {"key": "7",  "action_id": "local_scope",          "label": "Current project's LOCAL-scope extensions (.claude/settings.local.json + agents)"},
    {"key": "8",  "action_id": "project_scope",        "label": "Current project's PROJECT-scope extensions (.claude/settings.json + agents)"},
    {"key": "9",  "action_id": "user_scope",           "label": "Current user's USER-scope extensions (~/.claude/* — global agents/skills/MCP)"},
    {"key": "10", "action_id": "single_skill",         "label": "A specific skill (give me skills/<name>/SKILL.md or a folder)"},
    {"key": "11", "action_id": "single_agent",         "label": "A specific agent (give me agents/<name>.md)"},
    {"key": "12", "action_id": "single_hook",          "label": "A specific hook (give me hooks/hooks.json or a hook path)"},
    {"key": "13", "action_id": "single_mcp",           "label": "A specific MCP server (give me .mcp.json or a server name in the project)"},
    {"key": "14", "action_id": "single_monitor",       "label": "A specific monitor (give me monitors/<name>.json)"},
    {"key": "15", "action_id": "single_output_style",  "label": "A specific output-style (give me output-styles/<name>.md)"},
    {"key": "16", "action_id": "single_lsp",           "label": "A specific LSP server (give me .lsp.json or an LSP entry)"},
    {"key": "17", "action_id": "cache_cleanup",        "label": "Cache cleanup — prune older plugin versions (dry-run first)"},
    {"key": "18", "action_id": "install_scanners",     "label": "Install external scanners (cc-audit, tirith, trufflehog, semgrep, fclones, …)"},
    {"key": "19", "action_id": "auto_fix_orphans",     "label": "Auto-fix orphaned entries in settings.json / settings.local.json"},
    {"key": "20", "action_id": "quick_health_check",   "label": "Quick health check — CLI auth + settings integrity (no per-plugin validation)"},
    {"key": "21", "action_id": "dependency_tree",      "label": "Dependency tree + runtime errors (which plugins depend on which; prune orphans)"},
    {"key": "22", "action_id": "add_dependencies",     "label": "Add a dependency to a plugin (explicit URL/path OR copy from another plugin)"},
    {"key": "A",  "action_id": "ask_doctor_freeform", "label": "Tell the doctor it's something else (free-form description)"},
    {"key": "0",  "action_id": "cancel",              "label": "Cancel / Exit"}
  ],
  "footer": "Type a number (or A for free-form) to choose:"
}
EOF
)" 2>/tmp/cpv_doctor_action_map.json
```

Print the helper's stdout verbatim to the user. Keep the action_id map (in `/tmp/cpv_doctor_action_map.json`) for routing the next reply.

If the user passed a path as an argument to `/cpv-doctor`, skip the menu and dispatch directly with `action_id: single_plugin` + the provided path.

## Step 2 — collect any per-action follow-up

When the user replies with a key, read the action_id from `/tmp/cpv_doctor_action_map.json`, then ask the per-action follow-up question (if any):

| action_id | Follow-up (plain text, NO AskUserQuestion) | Pass to work agent |
|---|---|---|
| `single_plugin` | `Plugin path?` | `mode: single_plugin` + `target_path: <path>` |
| `current_folder` | (no question — uses `$PWD`) | `mode: current_folder` + `target_path: <pwd>` |
| `scan_all_installed` | `⚠ This will scan EVERY plugin in ~/.claude/plugins/cache/. On a typical install (15-30 plugins) this takes 3-8 minutes. Confirm? (y/N)` — on `N` reprint the menu, on `y` dispatch | `mode: scan_all_installed` |
| `github_plugin` | `Plugin GitHub owner/repo (or full URL)?` | `mode: github_plugin` + `target_path: <slug-or-url>` |
| `github_marketplace` | `Marketplace GitHub owner/repo?` | `mode: github_marketplace` + `target_path: <slug-or-url>` |
| `local_marketplace` | `Local marketplace path?` | `mode: local_marketplace` + `target_path: <path>` |
| `local_scope` / `project_scope` | `Project root (default $PWD)?` | `mode: <local_scope or project_scope>` + `target_path: <path>` |
| `user_scope` | (no question — uses `~/.claude/`) | `mode: user_scope` |
| `single_skill` / `single_agent` / `single_hook` / `single_mcp` / `single_monitor` / `single_output_style` / `single_lsp` | per-component path prompt | `mode: <action_id>` + `target_path: <path>` |
| `cache_cleanup` | (no question yet — work agent runs `--prune-dry-run` first, then asks before real prune) | `mode: cache_cleanup_dry_run` |
| `install_scanners` | `This will install cc-audit, tirith, trufflehog, semgrep, Cisco AI Defense skill-scanner, AND fclones via brew/snap/pipx/cargo (silent, idempotent, per-platform). Proceed? (yes/no)` — on `no` reprint the menu, on `yes` dispatch | `mode: install_scanners` |
| `auto_fix_orphans` / `quick_health_check` / `dependency_tree` | (no question — direct dispatch) | `mode: <action_id>` |
| `add_dependencies` | `Target plugin path?` then `Add specs (comma-separated, blank to skip)?` then `Copy-from sources (comma-separated paths/URLs, blank to skip)?` | `mode: add_dependencies` + the three answers |
| `ask_doctor_freeform` | `What would you like the doctor to look at? Describe in your own words.` | `mode: ask_doctor_freeform` + `description: <user's text>` |
| `cancel` | (no question) | Reply EXACTLY: `Cancelled.` and stop. NO dispatch. |

## Step 3 — dispatch the work agent

When you have the mode + target_path (and any extra arguments), dispatch via the Agent tool:

```
Use the Agent tool with:
  subagent_type: cpv-doctor-agent
  description: "CPV doctor dispatched from /cpv-doctor menu (mode: <mode>)"
  prompt: |
    <context>
    source: /cpv-doctor main-session menu
    user_choice: <the rendered key the user picked>
    action_id: <the resolved action_id from the action_map>
    mode: <one of the modes from the routing table above>
    target_path: <absolute path or owner/repo slug — empty if not applicable>
    add_specs: <only for mode=add_dependencies, comma-separated specs, optional>
    copy_from: <only for mode=add_dependencies, comma-separated source paths/URLs, optional>
    description: <only for mode=ask_doctor_freeform — the user's free-form text>
    </context>

    Per the routing table in your agent definition, run BOTH the validator (schema-correctness pass)
    AND the eight doctor-depth recipes D1..D8 (design-correctness pass). Append all findings to a
    single report and return:

      `Findings: <C> CRITICAL, <M> MAJOR, <n> MINOR, <t> NIT, <w> WARNING — <VALID|INVALID> (report: <abs-path>)`

    DO NOT render any menu yourself — the /cpv-doctor main-session orchestrator renders both the
    summary table and the post-scan menu.
```

## Step 4 — render the summary AND per-recipe breakdown after the work agent returns

Parse the work agent's one-line return (`Findings: C CRITICAL, M MAJOR, n MINOR, t NIT, w WARNING — VERDICT (report: PATH)`) into counts + verdict + report path.

**4a. Render the severity summary** (one-line counts + verdict + report path):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" summary "$(cat <<EOF
{
  "title": "Findings summary",
  "counts": {"critical": <C>, "major": <M>, "minor": <n>, "nit": <t>, "warning": <w>},
  "verdict": "<VALID|INVALID>",
  "report_path": "<abs-path>"
}
EOF
)"
```

**4b. Render the per-recipe breakdown** (where do the findings come from?). The doctor agent writes a `<report-basename>.breakdown.json` file alongside the markdown report containing per-recipe counts (schema validation + D1..D8). Pipe that JSON straight into `format_menu.py breakdown`:

```bash
BREAKDOWN_JSON="$(dirname '<report-path>')/$(basename '<report-path>' .md).breakdown.json"
if [ -f "$BREAKDOWN_JSON" ]; then
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" breakdown "$(cat "$BREAKDOWN_JSON")"
fi
```

Both helper outputs MUST appear BEFORE the post-scan menu so the user sees what was found (and where) before picking a follow-up action.

## Step 5 — render the post-scan menu

Build the row list with **"Fix ALL findings"** as the prominent first action when findings exist. Only include severity-restricted fix options when they would actually be DIFFERENT from "Fix ALL" (i.e. when more than one severity tier has findings) — never make the user pick a severity if there's only one tier with findings. Mark every fix row `disabled: true` when there are no findings to which it applies; the renderer drops every disabled row AND renumbers the rest 1..N (keeping `A` and `0` literal):

```bash
# Counts shorthand: total = C + M + n  (warnings/nits don't block, so don't
# include them in the "Fix all" count). The severity rows MUST collapse to a
# single "Fix all" row when only one severity has findings — never make the
# user pick a floor they don't have a choice about.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" menu "$(cat <<EOF
{
  "header": "What now?",
  "rows": [
    {"key": "1", "action_id": "fix_all",         "label": "Fix ALL findings (<total> total: <C> CRITICAL, <M> MAJOR, <n> MINOR)", "disabled": <true if total==0 else false>},
    {"key": "2", "action_id": "fix_at_critical", "label": "Fix only CRITICAL (<C> findings)", "disabled": <true if C==0 or (M==0 and n==0) else false>},
    {"key": "3", "action_id": "fix_at_major",    "label": "Fix at-or-above MAJOR (<C+M> findings)", "disabled": <true if M==0 or n==0 else false>},
    {"key": "4", "action_id": "fix_interactive", "label": "Pick which findings to fix (interactive)", "disabled": <true if total==0 else false>},
    {"key": "5", "action_id": "revalidate",      "label": "Re-validate now (no fixes)"},
    {"key": "6", "action_id": "open_report",     "label": "Open the report in your editor (returns the path)"},
    {"key": "7", "action_id": "post_issue",      "label": "Post a GitHub issue with the findings"},
    {"key": "8", "action_id": "another_scan",    "label": "Run another diagnosis (back to the first-contact menu)"},
    {"key": "9", "action_id": "skip",            "label": "Skip (do nothing, leave the report)"},
    {"key": "A", "action_id": "ask_findings",    "label": "Ask the doctor a free-form question about these findings"},
    {"key": "0", "action_id": "exit",            "label": "Exit"}
  ],
  "footer": "Type a number to choose:"
}
EOF
)" 2>/tmp/cpv_doctor_postscan_map.json
```

**Worked example** (after a scan returns C=0 M=0 n=2 t=0 w=1):

- `fix_all` row stays visible: `Fix ALL findings (2 total: 0 CRITICAL, 0 MAJOR, 2 MINOR)`
- `fix_at_critical` row disabled (C=0)
- `fix_at_major` row disabled (M=0 AND no further granularity to expose since only MINOR has findings)
- `fix_interactive` row visible
- Remaining rows (5..9, A, 0) visible

After dropping disabled + renumbering, the user sees:

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ What now?                                                            ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Fix ALL findings (2 total: 0 CRITICAL, 0 MAJOR, 2 MINOR)             │
│ 2 │ Pick which findings to fix (interactive)                             │
│ 3 │ Re-validate now (no fixes)                                           │
│ 4 │ Open the report in your editor (returns the path)                    │
│ 5 │ Post a GitHub issue with the findings                                │
│ 6 │ Run another diagnosis (back to the first-contact menu)               │
│ 7 │ Skip (do nothing, leave the report)                                  │
│ A │ Ask the doctor a free-form question about these findings             │
│ 0 │ Exit                                                                 │
└───┴──────────────────────────────────────────────────────────────────────┘
```

— no severity-floor friction when there's only one tier of findings.

## Step 6 — route the post-scan pick

Look up `action_id` from `/tmp/cpv_doctor_postscan_map.json`:

| action_id | Action |
|---|---|
| `fix_all` | Re-dispatch `cpv-doctor-agent` with `mode: fix_at_severity` + `min_severity: minor` (= every finding at-or-above MINOR, which is all of them since NIT/WARNING are non-blocking) + the report path. |
| `fix_at_critical` | Re-dispatch with `mode: fix_at_severity` + `min_severity: critical` + the report path. Only offered when CRITICAL and at least one lower-severity finding both exist. |
| `fix_at_major` | Re-dispatch with `min_severity: major` + the report path. Only offered when MAJOR and MINOR both exist. |
| `fix_interactive` | Re-dispatch with `mode: fix_interactive` + the report path. The work agent walks user finding-by-finding. |
| `revalidate` | Re-dispatch with `mode: revalidate` + the report's target. |
| `open_report` | Reply with the absolute path of the report. No dispatch. Re-print the post-scan menu (Step 5). |
| `post_issue` | Use Bash to run `gh issue create --repo Emasoft/claude-plugins-validation --title "<short>" --body-file <report>`. Show the issue URL. Re-print the post-scan menu. |
| `another_scan` | Re-print the Step-1 first-contact menu. |
| `skip` | Reply `Skipped. Report is at <path>.` Stop. |
| `ask_findings` | Ask plain-text: `What would you like to ask the doctor about these findings?`. Re-dispatch with `mode: ask_about_findings` + the user's text + the report path. |
| `exit` | Reply `Exit.` Stop. |

After the work agent returns from any fix/revalidate action, loop back to Step 4 (render summary) then Step 5 (render post-scan menu) so the user can keep iterating.

## Why menu-driven

The historical default `/cpv-doctor` invocation ran the per-plugin validator on EVERY cached plugin under `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`. On installs with 20+ plugins this dominated runtime (3-8 minutes), AND in 90 % of cases the user only wanted to diagnose ONE thing.

The menu makes the wholesale scan opt-in (option `3`) while exposing every existing CPV diagnostic surface as a one-keystroke choice.

## Architecture (v2.89.0 / v2.89.3)

Per TRDD-bcbceeed (v2.89.0): the menu orchestrator runs in the main session — subagents cannot spawn other subagents per the Claude Code spec, so only the main session can dispatch `cpv-doctor-agent` (opus) via the Agent tool.

Per TRDD-81e7fa34 (v2.89.3): menu/summary rendering is centralised in `scripts/format_menu.py`. The command body never embeds Unicode tables — it hands rows to the helper, which uses display-column widths (box-drawing/emoji/Asian-wide chars correct), drops disabled rows, renumbers the rest, and emits the action_id map on stderr so the orchestrator routes user input correctly even after renumbering. The doctor itself runs the validator AND eight design-correctness recipes (D1..D8); see `agents/cpv-doctor-agent.md`.
