---
name: cpv-fix-marketplace-validation
description: Fix issues from a marketplace validation report file (or migrate marketplace layout)
allowed-tools: Bash, Read, Agent, Skill
argument-hint: "<report_file_path>"
user-invocable: true
---

# /cpv-fix-marketplace-validation

You are the menu orchestrator. Pick a report (or accept a path argument), dispatch the opus `marketplace-fixer` work agent, render the result, loop until the user exits. Plugin reports go to `/cpv-fix-validation` — this command is marketplace-only.

This command body runs in the main session — whatever model the user is on (opus by default). Dynamic menu rendering is offloaded to the `cpv-format-menu` fork-skill, which spawns a fresh haiku subagent for the render itself.

**HARD RULES — do not violate:**

1. **Print every menu in your TEXT OUTPUT, verbatim.** Tool stdout (Bash) and Skill tool results are INVISIBLE to the user — only your prose text reaches the UI. When you invoke `cpv-format-menu` you MUST copy its text result into your next text message.
2. **Never use `AskUserQuestion`.** Ask via plain text only.

If the user invoked `/cpv-fix-marketplace-validation <path>` with a path argument, skip to **Step 4 — Dispatch** with `mode: auto` and that path.

## Step 1 — print this banner in your TEXT output

```text
Tip: run `/model haiku` once for cheaper menu navigation across this session.
```

## Step 2 — auto-discover recent reports + render the menu

Discover the most recent marketplace-relevant reports:

```bash
MAIN_ROOT="$(git worktree list 2>/dev/null | head -n1 | awk '{print $1}')"
[ -z "$MAIN_ROOT" ] && MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
find "$MAIN_ROOT/reports" -maxdepth 2 -type d \
  \( -name 'validate_marketplace' -o -name 'validate_github_marketplace' \
     -o -name 'validate_settings_marketplace' \) \
  -print 2>/dev/null | xargs -I{} find {} -maxdepth 1 -type f -name '*.md' 2>/dev/null \
  | sort -r | head -n 6
```

Build the row JSON (up to 6 discovered reports + 4 fixed rows + cancel) and write the spec to disk, then invoke the `cpv-format-menu` fork-skill:

```bash
cat > /tmp/cpv-fix-marketplace-validation-report-list-spec.json <<EOF
{
  "header": "Recent marketplace report — what to fix?",
  "rows": [
    {"key": "1", "action_id": "report_1",       "label": "<relative path> (<age>)", "disabled": <true|false>},
    {"key": "2", "action_id": "report_2",       "label": "<relative path> (<age>)", "disabled": <true|false>},
    {"key": "3", "action_id": "report_3",       "label": "<relative path> (<age>)", "disabled": <true|false>},
    {"key": "4", "action_id": "report_4",       "label": "<relative path> (<age>)", "disabled": <true|false>},
    {"key": "5", "action_id": "report_5",       "label": "<relative path> (<age>)", "disabled": <true|false>},
    {"key": "6", "action_id": "report_6",       "label": "<relative path> (<age>)", "disabled": <true|false>},
    {"key": "7", "action_id": "migrate_layout", "label": "Marketplace architecture migration (Layout A↔B↔C, non-CPV → CPV)"},
    {"key": "8", "action_id": "fix_pipeline",   "label": "Pipeline standardization (publish.py / cliff.toml / CI / CHANGELOG)"},
    {"key": "9", "action_id": "manual",         "label": "Provide a different path (report .md OR marketplace folder/owner-repo slug)"},
    {"key": "0", "action_id": "cancel",         "label": "Cancel / Exit"}
  ],
  "footer": "Type a number to choose:"
}
EOF
```

Now invoke the Skill tool to render the menu (forks to haiku, returns rendered text):

```
Skill({
  skill: "claude-plugins-validation:cpv-format-menu",
  args: "menu /tmp/cpv-fix-marketplace-validation-report-list-spec.json /tmp/cpv-fix-marketplace-validation-report-list-map.json"
})
```

**COPY THE SKILL TOOL'S TEXT RESULT VERBATIM INTO YOUR TEXT RESPONSE.** Skill tool output (like Bash stdout) is INVISIBLE to the user — without echoing the result into your prose the menu never appears in the UI.

If `find` returned zero reports, skip the menu and ask plain text instead:

> **What would you like me to do with your marketplace?** Reply with a path (report `.md` OR marketplace folder OR owner/repo slug). Reply `0` to cancel.

## Step 3 — route the user's reply

Look up `action_id` from `/tmp/cpv-fix-marketplace-validation-report-list-map.json`:

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
  Return ONE line:

    Marketplace fix: <N> issues addressed, <verdict> after re-validate (fix log: <abs-path>)

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

Then render the post-fix menu — compute `total = C + M + n` (NIT/WARNING are non-blocking), write the spec to disk, and invoke the `cpv-format-menu` fork-skill:

```bash
cat > /tmp/cpv-fix-marketplace-validation-postfix-spec.json <<EOF
{
  "header": "What now?",
  "rows": [
    {"key": "1", "action_id": "fix_all_remaining", "label": "Fix ALL remaining findings (<total> total)", "disabled": <true|false>},
    {"key": "2", "action_id": "fix_at_critical",   "label": "Fix only CRITICAL (<C> findings)", "disabled": <true|false>},
    {"key": "3", "action_id": "fix_at_major",      "label": "Fix at-or-above MAJOR (<C+M> findings)", "disabled": <true|false>},
    {"key": "4", "action_id": "revalidate",        "label": "Re-validate the marketplace"},
    {"key": "5", "action_id": "another_report",    "label": "Fix another marketplace"},
    {"key": "6", "action_id": "open_log",          "label": "Open the fix log (returns the path)"},
    {"key": "7", "action_id": "post_issue",        "label": "Post a GitHub issue about the remaining findings", "disabled": <true|false>},
    {"key": "8", "action_id": "done",              "label": "Skip / done"},
    {"key": "0", "action_id": "exit",              "label": "Exit"}
  ],
  "footer": "Type a number to choose:"
}
EOF
```

Now invoke the Skill tool to render the menu (forks to haiku, returns rendered text):

```
Skill({
  skill: "claude-plugins-validation:cpv-format-menu",
  args: "menu /tmp/cpv-fix-marketplace-validation-postfix-spec.json /tmp/cpv-fix-marketplace-validation-postfix-map.json"
})
```

**COPY THE SKILL TOOL'S TEXT RESULT VERBATIM INTO YOUR TEXT RESPONSE.** Skill tool output (like Bash stdout) is INVISIBLE to the user — without echoing the result into your prose the menu never appears in the UI.

## Step 6 — route the post-fix pick

| action_id | Action |
|---|---|
| `fix_all_remaining` | Re-dispatch the marketplace-fixer with `min_severity: minor` (= every remaining finding). |
| `fix_at_critical` / `fix_at_major` | Re-dispatch with the matching `min_severity`. |
| `revalidate` | Suggest `/cpv-validate-github-marketplace <target>`. Reply with the command. Stop. |
| `another_report` | Loop back to Step 2. |
| `open_log` | Reply with the absolute log path. Re-print the post-fix menu. |
| `post_issue` | `gh issue create --repo Emasoft/claude-plugins-validation --title "<short>" --body-file <fix-log>` → show URL → re-print menu. |
| `done` | Reply `Done.` Stop. |
| `exit` | Reply `Exit.` Stop. |

## Output

All outputs land at the **main-repo root** (never a linked worktree). Resolve via `git worktree list | head -n1`. Both `reports/` and `reports_dev/` are gitignored.

- Fix log: `$MAIN_ROOT/reports/marketplace-fixer/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md`
- Migration log (when `mode: architectural_migration`): `$MAIN_ROOT/reports/migrate-marketplace-architecture/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md`

## Related

- `/cpv-validate-github-marketplace` — Generate a marketplace validation report
- `/cpv-fix-validation` — Fix plugin validation reports
- `/cpv-create` — Create a new marketplace scaffold

## Architecture notes (v2.89.4)

- **Menu rendering is offloaded to the `cpv-format-menu` fork-skill.** The orchestrator writes the JSON spec to `/tmp/cpv-fix-marketplace-validation-<purpose>-spec.json`, invokes the Skill tool, and the fork-skill spawns a fresh `general-purpose` subagent on haiku that runs `scripts/format_menu.py`. The orchestrator MUST copy the Skill tool's text result into its prose output — both Bash stdout AND Skill tool results are invisible to the user.
- **Why a fork-skill instead of `model: haiku` on this command?** Per the Claude Code skills doc, `model:` overrides apply "for the rest of the current turn" while keeping the inherited conversation history. A multi-turn orchestrator command body on opus with a 1M-token context cannot safely degrade mid-turn to haiku — the override silently fails. `context: fork` (on the `cpv-format-menu` skill) creates a fresh subagent with no inherited history, so `model: haiku` actually takes effect for the render step alone. The orchestrator turn itself stays on the session model.
- The orchestrator runs in the main session; only the main session can dispatch the opus `marketplace-fixer` work agent.
