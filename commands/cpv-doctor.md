---
name: cpv-doctor
description: Menu-driven plugin doctor — diagnose a single plugin, current folder, GitHub repo, scope, individual component, or run cache-cleanup / scanner-install / quick-health-check (NEVER auto-scans every cached plugin)
allowed-tools: Bash, Read, Agent
model: haiku
user-invocable: true
---

# /cpv-doctor

`/cpv-doctor` opens a menu in the **main session** (haiku for cheap menu rendering). You pick a row, the main session dispatches the **cpv-doctor-agent** (opus) work agent via the Agent tool to do the actual diagnostic, the work returns to the main session, and the menu cycle continues until you exit.

The doctor NEVER scans every cached plugin by default — the wholesale "scan all installed plugins" path is option `3`, gated behind an explicit confirmation, because on a typical install (15-30 plugins) it takes 3-8 minutes.

## You are the menu orchestrator

You — the model running THIS turn — render the menu, parse the user's pick, dispatch the opus work agent, then render the next menu when the work returns. You do NOT scan anything yourself. You do NOT analyse reports. Every diagnostic recipe lives in `agents/cpv-doctor-agent.md`; you only orchestrate.

**Critical rules**:

- **NEVER use `AskUserQuestion`.** Print Unicode-bordered tables; ask plain text. The menu is multi-row and multi-column — AskUserQuestion would mangle it.
- **NEVER drop the `0 — Cancel / Exit` row.** The user must always have a one-key escape.
- **NEVER auto-pick a menu option.** Always print the menu and wait for input.
- **NEVER scan all installed plugins unless the user explicitly chose `3` AND confirmed `y` to the warning.**
- **Print the table once per turn** — don't re-print after every clarifying question.

## Step 1 — print the first-contact menu

On invocation, print this banner immediately followed by the table verbatim, then wait for the user's reply:

```
Session model: <whatever the current session model is>. Menu rendering is currently haiku (this turn only). For cheaper navigation across every menu step, run /model haiku once.

┏━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  # ┃ Diagnose what?                                                                  ┃
┡━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│  1 │ A specific plugin (give me a path)                                              │
│  2 │ The current folder ($PWD — if it contains a plugin project)                     │
│  3 │ All installed plugins (⚠ takes minutes — usually you want one of the above)     │
│  4 │ A plugin on GitHub (give me owner/repo or a URL)                                │
│  5 │ A marketplace on GitHub (give me owner/repo or a URL)                           │
│  6 │ A local marketplace (give me a path)                                            │
│  7 │ Current project's LOCAL-scope extensions (.claude/settings.local.json + agents) │
│  8 │ Current project's PROJECT-scope extensions (.claude/settings.json + agents)     │
│  9 │ Current user's USER-scope extensions (~/.claude/* — global agents/skills/MCP)   │
│ 10 │ A specific skill (give me skills/<name>/SKILL.md or a folder)                   │
│ 11 │ A specific agent (give me agents/<name>.md)                                     │
│ 12 │ A specific hook (give me hooks/hooks.json or a hook path)                       │
│ 13 │ A specific MCP server (give me .mcp.json or a server name in the project)       │
│ 14 │ A specific monitor (give me monitors/<name>.json)                               │
│ 15 │ A specific output-style (give me output-styles/<name>.md)                       │
│ 16 │ A specific LSP server (give me .lsp.json or an LSP entry)                       │
│ 17 │ Cache cleanup — prune older plugin versions (dry-run first)                     │
│ 18 │ Install external scanners (cc-audit, tirith, trufflehog, semgrep, fclones, …)   │
│ 19 │ Auto-fix orphaned entries in settings.json / settings.local.json                │
│ 20 │ Quick health check — CLI auth + settings integrity (no per-plugin validation)   │
│ 21 │ Dependency tree + runtime errors (which plugins depend on which; prune orphans) │
│ 22 │ Add a dependency to a plugin (explicit URL/path OR copy from another plugin)    │
│  A │ Tell the doctor it's something else (free-form description)                     │
│  0 │ Cancel / Exit                                                                   │
└────┴─────────────────────────────────────────────────────────────────────────────────┘
Type a number (or A for free-form) to choose:
```

If the user passed a path as an argument to `/cpv-doctor`, skip the table and dispatch directly with `mode: single_plugin` + the provided path.

## Step 2 — collect any per-row follow-up

| # | Follow-up question (plain text, NO AskUserQuestion) | Mode passed to work agent |
|---|---|---|
| 1 | `Plugin path?` | `mode: single_plugin` + `target_path: <path>` |
| 2 | (no question — uses `$PWD`) | `mode: current_folder` + `target_path: <pwd>` |
| 3 | `⚠ This will scan EVERY plugin in ~/.claude/plugins/cache/. On a typical install (15-30 plugins) this takes 3-8 minutes. Confirm? (y/N)` — on `N` reprint the menu, on `y` dispatch | `mode: scan_all_installed` + `target_path: ""` |
| 4 | `Plugin GitHub owner/repo (or full URL)?` | `mode: github_plugin` + `target_path: <slug-or-url>` |
| 5 | `Marketplace GitHub owner/repo?` | `mode: github_marketplace` + `target_path: <slug-or-url>` |
| 6 | `Local marketplace path?` | `mode: local_marketplace` + `target_path: <path>` |
| 7 | `Project root (default $PWD)?` | `mode: local_scope` + `target_path: <path>` |
| 8 | `Project root (default $PWD)?` | `mode: project_scope` + `target_path: <path>` |
| 9 | (no question — uses `~/.claude/`) | `mode: user_scope` + `target_path: "$HOME/.claude"` |
| 10 | `Skill path (folder containing SKILL.md, or the SKILL.md itself)?` | `mode: single_skill` + `target_path: <path>` |
| 11 | `Agent .md path?` | `mode: single_agent` + `target_path: <path>` |
| 12 | `Hook path (hooks/hooks.json, or a single hook entry's command file)?` | `mode: single_hook` + `target_path: <path>` |
| 13 | `MCP server name OR .mcp.json path?` | `mode: single_mcp` + `target_path: <path-or-name>` |
| 14 | `Monitor JSON path (monitors/<name>.json or monitors/monitors.json)?` | `mode: single_monitor` + `target_path: <path>` |
| 15 | `Output-style .md path?` | `mode: single_output_style` + `target_path: <path>` |
| 16 | `LSP entry name OR .lsp.json path?` | `mode: single_lsp` + `target_path: <path>` |
| 17 | (no question yet — the work agent runs `--prune-dry-run`, shows the list, asks the user to confirm before the real prune) | `mode: cache_cleanup_dry_run` + `target_path: ""` |
| 18 | `This will install cc-audit, tirith, trufflehog, semgrep, Cisco AI Defense skill-scanner, AND fclones via brew/snap/pipx/cargo (silent, idempotent, per-platform). Proceed? (yes/no)` — on `no` reprint the menu, on `yes` dispatch | `mode: install_scanners` + `target_path: ""` |
| 19 | (no question — direct dispatch) | `mode: auto_fix_orphans` + `target_path: ""` |
| 20 | (no question — direct dispatch) | `mode: quick_health_check` + `target_path: ""` |
| 21 | (no question — direct dispatch) | `mode: dependency_tree` + `target_path: ""` |
| 22 | `Target plugin path?` then `Add specs (comma-separated, blank to skip)?` then `Copy-from sources (comma-separated paths/URLs, blank to skip)?` | `mode: add_dependencies` + `target_path: <plugin>` + extra `add_specs` + `copy_from` lines |
| A | `What would you like the doctor to look at? Describe in your own words.` | `mode: ask_doctor_freeform` + `description: <user's text>` |
| 0 | (no question) | Reply EXACTLY: `Cancelled.` and stop. NO dispatch. |

## Step 3 — dispatch the work agent

When you have the mode + target_path (and any extra arguments), dispatch via the Agent tool:

```
Use the Agent tool with:
  subagent_type: cpv-doctor-agent
  description: "CPV doctor dispatched from /cpv-doctor menu (mode: <mode>)"
  prompt: |
    <context>
    source: /cpv-doctor main-session menu
    user_choice: <the integer or letter the user picked>
    mode: <one of the modes from the routing table above>
    target_path: <absolute path or owner/repo slug — empty if not applicable>
    add_specs: <only for mode=add_dependencies, comma-separated specs, optional>
    copy_from: <only for mode=add_dependencies, comma-separated source paths/URLs, optional>
    description: <only for mode=ask_doctor_freeform — the user's free-form text>
    </context>

    Per the routing table in your agent definition, run the matching diagnostic.
    Return only a one-line summary plus the report path. DO NOT render a
    follow-up menu yourself — the /cpv-doctor main-session orchestrator
    renders the post-scan menu from the work agent's structured findings.
```

## Step 4 — render the post-scan menu

When the work agent returns, summarize its result in one line for the user (e.g. `Findings: 2 CRITICAL, 5 MAJOR, 12 MINOR (report: reports/validate_plugin/...)`), then print this menu and wait for the next reply:

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ What now?                                                                       ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Fix ALL findings at-or-above CRITICAL                                           │
│ 2 │ Fix ALL findings at-or-above MAJOR                                              │
│ 3 │ Fix ALL findings at-or-above MINOR                                              │
│ 4 │ Pick which findings to fix (interactive)                                        │
│ 5 │ Re-validate now (no fixes)                                                      │
│ 6 │ Open the report in your editor (returns the path)                               │
│ 7 │ Post a GitHub issue with the findings                                           │
│ 8 │ Run another diagnosis (back to the first-contact menu)                          │
│ 9 │ Skip (do nothing, leave the report)                                             │
│ A │ Ask the doctor a free-form question about these findings                        │
│ 0 │ Exit                                                                            │
└───┴─────────────────────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

Disable (grey-out by replacing the row text with `— (no findings)`) rows 1-4 when the work returned zero findings. Disable rows 1-2 separately if CRITICAL = 0 / MAJOR = 0, etc.

## Step 5 — route the post-scan pick

| # | Action |
|---|---|
| 1-3 | Re-dispatch `cpv-doctor-agent` with `mode: fix_at_severity` + `min_severity: critical` (row 1) / `major` (row 2) / `minor` (row 3) + the report path from Step 4. |
| 4 | Re-dispatch with `mode: fix_interactive` + the report path. The work agent then walks the user finding-by-finding. |
| 5 | Re-dispatch with `mode: revalidate` + the report's target. |
| 6 | Reply with the absolute path of the report. No dispatch. Then re-print the post-scan menu so the user can pick something else. |
| 7 | Use Bash to run `gh issue create --repo Emasoft/claude-plugins-validation --title "<short>" --body-file <report>`. Show the issue URL. Re-print the post-scan menu. |
| 8 | Re-print the Step-1 first-contact menu. |
| 9 | Reply `Skipped. Report is at <path>.` Stop. |
| A | Ask plain-text: `What would you like to ask the doctor about these findings?`. Re-dispatch with `mode: ask_about_findings` + the user's text + the report path. |
| 0 | Reply `Exit.` Stop. |

After the work agent returns from any of these (except 0 / 9), loop back to Step 4 (render the post-scan menu) so the user can keep iterating.

## Power-user CLI examples (the same flags the work agent uses internally)

```bash
# Choice 1: dispatch plugin-diagnoser on a path
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
    plugin /path/to/plugin --strict

# Choice 17: cache cleanup — DRY-RUN first, then real
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --prune-dry-run
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --prune-old-versions

# Choice 18: install external scanners (one-shot bootstrap, bypasses launcher)
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --install-scanners

# Choice 19: auto-fix orphaned settings entries
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
    doctor --fix
```

## Why menu-driven

The historical default `/cpv-doctor` invocation ran the per-plugin validator on EVERY cached plugin under `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`. On installs with 20+ plugins this dominated runtime (3-8 minutes), AND in 90 % of cases the user only wanted to diagnose ONE thing — a specific plugin, a single skill, a marketplace, the current folder, etc.

The menu makes the wholesale scan opt-in (option `3`) while exposing every existing CPV diagnostic surface as a one-keystroke choice. The legacy CLI flags (`--verbose`, `--fix`, `--install-scanners`, `--prune-old-versions`, `--prune-dry-run`, `--prune-keep`) are deduplicated into menu choices `3`, `19`, `18`, `17` (plus the `prune-keep N` follow-up question) so power users get the same operations without remembering flag names.

## Architecture (v2.89.0)

Per TRDD-bcbceeed: the menu orchestrator runs in the main session (haiku for the invoking turn). Subagents cannot spawn other subagents per the Claude Code spec — only the main session can use the Agent tool to spawn `cpv-doctor-agent` (opus). The post-scan menu is rendered by this main-session orchestrator (not by the work agent), so it is always visible to the user. Closes issue #26.
