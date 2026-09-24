---
trdd-id: 8QWLECUZ
title: CPVPPC P11 - Privacy gate
column: backburner
status: tasked
created: 2026-09-24T20:01:22+0200
updated: 2026-09-24T20:23:15+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:01:22+0200
---

# CPVPPC P11 - Privacy gate

Files: scripts/cpvppc/privacy.py reusing CPV's path/username/email detectors. Key tests: home paths, emails, author names, undeclared hosts, private target names all fail; clean fixture passes. Done-when: fixture matrix clean. Plan: design/specs/cpvppc-plan.md section 6, row P11. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:01:22+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Plan excerpt: threat table heading (verbatim)

### 4.3 Threat table (every row is an assertion; a violation is `FAIL`)
| # | Trick | Control |
|---|---|---|

## Plan excerpt: G-PRIVACY gate row (verbatim)

| 8 | G-PRIVACY | home-directory paths (`/Users/…`, `/home/…`, `C:\Users\…`), git author names/emails of the release range, hostnames, email addresses; runtime hosts ⊆ declared; generated text (README sections, release notes, scan manifest) scanned; private targets never rendered into a public README; no allowlist from the repo | any finding |

## Plan excerpt: threat rows 22-24 (verbatim)

| 22 | Private data in shipped files or generated text | G-PRIVACY |
| 23 | Hidden telemetry or undeclared runtime network use | runtime hosts must be declared and shown in the README |
| 24 | Secrets in logs or artifacts | masked logs, no `set -x` near secrets, G-SECRETS |
