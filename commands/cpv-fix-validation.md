---
name: cpv-fix-validation
description: Fix issues from a PLUGIN validation report file (marketplace reports use /cpv-fix-marketplace-validation)
allowed-tools: Bash, Read, Agent, Skill
argument-hint: "<report_file_path>"
user-invocable: true
---

# /cpv-fix-validation

You are the menu orchestrator. Pick a report (or accept a path argument), dispatch the opus `plugin-fixer` work agent, render the result, loop until the user exits.

This command body runs in the main session — whatever model the user is on (opus by default). Dynamic menu rendering is offloaded to the `cpv-format-menu` fork-skill, which spawns a fresh haiku subagent for the render itself.

**HARD RULES — do not violate:**

1. **Print every menu in your TEXT OUTPUT, verbatim.** Tool stdout (Bash) and Skill tool results are INVISIBLE to the user — only your prose text reaches the UI. When you invoke `cpv-format-menu` you MUST copy its text result into your next text message.
2. **Never use `AskUserQuestion`.** Ask via plain text only.

If the user invoked `/cpv-fix-validation <path>` with a path argument, skip to **Step 4 — Dispatch** with that path.

## Step 1 — print this banner in your TEXT output

```text
Tip: run `/model haiku` once for cheaper menu navigation across this session.
```

## Step 2 — auto-discover recent reports + render the menu

Discover the most recent plugin-relevant reports:

```bash
MAIN_ROOT="$(git worktree list 2>/dev/null | head -n1 | awk '{print $1}')"
[ -z "$MAIN_ROOT" ] && MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
find "$MAIN_ROOT/reports" -maxdepth 2 -type d \
  \( -name 'validate_plugin' -o -name 'validate_skill' -o -name 'validate_security' \
     -o -name 'validate_cache' -o -name 'validate_hook' -o -name 'validate_agent' \
     -o -name 'validate_command' -o -name 'validate_mcp' -o -name 'validate_lsp' \
     -o -name 'validate_rules' -o -name 'validate_xref' -o -name 'validate_documentation' \
     -o -name 'validate_encoding' -o -name 'validate_enterprise' -o -name 'validate_scoring' \
     -o -name 'validate_local_scope' -o -name 'validate_project_scope' \
     -o -name 'validate_settings_marketplace' -o -name 'validate_github_plugin' \
     -o -name 'plugin-diagnoser' \) \
  -print 2>/dev/null | xargs -I{} find {} -maxdepth 1 -type f -name '*.md' 2>/dev/null \
  | sort -r | head -n 8
```

Build the row JSON (one entry per discovered report + `manual` + `cancel`) and write the spec to disk, then invoke the `cpv-format-menu` fork-skill:

```bash
cat > /tmp/cpv-fix-validation-report-list-spec.json <<EOF
{
  "header": "Recent validation report — what to fix?",
  "rows": [
    {"key": "1", "action_id": "report_1", "label": "<relative path> (<age>)", "disabled": <true|false>},
    ...
    {"key": "8", "action_id": "report_8", "label": "<relative path> (<age>)", "disabled": <true|false>},
    {"key": "9", "action_id": "manual",   "label": "Provide a different path (report .md OR plugin/skill folder to validate fresh)"},
    {"key": "0", "action_id": "cancel",   "label": "Cancel / Exit"}
  ],
  "footer": "Type a number to choose:"
}
EOF
```

Now invoke the Skill tool to render the menu (forks to haiku, returns rendered text):

```
Skill({
  skill: "claude-plugins-validation:cpv-format-menu",
  args: "menu /tmp/cpv-fix-validation-report-list-spec.json /tmp/cpv-fix-validation-report-list-map.json"
})
```

**COPY THE SKILL TOOL'S TEXT RESULT VERBATIM INTO YOUR TEXT RESPONSE.** Skill tool output (like Bash stdout) is INVISIBLE to the user — without echoing the result into your prose the menu never appears in the UI.

If `find` returned zero reports, skip the menu and ask plain text instead:

> **Which plugin should I fix?** Reply with a path (plugin folder OR pre-existing validation report `.md`). Reply `0` to cancel.

## Step 3 — route the user's reply

Look up `action_id` from `/tmp/cpv-fix-validation-report-list-map.json`:

| action_id | Action |
|---|---|
| `cancel` | Reply EXACTLY: `Cancelled — no actions taken.` and stop. |
| `report_N` | Resolve to the report path from the earlier `find` output; dispatch the work agent with that path. |
| `manual` | Ask plain-text: `Path to a report file or plugin/skill folder?`. Wait. Dispatch with that path. |
| any plain-text path | Treat as the work agent's input — dispatch directly. |
| anything else | Re-print the menu and ask once more. |

## Step 4 — dispatch the work agent

```
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
  Return ONE line:

    Fix loop: <N> issues addressed, <verdict> after re-validate (fix log: <abs-path>)

  DO NOT render menus yourself.
```

## Step 5 — after the agent returns, render results IN YOUR TEXT OUTPUT

If the agent returned re-validate finding counts, print this summary block VERBATIM in your text response:

```text
┏━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━┓
┃ CRITICAL ┃   MAJOR  ┃  MINOR  ┃   NIT   ┃  WARNING  ┃ VERDICT ┃
┡━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━┩
│    <C>   │    <M>   │   <n>   │   <t>   │    <w>    │ <VERD>  │
└──────────┴──────────┴─────────┴─────────┴───────────┴─────────┘
Fix log: <abs-path>
```

Then render the post-fix menu — write the spec to disk and invoke the `cpv-format-menu` fork-skill:

```bash
cat > /tmp/cpv-fix-validation-postfix-spec.json <<EOF
{
  "header": "What now?",
  "rows": [
    {"key": "1", "action_id": "revalidate",     "label": "Re-validate now (run /cpv-validate-plugin on the same target)"},
    {"key": "2", "action_id": "another_report", "label": "Fix another report"},
    {"key": "3", "action_id": "open_log",       "label": "Open the fix log (returns the path)"},
    {"key": "4", "action_id": "post_issue",     "label": "Post a GitHub issue about the remaining findings", "disabled": <true|false>},
    {"key": "5", "action_id": "done",           "label": "Skip / done"},
    {"key": "0", "action_id": "exit",           "label": "Exit"}
  ],
  "footer": "Type a number to choose:"
}
EOF
```

Now invoke the Skill tool to render the menu (forks to haiku, returns rendered text):

```
Skill({
  skill: "claude-plugins-validation:cpv-format-menu",
  args: "menu /tmp/cpv-fix-validation-postfix-spec.json /tmp/cpv-fix-validation-postfix-map.json"
})
```

**COPY THE SKILL TOOL'S TEXT RESULT VERBATIM INTO YOUR TEXT RESPONSE.** Skill tool output (like Bash stdout) is INVISIBLE to the user — without echoing the result into your prose the menu never appears in the UI.

## Step 6 — route the post-fix pick

| action_id | Action |
|---|---|
| `revalidate` | Suggest `/cpv-validate-plugin <target>`. Reply with the command. Stop. |
| `another_report` | Loop back to Step 2. |
| `open_log` | Reply with the absolute fix-log path. Re-print the post-fix menu. |
| `post_issue` | `gh issue create --repo Emasoft/claude-plugins-validation --title "<short>" --body-file <fix-log>` → show URL → re-print menu. |
| `done` | Reply `Done.` Stop. |
| `exit` | Reply `Exit.` Stop. |

## Output

Fix log saved to `$MAIN_ROOT/reports/plugin-fixer/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md` at the **main-repo root** (never a linked worktree). Both `reports/` and `reports_dev/` are gitignored.

## Related

- `/cpv-validate-plugin` — Generate a plugin validation report (read-only)
- `/cpv-fix-marketplace-validation` — Fix issues in a MARKETPLACE validation report
- `/cpv-semantic-validation` — Deep semantic analysis

## Architecture notes (v2.89.4)

- **Menu rendering is offloaded to the `cpv-format-menu` fork-skill.** The orchestrator writes the JSON spec to `/tmp/cpv-fix-validation-<purpose>-spec.json`, invokes the Skill tool, and the fork-skill spawns a fresh `general-purpose` subagent on haiku that runs `scripts/format_menu.py`. The orchestrator MUST copy the Skill tool's text result into its prose output — both Bash stdout AND Skill tool results are invisible to the user.
- **Why a fork-skill instead of `model: haiku` on this command?** Per the Claude Code skills doc, `model:` overrides apply "for the rest of the current turn" while keeping the inherited conversation history. A multi-turn orchestrator command body on opus with a 1M-token context cannot safely degrade mid-turn to haiku — the override silently fails. `context: fork` (on the `cpv-format-menu` skill) creates a fresh subagent with no inherited history, so `model: haiku` actually takes effect for the render step alone. The orchestrator turn itself stays on the session model.
- The orchestrator runs in the main session; only the main session can dispatch the opus `plugin-fixer` work agent (subagents cannot spawn subagents per the Claude Code spec).
