---
trdd-id: 22HCJULG
title: CPVPPC P14 - Agents
column: backburner
status: tasked
created: 2026-09-24T20:01:26+0200
updated: 2026-09-24T20:30:39+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:01:26+0200
---

# CPVPPC P14 - Agents

Files: cpv-the-skills-menu routes; cpv-plugin-creator-agent, cpv-plugin-fixer-agent, cpv-marketplace-fixer-agent gates (verify --remote COMPLIANT + current-CPV rescan clean + CI green); migrations migrations/<from>-<to>.py; repair flow (re-render, or stay NON-COMPLIANT). Key tests: agent contract tests; migration round-trips 0.x to 1.0. Done-when: an agent run on a fixture reaches COMPLIANT unattended. Plan: design/specs/cpvppc-plan.md section 6, row P14. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:01:26+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Plan excerpt: section 5 verdicts (verbatim)


- Verdicts: `COMPLIANT <v>` (exit 0; `(static)` without `--remote`, rejected by agent gates;
  qualifiers `unattested: N`, `locally built: N`, `unsigned`, `no provenance: private repo`, always
  printed when they apply), `NON-COMPLIANT <v>` (1), `UNKNOWN` (5), `NOT-DECLARED` (6, plus the highest
  canon version whose static assertions pass).
- Support window: the current and previous minor versions of the current canon major, plus the last
  minor of the previous major for 6 months after a new major ships; outside it, `NON-COMPLIANT`.
- The verdict is for the DECLARED version, with templated values taken from the lock (so a CPV release
  that does not change the canon changes no hash). A re-scan with the current CPV is reported as a
  separate section ("findings under CPV X"); it does not change the verdict of an already-published
  release, but agent gates require it clean before a NEW release. An older supported canon version gets
  a non-blocking "canon X.Y available" WARNING.
- Never executes target code; static checks work on uninstalled checkouts.
- `validate_plugin` / `validate_marketplace`: repo with a lock → failing assertions as MAJOR; without
  a lock → one INFO line.


