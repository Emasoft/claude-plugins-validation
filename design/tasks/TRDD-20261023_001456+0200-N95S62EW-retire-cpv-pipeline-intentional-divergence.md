---
trdd-id: N95S62EW
title: Retire cpv.pipeline.intentional_divergence
column: backburner
status: tasked
created: 2026-10-23T00:14:56+0200
updated: 2026-10-23T00:19:31+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: security
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: false
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-10-23T00:14:56+0200
parent-trdd: N4GLZXZ6
---

# Retire cpv.pipeline.intentional_divergence

## Symptom
cpv.pipeline.intentional_divergence is a plugin-declared relaxation: it downgrades pipeline drift to INFO and standardize uses it to skip files. The plugin-declared pipeline profile selector also needs review for the same reason.

## Code location
scripts/cpv_pipeline_profile.py:215-256; scripts/validate_plugin.py:7949-7962, 8145-8157, 8263; scripts/standardize_plugin.py:907-928, 953, 984, 3003, 4642.

## Proposed fix
Remove the validate_plugin severity downgrade (drift stays at its real severity). The two readers have different roles. The validate_plugin severity downgrade is a rule relaxation, so remove it. The standardize selector controls which files CPV rewrites in the author's own repo; assess it separately, because removing it may overwrite deliberate author divergence. Review the plugin-declared pipeline profile selector in scripts/cpv_pipeline_profile.py and decide whether it must also be removed.

Search words: a plugin declares a file as intentionally diverging and CPV stops reporting or rewriting it.
Line numbers as of the 5.23.0 hotfix tree.
Found during the 5.23.0 release review.
Fail toward flagging; no plugin-side exception (owner policy).

## Approval log

- 2026-10-23T00:14:56+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- Backlog record only; not approved as a work order. Owner approval required before dev.
- 2026-10-23 — Backlog record only; not approved as a work order.
