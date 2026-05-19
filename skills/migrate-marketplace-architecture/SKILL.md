---
name: migrate-marketplace-architecture
description: >
  Convert a non-CPV marketplace between Layout A (hub-and-spoke), Layout B
  (nested-with-discipline), and Layout C (marketplace-in-plugin self-referential).
  Use when migrating marketplace architecture. Used dynamically via skills-index (TRDD-478d9687).
tags:
  - marketplace
  - migration
  - architecture
allowed-tools: Read, Write, Edit, Bash(git:*,gh:*,uv:*,jq:*), Glob, Grep, AskUserQuestion
user-invocable: false
---

# Migrate Marketplace Architecture

## Overview

Converts a non-CPV marketplace into one of three CPV layouts: A (hub-and-spoke), B (nested single-repo), or C (marketplace-in-plugin self-referential). Preserves per-plugin git history and logs every decision.

## Prerequisites

- `gh` authenticated (BLOCKER for Layout A); `git` >= 2.34 (for subtree split); clean working tree; `uv` on PATH

## Instructions

Architectural migration with `AskUserQuestion` interaction. Loaded only on an `architecture` finding or explicit user request.

1. **Pre-migration audit** — [pre-migration-audit](references/pre-migration-audit.md). Verdict must be READY.
2. **Interrogate** — [interrogation-playbook](references/interrogation-playbook.md). Target layout is the first question.
3. **Execute**:
   - **A** — [layout-a-migration](references/layout-a-migration.md)
   - **B** — [layout-b-discipline](references/layout-b-discipline.md)
   - **C** — [layout-c-migration](references/layout-c-migration.md)
4. **Wire auto-notification (A only)** — `setup-marketplace-auto-notification`. B and C tag atomically.
5. **Verify** with `validate_marketplace.py --strict` and `validate_plugin.py --strict`.
6. **Write the migration log**.

Copy this checklist and track your progress:

- [ ] Pre-migration audit READY
- [ ] Interrogation answers recorded
- [ ] Target layout selected (A, B, or C)
- [ ] Layout migration executed
- [ ] Auto-notification wired for each new plugin repo (Layout A only — B and C tag atomically)
- [ ] `validate_marketplace.py --strict` passes
- [ ] Every plugin validates
- [ ] Migration log written

## Output

- **A**: N plugin repos + cleaned marketplace (github sources, tagged), each wired for auto-notify.
- **B**: same repo with `publish.py`, `cliff.toml`, CI, `CHANGELOG.md`, atomic commit tagged.
- **C**: same repo gains the second manifest with self-entry (`source: "./"`), name+version aligned, `publish.py` bumps both atomically.
- Migration log at `$MAIN_ROOT/reports/migrate-marketplace-architecture/<ts±tz>-<slug>.md`

## Error Handling

| Error | Resolution |
|-------|------------|
| `gh auth status` fails | BLOCKER (Layout A) — `gh auth login`, re-audit |
| Working tree dirty | BLOCKER — commit or stash, re-audit |
| Name collision on GitHub | `AskUserQuestion`: rename, skip, or abort |
| User cancels mid-flow | Write partial log, exit cleanly |
| Validation INVALID | Fix each finding, re-run the validator |

Never force-push or rewrite history. Forward-only commits.

## Examples

**Input:** 5 nested plugins, no git tags, mixed authors.
**Output (A):** 5 standalone repos + cleaned marketplace + notify chains.
**Output (B):** Same repo with CPV discipline files and consolidated authors.
**Output (C):** Single-plugin repo gains marketplace.json self-entry, version-synced.

## Resources

- [Pre-Migration Audit](references/pre-migration-audit.md)
  > Working Tree Cleanliness · Plugin Inventory Collection · Plugin Manifest Validation · Already-Migrated Detection · Name Collision Detection · Disk Space Check · GitHub Auth Check · MARKETPLACE_PAT Env Check · Audit Report Format
- [Interrogation Playbook](references/interrogation-playbook.md)
  > Purpose · Target layout selection · GitHub owner and visibility (Layout A only) · Primary author consolidation (Layout B only) · Per-plugin metadata · Guest contributor handling · Final confirmation
- [Layout A Migration](references/layout-a-migration.md)
  > Pre-Flight Checks · Per-Plugin Subtree Split · Per-Plugin Repo Creation · CPV Canonicalization · Per-Plugin Tagging · Per-Plugin Auto-Notify Setup · Marketplace.json Rewrite · Cleanup Commit · Verification · Rollback Recipe
- [Layout B Discipline](references/layout-b-discipline.md)
  > Pre-Flight Checks · Scaffold publish.py · Scaffold cliff.toml · Scaffold validate.yml · Generate CHANGELOG.md · Consolidate Authorship · Why Layout B Does Not Need Auto-Notification · Preserve Guest Contributors · Single Atomic Commit · Tag the Marketplace · Verification · Rollback Recipe
- [Layout C Migration](references/layout-c-migration.md)
  > When to migrate to C · Pre-Flight Checks · Migration paths (plugin-only or marketplace-only starting state) · Self-entry construction and version sync · publish.py and atomic commit · Verification and Rollback
- Sibling: `setup-marketplace-auto-notification`
