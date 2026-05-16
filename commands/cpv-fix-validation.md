---
name: cpv-fix-validation
description: Fix issues from a PLUGIN validation report file (marketplace reports use /cpv-fix-marketplace-validation)
allowed-tools: Bash, Read, Agent
argument-hint: "<report_file_path>"
model: haiku
user-invocable: true
---

# /cpv-fix-validation

`/cpv-fix-validation` runs in the **main session** (haiku for cheap menu rendering). You pick a report, the main session dispatches the **plugin-fixer** (opus) work agent via the Agent tool to do the validate → fix → re-validate loop, and the result returns to the main session for any follow-up.

Marketplace validation reports (from `validate_marketplace.py` or `validate_marketplace_pipeline.py`) must be routed to `/cpv-fix-marketplace-validation` instead — this command is plugin-only.

## You are the menu orchestrator

You — the model running THIS turn — render the menu, parse the user's pick, dispatch the opus work agent. You do NOT fix anything yourself. You do NOT read source files for analysis. The opus `plugin-fixer` agent owns the entire fix workflow.

**Critical rules**:

- **NEVER use `AskUserQuestion`.** Print Unicode-bordered tables; ask plain text.
- **NEVER drop the `0 — Cancel / Exit` row.**
- **NEVER auto-pick a menu option.** Always print and wait.
- **Print the table once per turn.**

## Step 1 — if the user passed a path argument, skip the table

If `/cpv-fix-validation <path>` was invoked with a path argument, jump straight to Step 3 dispatch with the provided path. Otherwise continue with Step 2.

## Step 2 — auto-discover recent reports and render the menu

Print this banner immediately followed by the Bash auto-discovery, then the table:

```
Session model: <whatever the current session model is>. Menu rendering is currently haiku (this turn only). For cheaper navigation across every menu step, run /model haiku once.
```

Run this Bash to discover recent plugin-relevant reports:

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
     -o -name 'validate_settings_marketplace' -o -name 'validate_github_plugin' \) \
  -print 2>/dev/null | xargs -I{} find {} -maxdepth 1 -type f -name '*.md' 2>/dev/null \
  | sort -r | head -n 8
```

If at least one report is found, print this Unicode table with one row per report (newest first):

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Recent validation report                                                              ┃ When                                        ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ <relative path of newest report>                                                      │ <human time + age>                          │
│ 2 │ <relative path of next report>                                                        │ ...                                         │
│ … │                                                                                       │                                             │
│ 8 │ <relative path of 8th-newest report>                                                  │ ...                                         │
│ 9 │ Provide a different path (report .md file OR plugin/skill folder to validate fresh)   │ Manual entry                                │
│ 0 │ Cancel / Exit                                                                         │ Terminate without action                    │
└───┴───────────────────────────────────────────────────────────────────────────────────────┴─────────────────────────────────────────────┘
Type a number to choose:
```

(Truncate to as many rows as `find` returned — always leave `9` (manual entry) and `0` (cancel) as the last two rows.)

If no reports are found, fall back to the plain-text prompt:

> **Which plugin should I fix?** I can work from either a path or a pre-existing report:
>
> - **A plugin folder** (e.g., `~/dev/my-plugin/`, `./plugin-foo/`, or even a parent/dev folder — the work agent will resolve it intelligently). The fix loop will validate, fix, re-validate, and loop until clean.
> - **A pre-existing validation report** (e.g., `reports/validate_plugin/20260421_183012+0200-my-plugin.md`). The fix loop starts from those findings.
>
> Reply with a path. Reply `0` to cancel.

## Step 3 — route the user's reply

| Reply | Action |
|---|---|
| `0` | Reply EXACTLY: `Cancelled — no actions taken.` and stop. No bash, no edits, no dispatch. |
| `1`..`8` | The user picked a recent report. Look up the matching report path from the `find` output and dispatch the work agent with that path. |
| `9` | Ask plain-text: `Path to a report file or plugin/skill folder?`. Wait for the answer. Then dispatch with that path. |
| any plain-text path | Treat as the work agent's input — dispatch directly. |
| anything else | Re-print the table and ask once more. |

## Step 4 — dispatch the work agent

When you have the path, dispatch via the Agent tool:

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
    Return a one-line summary plus the report path. DO NOT render a
    follow-up menu yourself.
```

If a parent orchestrator (e.g. the post-scan menu in `/cpv-doctor`) passed a `min_severity=` line in its prompt, forward it verbatim inside `<context>` so the work agent filters findings before fixing.

## Step 5 — render the post-fix menu

When the work agent returns, summarize its result in one line (e.g. `Fixed 17 of 22 issues. Report: reports/plugin-fixer/<ts>-<slug>.md`), then print this menu and wait:

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ What now?                                                                       ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Re-validate now (run /cpv-validate-plugin on the same target)                   │
│ 2 │ Fix another report                                                              │
│ 3 │ Open the fix log in your editor (returns the path)                              │
│ 4 │ Post a GitHub issue about the remaining findings                                │
│ 5 │ Skip / done                                                                     │
│ 0 │ Exit                                                                            │
└───┴─────────────────────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

| # | Action |
|---|---|
| 1 | Suggest the user invoke `/cpv-validate-plugin <target>` to re-validate. Reply with the suggested command and stop. (Composing two slash commands in one turn is out of scope.) |
| 2 | Re-enter Step 2 (re-print the auto-discovery table). |
| 3 | Reply with the absolute path of the fix log. Re-print the post-fix menu so the user can pick again. |
| 4 | Use Bash to run `gh issue create --repo Emasoft/claude-plugins-validation --title "<short>" --body-file <fix-log>`. Show the issue URL. Re-print the post-fix menu. |
| 5 | Reply `Done.` Stop. |
| 0 | Reply `Exit.` Stop. |

## Output

- Fix log saved to `$MAIN_ROOT/reports/plugin-fixer/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md` at the **main-repo root** (never a linked worktree). Resolve the root via `git worktree list | head -n1`. Both `reports/` and `reports_dev/` are gitignored.

## Related Commands

- `/cpv-validate-plugin` — Generate a plugin validation report
- `/cpv-validate-skill` — Generate a skill validation report
- `/cpv-fix-marketplace-validation` — Fix issues in a MARKETPLACE validation report (and run architectural migrations)
- `/cpv-semantic-validation` — Deep semantic analysis

## Architecture (v2.89.0)

Per TRDD-bcbceeed: the menu orchestrator runs in the main session (haiku for the invoking turn). Subagents cannot spawn other subagents per the Claude Code spec — only the main session can use the Agent tool to spawn `plugin-fixer` (opus). The post-fix menu is rendered by this main-session orchestrator (not by the work agent), so it is always visible to the user.
