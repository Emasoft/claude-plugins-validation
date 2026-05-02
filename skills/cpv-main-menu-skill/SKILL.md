---
name: cpv-main-menu-skill
description: Routes the /cpv-main-menu interactive menu. Loaded by cpv-main-menu command. Use when navigating CPV's many commands via a single entry point.
when_to_use: When the cpv-main-menu command needs the per-category sub-menu definitions and per-leaf execution recipes. Never invoke directly.
user-invocable: false
allowed-tools: Read, Bash(uv:*), Bash(python:*), Bash(python3:*), Bash(git:*), Bash(mkdir:*), Bash(date:*), Bash(basename:*), Bash(awk:*), Bash(printf:*), Bash(echo:*), Bash(cat:*), Bash(npm:*), Bash(brew:*), Bash(snap:*), Bash(cargo:*), Bash(pipx:*), Bash(gh:*), Glob, Grep, AskUserQuestion, Skill, Edit, Write
---

# CPV Main-Menu Routing Skill

## Overview

Backing skill for `/cpv-main-menu`. Holds the menu tree definition, per-leaf
argument prompts, and per-leaf execution recipes. Every menu/sub-menu MUST
include a "Cancel / Exit" option (terminates cleanly) and every sub-menu
ALSO includes a "Back" option (returns to the parent menu).

## Prerequisites

- The orchestrator is `/cpv-main-menu` (this skill is `user-invocable: false`)
- `${CLAUDE_PLUGIN_ROOT}` is set (Claude Code exports it on every command)
- `AskUserQuestion` tool is available

## Menu tree

Full tree, per-leaf arg prompts, and exact bash recipes: [menu-tree](references/menu-tree.md).
> Shell prologue · Menu definitions · Etiquette and error handling

Top-level categories (each is a sub-menu — see references for leaves):
1. **Validate** — plugin / skill / cache / marketplace / local-scope / project-scope / component
2. **Validate from GitHub** — plugin or marketplace by owner/repo
3. **Fix** — plugin findings / marketplace findings / cache optimize
4. **Create** — scaffold plugin or marketplace
5. **Manage** — list / install / doctor / install scanners / bump version / show version
6. **GitHub setup** — branch protection rules / link plugin to marketplace
7. **Deep semantic analysis** — opus A-F grading (confirms cost first)
8. **Help / About** — category overview / command list / version
9. **Cancel / Exit** — terminate cleanly with no side effects

## Instructions

1. Present the top-level menu with `AskUserQuestion`. Include 8
   categories + Cancel/Exit.
2. On Cancel/Exit at any depth → reply `Cancelled — no actions taken.`
3. On a category pick → load the sub-menu options from
   [`menu-tree.md`](references/menu-tree.md) (section per category) and present via
   another `AskUserQuestion`. Include "Back" + "Cancel / Exit".
4. On a leaf pick → look up the leaf's recipe in
   [`menu-tree.md`](references/menu-tree.md):
   - **arg-prompts**: any required arguments to ask via `AskUserQuestion`
     (e.g. plugin path, owner/repo slug)
   - **execution**: the exact bash to run (always via the launcher)
5. Run the bash. Report only the compact summary + report-file path.
6. After the leaf finishes, ask whether the user wants to do something
   else (loop back to top-level menu) or exit.

## Output

Each leaf returns the underlying command's output (compact summary +
report-path). The menu itself returns nothing extra — it's a routing
shell.

## Error Handling

- If the user provides an invalid path → `AskUserQuestion` again with a
  hint
- If a launcher invocation fails → surface the stderr verbatim, then
  return to the same sub-menu (do not re-prompt the whole tree)
- If `${CLAUDE_PLUGIN_ROOT}` is unset → abort with `CPV plugin not
  installed in this session — install via /plugin install
  claude-plugins-validation@emasoft-plugins`

## Resources

- [menu-tree](references/menu-tree.md) — full tree + per-leaf recipes.
  > Shell prologue · Menu definitions · Etiquette and error handling

## Checklist

Copy this checklist and track your progress:

- [ ] Present menu via `AskUserQuestion` (Cancel/Exit last)
- [ ] On Cancel/Exit → `Cancelled — no actions taken.` and stop
- [ ] Sub-menu → drill (Back option included)
- [ ] Leaf → ask args, run launcher recipe
- [ ] Return ≤3-line summary + report path
- [ ] Ask "Do something else?" → loop or exit

## Examples

Example 1:
- Input: `/cpv-main-menu` → user picks 1.1 (Validate plugin) → path `~/Code/my-plugin/`
- Output: `Plugin Validation: PASS. Report: reports/validate_plugin/<TS>-my-plugin.md`

Example 2:
- Input: `/cpv-main-menu` → user picks 9 (Cancel/Exit)
- Output: `Cancelled — no actions taken.`

Example 3:
- Input: `/cpv-main-menu` → user picks 5.4 (Install scanners) → confirms
- Output: `All 5 scanners + fclones installed (or already present).`
