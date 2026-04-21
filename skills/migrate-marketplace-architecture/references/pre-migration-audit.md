# Pre-Migration Audit

## Table of Contents

- [Working Tree Cleanliness](#1-working-tree-cleanliness)
- [Plugin Inventory Collection](#2-plugin-inventory-collection)
- [Plugin Manifest Validation](#3-plugin-manifest-validation)
- [Already-Migrated Detection](#4-already-migrated-detection)
- [Name Collision Detection](#5-name-collision-detection)
- [Disk Space Check](#6-disk-space-check)
- [GitHub Auth Check](#7-github-auth-check)
- [MARKETPLACE_PAT Env Check](#8-marketplace_pat-env-check)
- [Audit Report Format](#9-audit-report-format)

## Checklist

- [ ] Working tree clean (no uncommitted changes)
- [ ] Plugin inventory collected
- [ ] Every plugin manifest validates
- [ ] No already-migrated plugins blocking the plan
- [ ] No name collisions detected
- [ ] Enough disk space for subtree splits + clones
- [ ] `gh auth status` green
- [ ] `MARKETPLACE_PAT` env var set
- [ ] Audit report written and reviewed before proceeding

---

## Purpose

Runs BEFORE any Layout A or Layout B migration. Collects the plugin inventory,
detects conflicts, and verifies the environment is ready. The migration must
NOT proceed if any BLOCKER is found.

## 1. Working Tree Cleanliness

**Check**: `git status --porcelain`

**Rule**: The output MUST be empty. `git subtree split` writes into the object
store of the current repo and refuses to run on a dirty tree. A nested-marketplace
migration will rewrite many files, so any uncommitted change risks being lost.

**On fail**: BLOCKER. Ask the user to commit or stash, then re-run the audit.

```bash
if [ -n "$(git status --porcelain)" ]; then
  echo "BLOCKER: working tree is dirty"
  git status --short
  exit 1
fi
```

## 2. Plugin Inventory Collection

**Check**: Enumerate every subdirectory under the nested marketplace root that
contains either `.claude-plugin/plugin.json` (canonical) or a legacy top-level
`plugin.json`.

**Rule**: Build a list `plugins = [(name, subdir, version, manifest_path), ...]`.
This list is consumed by every downstream step (split, scaffold, rewrite). If
the list is empty the audit fails with BLOCKER — nothing to migrate.

```bash
find plugins -maxdepth 3 -type f \
  \( -name 'plugin.json' -o -path '*/.claude-plugin/plugin.json' \) \
  -not -path '*/node_modules/*'
```

Record each plugin's subdirectory path relative to the marketplace root. For
Layout A these paths become the `--prefix` argument for `git subtree split`.

## 3. Plugin Manifest Validation

**Check**: For every plugin found in step 2, parse `plugin.json` and require:
- `name` (string, matches directory name or is explicit)
- `version` (semver string — `x.y.z`)
- `description` (non-empty string)

**Rule**: If any plugin is missing a required field, mark it BLOCKER. The user
must fix the manifests before migrating, because Layout A tags each new repo
at `v<version>` and Layout B needs versions for the consolidated CHANGELOG.

**Fix command**: `uv run python scripts/validate_plugin.py plugins/<name>` to
see the exact field gaps, then edit `plugin.json` manually.

## 4. Already-Migrated Detection

**Check**: For every plugin entry currently in `.claude-plugin/marketplace.json`,
look at its `source` block. If any entry already uses
`{"source": "github", "repo": "<owner>/<name>"}`, that plugin has already been
extracted and the marketplace is in mixed state.

**Rule**: Mixed marketplaces are ALLOWED for Layout A — the audit must preserve
existing external references and only split the still-nested plugins. For
Layout B, mixed state is a BLOCKER: Layout B discipline is only meaningful if
every plugin lives as a subdirectory in the same repo.

**Output**: `already_migrated = [...]` and `still_nested = [...]` lists.

## 5. Name Collision Detection

**Check**: For each `still_nested` plugin, query GitHub for an existing repo at
`<target-owner>/<plugin-name>`:

```bash
gh repo view "<owner>/<name>" >/dev/null 2>&1 && echo "EXISTS"
```

**Rule**: If a repo with the same name already exists under the target owner
AND the plugin has never been extracted there, mark it as COLLISION. Present
the user with three choices via `AskUserQuestion`:
- **Rename the new repo** (user provides a new name)
- **Skip this plugin** (leave it nested)
- **Abort the whole migration**

## 6. Disk Space Check

**Check**: `git subtree split` can duplicate the entire object store for large
histories. Require at least 2x the size of the current `.git` directory as
free space on the same filesystem.

```bash
git_size=$(du -sk .git | cut -f1)
free=$(df -k . | awk 'NR==2 {print $4}')
[ "$free" -lt "$((git_size * 2))" ] && echo "BLOCKER: insufficient disk"
```

**On fail**: BLOCKER. Ask the user to free space or abort.

## 7. GitHub Auth Check

**Check**: `gh auth status` must return 0.

**Rule**: Layout A creates one new repo per plugin via `gh repo create`, which
requires an authenticated session with `repo` scope. Layout B only needs auth
if the user later chooses to push, so this check is a WARNING (not BLOCKER) for
Layout B and a BLOCKER for Layout A.

```bash
gh auth status 2>&1 | grep -q "Logged in" || echo "BLOCKER: gh not authenticated"
```

## 8. MARKETPLACE_PAT Env Check

**Applies to**: Layout A only. Skip for Layout B — see
[Why Layout B Does Not Need Auto-Notification](layout-b-discipline.md#why-layout-b-does-not-need-auto-notification).

**Check**: Is `MARKETPLACE_PAT` set in the current shell environment? Every
new plugin repo created by Layout A must receive this secret so its
`notify-marketplace.yml` workflow can fire `repository_dispatch` at the
marketplace repo. The value itself is never echoed — only its length is
inspected for the audit report:

```bash
if [ -n "${MARKETPLACE_PAT:-}" ]; then
  echo "MARKETPLACE_PAT present (${#MARKETPLACE_PAT} chars)"
else
  echo "WARNING: MARKETPLACE_PAT not set — user will be prompted during per-plugin auto-notify setup"
fi
```

**Rule**: This is a **WARNING**, not a BLOCKER. The migration can still begin
without the PAT, but the user will be interrupted during the
[Per-Plugin Auto-Notify Setup](layout-a-migration.md#per-plugin-auto-notify-setup)
step of `layout-a-migration.md` to create one and re-export it. Warning the
user up-front gives them time to create the token BEFORE the migration starts,
avoiding a mid-flight context switch.

**User guidance**: If absent, point the user at
`skills/setup-marketplace-auto-notification/references/pat-secret-setup.md`
for the full creation procedure (classic PAT with `repo` scope, or a
fine-grained PAT with cross-repo `contents:write` + `actions:write`).

**Security note**: Never echo `$MARKETPLACE_PAT` into logs or the audit
report. The audit report records only "present"/"absent" and the length in
characters — never the value or any prefix.

## 9. Audit Report Format

The audit writes `$MAIN_ROOT/reports/migrate-marketplace-architecture/<YYYYMMDD_HHMMSS±HHMM>-pre-migration-audit.md` at the main-repo root (first entry of `git worktree list` — never a linked worktree) containing:

1. Target layout (A or B) — collected from the interrogation playbook
2. Plugin inventory table: `| name | subdir | version | status |`
3. BLOCKER list (empty = ready to migrate)
4. WARNING list
5. `already_migrated` list (preserved as-is)
6. Name collisions and user decisions
7. Final verdict: READY / BLOCKED

Only proceed with the Layout A or Layout B procedure when the verdict is READY.
