---
trdd-id: RBPHVYR5
title: CPVPPC P5 - Notify job
column: backburner
status: tasked
created: 2026-09-24T20:00:46+0200
updated: 2026-09-24T20:26:15+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:00:46+0200
---

# CPVPPC P5 - Notify job

Files: notify as a job template included in the release workflow (not a separate workflow, since token pushes trigger none); migrator in standardize_plugin.py removing the old separate notify-marketplace.yml. Key tests: one job per target, fail-fast: false; payload entry_name with plugin.json name fallback; receiver accepts both. Done-when: D9 regression - repo name != entry name updates the listing. Plan: design/specs/cpvppc-plan.md section 6, row P5. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:00:46+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Plan excerpt: section 3.1 row 15 targets.marketplaces (verbatim)

| 15 | `targets.marketplaces[]` | `{owner, repo, entry_name, visibility: public (default) \| private}`; may be empty | one notification and one remote check per target |

## Plan excerpt: section 4.1 gate-sequence intro (verbatim)
### 4.1 Gate sequence
CI runs gates 1-9 on every push and PR (every declared target builds as a dry run: "test parity").
The release workflow (`workflow_dispatch`, triggered by `publish.py`) first runs step 0 — version bump
in every declared version file, the project's OWN version inside lockfiles only (e.g. `cargo update -p
<self> --offline`, `uv lock` for the root version), CHANGELOG by git-cliff, README sections,
`bin/manifest.json` — in its workspace; G-CONFIG asserts the step-0 diff touches only version fields
(a dependency change fails, so `--locked` / `npm ci` / `--frozen-lockfile` keep working); then gates
1-12 run on exactly that tree, and G-RELEASE commits exactly that tree. The latest-CPV re-scan makes a
release depend on the newest rules: a retry of the same release may need fixes if CPV changed. Local `publish.py` runs 1-9 as an advisory preflight
(`preflight.local`). Because pushes made with the workflow token trigger no other workflow, the
"green run" for a release commit is the release workflow run that produced it, and marketplace
notification is a job inside the release workflow (cross-repo dispatch uses `MARKETPLACE_PAT`).

## Plan excerpt: threat table heading (verbatim)

### 4.3 Threat table (every row is an assertion; a violation is `FAIL`)
| # | Trick | Control |
|---|---|---|

## Plan excerpt: threat row 12 (verbatim)

| 12 | Marketplace update silently doing nothing | receiver fails on an unknown plugin; remote listing check |
