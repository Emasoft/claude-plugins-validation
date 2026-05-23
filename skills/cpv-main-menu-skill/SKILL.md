---
name: cpv-main-menu-skill
description: Routes the /cpv-main-menu Stop-hook menu via scripts/cpv_menu.py + claude-menu-system. Used dynamically via the-skills-menu (TRDD-478d9687) — primarily by cpv-main-menu-agent. Use when navigating CPV's many commands via a single entry point.
when_to_use: When the cpv-main-menu command needs the per-category sub-menu definitions, per-leaf execution recipes, and per-menu fixed letter→action maps. Never invoke directly.
user-invocable: false
---

# CPV Main-Menu Routing Skill

## Overview

Backing skill for `/cpv-main-menu`. Holds the menu specs, per-leaf
execution recipes, AND the FIXED-KEY ROUTING CONTRACT (letter→action
maps) for every menu in the tree. Every menu is rendered by the
`claude-menu-system` plugin's Stop-hook emitter — the orchestrator
queues a spec via `scripts/cpv_menu.py` and ENDS its turn; the hook
prints the menu post-turn via the hook JSON `systemMessage` field,
so the menu is shown to the user but NEVER enters the transcript or
prompt cache. Every menu includes `0 — Cancel / Exit`; every sub-menu
also includes `B — Back`. NEVER print menus inline. NEVER use
`AskUserQuestion`.

## Prerequisites

- Orchestrator is `/cpv-main-menu` (this skill is `user-invocable: false`)
- `${CLAUDE_PLUGIN_ROOT}` is set by Claude Code
- `claude-menu-system` plugin is installed (declared as a hard dependency
  in `.claude-plugin/plugin.json`). If missing, `cpv_menu.py` fails fast
  with the install hint — there is NO inline fallback renderer
  (TRDD-4de479a0, no-legacy rule).

## Menu tree

Full tree, menu specs, per-leaf bash recipes, AND per-menu fixed
letter→action maps: [menu-tree](references/menu-tree.md).
> Shell prologue · Menu-spec rendering rules · Fixed-key routing contract · Menu definitions · Etiquette and error handling

Top-level categories (canonical layout in menu-tree.md §3.0 — v2.90.0):
1 Validate · 2 Fix · 3 Optimize for Cache · 4 Diagnose · 5 Update · 6 Create · 7 Publish & Migrate · 8 Manage · H Help · A Ask · 0 Cancel

## Fixed-key routing contract (single source of truth)

Two namespaces that never collide:

- **Numbers `1..N`** — DYNAMIC list, ordered alphabetically. The Nth
  number is always the Nth alpha-sorted dynamic item.
- **Letters** — FIXED actions. Each letter is permanently bound to one
  action across every CPV menu — `M` Main, `B` Back, `X`/`0` Exit, plus
  per-menu mnemonics (`V` Validate, `F` Fix, `D` Diagnose, `C` Create,
  …) documented per sub-menu in [menu-tree](references/menu-tree.md).

The fixed map lives in the skill/agent body — the orchestrator NEVER
inspects the rendered menu to interpret a key. This is what makes the
post-turn Stop-hook emit safe: the agent already knows every key's
meaning before the menu is ever shown. `cpv_menu.py` defaults
`renumber: false`, so the caller's keys are kept verbatim.

## Instructions

1. Build the top-level menu spec (see [menu-tree](references/menu-tree.md) §3.0
   for the canonical row list). Write the spec JSON to a tempfile.
2. Queue the menu by invoking
   `python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_menu.py" /tmp/cpv-mainmenu-top-spec.json`
   and END THE TURN. The claude-menu-system Stop hook emits the menu
   via `systemMessage`. NEVER print the menu inline; CMS Stop hook
   emits via `systemMessage`.
3. Wait for the user's next message. Parse the key (number or letter,
   case-insensitive).
4. On `0` at any depth → reply `Cancelled — no actions taken.` and stop.
5. On a valid category key → look up the action_id in the menu's fixed
   key→action map (in [menu-tree](references/menu-tree.md)). Build the
   corresponding sub-menu spec, queue it via `cpv_menu.py`, end the
   turn. Sub-menus include `0 — Cancel / Exit` AND `B — Back` rows.
6. On a leaf key → look up the recipe in [menu-tree](references/menu-tree.md):
   - **arg-prompts**: ask required arguments in plain text (one short line per question — NO AskUserQuestion)
   - **execution**: run the exact bash from the recipe (always via the launcher)
7. Report only the compact summary + report-file path.
8. After the leaf finishes, queue the §3.99 "do something else?" spec
   (or §3.10 post-validate fix menu, for Validate leaves) via
   `cpv_menu.py` and end the turn.

## Output

Each leaf returns the underlying command's output (compact summary + report-path).
The menu itself returns nothing extra — it's a routing shell. Menu text
is emitted by the Stop hook, not returned by the agent.

## Error Handling

- Invalid key (not in the menu's fixed map) → ask plain text:
  `Invalid choice. Pick a key from the menu (or B for back, 0 to cancel).`
  Then re-queue the SAME sub-menu spec via `cpv_menu.py` and end the turn.
- Invalid path at an arg-prompt → re-ask with a hint, do not abort.
- Launcher invocation fails → surface stderr verbatim, then re-queue
  the SAME sub-menu spec (do not jump back to top-level).
- `${CLAUDE_PLUGIN_ROOT}` unset → abort with `CPV plugin not installed in this session — install via /plugin install claude-plugins-validation@emasoft-plugins`.
- `cpv_menu.py` fails with `MenuSystemUnavailable` (claude-menu-system
  not installed) → surface the install hint verbatim and stop. There is
  NO inline fallback — fail-fast per TRDD-4de479a0.

## Resources

- [menu-tree](references/menu-tree.md) — full tree + menu specs + per-leaf recipes + per-menu fixed letter→action maps.
  > Shell prologue · Menu-spec rendering rules · Fixed-key routing contract · Menu definitions · Etiquette and error handling

## Checklist

Copy this checklist and track your progress:

- [ ] Build menu spec; queue via `cpv_menu.py`; END THE TURN
- [ ] Read user's next message → parse key (number or letter, case-insensitive)
- [ ] On `0` → `Cancelled — no actions taken.` and stop
- [ ] Sub-menu → look up action_id in fixed map, queue sub-menu spec
- [ ] Leaf → ask args inline as plain text, run launcher recipe
- [ ] Return ≤3-line summary + report path
- [ ] Queue §3.99 "do something else?" (or §3.10 post-validate) spec → loop or exit

## Examples

Example 1:
- Input: `/cpv-main-menu` → top-level spec queued via `cpv_menu.py`, Stop hook emits menu → user replies `1` (Validate) → §3.1 sub-menu spec queued → user replies `1` (Plugin) → user pastes `~/Code/my-plugin/`
- Output: `Plugin Validation: PASS. Report: reports/validate_skill/<TS>-my-plugin.md`

Example 2:
- Input: `/cpv-main-menu` → top-level spec queued → user replies `0`
- Output: `Cancelled — no actions taken.`

Example 3:
- Input: `/cpv-main-menu` → user navigates to `8` (Manage) → `4` (Install scanners) → confirms `yes`
- Output: `All 5 scanners + fclones installed (or already present).`
