---
name: migrate-marketplace-architecture
description: >
  Convert a non-CPV marketplace between Layout A (hub-and-spoke), Layout B
  (nested-with-discipline), and Layout C (marketplace-in-plugin self-referential).
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

Converts a non-CPV marketplace into one of three CPV-supported layouts:
- **Layout A** — one GitHub repo per plugin + a marketplace hub
- **Layout B** — nested single-repo with full discipline
- **Layout C** — marketplace-in-plugin (one repo, both manifests colocated, self-referential)

Preserves per-plugin git history and logs every decision.

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
   - **Layout C** — [layout-c-migration](references/layout-c-migration.md) (marketplace-in-plugin self-referential).

4. **Wire auto-notification (Layout A only)** — for every new plugin repo, configure the auto-notify chain via `setup-marketplace-auto-notification` (pat-secret-setup, notify-workflow-template, receiver-workflow-template). Repeat per plugin. Layout B tags atomically and does NOT need cross-repo notification.

5. **Verify** with `validate_marketplace.py --strict` and `validate_plugin.py --strict` on every new plugin. Fix every non-WARNING finding.

6. **Write the migration log** at `$MAIN_ROOT/reports/migrate-marketplace-architecture/<ts±tz>-<slug>.md`.

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

- **Layout A**: N plugin repos + cleaned marketplace (github sources, `plugins/*` removed, tagged). Each repo wired for auto-notify.
- **Layout B**: same repo with `publish.py`, `cliff.toml`, CI, `CHANGELOG.md`, `CONTRIBUTORS.md` (optional), one atomic commit tagged.
- **Layout C**: same repo gains `.claude-plugin/marketplace.json` (or `.claude-plugin/plugin.json` if migrating from a marketplace-only repo), self-entry with `source: "./"`, name and version cross-aligned. Single `publish.py` bumps both manifests in one commit.
- `$MAIN_ROOT/reports/migrate-marketplace-architecture/<ts±tz>-<slug>.md`

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
- [Layout C Migration](references/layout-c-migration.md)
  > When to migrate to C · Plugin-only → C (add marketplace.json) · Marketplace-only → C (add plugin.json) · Self-entry construction · Name/version sync · Why Layout C Does Not Need Auto-Notification · Single Atomic Commit · Verification · Rollback Recipe
- **Sibling skill**: `setup-marketplace-auto-notification` (loaded by plugin-creator and marketplace-fixer agents; wires per-plugin notify chain during Layout A migrations)
