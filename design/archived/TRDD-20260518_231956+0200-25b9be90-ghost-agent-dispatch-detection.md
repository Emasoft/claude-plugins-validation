---
trdd-id: 25b9be90-8088-4d17-b7f3-375223ef0de3
title: Ghost-agent dispatch detection in Task() and subagent_type literals
column: complete
created: 2026-05-18T23:19:56+0200
updated: 2026-08-25T17:25:05+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-25b9be90 — Ghost-agent dispatch detection in `Task()` / `subagent_type:` literals

**Filename:** `design/tasks/TRDD-20260518_231956+0200-25b9be90-ghost-agent-dispatch-detection.md`
**Tracked in:** this repo (`design/tasks/` is git-tracked)

## Origin (provenance)

While auditing user-scope skills under `~/.claude/skills/` in session 2026-05-18, we found seven references in dispatch tables and code blocks that named **agents that don't exist anywhere on disk**:

| # | Ghost agent | Where it appeared |
|---|---|---|
| 1 | `architect` | resume-handoff dispatch table, implement-plan "available agents to resume" |
| 2 | `herald` | resume-handoff |
| 3 | `maestro` | resume-handoff |
| 4 | `atlas` | resume-handoff |
| 5 | `oracle` | resume-handoff, premortem `Task(subagent_type="oracle", …)`, agent-context-isolation file-pipeline diagram |
| 6 | `plan-reviewer` | resume-handoff, implement-plan, agent-context-isolation |
| 7 | `chronicler` | resume-handoff |

These would silently fail at runtime: `Task(subagent_type: "oracle", …)` returns "agent not found" — the calling skill thinks it spawned a worker, but no work happens. The user only notices when the expected side-effect (file written, message sent) doesn't materialise.

This class of bug is **invisible to current CPV validators** — they check structural correctness (frontmatter, paths, schema), not whether `subagent_type:` literals resolve to real agents.

## Problem statement

CPV validators do not currently extract `subagent_type:` literals from skill/agent/command bodies and verify the referenced agent exists. As a result, plugins ship with `Task()` calls that name non-existent agents — a CRITICAL-severity silent failure class.

## Goal

Add a new validator rule (let's call it `RC-GHOST-DISPATCH-001`) that:

1. Walks every `skills/**/SKILL.md`, `agents/**/*.md`, `commands/**/*.md` body in a plugin (and optionally in user-scope when invoked via `/cpv-doctor` option 9).
2. Extracts every literal that names an agent in a dispatch context:
   - `subagent_type: "<name>"` (YAML-style)
   - `subagent_type="<name>"` (Python-style)
   - `subagent_type: <name>` (bare, unquoted)
   - `Task({…subagent_type: "<name>"…})` (TypeScript/JS-style)
3. For each extracted name, verifies it exists in one of:
   - The current plugin's `agents/` directory
   - Any installed plugin's `agents/` directory (`~/.claude/plugins/cache/*/*/<version>/agents/`)
   - User scope: `~/.claude/agents/`
   - The hardcoded list of Claude Code built-ins: `general-purpose`, `Explore`, `Plan` (the only ones documented as universal)
4. Flags each unresolved name as **CRITICAL** with finding code `RC-GHOST-DISPATCH-001`, message: `Task() dispatch to non-existent agent "<name>" — runtime will silently no-op`.

## Out of scope

- Cross-plugin dependency resolution (if plugin A's skill dispatches to plugin B's agent, and plugin B is not installed, we accept that as a runtime concern, not a validator concern — unless plugin A declares plugin B as a dependency in plugin.json).
- Dynamic agent names (`Task({subagent_type: someVar})`) — best-effort only; flag as `RC-GHOST-DISPATCH-002` MINOR ("dynamic subagent_type — cannot statically verify").

## Design

### Extraction grammar

Implement in `scripts/validate_plugin_common.py` (or a new `scripts/cpv_agent_dispatch_check.py` module). The regex set (all multiline, case-sensitive):

| # | Pattern | Captures |
|---|---|---|
| 1 | `subagent_type:\s*["']([a-z][a-z0-9_-]+)["']` | YAML-quoted |
| 2 | `subagent_type:\s*([a-z][a-z0-9_-]+)\b` | YAML-bare |
| 3 | `subagent_type\s*=\s*["']([a-z][a-z0-9_-]+)["']` | Python kwarg |
| 4 | `["']subagent_type["']\s*:\s*["']([a-z][a-z0-9_-]+)["']` | JSON / JS-object |

False-positive guards:
- Skip matches inside fenced code blocks marked as `text`, `console`, `output`, or `log` (they're examples of output, not directives).
- Skip matches inside frontmatter (`---` … `---` at file start) — frontmatter is metadata, not code.
- Skip matches inside HTML comments `<!-- … -->` and Markdown link references.

### Resolution algorithm

```
For each extracted (file, line, agent_name):
    if agent_name in BUILTIN_AGENTS:
        continue  # always resolvable
    if exists("<plugin_root>/agents/<agent_name>.md"):
        continue  # in-plugin
    for cached_plugin in glob("~/.claude/plugins/cache/*/*/<latest>/agents/<agent_name>.md"):
        continue  # in installed plugin
    if exists("~/.claude/agents/<agent_name>.md"):
        continue  # user scope
    emit RC-GHOST-DISPATCH-001(file, line, agent_name)
```

`<latest>` = the highest-versioned subdir per plugin (already resolved elsewhere in CPV).

### Where the rule plugs in

| # | Validator | Hook point |
|---|---|---|
| 1 | `validate_skill_comprehensive.py` | New `validate_skill_dispatches()` called from `validate_skill()` |
| 2 | `validate_agent.py` | New `validate_agent_dispatches()` called from `validate_agent()` |
| 3 | `validate_command.py` | New `validate_command_dispatches()` called from `validate_command()` |
| 4 | `cpv-doctor-agent.md` recipe D-NEW | When mode=user_scope, also walk `~/.claude/skills/` and `~/.claude/agents/` with the same extractor |

The extraction + resolution logic lives in **one** shared helper (`scripts/cpv_dispatch_check.py`) so the three validators don't drift.

## Test plan

| # | Test file | What it checks |
|---|---|---|
| 1 | `tests/test_ghost_dispatch_extraction.py` | All 4 regex variants extract the right name; false-positive guards work |
| 2 | `tests/test_ghost_dispatch_resolution.py` | BUILTIN/in-plugin/cached-plugin/user-scope resolution paths; missing-agent fails |
| 3 | `tests/test_ghost_dispatch_e2e_skill.py` | End-to-end: a skill with a ghost dispatch produces exactly one CRITICAL with code `RC-GHOST-DISPATCH-001` |
| 4 | `tests/test_ghost_dispatch_e2e_agent.py` | Same for agent body dispatches |
| 5 | `tests/test_ghost_dispatch_e2e_command.py` | Same for command body dispatches |
| 6 | `tests/test_ghost_dispatch_fixtures.py` | Self-test: CPV's own 32 skills + 11 agents + 1 command resolve cleanly |
| 7 | `tests/test_ghost_dispatch_dynamic.py` | Dynamic `subagent_type=someVar` produces MINOR `RC-GHOST-DISPATCH-002` |

Target: 25 new tests, 0 regressions on the existing 5000+ test suite.

## Severity rationale

CRITICAL because:
- Silent failure — runtime doesn't error, the call just no-ops.
- Self-replicating — every skill copied from a project with ghost agents inherits the bug.
- Inverse-correlated with how hard it is to find — the user only notices when a downstream side-effect fails, often hours later.
- Fixable with a single Edit (rename to a real agent or remove the dispatch).

## Acceptance criteria

- [ ] Rule `RC-GHOST-DISPATCH-001` implemented in `scripts/cpv_dispatch_check.py`
- [ ] Wired into `validate_skill_comprehensive.py`, `validate_agent.py`, `validate_command.py`
- [ ] All 25 new tests pass
- [ ] CPV's own validators self-scan with 0 CRITICAL `RC-GHOST-DISPATCH-001` (i.e. CPV itself doesn't have ghost dispatches)
- [ ] Documentation: `references/finding-codes.md` updated with `RC-GHOST-DISPATCH-001` and `RC-GHOST-DISPATCH-002`

## Risks + mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | False positives on agent-name strings that aren't dispatch directives (e.g. mentioning an agent in prose) | Extraction is anchored to `subagent_type:` / `Task(subagent_type=…)` only — prose mentions don't match |
| 2 | Cross-plugin dependency: plugin A dispatches to plugin B's agent, B not installed | Out of scope (see "Out of scope") — accept that as runtime concern. Add `RC-GHOST-DISPATCH-003` MINOR later if needed |
| 3 | Built-in agents list drifts over time as Claude Code adds new ones | Keep `BUILTIN_AGENTS` in one place (`scripts/cpv_dispatch_check.py:BUILTIN_AGENTS`), comment with last-verified date, easy to update |
| 4 | Performance on large plugins | Extraction is regex on ~100KB of bodies — negligible vs existing validators |

## Approval log

- 2026-08-25T17:25:05+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED commit b7bc5273 — RC-GHOST-DISPATCH-001/002/003 in validate_xref.py (batch_aa)
