---
trdd-id: I3TZG9LN
title: CPVPPC P12 - Strictness audit
column: backburner
status: tasked
created: 2026-09-24T20:01:23+0200
updated: 2026-09-24T20:23:17+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:01:23+0200
---

# CPVPPC P12 - Strictness audit

Files: report of every escape hatch in the canon and CPV's pipeline paths (lock repos only); removals in canon templates; lock repos reject cpv.canon: none, intentional_divergence, skip env vars, non-failing smoke, NIT-not-blocking; timeout knobs raise-only. Key tests: one mutation per hatch fails verify. Done-when: the user rules on the #101 sentinel and #194 consent registry. Plan: design/specs/cpvppc-plan.md section 6, row P12. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:01:23+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Plan excerpt: threat table heading (verbatim)

### 4.3 Threat table (every row is an assertion; a violation is `FAIL`)
| # | Trick | Control |
|---|---|---|

## Plan excerpt: threat rows 1-11 (verbatim)

| 1 | Config knob that skips or weakens a gate | schema has none; a test fails if any schema property matches `skip\|allow\|ignore\|disable\|bypass\|except\|divergen\|optional`; floors on numbers |
| 2 | Repo ignore/config files hiding content (`.gitignore`, `.gitattributes export-ignore/linguist-*`, `.trufflehogignore`, `.semgrepignore`, `cpv.exclude_paths`, linter excludes, Mega-Linter `DISABLE`) | security/privacy/secret gates read none of them; they scan the assembled artifact |
| 3 | Shipped but unscanned content (outputs, bundles, assets, first-use downloads, submodules, history) | part of the assembled artifact or the release-range history scan; first-use downloads hash-pinned |
| 4 | Editing a canon-managed file | hash-locked to the render of the lock's config at the lock's canon version |
| 5 | Custom step neutering or editing the pipeline | G-CUSTOM sandbox, before assembly; re-hash after; can only add failures |
| 6 | Releasing around the pipeline (local tag, `gh release create`, `--no-verify`, another workflow with write) | only the release workflow writes, apart from the `staging-v<version>` draft of signed `local_release` binaries, which it consumes and deletes; tag rulesets; every release needs provenance from that workflow and the green release-workflow run that produced its commit |
| 7 | Weakening a step inside a workflow | workflows hash-locked; G-CONFIG rejects bypass constructs; no gate reads repo variables |
| 8 | Old or fake CPV | CPV pinned by commit SHA per canon version, installed from the official repo; vendored CPV copies fail |
| 9 | Downgrading the declared canon version | support window; a lock whose canon version decreased in history fails |
| 10 | Test tricks (`addopts`, `testpaths`, `conftest` hooks, deselection, all skipped) | adapters override config args; collected ≥ discovered; skip ratio from JUnit |
| 11 | Mutable or untrusted actions, `pull_request_target`, broad permissions | SHA pins from the canon allowlist; `pull_request_target` forbidden; `permissions: {}` default; `persist-credentials: false` |
