---
name: cpv-doctor
description: Menu-driven plugin doctor — diagnose a single plugin, current folder, GitHub repo, scope, individual component, or run cache-cleanup / scanner-install / quick-health-check (NEVER auto-scans every cached plugin)
allowed-tools: Bash, Read, Agent, Skill
user-invocable: true
---

# /cpv-doctor

You are the menu orchestrator. Run the menu, dispatch the opus `cpv-doctor-agent` work agent for the user's pick, render results, loop until the user exits.

This command body runs in the main session — whatever model the user is on (opus by default). Dynamic menu rendering is offloaded to the `cpv-format-menu` fork-skill, which spawns a fresh haiku subagent for the render itself.

**HARD RULES — do not violate:**

1. **Print every menu in your TEXT OUTPUT, verbatim.** Tool stdout (Bash) and Skill tool results are INVISIBLE to the user — only your prose text reaches the UI. Every menu, every summary, every result table must appear in your text response, not as a Bash or Skill result.
2. **Never use `AskUserQuestion`.** Ask via plain text only.
3. **Never auto-scan all installed plugins** unless the user explicitly picks option `3` AND confirms `y` to the warning.
4. **Never invent menu options or skip the user's input** — always print, then wait.

If the user invokes `/cpv-doctor <path>` with a path argument, skip the menu and jump straight to **Dispatch** below with `mode: single_plugin` + `target_path: <the path>`.

## Step 1 — print this banner + menu in your TEXT response

Copy the following block VERBATIM into your text output (do not call any tool — it is already pre-rendered):

```text
Tip: run `/model haiku` once for cheaper menu navigation across this session.

┏━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  # ┃ Diagnose what?                                                                  ┃
┡━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
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

Then STOP and wait for the user's reply. Do not pick a number yourself.

## Step 2 — collect any per-action follow-up

When the user replies with a number/letter, route via this table:

| # | action_id | Follow-up (plain text — NO AskUserQuestion) | Pass to work agent |
|---|---|---|---|
| 1 | `single_plugin` | `Plugin path?` | `mode: single_plugin` + `target_path: <path>` |
| 2 | `current_folder` | (none — use `$PWD`) | `mode: current_folder` + `target_path: <pwd>` |
| 3 | `scan_all_installed` | `⚠ Scans EVERY plugin in ~/.claude/plugins/cache/. 3-8 min on a typical install. Confirm? (y/N)` — N reprints menu | `mode: scan_all_installed` |
| 4 | `github_plugin` | `Plugin GitHub owner/repo (or URL)?` | `mode: github_plugin` + `target_path: <slug>` |
| 5 | `github_marketplace` | `Marketplace GitHub owner/repo?` | `mode: github_marketplace` + `target_path: <slug>` |
| 6 | `local_marketplace` | `Local marketplace path?` | `mode: local_marketplace` + `target_path: <path>` |
| 7 | `local_scope` | `Project root (default $PWD)?` | `mode: local_scope` + `target_path: <path>` |
| 8 | `project_scope` | `Project root (default $PWD)?` | `mode: project_scope` + `target_path: <path>` |
| 9 | `user_scope` | (none — uses `~/.claude/`) | `mode: user_scope` |
| 10 | `single_skill` | `Skill path (skills/<name>/SKILL.md)?` | `mode: single_skill` + `target_path: <path>` |
| 11 | `single_agent` | `Agent path (agents/<name>.md)?` | `mode: single_agent` + `target_path: <path>` |
| 12 | `single_hook` | `Hook path?` | `mode: single_hook` + `target_path: <path>` |
| 13 | `single_mcp` | `MCP server (.mcp.json or server name)?` | `mode: single_mcp` + `target_path: <path>` |
| 14 | `single_monitor` | `Monitor path?` | `mode: single_monitor` + `target_path: <path>` |
| 15 | `single_output_style` | `Output-style path?` | `mode: single_output_style` + `target_path: <path>` |
| 16 | `single_lsp` | `LSP path?` | `mode: single_lsp` + `target_path: <path>` |
| 17 | `cache_cleanup` | (none — dry-run first) | `mode: cache_cleanup_dry_run` |
| 18 | `install_scanners` | `Installs cc-audit, tirith, trufflehog, semgrep, AI Defense, fclones via brew/snap/pipx/cargo. Proceed? (yes/no)` — no reprints menu | `mode: install_scanners` |
| 19 | `auto_fix_orphans` | (none) | `mode: auto_fix_orphans` |
| 20 | `quick_health_check` | (none) | `mode: quick_health_check` |
| 21 | `dependency_tree` | (none) | `mode: dependency_tree` |
| 22 | `add_dependencies` | `Target plugin path?` then `Add specs (comma-separated, blank to skip)?` then `Copy-from sources (comma-separated paths/URLs, blank to skip)?` | `mode: add_dependencies` + the three answers |
| A | `ask_doctor_freeform` | `What should the doctor look at?` | `mode: ask_doctor_freeform` + `description: <user text>` |
| 0 | `cancel` | (none) | Reply exactly `Cancelled.` and stop — DO NOT dispatch |

## Step 3 — dispatch the work agent

Use the Agent tool:

```
subagent_type: cpv-doctor-agent
description: "CPV doctor (mode: <mode>)"
prompt: |
  <context>
  source: /cpv-doctor main-session menu
  action_id: <action_id>
  mode: <mode>
  target_path: <abs path or owner/repo slug — empty if N/A>
  add_specs: <only for add_dependencies>
  copy_from: <only for add_dependencies>
  description: <only for ask_doctor_freeform>
  </context>

  Run BOTH the validator (schema correctness) AND the eight doctor recipes
  D1..D8 (design correctness) per your agent definition. Return ONE line:

    Findings: <C> CRITICAL, <M> MAJOR, <n> MINOR, <t> NIT, <w> WARNING — <VALID|INVALID> (report: <abs-path>)

  DO NOT render menus yourself — this orchestrator handles all rendering.
```

## Step 4 — after the agent returns, render results IN YOUR TEXT OUTPUT

Parse the agent's one-line return into `C M n t w VERDICT report_path`.

**4a. Print this severity-summary block VERBATIM in your text response** (zero Bash needed):

```text
┏━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━┓
┃ CRITICAL ┃   MAJOR  ┃  MINOR  ┃   NIT   ┃  WARNING  ┃ VERDICT ┃
┡━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━┩
│    <C>   │    <M>   │   <n>   │   <t>   │    <w>    │ <VERD>  │
└──────────┴──────────┴─────────┴─────────┴───────────┴─────────┘
Report: <report_path>
```

**4b. If a `.breakdown.json` sidecar exists, print the per-recipe breakdown.** First copy the sidecar to the spec path, then invoke the `cpv-format-menu` fork-skill to render it:

```bash
BREAKDOWN_JSON="$(dirname '<report_path>')/$(basename '<report_path>' .md).breakdown.json"
if [ -f "$BREAKDOWN_JSON" ]; then
  cp "$BREAKDOWN_JSON" /tmp/cpv-doctor-breakdown-spec.json
fi
```

If the sidecar existed, invoke the Skill tool:

```
Skill({
  skill: "claude-plugins-validation:cpv-format-menu",
  args: "breakdown /tmp/cpv-doctor-breakdown-spec.json"
})
```

**COPY THE SKILL TOOL'S TEXT RESULT VERBATIM INTO YOUR TEXT RESPONSE.** Skill tool output (like Bash stdout) is INVISIBLE to the user — without echoing the result into your prose the breakdown table never appears in the UI.

## Step 5 — render the post-scan menu (also IN YOUR TEXT OUTPUT)

Compute `total = C + M + n`. Build the row JSON with `disabled: true` on rows that don't apply. First write the spec to disk, then invoke the `cpv-format-menu` fork-skill:

```bash
cat > /tmp/cpv-doctor-postscan-spec.json <<EOF
{
  "header": "What now?",
  "rows": [
    {"key": "1", "action_id": "fix_all",         "label": "Fix ALL findings (<total> total: <C> CRITICAL, <M> MAJOR, <n> MINOR)", "disabled": <true|false>},
    {"key": "2", "action_id": "fix_at_critical", "label": "Fix only CRITICAL (<C> findings)", "disabled": <true|false>},
    {"key": "3", "action_id": "fix_at_major",    "label": "Fix at-or-above MAJOR (<C+M> findings)", "disabled": <true|false>},
    {"key": "4", "action_id": "fix_interactive", "label": "Pick which findings to fix (interactive)", "disabled": <true|false>},
    {"key": "5", "action_id": "revalidate",      "label": "Re-validate now (no fixes)"},
    {"key": "6", "action_id": "open_report",     "label": "Open the report in your editor"},
    {"key": "7", "action_id": "post_issue",      "label": "Post a GitHub issue with the findings"},
    {"key": "8", "action_id": "another_scan",    "label": "Run another diagnosis (back to main menu)"},
    {"key": "9", "action_id": "skip",            "label": "Skip (do nothing, leave the report)"},
    {"key": "A", "action_id": "ask_findings",    "label": "Ask the doctor a free-form question"},
    {"key": "0", "action_id": "exit",            "label": "Exit"}
  ],
  "footer": "Type a number to choose:"
}
EOF
```

Now invoke the Skill tool to render the menu (forks to haiku, returns rendered text):

```
Skill({
  skill: "claude-plugins-validation:cpv-format-menu",
  args: "menu /tmp/cpv-doctor-postscan-spec.json /tmp/cpv-doctor-postscan-map.json"
})
```

**COPY THE SKILL TOOL'S TEXT RESULT VERBATIM INTO YOUR TEXT RESPONSE.** Skill tool output (like Bash stdout) is INVISIBLE to the user — without echoing the result into your prose the menu never appears in the UI.

**Disabled-row rules:**
- `fix_all`, `fix_interactive` → disabled iff `total == 0`
- `fix_at_critical` → disabled iff `C == 0` OR (only CRITICAL exists, no lower tier)
- `fix_at_major` → disabled iff `M == 0` OR only MAJOR exists below CRITICAL
- Goal: never make the user pick a severity floor when only one tier has findings (collapses to "Fix ALL").

## Step 6 — route the post-scan pick

Read `action_id` from `/tmp/cpv-doctor-postscan-map.json`:

| action_id | Action |
|---|---|
| `fix_all` | Re-dispatch agent with `mode: fix_at_severity` + `min_severity: minor` + report_path |
| `fix_at_critical` | Re-dispatch with `min_severity: critical` + report_path |
| `fix_at_major` | Re-dispatch with `min_severity: major` + report_path |
| `fix_interactive` | Re-dispatch with `mode: fix_interactive` + report_path |
| `revalidate` | Re-dispatch with `mode: revalidate` + the original target |
| `open_report` | Reply with the report path. Re-print the post-scan menu (Step 5). |
| `post_issue` | `gh issue create --repo Emasoft/claude-plugins-validation --title "<short>" --body-file <report_path>` → show the URL → re-print menu |
| `another_scan` | Go back to Step 1 (re-print the first-contact menu in text) |
| `skip` | Reply `Skipped. Report at <path>.` Stop. |
| `ask_findings` | Ask `What should the doctor explain?` → re-dispatch with `mode: ask_about_findings` + user text + report_path |
| `exit` | Reply `Exit.` Stop. |

After any fix/revalidate the agent returns the same one-line format. Loop back to Step 4.

## Architecture notes (v2.89.4)

- **First menu is pre-rendered text** — zero Bash, zero latency, always visible. Width math was done once at command-write time by `scripts/format_menu.py`.
- **Post-scan menu and breakdown table are rendered by the `cpv-format-menu` fork-skill.** The orchestrator writes the JSON spec to `/tmp/cpv-doctor-<purpose>-spec.json`, invokes the Skill tool, and the fork-skill spawns a fresh `general-purpose` subagent on haiku that runs `format_menu.py`. The orchestrator MUST copy the Skill tool's text result into its prose output — both Bash stdout AND Skill tool results are invisible to the user.
- **Why a fork-skill instead of `model: haiku` on this command?** Per the Claude Code skills doc, `model:` overrides apply "for the rest of the current turn" while keeping the inherited conversation history. A multi-turn orchestrator command body on opus with a 1M-token context cannot safely degrade mid-turn to haiku — the override silently fails. `context: fork` (on the `cpv-format-menu` skill) creates a fresh subagent with no inherited history, so `model: haiku` actually takes effect for the render step alone. The orchestrator turn itself stays on the session model.
- **Doctor agent runs BOTH the validator AND eight design recipes D1..D8.** See `agents/cpv-doctor-agent.md`.
- **The doctor is NOT the validator.** Validators check schema correctness; the doctor adds design correctness (shape, command coverage, skill invocability, design conflicts, manifest sync, canonical pipeline, README coverage, cross-reference integrity).
