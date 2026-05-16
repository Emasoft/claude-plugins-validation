---
name: cpv-fix-marketplace-validation
description: Fix issues from a marketplace validation report file (or migrate marketplace layout)
allowed-tools: Bash, Read, Agent
argument-hint: "<report_file_path>"
model: haiku
user-invocable: true
---

# /cpv-fix-marketplace-validation

`/cpv-fix-marketplace-validation` runs in the **main session** (haiku for cheap menu rendering). You pick a report (or an architectural-migration / pipeline-standardization mode), the main session dispatches the **marketplace-fixer** (opus) work agent via the Agent tool to do the validate → fix → re-validate loop (or run the migration playbook), and the result returns here.

Plugin validation reports must be routed to `/cpv-fix-validation` instead — this command is marketplace-only.

## You are the menu orchestrator

You — the model running THIS turn — render the menu, parse the user's pick, dispatch the opus work agent. You do NOT fix anything yourself. The opus `marketplace-fixer` owns the entire fix workflow (mechanical + architectural + pipeline standardization).

**Critical rules**:

- **NEVER use `AskUserQuestion`.** Print Unicode-bordered tables; ask plain text.
- **NEVER drop the `0 — Cancel / Exit` row.**
- **NEVER auto-pick a menu option.**
- **Print the table once per turn.**

## Step 1 — if the user passed a path argument, skip the table

If `/cpv-fix-marketplace-validation <path>` was invoked with a path argument, jump straight to Step 4 dispatch with `mode: auto` and the provided path. Otherwise continue with Step 2.

## Step 2 — auto-discover recent reports and render the menu

Print this banner:

```
Session model: <whatever the current session model is>. Menu rendering is currently haiku (this turn only). For cheaper navigation across every menu step, run /model haiku once.
```

Then run this Bash to find recent marketplace-relevant reports:

```bash
MAIN_ROOT="$(git worktree list 2>/dev/null | head -n1 | awk '{print $1}')"
[ -z "$MAIN_ROOT" ] && MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
find "$MAIN_ROOT/reports" -maxdepth 2 -type d \
  \( -name 'validate_marketplace' -o -name 'validate_github_marketplace' \
     -o -name 'validate_settings_marketplace' \) \
  -print 2>/dev/null | xargs -I{} find {} -maxdepth 1 -type f -name '*.md' 2>/dev/null \
  | sort -r | head -n 6
```

If at least one report is found, print this Unicode table (up to 6 report rows + the fixed rows 7/8/9/0):

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Recent marketplace report                                                             ┃ When                                        ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ <relative path of newest report>                                                      │ <human time + age>                          │
│ 2 │ <relative path of next report>                                                        │ ...                                         │
│ … │                                                                                       │                                             │
│ 6 │ <relative path of 6th-newest report>                                                  │ ...                                         │
│ 7 │ Marketplace architecture migration (Layout A↔B↔C, non-CPV → CPV conversion)           │ Interactive — uses migration playbook       │
│ 8 │ Pipeline standardization (add/repair publish.py / cliff.toml / CI / CHANGELOG)        │ Uses canonical-pipeline skill               │
│ 9 │ Provide a different path (report .md OR marketplace folder/owner-repo slug)           │ Manual entry                                │
│ 0 │ Cancel / Exit                                                                         │ Terminate without action                    │
└───┴───────────────────────────────────────────────────────────────────────────────────────┴─────────────────────────────────────────────┘
Type a number to choose:
```

(If fewer than 6 recent reports exist, omit the empty report rows — always keep rows 7/8/9/0.)

If no reports are found, fall back to the plain-text prompt:

> **What would you like me to do with your marketplace?** Give me either a path or a report:
>
> - **Marketplace folder/repo** — the work agent will validate, fix, re-validate, and loop until clean (zero CRITICAL/MAJOR/MINOR/NIT + zero publish-blocking WARNINGs).
> - **Existing validation report** (`reports/validate_marketplace/<ts>-<slug>.md`) — the work agent picks up the findings and enters the loop from there.
> - **Marketplace architecture migration** — point at a non-CPV marketplace (community monorepo, mixed authorship, git-subdir, hybrid layout) and the work agent walks through Layout A ↔ B ↔ C conversion via `migrate-marketplace-architecture`.
> - **Pipeline standardization** — add or repair `scripts/publish.py`, `cliff.toml`, `.github/workflows/validate.yml`, `update-submodules.yml`, `CHANGELOG.md`, and tag discipline.
>
> Reply with a path. Reply `0` to cancel.

## Step 3 — route the user's reply

| Reply | Action |
|---|---|
| `0` | Reply EXACTLY: `Cancelled — no actions taken.` and stop. |
| `1`..`6` | The user picked a recent report. Look up the matching report path from the `find` output and dispatch the work agent with `mode: mechanical_or_architectural` (work agent screens findings for `category: architecture`). |
| `7` | Ask plain-text: `Marketplace folder/repo or owner/repo slug?`. Wait for the answer. Dispatch in `mode: architectural_migration`. |
| `8` | Ask plain-text: `Marketplace folder/repo path?`. Wait. Dispatch in `mode: pipeline_standardization`. |
| `9` | Ask plain-text: `Path to a report file or marketplace folder/owner-repo slug?`. Dispatch with that path in `mode: auto`. |
| any plain-text path | Treat as the work agent's input — dispatch directly in `mode: auto`. |
| anything else | Re-print the table and ask once more. |

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
    If the report carries `category: architecture` findings, hand off to
    migrate-marketplace-architecture (you own that handoff, not the menu).
    Return a one-line summary plus the report path. DO NOT render a
    follow-up menu yourself.
```

## Step 5 — render the post-fix menu

When the work agent returns, summarize the result in one line (e.g. `Fixed 23 of 28 issues. Report: reports/marketplace-fixer/<ts>-<slug>.md`), then print:

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ What now?                                                                       ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Re-validate the marketplace                                                     │
│ 2 │ Fix another marketplace                                                         │
│ 3 │ Open the fix log in your editor (returns the path)                              │
│ 4 │ Post a GitHub issue about the remaining findings                                │
│ 5 │ Skip / done                                                                     │
│ 0 │ Exit                                                                            │
└───┴─────────────────────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

| # | Action |
|---|---|
| 1 | Suggest the user invoke `/cpv-validate-github-marketplace <target>`. Reply with the suggested command and stop. |
| 2 | Re-enter Step 2 (re-print the auto-discovery table). |
| 3 | Reply with the absolute path of the fix log. Re-print the post-fix menu. |
| 4 | Use Bash to run `gh issue create --repo Emasoft/claude-plugins-validation --title "<short>" --body-file <fix-log>`. Show URL. Re-print menu. |
| 5 | Reply `Done.` Stop. |
| 0 | Reply `Exit.` Stop. |

## Output

All outputs land at the **main-repo root** (never a linked worktree). Resolve via `git worktree list | head -n1`. Both `reports/` and `reports_dev/` are gitignored.

- Fix log: `$MAIN_ROOT/reports/marketplace-fixer/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md`
- For architectural migrations: migration log at `$MAIN_ROOT/reports/migrate-marketplace-architecture/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md` recording every user decision and every command run

## Related Commands

- `/cpv-validate-github-marketplace` — Generate a marketplace validation report (required input for this command)
- `/cpv-fix-validation` — Fix issues in a plugin validation report (plugin-fixer, not marketplace-fixer)
- `/cpv-create` — Create a new marketplace scaffold from scratch

## Architecture (v2.89.0)

Per TRDD-bcbceeed: the menu orchestrator runs in the main session (haiku for the invoking turn). Subagents cannot spawn other subagents per the Claude Code spec — only the main session can use the Agent tool to spawn `marketplace-fixer` (opus). The post-fix menu is rendered by this main-session orchestrator (not by the work agent), so it is always visible to the user.
