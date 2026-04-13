---
name: migrate-marketplace-architecture
description: >
  Convert a non-CPV marketplace to Layout A (hub-and-spoke) or Layout B (nested-with-discipline).
  Use when migrating marketplace architecture. Loaded by marketplace-fixer agent.
tags:
  - marketplace
  - migration
  - architecture
allowed-tools: Read, Write, Edit, Bash(git:*,gh:*,uv:*,jq:*), Glob, Grep, AskUserQuestion
user-invocable: false
---

# Migrate Marketplace Architecture

## Overview

Converts a non-CPV marketplace to Layout A (one GitHub repo per plugin) or Layout B (nested plus discipline). Preserves per-plugin git history and logs every decision.

## Prerequisites

- `gh` CLI authenticated (BLOCKER for Layout A)
- `git` >= 2.34 (for `git subtree split`)
- Clean working tree
- `uv` on PATH, `validate_marketplace.py` accessible

## Instructions

Full architectural migration with extensive `AskUserQuestion` interaction. Loaded only on an `architecture` finding or explicit user request.

1. **Pre-migration audit** — see [pre-migration-audit](references/pre-migration-audit.md). Collect plugin inventory, detect collisions, verify disk and gh auth. Verdict must be READY.

2. **Interrogate the user** — see [interrogation-playbook](references/interrogation-playbook.md). Target layout is ALWAYS the first question; never pick for the user.

3. **Execute the migration**:
   - **Layout A** — [layout-a-migration](references/layout-a-migration.md) (hub-and-spoke).
   - **Layout B** — [layout-b-discipline](references/layout-b-discipline.md) (nested).

4. **Wire auto-notification (Layout A only)** — for every new plugin repo, configure the auto-notify chain via the `setup-marketplace-auto-notification` skill: `pat-secret-setup.md` (env-var auto-detect), `notify-workflow-template.md`, `receiver-workflow-template.md`. Repeat per plugin. Layout B tags atomically and does NOT need cross-repo notification.

5. **Verify** with `validate_marketplace.py --strict` and `validate_plugin.py --strict` on every new plugin. Fix every non-WARNING finding.

6. **Write the migration log** at `docs_dev/migration-log_<marketplace>_<date>.md`.

Copy this checklist and track your progress:

- [ ] Pre-migration audit READY
- [ ] Interrogation answers recorded
- [ ] Target layout selected (A or B)
- [ ] Layout migration executed
- [ ] Auto-notification wired for each new plugin repo (Layout A only)
- [ ] `validate_marketplace.py --strict` passes
- [ ] Every plugin validates
- [ ] Migration log written

## Output

- **Layout A**: N plugin repos + cleaned marketplace (github sources, `plugins/*` removed, tagged). Each repo wired for auto-notify.
- **Layout B**: same repo with `publish.py`, `cliff.toml`, CI, `CHANGELOG.md`, `CONTRIBUTORS.md` (optional), one atomic commit tagged.
- `docs_dev/migration-log_<marketplace>_<date>.md`.

## Error Handling

| Error | Resolution |
|-------|------------|
| `gh auth status` fails | BLOCKER for Layout A — run `gh auth login`, re-audit. |
| Working tree dirty | BLOCKER — commit or stash, re-audit. |
| Name collision on GitHub | `AskUserQuestion`: rename, skip, or abort. |
| User cancels mid-flow | Write partial log, exit cleanly. No orphan branches. |
| Validation still INVALID | Fix each finding, re-run the validator. |

Never force-push or rewrite history. Forward-only commits.

## Examples

**Input:** 5 nested plugins, no git tags, mixed authors.
**Output (A):** 5 standalone repos + cleaned marketplace + per-repo notify chains.
**Output (B):** Same repo with CPV discipline files and consolidated authors.

## Resources

- [Pre-Migration Audit](references/pre-migration-audit.md)
  > Working Tree Cleanliness · Plugin Inventory Collection · Plugin Manifest Validation · Already-Migrated Detection · Name Collision Detection · Disk Space Check · GitHub Auth Check · MARKETPLACE_PAT Env Check · Audit Report Format
- [Interrogation Playbook](references/interrogation-playbook.md)
  > Purpose · Target layout selection · GitHub owner and visibility (Layout A only) · Primary author consolidation (Layout B only) · Per-plugin metadata · Guest contributor handling · Final confirmation
- [Layout A Migration](references/layout-a-migration.md)
  > Pre-Flight Checks · Per-Plugin Subtree Split · Per-Plugin Repo Creation · CPV Canonicalization · Per-Plugin Tagging · Per-Plugin Auto-Notify Setup · Marketplace.json Rewrite · Cleanup Commit · Verification · Rollback Recipe
- [Layout B Discipline](references/layout-b-discipline.md)
  > Pre-Flight Checks · Scaffold publish.py · Scaffold cliff.toml · Scaffold validate.yml · Generate CHANGELOG.md · Consolidate Authorship · Why Layout B Does Not Need Auto-Notification · Preserve Guest Contributors · Single Atomic Commit · Tag the Marketplace · Verification · Rollback Recipe
- **Sibling skill**: `setup-marketplace-auto-notification` (loaded by plugin-fixer agent; wires per-plugin notify chain during Layout A migrations)
