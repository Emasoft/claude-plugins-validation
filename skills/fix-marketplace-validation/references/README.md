# fix-marketplace-validation — References (Stub)

## Table of Contents

- [Purpose](#purpose)
- [Transition note](#transition-note)
- [Canonical fix guide locations](#canonical-fix-guide-locations)
- [Marketplace Error Index](#marketplace-error-index)
- [Marketplace Fixes](#marketplace-fixes)
- [Pipeline Fixes](#pipeline-fixes)

## Purpose

This directory will host the dedicated marketplace fix guides once the split from
`skills/fix-validation/` completes. In the current transition state the guides live
in the shared `skills/fix-validation/references/` directory and this stub records the
canonical file locations so the `marketplace-fixer` agent can locate them.

## Transition note

A later cleanup pass (a separate task, not this one) will physically move the
marketplace-scope fix guides out of `skills/fix-validation/references/` and into
`skills/fix-marketplace-validation/references/`. Until that cleanup runs, this
skill shares its detailed fix guides with the existing guides under
`skills/fix-validation/references/marketplace-fixes.md` and
`skills/fix-validation/references/marketplace-error-index.md`.

Do NOT duplicate the content here. The single source of truth during the
transition is in `skills/fix-validation/references/`.

## Canonical fix guide locations

During the transition, the marketplace-fixer agent should read from these paths:

### Marketplace Error Index

- Current location: `skills/fix-validation/references/marketplace-error-index.md`
- Eventual location: `skills/fix-marketplace-validation/references/marketplace-error-index.md`
- Content: Maps each marketplace-scope CPV validator to its fix reference guide with
  section numbers. Covers `validate_marketplace.py` (structure, plugin entries,
  source types, submodules) and `validate_marketplace_pipeline.py` (publish.py,
  cliff.toml, CI workflow, tagging, secrets). Includes the architecture / layout
  migration warning signals.

### Marketplace Fixes

- Current location: `skills/fix-validation/references/marketplace-fixes.md`
- Eventual location: `skills/fix-marketplace-validation/references/marketplace-fixes.md`
- Content: Step-by-step fixes for every error emitted by `validate_marketplace.py`:
  marketplace.json structure, plugin entry validation, source type issues, git
  submodule issues.

### Pipeline Fixes

- Current location: `skills/fix-validation/references/marketplace-fixes.md` (§5-8)
  — the pipeline sections are currently folded into the main marketplace guide.
- Eventual location: `skills/fix-marketplace-validation/references/pipeline-fixes.md`
- Content: Step-by-step fixes for `validate_marketplace_pipeline.py` — publish.py
  scaffolding, cliff.toml templates, `.github/workflows/validate.yml`, CHANGELOG.md
  generation, release ceremony, secret configuration, tag discipline.

A follow-up task will extract the pipeline sections from `marketplace-fixes.md` into
a standalone `pipeline-fixes.md` during the move.
