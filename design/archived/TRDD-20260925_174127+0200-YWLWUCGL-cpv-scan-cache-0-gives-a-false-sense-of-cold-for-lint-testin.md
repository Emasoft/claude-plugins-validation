---
trdd-id: YWLWUCGL
title: CPV_SCAN_CACHE=0 gives a false sense of cold for lint testing — the lint-result cache reads no env var
column: complete
status: archived
created: 2026-09-25T17:41:27+0200
updated: 2026-09-25T18:39:40+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: bugfix
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-25T17:41:27+0200
---

# CPV_SCAN_CACHE=0 gives a false sense of cold for lint testing — the lint-result cache reads no env var

SOURCE-VERIFIED 2026-09-25 (lean-worker, file:line evidence): CPV_SCAN_CACHE=0 disables ONLY the skillaudit scanner-result cache (cpv_scan_cache.py:134, sole consumer cpv_skillaudit_native.py). The LINT-RESULT cache (~/.cache/cpv/scanner-results/, cpv_scanner_cache.py ScannerCache.get :170-211) reads NO env var at all — it is bypassed only by deleting the dir. Its key self-invalidates on engine change (lint_engine_rev=_LINT_ENGINE_CODE_REV, the sha256 of cpv_lint_engine.py, folded in at cpv_lint_engine.py:2350 from :99), so classifier/engine edits are safe — but a developer following CLAUDE.md's canonical-command note ('CPV_SCAN_CACHE=0 → bypass the skillaudit result cache (MANDATORY when testing a classifier change)') can believe a lint-phase run is cold when the lint cache is serving results. CLAUDE.md does not mention the lint cache at all. Two acceptable fixes (decide ONE): (1) add an env bypass to the lint cache (e.g. honour CPV_SCAN_CACHE=0 or a dedicated CPV_LINT_CACHE=0) with a two-sided test; (2) CLAUDE.md canonical-commands gains a caveat naming the lint cache, its location, its key composition, and the only bypass (delete the dir / PLUGIN_SKIP_REPO_LINT skips the whole phase). Found while source-verifying a recorded claim on TRDD-1VU6Y5MS (review round 5, finding a).

## Approval log

- 2026-09-25T17:41:27+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
2026-09-25 DECISION RECORDED (review round 6): RECOMMENDED DEFAULT is option 2 — the CLAUDE.md canonical-commands caveat naming the lint cache (location ~/.cache/cpv/scanner-results/, key composition incl. _LINT_ENGINE_CODE_REV self-invalidation, only bypasses = delete the dir or PLUGIN_SKIP_REPO_LINT). Rationale: reversible, evidence-complete, does not touch cache semantics (option 1's new env bypass would need its own two-sided test and cache-key review). Option 1 remains the alternative if the fleet wants a real cold-lint flag. Executor: any session holding this card; a CLAUDE.md edit is load-bearing here, so follow the v5.12.0 over-delete discipline (count-assert + token-set diff). The publish.py NOTE comment (6842f58b) is the recorded sole home for the production-observation reminder — a deliberate choice, not an omission; CLAUDE.md's publish recipe was considered and declined to avoid duplicating a reminder in two prose homes.
- 2026-09-25T18:39:40+0200 — COMPLETE by main-agent@claude-plugins-validation. recommended default (option 2) executed and committed 37d21213.

## Execution log

2026-09-25: Option 2 SHIPPED — CLAUDE.md canonical-commands block now carries the lint-cache caveat (location, _LINT_ENGINE_CODE_REV key, both bypasses) at the exact spot the question is asked, plus the publish-recipe NOTE naming the next release's Gate 14 as the first live exercise of the advisory CI-RED relabel (6842f58b). Commit 37d21213; self-hashes regenerated last.

## Acceptance checklist

- [x] CLAUDE.md canonical-commands block names the lint-result cache location, its key (_LINT_ENGINE_CODE_REV), and both bypasses
- [x] Publish recipe notes next Gate 14 run = first live exercise of the advisory CI-RED relabel (6842f58b)
- [x] Self-hash manifest regenerated AFTER the CLAUDE.md edit; caveat + manifest committed together (37d21213)
