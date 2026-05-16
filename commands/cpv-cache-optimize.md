---
name: cpv-cache-optimize
description: Audit AND fix prompt-cache invalidation patterns (CA-01..CA-06) — interactive cache-optimizer
allowed-tools: Bash, Read, Agent
argument-hint: "<plugin_or_project_path_or_report> [--broader]"
model: haiku
user-invocable: true
---

# /cpv-cache-optimize

`/cpv-cache-optimize` runs in the **main session** (haiku for cheap menu rendering). You pick a target (recent cache-audit report, fresh audit-then-fix, or "broader" mode), the main session dispatches the **cache-optimizer-agent** (opus) via the Agent tool to run the actual audit + fix + re-validate loop, and the result returns here.

Unlike `/cpv-validate-cache` (which only audits), this command runs the full **validate → fix → re-validate loop**.

## You are the menu orchestrator

You — the model running THIS turn — render the menu, parse the user's pick, dispatch the opus work agent. You do NOT audit anything yourself. The opus `cache-optimizer-agent` owns Phase 1 (Audit), Phase 2 (Fix), Phase 3 (Re-validate), and the optional Phase 4 (Broader cache-aware refactor with per-step user-approval prompts).

**Critical rules**:

- **NEVER use `AskUserQuestion`.** Print Unicode-bordered tables; ask plain text.
- **NEVER drop the `0 — Cancel / Exit` row.**
- **NEVER auto-pick a menu option.**
- **Print the table once per turn.**

## Step 1 — if the user passed a path argument, skip the table

If `/cpv-cache-optimize <path>` was invoked with a path argument (with optional `--broader` flag), jump straight to Step 4 dispatch:

- Path looks like a `.md` file under a `validate_cache/` or `cache/` directory → `mode: from_report`
- Path looks like a folder → `mode: audit_then_fix` (or `mode: audit_then_fix_broader` if `--broader` was passed)

Otherwise continue with Step 2.

## Step 2 — auto-discover recent reports and render the menu

Print this banner:

```
Session model: <whatever the current session model is>. Menu rendering is currently haiku (this turn only). For cheaper navigation across every menu step, run /model haiku once.
```

Then run this Bash to find recent cache-audit reports:

```bash
MAIN_ROOT="$(git worktree list 2>/dev/null | head -n1 | awk '{print $1}')"
[ -z "$MAIN_ROOT" ] && MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
find "$MAIN_ROOT/reports/validate_cache" -maxdepth 1 -type f -name '*.md' 2>/dev/null \
  | sort -r | head -n 7
```

If at least one report is found, print this Unicode table (up to 7 report rows + the fixed rows 8/9/0):

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Recent cache-audit report                                                             ┃ When                                        ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ <relative path of newest cache report>                                                │ <human time + age>                          │
│ 2 │ <relative path of next report>                                                        │ ...                                         │
│ … │                                                                                       │                                             │
│ 7 │ <relative path of 7th-newest report>                                                  │ ...                                         │
│ 8 │ Audit + optimize a path (CA-01..CA-06 audit, then fix loop)                           │ Fresh audit then fix                        │
│ 9 │ "Broader" mode (path) — go beyond CA-01..CA-06 to maximise cache hit rate             │ Fresh audit + Phase 4 broader refactor      │
│ 0 │ Cancel / Exit                                                                         │ Terminate without action                    │
└───┴───────────────────────────────────────────────────────────────────────────────────────┴─────────────────────────────────────────────┘
Type a number to choose:
```

If no reports are found, present only rows 8/9/0 (omit the empty report rows).

## Step 3 — route the user's reply

| Reply | Action |
|---|---|
| `0` | Reply EXACTLY: `Cancelled — no actions taken.` and stop. |
| `1`..`7` | The user picked a recent cache report. Look up the matching report path from the `find` output. Dispatch in `mode: from_report` (skips Phase 1 audit — the report already has the findings). |
| `8` | Ask plain-text: `Path to plugin or project root?`. Wait. Dispatch in `mode: audit_then_fix` (Phases 1-3, no broader). |
| `9` | Ask plain-text: `Path to plugin or project root?`. Wait. Dispatch in `mode: audit_then_fix_broader` (Phases 1-4, with Phase 4 user-approval per refactor). |
| any plain-text path | Treat as the work agent's input — dispatch in `mode: audit_then_fix`. |
| anything else | Re-print the table and ask once more. |

## Step 4 — dispatch the work agent

```
Use the Agent tool with:
  subagent_type: cache-optimizer-agent
  description: "Cache optimizer dispatched from /cpv-cache-optimize"
  prompt: |
    <context>
    source: /cpv-cache-optimize main-session menu
    user_choice: <the integer the user picked, OR "manual" / "argument">
    mode: <from_report | audit_then_fix | audit_then_fix_broader>
    target_path: <absolute path to a plugin/project folder OR an existing cache-audit report .md>
    </context>

    Run Phases 1-3 (or 1-4 for broader mode) per your authoritative algorithm.
    Return a one-line summary plus the report path. DO NOT render a
    follow-up menu yourself.
```

## Step 5 — render the post-optimize menu

When the work agent returns, summarize the result in one line (e.g. `Fixed 12 of 14 cache issues. Report: reports/cache/<ts>-<slug>-final.md`), then print:

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ What now?                                                                       ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Re-audit (run validate_cache fresh, no fixes)                                   │
│ 2 │ Optimize another target                                                         │
│ 3 │ Run broader mode on the same target (Phase 4 refactor)                          │
│ 4 │ Open the final report in your editor (returns the path)                         │
│ 5 │ Skip / done                                                                     │
│ 0 │ Exit                                                                            │
└───┴─────────────────────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

| # | Action |
|---|---|
| 1 | Suggest the user invoke `/cpv-validate-cache <target>` to re-audit read-only. Reply with the suggested command and stop. |
| 2 | Re-enter Step 2 (re-print the auto-discovery table). |
| 3 | Re-dispatch the work agent with `mode: audit_then_fix_broader` and the same target. |
| 4 | Reply with the absolute path of the final report. Re-print the post-optimize menu. |
| 5 | Reply `Done.` Stop. |
| 0 | Reply `Exit.` Stop. |

## Phases (run by the work agent)

| # | Phase | What happens |
|---|---|---|
| 1 | **Audit** | Run `validate_cache.py` against the target. Auto-saves the aggregated report to `${MAIN_ROOT}/reports/cache/<TS>-<slug>.md`; the agent reads it. |
| 2 | **Fix** | Apply per-rule fix recipes from `skills/fix-validation/references/cache-fixes.md` in priority order (CA-01..CA-03 MAJOR → CA-04..CA-05 MINOR → CA-06 WARNING). Each batch is its own `fix(cache-CA-NN): ...` commit. |
| 3 | **Re-validate** | Re-run the validator. Iterate until verdict = VALID or a residual issue cannot be safely fixed (in which case the agent stops and reports). |
| 4 | **Broader refactor** *(optional, only in `audit_then_fix_broader` mode)* | Cached-prefix size audit, dynamic-content migration, model-switch audit, `CLAUDE.md` decomposition, append a `## Cache Notes` block. Each material refactor is approved by the user before the edit lands. |

## Output

The cache-optimizer agent returns:

```
[DONE|PARTIAL|FAILED] <summary>. Report: <abs-path-to-final-report>
```

The full per-rule fix list and diff stats live in the report file at `$MAIN_ROOT/reports/cache/<TS>-<slug>-final.md`, where `MAIN_ROOT` is the **main checkout root** (first entry of `git worktree list`) — never the linked worktree's own root. Anchoring to the main checkout is the only way the report survives when the worktree is removed/merged, since `./reports/` is gitignored everywhere.

## Related

- `/cpv-validate-cache` — Audit only (read-only, no fixes). Use when you just want to see what's wrong before authorising changes.
- `/cpv-validate-plugin` — Full plugin validation (structure, security, skills). Run this in addition for full coverage.
- `/cpv-fix-validation` — Generic plugin-fixer for non-cache validation reports.
- See `skills/fix-validation/references/cache-fixes.md` for the per-rule fix recipes the agent applies.

## Reference

The CA-01..CA-06 rule pack is derived from *"Lessons from Building Claude Code: Prompt Caching Is Everything"* by Thariq Shihipar (Anthropic) and from the open-source [ussumant/cache-audit](https://github.com/ussumant/cache-audit) corpus.

## Architecture (v2.89.0)

Per TRDD-bcbceeed: the menu orchestrator runs in the main session (haiku for the invoking turn). Subagents cannot spawn other subagents per the Claude Code spec — only the main session can use the Agent tool to spawn `cache-optimizer-agent` (opus). The post-optimize menu is rendered by this main-session orchestrator (not by the work agent), so it is always visible to the user.
