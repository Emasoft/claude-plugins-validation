---
trdd-id: e2b17a61
title: TRDD-e2b17a61-e6f0-4bff-ac9e-feb3a7cd046b — settings.json extraKnownMarketplaces validator
column: complete
updated: 2026-08-25T17:25:39+0200
---

# TRDD-e2b17a61-e6f0-4bff-ac9e-feb3a7cd046b — settings.json extraKnownMarketplaces validator

**TRDD ID:** `e2b17a61-e6f0-4bff-ac9e-feb3a7cd046b`
**Filename:** `design/tasks/TRDD-e2b17a61-e6f0-4bff-ac9e-feb3a7cd046b-settings-json-marketplace-validator.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

**Status:** Done — 2026-05-10. validate_settings_marketplace.py + constants in
cpv_validation_common.py + 24 schema tests already shipped. This pass added
strictKnownMarketplaces wiring into validate_plugin.py (was triggered only by
extraKnownMarketplaces), recognized strictKnownMarketplaces in plugin
settings.json (no false "unrecognized key" MINOR), implemented Open Question 3
(plugin-shipped scope-mismatch warnings: WARNING for extraKnownMarketplaces +
MAJOR for strictKnownMarketplaces, the latter being managed-only per
cc_scope_rules.MANAGED_ONLY_KEYS), and wired schema validation into
manage_doctor.do_doctor() so user-settings violations surface during health
checks. 9 new tests (5 settings-marketplace pipeline, 4 doctor wiring) all
passing alongside 4489 existing tests.

## Context

Claude Code v2.1.80 introduced an inline marketplace mechanism in
`settings.json` via `extraKnownMarketplaces`. A user can declare a named
marketplace inside their `settings.json` whose `source` has one of the
standard source-type values (`github`, `url`, `git-subdir`, `npm`), or
an inline `type: "settings"` that points at a plugin list defined inside
the same settings.json file.

Initially (v2.12.11) CPV tried to model this by adding `"settings"` to
`validate_marketplace.py::VALID_SOURCE_TYPES` and `SOURCE_REQUIRED_FIELDS`.
This was wrong: `validate_marketplace.py` validates `marketplace.json` files
that list per-plugin sources; `source: "settings"` lives at a different
schema level (per-marketplace, not per-plugin). The audit at
`docs_dev/cpv_audit_findings_20260412.md` (M4) reverted that change.

## Goal

Ship a dedicated validator for `settings.json → extraKnownMarketplaces`
that understands the correct schema level and does not conflate it with
per-plugin sources inside a `marketplace.json`.

## Open questions

1. Where in the CPV pipeline should this run? Probably alongside
   `validate_marketplace.py` but guarded by a filename check
   (`settings.json` / `.claude/settings.json` / `CLAUDE_CODE_SETTINGS`
   paths).
2. What exact schema does `extraKnownMarketplaces[<name>]` require? Confirm
   from the v2.1.80 release notes and `/en/settings.md` before coding.
3. Should CPV distinguish user-level vs project-level settings.json files,
   and warn if `extraKnownMarketplaces` is used in a shipped plugin's
   settings snippet?

## Deliverables

- New validator module: `scripts/validate_settings_marketplaces.py`
- Shared constants for the valid schema (VALID_SETTINGS_SOURCE_TYPES,
  SETTINGS_SOURCE_REQUIRED_FIELDS) living in `cpv_validation_common.py`
- Tests under `tests/test_validate_settings_marketplaces.py` covering:
  - valid github/url/git-subdir/npm sources
  - inline `source: "settings"` pointing at an inline plugin list
  - missing required fields per source type
  - unknown source type
- Wire into the main `cpv-doctor` / orchestrator pipeline

## Out of scope

- Touching `validate_marketplace.py`'s existing `VALID_SOURCE_TYPES` /
  `SOURCE_REQUIRED_FIELDS` — those are the per-plugin source types inside
  a marketplace.json file and must stay stable.

## Approval log

- 2026-08-25T17:25:39+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED — validate_settings_marketplace.py live and wired (batch_aj)
