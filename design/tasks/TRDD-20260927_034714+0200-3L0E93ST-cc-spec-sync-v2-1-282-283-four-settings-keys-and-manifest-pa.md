---
trdd-id: 3L0E93ST
title: CC spec sync v2.1.282-283 four settings keys and manifest path-field existence check
column: todo
status: tasked
created: 2026-09-27T03:47:14+0200
updated: 2026-09-27T03:56:35+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-27T03:47:14+0200
---

# CC spec sync v2.1.282-283 four settings keys and manifest path-field existence check

## Approval log

- 2026-09-27T03:47:14+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- 2026-09-27T03:48:05+0200 — column → todo. Verified actionable spec drift from the 2026-09-27 scan; user directive was to update the codebase to CC changelog

## Notes

CONTEXT (2026-09-27, minted from the verified spec-drift scan at reports/cc-spec-sync/20260927_034152+0200-changelog-sync-after-v2.1.281.md): CC shipped v2.1.282 (2026-09-24) and v2.1.283 (2026-09-25) after CPV's v5.19.0 baseline of v2.1.281. The scan set-diffed all 29 changelog items against CPV's constants; 26 verified NOT-SPEC-RELEVANT or already-covered, five are actionable. CLAIMS SPOT-VERIFIED FIRST-HAND by the main agent: the four keys grep to 0 hits in scripts/cc_scope_rules.py while sibling availableModels is present (1 hit); the path-field block at validate_plugin.py:1583-1660 contains no exists() call; and all four key names plus the 2.1.283 path-field fix sentence appear in the raw changelog at code.claude.com/docs/en/changelog.md (857 KB fetched). WORK ITEMS, all allowlist-widening FP-reduction or parity alignment — no rule suppressed, no gate relaxed: (A) add maxProseWidth to KNOWN_SETTINGS_KEYS (v2.1.282, cosmetic UI setting — a settings file using it currently draws an unknown-key warning); (B) add allowClaudeInChromeWithManagedMcp to KNOWN_SETTINGS_KEYS + MANAGED_ONLY_KEYS (v2.1.282, managed-only); (C) add availableModelsMatch to KNOWN_SETTINGS_KEYS + MANAGED_ONLY_KEYS (v2.1.283, managed-only, sibling of already-known availableModels — exact mode constrains an availableModels entry to the named version); (D) add deniedModels to KNOWN_SETTINGS_KEYS + MANAGED_ONLY_KEYS (v2.1.283, managed-only); (E) the MAJOR item — CC 2.1.283's claude plugin validate now REJECTS plugins whose outputStyles/themes/monitors/lspServers manifest paths are missing or point outside the plugin directory, but CPV's path-field block (validate_plugin.py:1583-1660) checks only ./ prefix and .. traversal with NO existence check, so CPV passes plugins CC itself now fails — add existence validation to the path_fields loop mirroring CC's tightened validator, with a decision to record on the deviation policy: CC treats missing-dir as a hard fail, CPV should match (parity with CC's own validator is the documented sync rule) and each new finding needs its two-sided test (missing dir fires, present dir and an ./-prefixed valid path stay clean). Acceptance: two-sided tests per item, ruff clean, full tirith-independent slice of the suite green, self-hash manifest regen'd last, CLAUDE.md spec-sync paragraph updated, release via the normal publish gate.
SCOPE REFINEMENT (2026-09-27, review round 3): item E's 'NO existence check' was verified only for the validate_plugin.py:1583-1660 path-field BLOCK (sed-scoped grep: 0 exists() calls); repo-wide absence was NOT established — the implementer must grep the whole manifest-validation surface for existing existence checks before adding one, and record what they find. Also: the parity-severity ruling embedded at mint ('CPV should match CC's hard fail') is a design decision to CONFIRM against the sync policy during implementation, not a pre-approved transcription. Update on the board snapshot (measured 2026-09-27): the CLAUDE.md 'Six board cards' line was stale — RU0POO65/3T170X2M/21ES7XEX-class cards completed or archived; the live todo set at mint was TRDD-8Z7QGYHU, TRDD-DLMX817H (open, defense-in-depth items), and this card.
