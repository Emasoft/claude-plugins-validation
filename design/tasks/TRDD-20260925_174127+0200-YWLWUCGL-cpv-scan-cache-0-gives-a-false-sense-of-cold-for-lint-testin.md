---
trdd-id: YWLWUCGL
title: CPV_SCAN_CACHE=0 gives a false sense of cold for lint testing — the lint-result cache reads no env var
column: todo
status: tasked
created: 2026-09-25T17:41:27+0200
updated: 2026-09-25T17:41:27+0200
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
