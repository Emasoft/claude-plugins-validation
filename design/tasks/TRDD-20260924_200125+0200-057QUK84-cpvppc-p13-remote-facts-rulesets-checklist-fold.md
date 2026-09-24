---
trdd-id: 057QUK84
title: CPVPPC P13 - Remote facts, rulesets, checklist fold
column: backburner
status: tasked
created: 2026-09-24T20:01:25+0200
updated: 2026-09-24T20:23:18+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:01:25+0200
---

# CPVPPC P13 - Remote facts, rulesets, checklist fold

Files: remote fact types; ruleset setup script; 87-item checklist folded into canon.json. Key tests: tamper tests on the throwaway repo (asset swap, ruleset disabled, bypass actor added). Done-when: each tamper caught. Plan: design/specs/cpvppc-plan.md section 6, row P13. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:01:25+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Plan excerpt: section 5 fact types (verbatim)

- Fact types (closed set, each a pure tested function): `file_present`, `file_sha_in_set`,
  `file_matches_render`, `json_path_equals`, `workflow_step`, `workflow_no_bypass`,
  `readme_section_current`, `config_valid`, `lock_consistent`, `actions_pinned`,
  `unit_covers_executables`, `artifact_privacy_clean`, `runtime_hosts_declared`,
  `generated_text_privacy_clean`; remote: `gh_ruleset_matches`, `gh_tag_exists`,
  `gh_run_success_for_commit`, `gh_secret_present`, `gh_release_assets_match_manifest`,
  `gh_provenance_verified`, `listing_matches_manifest`.

## Plan excerpt: threat table heading (verbatim)

### 4.3 Threat table (every row is an assertion; a violation is `FAIL`)
| # | Trick | Control |
|---|---|---|

## Plan excerpt: threat rows 19-20 (verbatim)

| 19 | Bot or human token as a standing bypass | the GitHub Actions app is the only bypass actor; no human token; `gh_ruleset_matches` checks the bypass list; G-CONFIG proves no other workflow can write |
| 20 | Admin disabling rulesets, deleting attestations, re-tagging | trust verdict rests on provenance + scan manifest matching the tag commit; missing provenance = `NON-COMPLIANT` |
