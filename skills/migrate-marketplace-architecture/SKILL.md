---
name: migrate-marketplace-architecture
description: >
  Convert a non-CPV marketplace to Layout A (hub-and-spoke) or Layout B (nested-with-discipline).
  Use when migrating marketplace architecture. Loaded by plugin-fixer agent.
tags:
  - marketplace
  - migration
  - architecture
allowed-tools: Read, Write, Edit, Bash(git:*,gh:*,uv:*,jq:*), Glob, Grep, AskUserQuestion
agent: plugin-fixer
context: fork
user-invocable: false
---

# Migrate Marketplace Architecture

## Overview

Converts a non-CPV marketplace into Layout A (one GitHub repo per plugin) or Layout B (nested layout plus CPV discipline files). Preserves per-plugin git history and records every decision in a migration log.

## Prerequisites

- `gh` CLI authenticated (BLOCKER for Layout A)
- `git` >= 2.34 (for `git subtree split`)
- Clean working tree
- `uv` on PATH, `validate_marketplace.py` accessible

## Instructions

Separate from `fix-validation`: full architectural migration with extensive `AskUserQuestion` interaction. Loaded only on an `architecture` finding or explicit user request.

1. **Run the pre-migration audit** — see [pre-migration-audit](references/pre-migration-audit.md). Collect plugin inventory, detect name collisions, verify disk space and gh auth. Verdict must be READY before any migration command runs.

2. **Interrogate the user** — see [interrogation-playbook](references/interrogation-playbook.md) for every `AskUserQuestion` prompt. Target layout is ALWAYS the first question; never pick for the user.

3. **Execute the chosen migration**:
   - **Layout A** — see [layout-a-migration](references/layout-a-migration.md) for the hub-and-spoke procedure.
   - **Layout B** — see [layout-b-discipline](references/layout-b-discipline.md) for the nested-with-discipline procedure.

4. **Verify** with `validate_marketplace.py --strict` on the migrated marketplace and `validate_plugin.py --strict` on every new or canonicalized plugin. Fix every non-WARNING finding.

5. **Write the migration log** at `docs_dev/migration-log_<marketplace>_<date>.md` recording every decision and command.

Copy this checklist and track your progress:

- [ ] Pre-migration audit READY
- [ ] Interrogation answers recorded
- [ ] Target layout selected (A or B)
- [ ] Layout migration executed
- [ ] `validate_marketplace.py --strict` passes
- [ ] Every plugin validates
- [ ] Migration log written

## Output

- **Layout A**: N new plugin repos + cleaned marketplace repo (github sources, `plugins/*` removed, tagged at next patch).
- **Layout B**: same repo with `scripts/publish.py`, `cliff.toml`, CI workflow, `CHANGELOG.md`, optional `CONTRIBUTORS.md`, one atomic commit tagged at current version.
- Plus `docs_dev/migration-log_<marketplace>_<date>.md`.

## Error Handling

| Error | Resolution |
|-------|------------|
| `gh auth status` fails | BLOCKER for Layout A — run `gh auth login`, re-audit. Layout B still possible. |
| Working tree dirty | BLOCKER — ask the user to commit or stash, then re-audit. |
| Name collision on GitHub | Ask via `AskUserQuestion`: rename, skip, or abort. |
| User cancels mid-flow | Write the partial log and exit cleanly. Never leave orphan branches. |
| Validation still INVALID | Read the report, fix each finding, re-run the specific validator. |

Never force-push or rewrite history. All changes are forward-only commits.

## Examples

**Input:** A marketplace with 5 nested plugins, no git tags, no CHANGELOG, three different authors.

**Output (Layout A):** 5 new standalone repos + cleaned marketplace.

**Output (Layout B):** Same repo with CPV discipline files added and authors consolidated.

Final verification (both layouts):

```bash
uv run --with pyyaml python scripts/validate_marketplace.py . --strict
for d in plugins/*/; do
  uv run --with pyyaml python scripts/validate_plugin.py "$d" --strict
done
```

## Resources

- [Pre-Migration Audit](references/pre-migration-audit.md)
  > Working Tree Cleanliness · Plugin Inventory Collection · Plugin Manifest Validation · Already-Migrated Detection · Name Collision Detection · Disk Space Check · GitHub Auth Check · Audit Report Format
- [Interrogation Playbook](references/interrogation-playbook.md)
  > Purpose · Target layout selection · GitHub owner and visibility (Layout A only) · Primary author consolidation (Layout B only) · Per-plugin metadata · Guest contributor handling · Final confirmation
- [Layout A Migration](references/layout-a-migration.md)
  > Pre-Flight Checks · Per-Plugin Subtree Split · Per-Plugin Repo Creation · CPV Canonicalization · Per-Plugin Tagging · Marketplace.json Rewrite · Cleanup Commit · Verification · Rollback Recipe
- [Layout B Discipline](references/layout-b-discipline.md)
  > Pre-Flight Checks · Scaffold publish.py · Scaffold cliff.toml · Scaffold validate.yml · Generate CHANGELOG.md · Consolidate Authorship · Preserve Guest Contributors · Single Atomic Commit · Tag the Marketplace · Verification · Rollback Recipe
