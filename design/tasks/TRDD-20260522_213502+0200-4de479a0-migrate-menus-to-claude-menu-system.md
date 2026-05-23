---
trdd-id: 4de479a0-b2f2-48bb-ad79-a89ae80bd934
title: Migrate CPV menu rendering to the claude-menu-system Stop-hook plugin
status: in-progress
created: 2026-05-22T21:35:02+0200
updated: 2026-05-22T22:26:19+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-4de479a0 — Migrate CPV menu rendering to claude-menu-system

**Filename:** `design/tasks/TRDD-20260522_213502+0200-4de479a0-migrate-menus-to-claude-menu-system.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

## User request (verbatim intent)

In a cache-optimisation thread, the user established:

1. A `model:` frontmatter fragments the prompt cache → removed from all CPV
   components; CA-04 broadened to flag it everywhere (WARNING). **DONE** (this
   session, separate work).
2. `context: fork`/`branch` ALSO fragments the cache (re-primes the whole
   prefix from cold, ~1M tokens with many skills/MCP/tools) → CA-07 added
   (WARNING). **DONE** (this session).
3. CPV's `cpv-format-menu` skill uses `context: fork` purely to render menus —
   no freshness/many-file justification, so CA-07 flags it.
4. **The menu system was already externalised to a separate plugin**
   (`claude-menu-system`, emasoft-plugins) which emits menus via a `Stop` /
   `SubagentStop` / `StopFailure` hook (post-turn, **zero cache cost** — no
   fork, no inline render consuming the agent's context).
5. **Decision (AskUserQuestion):** *Migrate CPV menus to claude-menu-system.*

This TRDD specifies that migration.

## Why (the cache argument)

Today CPV renders menus two ways, both sub-optimal:

- **Inline Unicode tables** embedded in `cpv-main-menu-skill/menu-tree.md` and
  printed in the agent's response — consumes the agent's context every render.
- **`scripts/format_menu.py` via Bash** (cpv-doctor-agent, the batch commands,
  cpv_batch_orchestrator) — the agent shells out, captures stdout, prints it
  inline. Plus the `cpv-format-menu` **fork-skill** wraps format_menu.py in a
  `context: fork` subagent — the worst case (full cold-cache re-prime).

`claude-menu-system` solves this: the orchestrator writes a spec JSON, calls
`menu_write.py`, and ENDS ITS TURN. A bundled `Stop`/`SubagentStop` hook emits
the rendered menu AFTER the turn — so the menu never enters the agent's cached
prefix and never forks a subagent. The user replies in the next turn; the
orchestrator reads the sibling `.actions.json` (key → action_id) to route.

## claude-menu-system API (verified v0.1.5)

- Spec JSON fields: `spec_version: 1`, `mode`, `plugin`, `slug`, `header`,
  `rows: [{key, action_id, label, disabled?}]`, `footer`.
- `python3 "$CMS_ROOT/scripts/menu_write.py" /tmp/spec.json` queues the menu.
  The Stop/SubagentStop/StopFailure hook (`menu_emit.py`) emits it at turn end.
- `menu_write.py` also writes `/tmp/spec.actions.json` = `{"1":"scan",...}` for
  next-turn routing.
- Cross-plugin root resolution (CPV is in a different marketplace context, so
  `$CLAUDE_PLUGIN_ROOT` points at CPV, not CMS):
  ```bash
  CMS_ROOT=$(ls -d ~/.claude/plugins/cache/emasoft-plugins/claude-menu-system/*/ | sort -V | tail -1)
  ```
- 8 modes, a SUPERSET of CPV's format_menu.py 4 modes:
  | format_menu.py mode | claude-menu-system mode |
  |---|---|
  | `menu` | `menu` |
  | `summary` | `summary` |
  | `breakdown` | `breakdown` |
  | `status_table` | `status_table` |
  | (none) | `panel`, `multi_box`, `progress`, `confirm` (bonus) |

  The spec schemas are close but NOT identical (CMS adds `spec_version`,
  `plugin`, `slug`). The migration adapts CPV's emitted specs.

## Dependency + missing-plugin behaviour (fail-fast)

CPV gains a hard runtime dependency on `claude-menu-system`. Per the user's
fail-fast / no-fallback rule:

- Declare the dependency in CPV's `plugin.json` (and the marketplace
  cross-dep allowlist, per plugin-dependencies.md) so `add-dependency` /
  validate know about it.
- A small shared helper `scripts/cpv_menu.py::resolve_cms_root()` resolves the
  CMS cache path and **fails fast with a clear, actionable message** if
  claude-menu-system is not installed (`claude plugin install
  Emasoft/claude-menu-system@emasoft-plugins`). NO silent fallback to inline
  rendering (no-legacy rule).

## Open design decision (resolve in Phase 0)

**Fate of `scripts/format_menu.py`:** claude-menu-system's `menu_render.py`
fully supersedes it. Options:
- **(A) Remove format_menu.py entirely** — pure migration, no redundant
  renderer. All callers use menu_write.py. (Aligns with no-legacy rule.)
- **(B) Keep format_menu.py only as the spec-builder** CPV uses to produce the
  JSON it hands to menu_write.py — but CMS takes a spec directly, so this adds
  nothing.

Recommendation: **(A)**. format_menu.py + cpv-format-menu skill + their tests
are removed; CPV emits CMS specs directly.

## File inventory (≈19 files)

**Remove (via janitor safe-delete — they're tracked, so recoverable):**
- `scripts/format_menu.py`
- `skills/cpv-format-menu/SKILL.md` (the fork-skill)
- `tests/test_format_menu.py`
- `tests/test_cpv_format_menu_skill.py`

**New (LANDED in Phase 0):**
- `scripts/cpv_menu.py` — `resolve_cms_root()` + `write_menu(spec) -> Path`
  (fail-fast if CMS missing; defaults `renumber: false` per the fixed-key
  contract; no action-map persistence).
- `tests/test_cpv_menu.py` — resolver + fail-fast + renumber-default + spec
  immutability tests (12, green).

**Modify (replace format_menu.py calls / inline tables with cpv_menu / CMS specs):**
- `agents/cpv-doctor-agent.md`
- `agents/cpv-main-menu-agent.md` + `skills/cpv-main-menu-skill/SKILL.md` +
  `skills/cpv-main-menu-skill/references/menu-tree.md` (inline tables → CMS specs)
- `commands/cpv-batch-*.md` (8 files)
- `scripts/cpv_batch_orchestrator.py`
- `skills/cpv-batch-validate/SKILL.md`
- `skills/the-skills-menu/SKILL.md` + `references/skills-catalog.md` (drop the
  cpv-format-menu catalog entry)
- `.claude-plugin/plugin.json` (declare claude-menu-system dependency)
- Tests: `tests/test_agent_model_tiers.py`, `tests/test_menu_visibility.py`,
  `tests/test_cpv_batch_orchestrator.py`, `tests/test_consolidation_v211.py`
  (drop cpv-format-menu assertions; assert cpv_menu/CMS usage instead)
- `scripts/validate_security.py` (only a self-scan-eligibility reference to
  format_menu.py — remove the dead reference once the file is gone)

## Phases (each ≤5 files where possible; verify before next)

- **Phase 0** — Resolve the format_menu.py fate decision (A vs B); confirm CMS
  spec schema diffs by reading every `examples/*.json`. Write `cpv_menu.py` +
  its tests. Declare the dependency in plugin.json.
- **Phase 1** — Migrate the batch commands + cpv_batch_orchestrator (status_table
  - post-scan menus) to cpv_menu. Verify with batch tests.
- **Phase 2** — Migrate cpv-doctor-agent (first-contact + summary + breakdown +
  post-scan menu). Verify.
- **Phase 3** — Migrate cpv-main-menu (agent + skill + menu-tree inline tables).
  This is the biggest single surface. Verify.
- **Phase 4** — Remove format_menu.py + cpv-format-menu skill + their tests via
  safe-delete; drop the-skills-menu catalog entry; clean validate_security
  reference. Update test_menu_visibility / test_consolidation_v211 / model-tiers.
- **Phase 5** — Full suite green; CPV self-scan 0/0/0/0 + cache audit clean
  (cpv-format-menu CA-07 finding gone). Re-generate integrity manifest.

## Test scenarios

- `resolve_cms_root()` returns the highest cached CMS version; fails fast with
  the install hint when none present.
- `write_menu()` produces a valid CMS spec (spec_version/plugin/slug/rows) and
  returns both the queued path and the `.actions.json` path.
- Routing: a queued menu's `.actions.json` maps rendered keys → action_ids.
- No CPV file references format_menu.py or cpv-format-menu after Phase 4.
- CPV self cache-audit emits zero CA-07 (the fork is gone).

## Security / edge cases

- `resolve_cms_root()` must reject a path outside `~/.claude/plugins/cache/`
  (no traversal); glob only the canonical marketplace dir.
- Spec JSON written to a tempfile with a unique name (no cross-run collision).
- If a menu has >N rows, CMS handles it (no 4-option cap) — confirm no CPV-side
  truncation re-introduced.
- Interaction-model change: orchestrators MUST end their turn after
  `menu_write.py` (the hook emits post-turn). Document this in each migrated
  orchestrator so a future edit doesn't re-add inline printing.

## Acceptance

1. Every CPV menu renders via claude-menu-system (no format_menu.py, no
   cpv-format-menu fork).
2. CPV self-scan 0/0/0/0 + warnings-only; cache audit reports zero CA-07.
3. Full test suite green.
4. Dependency declared + fail-fast verified when CMS absent.

## Status note

Authored after the model/cache + CA-07 work landed (this session). That work
is independent and already complete + green; this TRDD is the follow-on the
user selected.

### Phase 0 — LANDED (2026-05-22)

Verified the claude-menu-system v0.1.5 API by reading its source:

- The emit hook (`menu_emit.py`) prints the rendered menu via the hook JSON
  **`systemMessage`** field (`{"systemMessage": "\n" + payload}`) — so the menu
  is shown to the user but NEVER enters the transcript/context. Zero token cost
  regardless of size; no subagent fork. (Confirms the user's account.)
- `_handle_emit_event` is *"scan queue, emit, **delete**"* — the queued
  `.menu.md` + its `.actions.json` sidecar are removed right after emit.
- CMS's `menu_render.py` is *literally ported from CPV's format_menu.py*
  (same `disabled`-drop + renumber; `renumber` default True; static keys
  `0`/`A` preserved). So CMS is a behavioural drop-in — option **(A)** (remove
  format_menu.py) is confirmed correct. Crucially, CMS honours
  `renumber: false`, which is the foundation of the fixed-key routing contract
  below.

### FIXED-KEY ROUTING CONTRACT (user directive — supersedes any renumber model)

CPV menus use **two key namespaces that never collide**:

- **Numbers `1,2,3,…` — the DYNAMIC list.** Reserved for the things that vary
  (plugins / paths / URLs / folders to choose from). Always presented in
  **alphabetical order**, so "the user typed N" deterministically means *the
  Nth entry of the sorted list*. The count varies run-to-run (1..N); the agent
  knows N because it built the sorted list.
- **Letters — the FIXED actions + navigation.** Each letter is permanently
  bound to one meaning across ALL menus and states:
  - **Reserved navigation (global, identical everywhere):**
    `M` = back to Main Menu · `B` = Back to previous menu · `X` = Exit the
    plugin menu.
  - **Fixed actions:** one stable letter per action (e.g. `D` = Diagnose,
    `C` = Check project extensions, `A` = Ask). The letter never changes
    meaning; an action that doesn't apply right now is simply **omitted** —
    its row is NOT printed at all (no blank line, no placeholder). The
    displayed rows stay contiguous; only the key SEQUENCE skips (e.g. `D`
    then `A`, with `C` absent), and no surviving key is relettered.

**Choosing the action order + letters (skill-design time, once per menu):**

1. **Order** the action rows deliberately — by **importance**, or **grouped by
   category/target**; fall back to alphabetical only when no better criterion
   applies.
2. **Assign** each action a **mnemonic** letter: the first letter of its name
   if free, else the second, else the third, … (`D`iagnose, `C`heck, `A`sk).
   Pure-alphabetical lettering is also valid but mnemonic is preferred.
3. **Reserved keys win:** `M`/`B`/`X` are never available to actions — an
   action whose name starts with one falls to its next free letter.
4. **Bijective invariant:** within a menu each action has exactly one letter
   and each letter exactly one action — zero ambiguity. This map is FIXED at
   skill-design time and documented in the skill body; it does not vary at
   runtime (only active/omitted does).

Worked example (3 dynamic paths + 3 actions + nav):

```
1 - Validate `C:/Power/plugins/tokencounter`
2 - Validate `C:/Power/plugins/merrymound-plugin`
3 - Validate `C:/Power/plugins/visitorbadhe-plugin`
D - Diagnose a plugin or a skill
C - Check a project extensions
A - Ask the Doctor something else
M - Go back to Main Menu
B - Back to previous Menu
X - Exit from the plugin menu
```

Consequence: routing is unambiguous with NO read-back. A typed **letter** →
the skill's fixed letter→action map (documented in the skill body). A typed
**number** → the Nth entry of the alphabetically-sorted dynamic list the agent
presented. The emit-delete of `.actions.json` is therefore irrelevant, and
`cpv_menu` persists NO sidecar. `renumber: false` keeps the letters verbatim;
the agent assigns `1..N` over the sorted list itself.

**Single source of truth (hard invariant):** the letter→action map lives in
the skill/agent body, is FIXED at skill-creation time, and is the SOLE
reference the orchestrator uses to interpret a typed key. The orchestrator
NEVER inspects the rendered menu (which rows printed, in what order) to decide
what a key means — the printed menu is presentation only; the typed key is
resolved purely against the immutable skill table. The Python side merely
receives those correspondences, builds the JSON spec, and the Stop hook prints
it. This is what lets emission be a fire-and-forget post-turn hook with zero
context cost: the agent already knows every key's meaning before the menu is
ever shown.

Implementation: `cpv_menu.write_menu()` defaults `renumber: false` (CMS keeps
the caller's keys verbatim, only dropping `disabled` rows). Each migrated menu
MUST: (1) use **numbers `1..N`** for the alphabetically-sorted dynamic list and
**letters** for fixed actions, with `M`/`B`/`X` reserved for Main/Back/Exit
navigation; (2) omit (or mark `disabled`) any action that doesn't apply right
now — its letter never changes meaning and no other key shifts; (3) document
the complete fixed letter→action map in the skill/agent body so next-turn
routing is unambiguous (numbers route positionally into the sorted list the
agent built). This is a behavioural IMPROVEMENT over the legacy renumbering
menus (menu-tree.md currently drops+renumbers) and is applied during Phases 1-4.
- Queue conventions: `<ts_ns>-<plugin>-<slug>.menu.md`; sidecar
  `<...>.actions.json`. `menu_write.py` prints ONLY the queue path on stdout.
- 8 modes, superset of CPV's 4 (menu/summary/breakdown/status_table +
  panel/multi_box/progress/confirm).

Landed in Phase 0:
- `scripts/cpv_menu.py` — `resolve_cms_root()` (fail-fast + install hint, numeric
  version ordering, skips incomplete installs), `write_menu(spec)` (defaults
  `renumber: false` per the fixed-key contract, subprocess-invokes menu_write.py,
  returns the queue path — NO action-map persistence), `_cli` for Bash callers.
- `tests/test_cpv_menu.py` — 12 tests (real temp-cache dirs + a REAL stub
  menu_write.py that echoes the received spec; no mocks; asserts the
  `renumber: false` default + caller-dict immutability; one skip-guarded
  real-CMS resolve check). All green.
- `.claude-plugin/plugin.json` — declared `dependencies: [{claude-menu-system,
  >=0.1.5}]`. Validates clean (0 findings). Same marketplace (emasoft-plugins)
  → no cross-marketplace allowlist needed.

Open design decision RESOLVED: **(A)** — format_menu.py + cpv-format-menu
removed in Phase 4.

Next: Phase 1 (batch commands + cpv_batch_orchestrator) — awaiting go-ahead.
