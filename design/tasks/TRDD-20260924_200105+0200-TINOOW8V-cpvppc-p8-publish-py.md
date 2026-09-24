---
trdd-id: TINOOW8V
title: CPVPPC P8 - publish.py
column: backburner
status: tasked
created: 2026-09-24T20:01:05+0200
updated: 2026-09-24T20:26:18+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:01:05+0200
---

# CPVPPC P8 - publish.py

Files: templates/0.x/plugin/publish.py. Key tests: local preflight = gates 1-9; triggers the release workflow via gh workflow run and watches it; no stamp files, no process-tree checks, no tag pushes. Done-when: preflight on the fixture matrix; dry-run dispatch. Plan: design/specs/cpvppc-plan.md section 6, row P8. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:01:05+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

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

## Plan excerpt: threat row 6 (verbatim)

| 6 | Releasing around the pipeline (local tag, `gh release create`, `--no-verify`, another workflow with write) | only the release workflow writes, apart from the `staging-v<version>` draft of signed `local_release` binaries, which it consumes and deletes; tag rulesets; every release needs provenance from that workflow and the green release-workflow run that produced its commit |
