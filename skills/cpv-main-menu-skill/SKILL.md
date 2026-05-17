---
name: cpv-main-menu-skill
description: Routes the /cpv-main-menu numbered-table menu. Loaded by cpv-main-menu command. Use when navigating CPV's many commands via a single entry point.
when_to_use: When the cpv-main-menu command needs the per-category sub-menu definitions and per-leaf execution recipes. Never invoke directly.
user-invocable: false
allowed-tools: Read, Bash(uv:*), Bash(python:*), Bash(python3:*), Bash(git:*), Bash(mkdir:*), Bash(date:*), Bash(basename:*), Bash(awk:*), Bash(printf:*), Bash(echo:*), Bash(cat:*), Bash(npm:*), Bash(brew:*), Bash(snap:*), Bash(cargo:*), Bash(pipx:*), Bash(gh:*), Glob, Grep, Skill, Edit, Write
---

# CPV Main-Menu Routing Skill

## Overview

Backing skill for `/cpv-main-menu`. Holds the numbered-table menu definitions
and per-leaf execution recipes. Every menu is a Unicode box-drawing table
with numbered rows. The user picks by typing the number. Every table
includes `0 — Cancel / Exit`; every sub-menu also includes `9 — Back`.
NEVER use `AskUserQuestion` for menus.

## Prerequisites

- Orchestrator is `/cpv-main-menu` (this skill is `user-invocable: false`)
- `${CLAUDE_PLUGIN_ROOT}` is set by Claude Code

## Menu tree

Full tree, table layouts, and per-leaf bash recipes: [menu-tree](references/menu-tree.md).
> Shell prologue · Table-rendering rules · Menu definitions · Etiquette and error handling

Top-level categories (canonical table in menu-tree.md §3.0 — v2.90.0):
1 Validate · 2 Fix · 3 Optimize for Cache · 4 Diagnose · 5 Update · 6 Create · 7 Publish & Migrate · 8 Manage · H Help · A Ask · 0 Cancel

## Instructions

1. Print the top-level menu as a Unicode box-drawing table (see [menu-tree](references/menu-tree.md) §3.0). Last row is `0 — Cancel / Exit`.
2. End the output with a single line: `Type a number to choose:`
3. Wait for the user's next message. Parse the leading integer (ignore surrounding whitespace).
4. On `0` at any depth → reply `Cancelled — no actions taken.` and stop.
5. On a valid category number → load the sub-menu's table from [menu-tree](references/menu-tree.md) and print it. Sub-menus include `0 — Cancel / Exit` AND `B — Back to top-level menu` (or `9 — Back` when 0 is taken).
6. On a leaf number → look up the recipe in [menu-tree](references/menu-tree.md):
   - **arg-prompts**: ask required arguments in plain text (one short line per question — NO AskUserQuestion)
   - **execution**: run the exact bash from the recipe (always via the launcher)
7. Report only the compact summary + report-file path.
8. After the leaf finishes, print a 2-row table: `1 — Do something else (back to top-level)` / `0 — Done (exit)` and wait.

## Output

Each leaf returns the underlying command's output (compact summary + report-path).
The menu itself returns nothing extra — it's a routing shell.

## Error Handling

- Invalid number (not in the table) → print `Invalid choice. Pick a number from the table.` and re-print the SAME table.
- Invalid path at an arg-prompt → re-ask with a hint, do not abort.
- Launcher invocation fails → surface stderr verbatim, then re-print the SAME sub-menu table (do not jump back to top-level).
- `${CLAUDE_PLUGIN_ROOT}` unset → abort with `CPV plugin not installed in this session — install via /plugin install claude-plugins-validation@emasoft-plugins`.

## Resources

- [menu-tree](references/menu-tree.md) — full tree + table layouts + per-leaf recipes.
  > Shell prologue · Table-rendering rules · Menu definitions · Etiquette and error handling

## Checklist

Copy this checklist and track your progress:

- [ ] Print menu as Unicode box-drawing table (last row = `0 — Cancel / Exit`)
- [ ] Read user's next message → parse integer choice
- [ ] On `0` → `Cancelled — no actions taken.` and stop
- [ ] Sub-menu → drill (Back option included)
- [ ] Leaf → ask args inline as plain text, run launcher recipe
- [ ] Return ≤3-line summary + report path
- [ ] Print "Do something else?" 2-row table → loop or exit

## Examples

Example 1:
- Input: `/cpv-main-menu` → table prints → user replies `1` (Validate) → sub-menu prints → user replies `1` (Plugin) → user pastes `~/Code/my-plugin/`
- Output: `Plugin Validation: PASS. Report: reports/validate_plugin/<TS>-my-plugin.md`

Example 2:
- Input: `/cpv-main-menu` → table prints → user replies `0`
- Output: `Cancelled — no actions taken.`

Example 3:
- Input: `/cpv-main-menu` → user navigates to 5 → 4 (Install scanners) → confirms `yes`
- Output: `All 5 scanners + fclones installed (or already present).`
