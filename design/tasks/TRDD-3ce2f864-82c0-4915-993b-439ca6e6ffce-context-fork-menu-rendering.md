# TRDD-3ce2f864 — Use `context: fork` for haiku menu rendering

**TRDD ID:** `3ce2f864-82c0-4915-993b-439ca6e6ffce`
**Filename:** `design/tasks/TRDD-3ce2f864-82c0-4915-993b-439ca6e6ffce-context-fork-menu-rendering.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Implementation complete (Deliverables 1–11). Version bump (Deliverable 12) pending user approval.
**Target release:** v2.89.4

## Problem

Per [Claude Code skills docs](https://code.claude.com/docs/en/skills#run-skills-in-a-subagent):

> `model` … The override applies for the rest of the **current turn** and is not saved to settings; the session model resumes on your next prompt.

When the parent session is opus with a 1M-token context window and a slash command's frontmatter says `model: haiku`, the override tries to switch the model mid-turn while keeping the inherited conversation history. Haiku's context window is much smaller than opus's, so the override silently degrades or fails — the orchestrator is NOT actually running on haiku, it's just lying about it.

This affects the four CPV menu orchestrator commands shipped in v2.89.0:

- `commands/cpv-doctor.md`
- `commands/cpv-fix-validation.md`
- `commands/cpv-fix-marketplace-validation.md`
- `commands/cpv-cache-optimize.md`

All four declare `model: haiku` in their frontmatter but run as multi-turn state machines in the main session, so the model override is functionally broken.

## The fix: `context: fork` on a dedicated rendering skill

Per the same docs:

> Add `context: fork` to your frontmatter when you want a skill to run in isolation. The skill content becomes the prompt that drives the subagent. **It won't have access to your conversation history.**

`context: fork` creates a fresh subagent context, so `model: haiku` actually takes effect. But fork-skills are single-turn — they cannot host a multi-turn orchestrator. So we split the responsibility:

1. **Multi-turn orchestrator command body** stays in the main session (whatever model the user is on — opus by default). It dispatches work agents via the Agent tool and tracks state across turns.
2. **Single-turn menu rendering** moves to a new `context: fork` + `model: haiku` + `agent: general-purpose` skill (`cpv-format-menu`), invoked via the Skill tool wherever the orchestrator currently calls `format_menu.py` via Bash.

Token accounting:
- Before: orchestrator runs on opus (lying about haiku) → Bash format_menu.py is cheap but the orchestrator turn was opus anyway. Lie cost: 0; correctness cost: high (false trust in `model:`).
- After: orchestrator runs on opus (honestly) → Skill invocation of cpv-format-menu spawns a fresh haiku fork that runs format_menu.py and returns the rendered text. Honest haiku usage; orchestrator turn unchanged.

The net effect is HONESTY about which model runs each step, not a token reduction. The "haiku menu rendering" claim from v2.89.0 finally becomes true.

## Architecture

```
User: /cpv-doctor
  ↓
Main session (opus):
  cpv-doctor command body runs (Step 1 — first menu is PRE-RENDERED, no fork needed)
  → main session emits pre-rendered Unicode table as text
User picks "3"
  ↓
Main session (opus):
  cpv-doctor body Step 2 — collects follow-up via plain text
User confirms "y"
  ↓
Main session (opus):
  cpv-doctor body Step 3 — dispatches cpv-doctor-agent (opus, isolated subagent context)
cpv-doctor-agent returns one-line summary
  ↓
Main session (opus):
  Step 4a — emits pre-rendered severity-summary block as text
  Step 4b — writes breakdown.json path to /tmp/cpv-doctor-breakdown-spec.json
           — invokes Skill tool: cpv-format-menu (haiku, fresh fork)
             → fork runs format_menu.py breakdown
             → fork returns rendered text
           — main session copies the Skill tool's text result VERBATIM into its response
  Step 5 — writes post-scan menu spec to /tmp/cpv-doctor-postscan-spec.json
           — invokes Skill tool: cpv-format-menu (haiku, fresh fork)
             → fork runs format_menu.py menu, writes action_map to /tmp/...map.json
             → fork returns rendered menu text
           — main session copies the Skill tool's text result VERBATIM into its response
  Step 6 — routes user's post-scan pick (reads action_map.json), loops back
```

## New skill: `skills/cpv-format-menu/SKILL.md`

Already created in this branch. Frontmatter:

```yaml
---
name: cpv-format-menu
description: Render a Unicode-bordered menu/summary/breakdown/status_table from a JSON spec file. Forks to haiku in an isolated context so menu rendering never inherits the parent session's (often opus-sized) conversation history. Loaded by the four CPV orchestrator commands. Never invoke directly.
user-invocable: false
allowed-tools: Bash, Read
context: fork
model: haiku
agent: general-purpose
arguments: mode spec_path action_map_path
---
```

Body: single-turn, reads the spec, runs `format_menu.py`, emits stdout verbatim.

## Orchestrator refactor pattern (apply to all 4)

Each orchestrator command file change:

1. **Frontmatter:**
   - REMOVE `model: haiku` (it was a lie — the multi-turn body cannot honor it).
   - ADD `Skill` to `allowed-tools`.
2. **Body intro:**
   - DELETE banner lines claiming "haiku" for the orchestrator turn — keep the `/model haiku` opt-in tip only if it's still useful for the USER, not for the orchestrator.
3. **Every existing `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" <mode> "..."` Bash block:**
   - REPLACE with two steps:
     ```bash
     cat > /tmp/<orchestrator>-<purpose>-spec.json <<'EOF'
     <the JSON spec that used to be inline>
     EOF
     ```
     Then invoke the Skill tool:
     ```
     Skill({
       skill: "claude-plugins-validation:cpv-format-menu",
       args: "<mode> /tmp/<orchestrator>-<purpose>-spec.json [/tmp/<orchestrator>-<purpose>-map.json]"
     })
     ```
   - Add a one-line "COPY THE SKILL TOOL'S TEXT RESULT VERBATIM INTO YOUR TEXT RESPONSE" directive within 5 lines of the Skill invocation.
4. **Architecture notes:**
   - UPDATE the "v2.89.x" section header to v2.89.4.
   - REPLACE the "`model: haiku` is best-effort" bullet with an honest explanation: orchestrator runs on the session model; menu rendering forks to haiku via cpv-format-menu.

## Main-menu surfaces

| File | Type | Current state | Action |
|---|---|---|---|
| `commands/cpv-main-menu.md` | command | delegates to `cpv-main-menu-agent` (no `model: haiku` on the command) | No change needed (agent is isolated). |
| `agents/cpv-main-menu-agent.md` | agent | `model: haiku` | Agents already run in isolated subagent contexts. The `model: haiku` SHOULD take effect because agents don't inherit parent context. Verify behavior; no change needed. |
| `skills/cpv-main-menu-skill/SKILL.md` | skill (loaded by agent) | no `model:` field | No change needed — loaded inside the agent's already-isolated haiku context. |

If user reports menu-render failures on the main-menu, add `context: fork` to the skill as a follow-up.

## Test updates

`tests/test_menu_visibility.py`:
- Update `test_orchestrator_has_copy_stdout_directive` to look for BOTH Bash `format_menu.py` AND Skill `cpv-format-menu` invocations.
- Add a new `test_orchestrators_have_no_lying_model_haiku` test asserting the 4 orchestrators do NOT have `model: haiku` in frontmatter.
- Add a new `test_orchestrators_invoke_cpv_format_menu_skill` test asserting the 4 orchestrators reference `cpv-format-menu` (proof they offload menu rendering).

`tests/test_agent_model_tiers.py`:
- Update the assertion that orchestrators have `model: haiku` (was added in v2.89.0) — flip it: orchestrators MUST NOT have `model: haiku`, but the new `cpv-format-menu` skill MUST have `model: haiku` + `context: fork`.

Add `tests/test_cpv_format_menu_skill.py` (new):
- Skill exists at `skills/cpv-format-menu/SKILL.md`.
- Frontmatter has `context: fork`, `model: haiku`, `agent: general-purpose`, `user-invocable: false`.
- Description mentions all 4 orchestrators that load it.

## Deliverables

1. ✓ `skills/cpv-format-menu/SKILL.md` (created)
2. Refactor `commands/cpv-doctor.md`
3. Refactor `commands/cpv-fix-validation.md`
4. Refactor `commands/cpv-fix-marketplace-validation.md`
5. Refactor `commands/cpv-cache-optimize.md`
6. Update `tests/test_menu_visibility.py`
7. Update `tests/test_agent_model_tiers.py`
8. Create `tests/test_cpv_format_menu_skill.py`
9. Run `uv run python scripts/validate_plugin.py . --strict` — expect 0/0/0/0
10. Run `uv run pytest tests/test_menu_visibility.py tests/test_agent_model_tiers.py tests/test_cpv_format_menu_skill.py -v` — expect all green
11. Update `MEMORY.md` index entry for v2.89.4
12. Bump version via `publish.py --patch` (LOCAL only — do NOT push without explicit user approval per RULE 0)

## Out of scope (future TRDDs)

- Refactoring `agents/cpv-main-menu-agent.md` (already isolated; only revisit if it misbehaves)
- Consolidating the 4 orchestrator command bodies into one shared template (they're each ~150-200 lines but with different menu specs and dispatch targets)
- Compacting MEMORY.md to one-line index entries (raised by user as a separate concern; will become its own TRDD)
