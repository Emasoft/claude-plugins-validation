---
trdd-id: 7KS7KP7U
title: Resolve the agent to skill closure and validate every reachable skill
column: dev
created: 2026-07-30T11:55:20+0200
updated: 2026-07-30T11:55:20+0200
current-owner: cpv-main-session
task-type: feature
approval-tier: 0
eht: [06JG1XC9, XUNZQ70I]
relevant-rules: []
---

# Resolve the agent to skill closure and validate every reachable skill

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-07-30

- **Design:** `design/specs/agent-closure-and-variants.md` §1–§3 is NORMATIVE.
  Signatures are pinned there verbatim because T2/T3/T4 import this surface
  concurrently. Read the spec before touching code.
- **NEXT ACTION:** implement `scripts/cpv_agent_closure.py` exactly to spec §2,
  then wire AC1–AC4 into `validate_agent.py` per spec §3.
- **State:** spec written; probe fixture proving the gap lives at
  `<scratchpad>/probe/` (rebuildable from the table below).

## The verified gap

Probed against v3.24.0 on a purpose-built fixture — not inferred:

| Probe | CPV today |
|---|---|
| agent with `skills: [real-skill, totally-nonexistent-skill-xyz]` | **0 findings**, 100/100, PASSED |
| the same fixture through `validate_xref` on the whole plugin | **0 findings** |
| body `Skill({skill: "nonexistent"})` with `tools: Read, Grep` | one WARNING (never blocks); the name is never resolved |

**Cause:** `validate_agent.py` is a SINGLE-FILE validator with no plugin root, so
it structurally cannot resolve a skill name; `validate_xref.py` matches only
`skills/<name>` PATH shapes and blanks frontmatter out of its body scan. So an
agent's `skills:` list is existence-checked by **nothing**. A preload that
silently does nothing ships at 100/100 — the v3.18.0 D1–D6 false-negative family.

## Scope

`scripts/cpv_agent_closure.py` (new, the SSOT) + closure findings AC1–AC4 and the
`--skills-root` / `--closure` / `--closure-ambient` flags in `validate_agent.py`.

## The load-bearing correctness point

`skills:` frontmatter is a **pre-load hint, not an ACL**, so "skills accessible to
an agent" is not the `skills:` list. Reachability is TOOL-GATED on the `Skill`
grant: no `tools:` field → inherits all → gate open; `tools:` without `Skill` →
every runtime invocation in that body is DEAD and the preloads are the agent's
only skill access. Getting this backwards would flag the CORRECT dynamic-router
pattern as a defect (the v3.18.0 lesson: a runtime `Skill()` load is right, not a
cache hazard).

## Severity discipline

WARNING is the ONLY non-blocking tier under `--strict`, so every advisory is
WARNING. A MAJOR requires the **non-vacuity guard**: at least one OTHER named
skill of the same agent must have resolved, proving the roots are correct.
Without that proof "this skill does not exist" would be a fabricated finding on a
single-file or moved-plugin scan, so it degrades to WARNING. AC3 escalates only
when the named skill RESOLVES — resolution is what distinguishes a real
invocation from prose.

## Pass criteria

- `cpv_agent_closure.py` matches spec §2 signatures verbatim; cycle-safe,
  depth-bounded, fence-aware, namespace-aware, fail-safe on I/O.
- AC1–AC4 fire on the probe fixture and stay silent on its legitimate sibling
  (two-sided, every case).
- Non-vacuity guard proven: same fixture with `--skills-root` pointing nowhere
  yields WARNING, never MAJOR.
- No second copy of the tool-token normalizer, the fence tracker, or the
  `Skill({skill:...})` grammar — reuse or promote-and-import.
- `ruff` + `mypy` clean; cold strict self-validate 0/0/0/0.

## Approval log

- 2026-07-30T11:55:20+0200 — Tier 0, authored directly as authorized work: the
  USER directed this feature explicitly ("make the plugin able to check agents
  and all skills accessible to each agent").
