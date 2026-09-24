---
trdd-id: 2O73MIAL
title: CPVPPC P10 - README sections
column: backburner
status: tasked
created: 2026-09-24T20:01:10+0200
updated: 2026-09-24T20:23:14+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:01:10+0200
---

# CPVPPC P10 - README sections

Files: plugin section renderer + --check; marketplace canon line. Key tests: section current after release; hand edit fails; private target not rendered publicly. Done-when: both READMEs regenerate. Plan: design/specs/cpvppc-plan.md section 6, row P10. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:01:10+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Plan excerpt: section 3.1 row 17 docs (verbatim)

| 17 | `docs` | README sections (`install`, `platforms`, `components`, `badges`, `network`, `unattested`) | |

## Plan excerpt: G-RELEASE gate row (verbatim)

| 11 | G-RELEASE (release) | the workflow commits the version bump + `bin/` (if `committed-bin`) + manifest + README sections as ONE release commit whose tree equals the scanned artifact, creates both tags, creates the GitHub release, uploads assets, sends notifications; idempotent on re-run | any error |

## Plan excerpt: threat table heading (verbatim)

### 4.3 Threat table (every row is an assertion; a violation is `FAIL`)
| # | Trick | Control |
|---|---|---|

## Plan excerpt: threat row 21 (verbatim)

| 21 | Shrinking scope in the config | allowed, never silent: verify and release notes report "scope reduced" with the diff |
