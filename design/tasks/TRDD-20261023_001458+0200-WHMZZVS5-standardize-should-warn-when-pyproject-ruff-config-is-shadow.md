---
trdd-id: WHMZZVS5
title: standardize should warn when pyproject ruff config is shadowed by .ruff.toml
column: backburner
status: tasked
created: 2026-10-23T00:14:58+0200
updated: 2026-10-23T00:19:32+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: bugfix
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: false
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-10-23T00:14:58+0200
parent-trdd: N4GLZXZ6
---

# standardize should warn when pyproject ruff config is shadowed by .ruff.toml

## Symptom
When pyproject.toml has [tool.ruff*] tables and standardize adds .ruff.toml, the adopter's ruff config becomes inert silently. Also the pre-push extend-include is vacuous because the hook is POSIX sh.

## Code location
scripts/standardize_plugin.py:66 (.ruff.toml canonical), :260 ([tool.ruff] marker), :792 and :824 (gen_ruff_toml); the pre-push hook extend-include generation in the same file.

## Proposed fix
Emit a WARN when pyproject.toml contains [tool.ruff*] that the new .ruff.toml shadows; remove or fix the vacuous extend-include.

Search words: standardize adds .ruff.toml and my pyproject ruff settings stop being used, with no warning.
Line numbers as of the 5.23.0 hotfix tree.
Found during the 5.23.0 release review.

## Approval log

- 2026-10-23T00:14:58+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- Backlog record only; not approved as a work order. Owner approval required before dev.
- 2026-10-23 — Backlog record only; not approved as a work order.
