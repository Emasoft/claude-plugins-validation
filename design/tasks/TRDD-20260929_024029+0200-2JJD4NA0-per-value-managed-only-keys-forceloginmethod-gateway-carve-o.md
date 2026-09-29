---
trdd-id: 2JJD4NA0
title: Per-value managed-only keys (forceLoginMethod gateway carve-out) + T13 anomaly
column: backburner
status: tasked
created: 2026-09-29T02:40:29+0200
updated: 2026-09-29T02:43:51+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-29T02:40:29+0200
---

# Per-value managed-only keys (forceLoginMethod gateway carve-out) + T13 anomaly

## Approval log

- 2026-09-29T02:40:29+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Problem

MANAGED_ONLY_KEYS is per-key; CC's forceLoginMethod scope is PER-VALUE: only the gateway value is managed-enforced (settings-reference.md scope row + body, managed-settings.md:59), while claudeai/console are honored from any file. Wholesale removal (v2.1.284 sync, ea464276) opened a hole (project gateway silently accepted); wholesale keeping false-MAJORs the other two values. Re-added wholesale post-review R8 (ambiguous-KEEP direction); the per-value refinement needs its own rule shape.

## Also owed

T13: allowClaudeInChromeWithManagedMcp (added v2.1.282-283 on changelog authority) is absent from ALL current doc pages fetched 2026-09-29 — either docs dropped/renamed it post-changelog or it lives on an unfetched page. Decide keep (over-warn, safe) vs remove (risk hole) after fetching the claude-in-chrome page. Review R8 also noted: the 3 stale pins were deleted rather than renamed-and-inverted (GAP-33 precedent); restore inverted pins if the card's fix touches them.

## Known live trade (R9)

Until the per-value rule lands, the wholesale re-add MAJORs forceLoginMethod:claudeai/console from a project file — a mainstream config CC honors. Documented in-test (test_readded_key_known_false_major_documented): when the per-value rule ships, flip exactly that assertion. Note: the sibling membership in KNOWN_SETTINGS_KEYS is CORRECT (typo detector; semantics via MANAGED_ONLY_KEYS) — R9 Q2 resolved by reading the set's consumers, not by edit.
