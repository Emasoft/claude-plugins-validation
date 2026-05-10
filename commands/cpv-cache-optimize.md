---
name: cpv-cache-optimize
description: Audit AND fix prompt-cache invalidation patterns (CA-01..CA-06) — interactive cache-optimizer agent
allowed-tools: Read, Bash, Glob, Grep, Write, Edit, AskUserQuestion
argument-hint: "<plugin_or_project_path_or_report> [--broader]"
agent: cache-optimizer-menu
user-invocable: true
---

# /cpv-cache-optimize Command

Dispatches the **cache-optimizer-menu** agent (haiku — TRDD-82e836dc), which
either runs immediately (when a path argument is provided) or renders the
First Contact menu (auto-discovered recent cache-audit reports + audit-then-fix
+ broader rows) and parses the user's integer reply. On a leaf pick the menu
agent dispatches the **cache-optimizer-agent** work agent (opus) to run the
actual audit + fix + re-validate loop. With `--broader`, the work agent also
performs Phase 4 cache-aware refactoring of the plugin's
skills/agents/commands/CLAUDE.md/rules.

Unlike `/cpv-validate-cache` (which only audits), this command runs the full
**validate → fix → re-validate loop**.

## Usage

```
/cpv-cache-optimize <plugin_or_project_path>            # audit + fix CA-01..CA-06
/cpv-cache-optimize <existing-cache-audit-report.md>    # fix only what the report found
/cpv-cache-optimize <path> --broader                    # also do cache-aware refactoring
```

## Arguments

| Argument | Required | Description |
|---|---|---|
| `plugin_or_project_path_or_report` | Yes | Either a plugin/project directory to audit + fix, OR a previously generated `validate_cache.py` report `.md` file to fix from |
| `--broader` | No | Authorise Phase 4 — broader cache-aware refactoring beyond the strict CA rules. The agent will propose each refactor via `AskUserQuestion` before applying. |

## Phases (run by the agent)

| # | Phase | What happens |
|---|---|---|
| 1 | **Audit** | Run `validate_cache.py` against the target. Auto-saves the aggregated report to `${CLAUDE_PROJECT_DIR}/reports/cache/<TS>-<slug>.md`; the agent reads it. |
| 2 | **Fix** | Apply per-rule fix recipes from `skills/fix-validation/references/cache-fixes.md` in priority order (CA-01..CA-03 MAJOR → CA-04..CA-05 MINOR → CA-06 WARNING). Each batch is its own `fix(cache-CA-NN): ...` commit. |
| 3 | **Re-validate** | Re-run the validator. Iterate until verdict = VALID or a residual issue cannot be safely fixed (in which case the agent stops and reports). |
| 4 | **Broader refactor** *(optional, with `--broader`)* | Cached-prefix size audit, dynamic-content migration, model-switch audit, `CLAUDE.md` decomposition, append a `## Cache Notes` block. Each material refactor is approved via `AskUserQuestion` before the edit lands. |

## Examples

```
# Audit + fix a published plugin
/cpv-cache-optimize ~/Code/my-plugin/

# Fix from an already-generated report (e.g. from a previous /cpv-validate-cache run)
/cpv-cache-optimize ~/.claude/plugins/cache/my/.../my-plugin/reports/cache/20260501_120000+0200-my-plugin.md

# Aggressively optimize everything for cache hit rate (asks before each refactor)
/cpv-cache-optimize ~/Code/my-plugin/ --broader

# Audit + fix a project root (CLAUDE.md, .claude/ configs, etc.) — works for ANY project that uses Claude Code, not just plugins
/cpv-cache-optimize ~/Code/my-typescript-app/
```

## Output

The cache-optimizer agent returns ONLY:

```
[DONE|PARTIAL|FAILED] <summary>. Report: <abs-path-to-final-report>
```

The full per-rule fix list and diff stats live in the report file at `$MAIN_ROOT/reports/cache/<TS>-<slug>-final.md`, where `MAIN_ROOT` is the **main checkout root** (first entry of `git worktree list`) — never the linked worktree's own root. Anchoring to the main checkout is the only way the report survives when the worktree is removed/merged, since `./reports/` is gitignored everywhere. Path-only return so the calling agent's context never gets flooded.

## Related

- `/cpv-validate-cache` — Audit only (read-only, no fixes). Use when you just want to see what's wrong before authorising changes.
- `/cpv-validate-plugin` — Full plugin validation (structure, security, skills). Run this in addition for full coverage.
- `/cpv-fix-validation` — Generic plugin-fixer agent for non-cache validation reports.
- See `skills/fix-validation/references/cache-fixes.md` for the per-rule fix recipes the agent applies.

## Reference

The CA-01..CA-06 rule pack is derived from *"Lessons from Building Claude Code: Prompt Caching Is Everything"* by Thariq Shihipar (Anthropic) and from the open-source [ussumant/cache-audit](https://github.com/ussumant/cache-audit) corpus.
