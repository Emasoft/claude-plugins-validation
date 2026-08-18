---
trdd-id: 1YNY73NT
title: canonical release tool cannot emit the Agent self-identification trailer mandated by golden rule G1.1
column: ai_review
created: 2026-08-18T20:29:38+0200
updated: 2026-08-19T00:05:00+0200
current-owner: cpv-session
task-type: feature
priority: 2
labels: [phase-2, hub-dispatch, canonical-pipeline, governance]
external-refs: [TRDD-BRRJK57P]
relevant-rules: [1]
---

# Release tool cannot emit the `Agent:` trailer (P2, CANONICAL template fix)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-08-18

**NEXT ACTION:** add `Agent:` trailer emission to the canonical `scripts/publish.py`
template — the fleet golden rule G1.1 (GitHub authorship self-identification) mandates a
one-line self-ID on every GitHub write, and the release tool has NO mechanism to emit it
(verified: zero `Agent:`/trailer hits in publish.py). Hub-verified: 21 of 22 repos affected.

## Design constraints

- Template-level: fix the canon; downstream inherits on next template sync.
- The self-ID line carries NO `@` — a bare handle pages a real GitHub account
  (github-mentions iron rule). Plain words only.
- Emission points: the release body created by `gh release create` (self-ID first line)
  and/or a git `Agent:` commit trailer on the release commit — match whatever shape G1.1
  and the hub's ledgered finding specify; confirm with the hub if ambiguous before
  implementing.
- Configurable agent identity (env/CLAUDE project identity), never hardcoded to one
  plugin's name in the shared template.

## Acceptance criteria

- [ ] A publish run produces the self-ID/`Agent:` trailer on its GitHub-visible artifacts.
- [ ] No `@`-mention can be emitted (test with a handle-shaped identity string).
- [ ] Regression test; suite green.
- [ ] The emitted canon carries the fix.

## Approval log

- 2026-08-18T20:29:38+0200 — Authored at `todo` under the USER delegation recorded in
  TRDD-BRRJK57P (hub dispatch, Phase 2).
