---
trdd-id: bcbceeed
title: TRDD-bcbceeed — Menu orchestrators run in the main session (haiku), not as subagents
column: superseded
updated: 2026-08-25T17:25:39+0200
superseded-by: TRDD-c50531c2
---

# TRDD-bcbceeed — Menu orchestrators run in the main session (haiku), not as subagents

**TRDD ID:** `bcbceeed-6199-48f4-87a4-7597f29201d8`
**Filename:** `design/tasks/TRDD-bcbceeed-6199-48f4-87a4-7597f29201d8-menu-orchestrator-haiku-main-session.md`
**Tracked in:** this repo (`design/tasks/` is git-tracked)
**Status:** In progress
**Created:** 2026-05-16

---

## Context

The user reports that CPV's menu commands "often fail to show the menu,
especially after the first answers or results", and that "the other main
menu commands do it too". Issue #26 (still open) documents the
manifestation for `/cpv-doctor`: the post-scan follow-up menu rendered by
the opus work agent never reaches the user.

A first reading suggests issue #26's title is the whole story (post-scan
menu visibility). It is not. The root cause runs deeper: per the current
Anthropic spec, **subagents cannot spawn other subagents**, but the entire
CPV menu architecture (TRDD-82e836dc, shipped v2.11.x) is built on that
assumption. The current architecture has four
"menu subagents" (`cpv-doctor-menu`, `plugin-fixer-menu`,
`marketplace-fixer-menu`, `cache-optimizer-menu`) — each a haiku subagent
dispatched by a slash command via the `agent:` frontmatter field — and
each one tries to spawn the corresponding work agent (`cpv-doctor-agent`,
`plugin-fixer`, `marketplace-fixer`, `cache-optimizer-agent`) on opus via
the Agent tool. Per the docs that dispatch is a documented no-op:

> *"Subagents cannot spawn other subagents. If your workflow requires
> nested delegation, use Skills or chain subagents from the main
> conversation."*  — https://code.claude.com/docs/en/sub-agents
>
> *"Subagents cannot spawn other subagents, so `Agent(agent_type)` has no
> effect in subagent definitions."*  — same page

That means the menu commands have been quietly broken for any Claude Code
build that enforces the rule strictly. Issue #26's visibility symptom is
one shape; the same root cause also produces the "menu fails to render
after the first answer" symptom the user is reporting now.

## Corrected design

Per https://code.claude.com/docs/en/commands and
https://code.claude.com/docs/en/skills (custom commands have been merged
into skills), a slash command's frontmatter accepts a `model:` field that
*forces the invoking turn to that model*. The body of the slash command
loads into the main session as a single message and stays in context for
subsequent turns.

The corrected architecture for each of the four menu chains is:

1. **The slash command itself is the menu orchestrator.** Its frontmatter
   sets `model: haiku` so the first menu-render turn is haiku regardless
   of session model.
2. **All menu presets are baked into the command body** as Unicode-bordered
   tables — no external file, no menu subagent layer. The body also
   includes the per-row routing table and the dispatch protocol.
3. **The main session runs the loop.** Render menu → wait for the user's
   reply → dispatch the matching work agent via the Agent tool (the main
   session IS allowed to spawn subagents per the docs) → when the work
   agent returns, render the next menu inline (also from the baked
   presets) → wait for the next reply → loop.
4. **The work agents stay unchanged.** They are opus-tier, they do the
   real work, they return findings + a short summary. Crucially, they no
   longer render the post-scan menu themselves — that menu is just the
   "next state" of the main-session loop, baked into the same slash
   command body.
5. **A one-line banner at the top of every menu** says
   `Session model: <model>. For cheaper menu navigation: /model haiku.`
   The user opts into haiku-everywhere with one keystroke. CPV cannot
   force the session model, but the banner means the haiku-everywhere
   path is one keystroke away.
6. **The four menu-subagent files are deleted.** They are subagents that
   try to spawn other subagents — a documented no-op — so they have no
   path to working under the current spec.

The fifth menu surface (`cpv-main-menu-agent` + `commands/cpv-main-menu.md`)
has a different shape (multi-level nested sub-menus). That migration is
deferred to a follow-up TRDD — it requires a fuller rework of the menu
tree.

## Files to modify

| File | Action |
|---|---|
| `commands/cpv-doctor.md` | Rewrite. Drop `agent: cpv-doctor-menu`. Add `model: haiku`. Bake the 22-row first-contact menu + per-row routing + dispatch protocol + post-scan menu instruction into the body. Body instructs main session to render → wait → dispatch `cpv-doctor-agent` via Agent tool → render post-scan menu. |
| `commands/cpv-fix-validation.md` | Rewrite same pattern. Drop `agent: plugin-fixer-menu`. Bake the auto-discovered-reports table + free-text fallback + routing into the body. Body dispatches `plugin-fixer` (opus). |
| `commands/cpv-fix-marketplace-validation.md` | Rewrite same pattern. Drop `agent: marketplace-fixer-menu`. Bake the report table + architecture-migration + pipeline-standardization + manual-entry rows. Body dispatches `marketplace-fixer` (opus). |
| `commands/cpv-cache-optimize.md` | Rewrite same pattern. Drop `agent: cache-optimizer-menu`. Bake the report table + audit-then-fix + broader rows. Body dispatches `cache-optimizer-agent` (opus). |
| `agents/cpv-doctor-menu.md` | DELETE (via `janitor-safe-delete` per the use-safe-delete rule — it goes to `.trashcan/`). |
| `agents/plugin-fixer-menu.md` | DELETE same way. |
| `agents/marketplace-fixer-menu.md` | DELETE same way. |
| `agents/cache-optimizer-menu.md` | DELETE same way. |
| `tests/test_agent_model_tiers.py` | Remove the four menu-agent fixtures (rows 157-177). Add new fixtures: each of the four slash commands has `model: haiku` and no `agent:` field; each command body contains the baked menu table; each command body references the matching work agent via `subagent_type:` in a documented dispatch block. |
| `tests/test_consolidation_v211.py` | Remove the `cpv-fix-validation → plugin-fixer-menu` and 3 siblings (lines 77-80, 281-284). |
| `tests/test_menu_orchestrator_v289.py` | NEW. Asserts the four slash commands are self-contained main-session orchestrators per the new contract. |

The work agents (`cpv-doctor-agent`, `plugin-fixer`, `marketplace-fixer`,
`cache-optimizer-agent`) are NOT modified in this TRDD. Their post-scan
menu rendering responsibility is REMOVED from prose (the slash command
body now owns it), but their work loop is unchanged. A follow-up TRDD
will optionally trim the post-scan menu rendering instructions out of
`agents/cpv-doctor-agent.md` once the new dispatch is verified in
production.

## Tests

`tests/test_menu_orchestrator_v289.py` covers, for each of the four
commands:

1. Frontmatter has `model: haiku` and `user-invocable: true`.
2. Frontmatter has NO `agent:` field (no menu subagent layer).
3. Body contains the Unicode-bordered menu table (search for
   `┏━━━` and `┡━━━`).
4. Body contains a dispatch block with the correct `subagent_type:`
   referencing the matching work agent.
5. Body documents the haiku-session banner.

The existing `test_agent_model_tiers.py` and `test_consolidation_v211.py`
expectations on the four menu subagents are removed (they referenced
the now-deleted files).

## Verification

```bash
cd "${CLAUDE_PLUGIN_ROOT}"
uv run pytest tests/test_menu_orchestrator_v289.py -v
uv run pytest tests/ -x -q --tb=short
uv run ruff check .
PLUGIN_SKIP_GITHUB_INTEGRITY=1 CPV_SKIP_GITHUB_INTEGRITY=1 \
  uv run python scripts/validate_plugin.py . --strict
```

## Release

```bash
uv run python scripts/publish.py --minor    # v2.88.0 → v2.89.0
```

Minor bump because the architectural change is invisible to the user
when things work, but the per-turn behaviour does change (no more
sub-agent dispatch hop). After publish, close issue #26 with a comment
pointing to v2.89.0.

## Cross-references

- Previous design: `TRDD-82e836dc-...-agent-model-tier-policy.md` (the
  menu-subagent split being undone here).
- Companion fix from this conversation: v2.88.0 catch-up
  (TRDD-ebc745b5) shipped the v2.1.143 changelog spec.
- Issue: https://github.com/Emasoft/claude-plugins-validation/issues/26
- Anthropic docs (verified against during design):
  - https://code.claude.com/docs/en/sub-agents
  - https://code.claude.com/docs/en/skills
  - https://code.claude.com/docs/en/commands
  - https://code.claude.com/docs/en/plugins-reference

## Approval log

- 2026-08-25T17:25:39+0200 — CLOSED as superseded by the CPV session (board drain; authority delegated by USER 2026-08-25). the 4 menu-subagents+commands it targeted were removed by menu unification (batch_aj)
