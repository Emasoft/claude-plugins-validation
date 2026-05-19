---
trdd-id: 14cc93a6-c42a-412e-8e89-7c250faf4236
title: Decouple skills from agents — runtime routing via the Skill tool
status: in-progress
created: 2026-05-19T13:07:40+0200
updated: 2026-05-19T13:07:40+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-14cc93a6 — Decouple skills from agents; all skills available to all agents; runtime routing

**Filename:** `design/tasks/TRDD-20260519_130740+0200-14cc93a6-decouple-skills-runtime-routing.md`
**Tracked in:** this repo (`design/tasks/` is git-tracked)

## Origin (provenance)

User feedback 2026-05-19 immediately after the v2.91.0 batch-fix launch:

> "you must do an even more radical thing: decouple the skills from the
> agents. Make all skills available to all agents. And let the agents
> pick the skills they need according to the situation or the things
> the user asks. for example if the agent sees that the report saved by
> the validation script contains more than 100 issues, it must pick the
> batch-fix skill. instead if the reports is <100 items, it will pick
> the normal-fix skill."

This subsumes the simpler "auto-routing for big plugins" fix discussed
in the same exchange — the architectural change is the right level to
solve it.

## Problem statement

Current architecture binds each skill to a fixed list of consumer
agents via the agent's frontmatter ``skills:`` field. Consequences:

| # | Symptom | Root cause |
|---|---------|------------|
| 1 | An agent confronted with a situation outside its declared skill list cannot adapt — it tries to fit the work into a skill that's wrong for the case | Tight coupling between agent and skill set |
| 2 | The decision "which skill to use" is made at plugin authoring time, not at runtime — agents can't react to evidence | Skill selection is static |
| 3 | The user's example fails today: a plugin-fixer invoked on a 300+-finding plugin enters the normal fix loop and dies silently mid-way, even though `batch-fix-protocol` exists and would have been the right pick | No runtime routing logic |
| 4 | Adding a new skill requires hand-wiring it into the loader agent's frontmatter | Lots of churn for cross-cutting capabilities |

## Goal

Move to a **decoupled skill library** model:

1. Skills are global resources — any agent can invoke any skill via the
   `Skill` tool at runtime.
2. Agents are decision-makers — their bodies document a "Phase 0:
   Situation triage" step that picks the right skill (or skills) based
   on evidence.
3. The `skills:` frontmatter list, when present, is **purely a hint to
   the harness for skill preloading** (not an ACL). Agents are free to
   invoke skills outside the list. Most agents will keep an empty
   `skills:` list and document available skills in their body's
   "Skills used" section.
4. The orphan-detection test (`test_every_skill_is_loaded_by_at_least_one_agent`)
   gains a third scan path: agent BODIES (in addition to agent
   frontmatter lists + command bodies).

## Out of scope

| # | Item | Reason |
|---|------|--------|
| 1 | Removing the `skills:` frontmatter field entirely from Claude Code | That's a CC harness change, not a plugin change |
| 2 | Removing skill preloading | Some skills are used on every turn — keeping them preloaded is genuinely faster; we make preloading optional, not forbidden |
| 3 | Renaming any skill | Out of scope; this is a structural refactor |
| 4 | Cross-plugin skill invocation (e.g. plugin-fixer in another plugin invoking CPV's fix-validation) | Already supported via fully-qualified names; not part of this TRDD |

## Design

### The two-path routing pattern

Every agent's body gains a **Phase 0: Situation triage** section
documenting:

```
1. Read the dispatch context (mode, target_path, etc.)
2. Gather the minimum evidence needed to decide which skill to use:
   - For plugin-fixer: run validate_plugin --json, count findings,
     classify by severity
   - For doctor: same, plus run the design-correctness recipes
   - For other agents: domain-specific evidence
3. Apply the routing table (documented in the agent body) to pick
   a skill — or refuse with an explicit [BLOCKED] message
4. Invoke the chosen skill via the Skill tool
5. Follow the skill body's instructions for the rest of the run
```

### Concrete routing tables

| Agent | Situation | Skill to invoke (via `Skill({skill: "claude-plugins-validation:<name>"})`) |
|-------|-----------|------------------------------------------------------------------------------|
| `plugin-fixer` | Validate report has 0 findings | (no skill — return `[DONE] clean` immediately) |
| `plugin-fixer` | Report has ≤ safe-ceiling findings | `fix-validation` (existing) |
| `plugin-fixer` | Report has > safe-ceiling findings | `batch-fix-protocol`, then exit with `[BATCH_REQUIRED]` so the orchestrator dispatches `/cpv-batch-fix` |
| `plugin-fixer` | Mode is `batch_shard` | `batch-fix-protocol` for schema, `fix-validation` for per-finding fixes |
| `plugin-fixer` | Mode is canonical-pipeline migration | `canonical-pipeline` |
| `cpv-doctor-agent` | Findings ≤ safe-ceiling | `plugin-validation-skill` for diagnostics |
| `cpv-doctor-agent` | Findings > safe-ceiling | Same as above, BUT return line gets the `— recommend-batch-fix` token so the orchestrator surfaces a batch-fix action in its post-scan menu |
| `marketplace-fixer` | Marketplace findings | `fix-marketplace-validation`, `marketplace-authoring-contract` |
| `plugin-creator` | New plugin | `create-plugin`, `standardize-plugin`, `canonical-pipeline`, `setup-plugin-repo` |
| `plugin-manager` | Install / configure / list | `plugin-management` |
| `cache-optimizer-agent` | Cache pattern audit | `cache-validation-skill` |
| `semantic-validator` | Semantic checks | `semantic-validation-skill` |

The "safe-ceiling" is per-model: opus/sonnet bare ≈ 30-40 findings;
opus[1m] / sonnet[1m] ≈ 100-150. The agent body documents how to
compute it from its declared `model:` frontmatter.

### Orphan-detection test update

The current test scans:

1. Agent frontmatter `skills:` lists
2. Command bodies for `skill: "claude-plugins-validation:<name>"` invocations

Extend it to also scan:

3. **Agent BODIES** for `Skill({skill: "claude-plugins-validation:<name>"})` invocations
4. Optionally — **other skills' bodies** for cross-references (only via the same fully-qualified marker)

A skill is "loaded" if it appears in ANY of these four paths.

### `skills:` frontmatter semantics — clarified

| Status | What it means |
|--------|---------------|
| `skills:` present + non-empty | "Pre-load these — they're used on essentially every run of this agent" |
| `skills:` present + empty | "No pre-loading; everything via Skill tool on demand" |
| `skills:` absent | Same as empty — defaults to no pre-loading |

The agent body's "Phase 0 triage" section is authoritative for which
skill actually gets used. Pre-loading is a performance hint that the
harness MAY honour.

## Phases

| # | Phase | Deliverables |
|---|-------|--------------|
| 1 | Test infrastructure | Extend `test_every_skill_is_loaded_by_at_least_one_agent` to scan agent BODIES for fully-qualified Skill calls |
| 2 | Plugin-fixer rewrite | Add Phase 0 triage; document routing table; add `[BATCH_REQUIRED]` exit path; keep current behaviour for small plugins |
| 3 | Doctor rewrite | Big-plugin handoff already emits `recommend-batch-fix` token (v2.91.0); add explicit Skill-tool routing in doctor body |
| 4 | Menu-tree Fix-leaf | Update `cpv-main-menu-skill` so the Fix leaf does a quick validate → count → route between `plugin-fixer` and `/cpv-batch-fix` |
| 5 | Other agents | Add (minimal) Phase 0 triage where it adds value; remove ACL-style `skills:` declarations |
| 6 | Documentation | Update MEMORY.md, README, CLAUDE.md hints about the new decoupled pattern |
| 7 | Tests | Add `tests/test_decoupled_skill_routing.py` with regression locks for the routing tables |
| 8 | Ship | v2.91.1, full publish pipeline |

## Test plan

| # | Test file | What it pins |
|---|-----------|--------------|
| 1 | `test_consolidation_v211.py` (extended) | Orphan-detection scans agent bodies too |
| 2 | `tests/test_decoupled_skill_routing.py` (new) | Each agent's body contains a "Phase 0" triage section and a routing table |
| 3 | `tests/test_decoupled_skill_routing.py` | plugin-fixer body documents the `[BATCH_REQUIRED]` exit path and the finding-count threshold derivation |
| 4 | `tests/test_decoupled_skill_routing.py` | cpv-doctor-agent body documents the `recommend-batch-fix` token AND uses Skill tool to invoke plugin-validation-skill |
| 5 | `tests/test_decoupled_skill_routing.py` | The menu-tree Fix-leaf has a quick-validate-then-route recipe |
| 6 | `test_batch_fix_v291.py` (existing) | Continues to pass — new architecture doesn't break v2.91.0 batch protocol |

Target: 10-15 new tests + adjusted existing tests. Total suite still
green (5373+ → ~5390).

## Severity rationale

MAJOR architectural shift — touches every CPV agent and the test
infrastructure. Not a bug fix; user-driven design improvement.

## Acceptance criteria

- [ ] `test_every_skill_is_loaded_by_at_least_one_agent` scans agent bodies
- [ ] `plugin-fixer.md` has Phase 0 triage + routing table + `[BATCH_REQUIRED]` exit path
- [ ] `cpv-doctor-agent.md` has Phase 0 triage + `recommend-batch-fix` token emission
- [ ] `cpv-main-menu-skill/references/menu-tree.md` Fix-leaf auto-routes between single-agent + batch
- [ ] All 5373+ tests still green; +10-15 new tests pinning the routing
- [ ] Self-scan 0/0/0/0/0
- [ ] Documentation updated
- [ ] Shipped as v2.91.1

## Risks + mitigations

| # | Risk | Mitigation |
|---|------|------------|
| 1 | Removing the `skills:` list breaks skill preloading in the harness | Keep `skills:` field semantics as a hint (per "skills frontmatter semantics" table) — agents can still preload by listing skills there |
| 2 | Phase 0 triage adds turn-cost overhead to every agent run | Triage is one validate call (already part of every fix loop) + one in-memory count — negligible cost |
| 3 | Agents make wrong skill choices at runtime | Routing tables are explicit and tested via the new regression-lock test file |
| 4 | The "all skills available" claim is technically untrue if the harness's `Skill` tool only allows declared skills | Empirically the Skill tool allows any installed skill — verify via a smoke test; if false, fall back to explicit `skills:` listings with the broadest possible set |
