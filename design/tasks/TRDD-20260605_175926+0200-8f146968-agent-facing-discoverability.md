---
trdd-id: 8f146968-9164-4e4a-affe-73988fea88a3
title: CPV agent-facing discoverability — universal à la carte skills menu + cpv router agent + README catalog
column: complete
created: 2026-06-05T17:59:26+0200
updated: 2026-06-05T17:59:26+0200
current-owner: cpv-spark
assignee: cpv-spark
priority: 3
severity: LOW
effort: M
labels: [discoverability, agents, skills, menu, docs]
task-type: docs
artifact-kinds: []
parent-trdd: null
npt: []
eht: []
blocked-by: []
supersedes: []
superseded-by: []
pre-block-column: null
relevant-rules: []
release-via: none
delivery: direct-push
target-branch: main
feature-branch: null
merge-strategy: squash
must-pass-tests-before-merge: true
publish-target: null
publish-channel: null
deploy-target: null
soak-duration: null
test-requirements: [lint]
audit-requirements: []
review-requirements: [code-review]
fixtures: []
required-credentials: []
runtime-targets: [macos, linux]
docker-image: null
impacts: []
migration-direction: null
attempts: 1
test-failures: 0
last-test-result: pass
last-test-at: 2026-06-05T17:59:26+0200
implementation-commits: []
pr-url: null
ci-runs: []
published-version: null
published-at: null
live-since: null
audit-trigger: null
audit-target: null
audit-evidence: []
audit-conclusion: null
external-refs: []
---

# TRDD-8f146968 — CPV agent-facing discoverability

**Filename:** `design/tasks/TRDD-20260605_175926+0200-8f146968-agent-facing-discoverability.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-06-05

- **Current state:** DONE this session. All three changes implemented and validated.
  - `skills/the-skills-menu/SKILL.md` flipped to `user-invocable: true`, rewritten as the universal agent-facing router. Validated 100/100.
  - `agents/cpv.md` NEW general-purpose router agent. Validated 100/100.
  - `README.md` top callout + "Features at a glance" capability catalog added; stale dev-counts corrected.
- **NEXT ACTION:** none — work complete. This TRDD is the record. Do NOT revert the change described in "KEY INVARIANT".
- **KEY INVARIANT (do NOT revert):** `the-skills-menu` is now **INTENTIONALLY** `user-invocable: true`. This is a deliberate evolution of the v2.90.0 menu-unification (TRDD-c50531c2), NOT drift. Do not restore `user-invocable: false`.
- **Durable artifacts to read before acting:**
  - `skills/the-skills-menu/SKILL.md` — the universal agent-facing à la carte router.
  - `agents/cpv.md` — the general-purpose router agent.
  - `README.md` — the human-facing capability catalog.

## What changed (this session)

This TRDD documents an already-completed change whose purpose is to make
**every CPV feature discoverable by any Claude instance**, not just by
the human operator who knows the command names. Three edits:

### 1. `skills/the-skills-menu/SKILL.md` — universal agent-facing "à la carte" router

Flipped from `user-invocable: false` to `user-invocable: true` and
rewritten into CPV's UNIVERSAL AGENT-FACING router — the agent
counterpart to the human-facing `/cpv-main-menu` (which is a rendered
Stop-hook menu). It now carries:

- an **auto-triggering description** (triggers on phrases like "read the
  CPV skills menu" / "use CPV to <task>");
- an **Intent → Action routing table** mapping every plugin-quality
  intent to the right agent/skill/script;
- a **"Scripts à la carte"** list of every validator alias;
- an **Agents table**;
- **autonomous-routing instructions**.

It still serves as the runtime catalog that CPV agents consult
(TRDD-478d9687). Validated 100/100.

### 2. NEW `agents/cpv.md` — general-purpose router agent

A new general-purpose router agent that:

- receives a free-form plugin-quality request;
- loads `the-skills-menu`;
- classifies intent;
- autonomously routes + executes — validate / security-scan /
  fix-via-plugin-fixer-**never-hand-edit** / cache-optimize / create /
  publish+marketplace / migrate / manage / semantic-grade;
- chains steps and prefers `/cpv-batch-*` for fleets.

Frontmatter: `skills: [the-skills-menu]`, **no model pin** (CA-04
cache-warmth), **no First Contact section**. Validated 100/100.

### 3. `README.md` — capability catalog

Added a top callout + a "Features at a glance" capability catalog so
every feature (security scanner, publishing, cache optimization, the
fixer agent) is discoverable; corrected stale dev-counts.

## WHY (verbatim — capture the reported problem)

> users reported that other Claude instances couldn't discover CPV's
> features — they didn't know how to run the security scanner, how to
> make CPV publish a plugin to GitHub / add it to a marketplace, that a
> cache-optimization feature existed, and they kept fixing issues by
> hand instead of dispatching the fixer agent. This change makes the
> plugin offer all its skills and scripts "à la carte" and gives one
> universal instruction that works for every Claude: "read the CPV
> skills menu and use whatever you need."

## KEY INVARIANT (so a future agent does NOT revert it)

`the-skills-menu` is now **INTENTIONALLY** `user-invocable: true` — this
is a deliberate evolution of the v2.90.0 menu-unification
(TRDD-c50531c2), **NOT drift**. The v2.90 invariant "only
`/cpv-main-menu` is a user-visible **COMMAND**" is **unchanged** — no
new command was added; `the-skills-menu` is a user-invocable **SKILL**,
like the `cpv-batch-*` family. **Do not restore
`user-invocable: false`.**

## Verification

- `skills/the-skills-menu/SKILL.md` — validated 100/100.
- `agents/cpv.md` — validated 100/100.
- `README.md` — top callout + capability catalog added; stale dev-counts
  corrected.

## Relationship to prior TRDDs

- **TRDD-c50531c2** (Menu Unification v2.90.0) — this change is its
  deliberate evolution, not a regression of it. The single-visible-
  command invariant is preserved.
- **TRDD-478d9687** — `the-skills-menu` continues to serve as the runtime
  catalog CPV agents consult; that role is retained and extended.
