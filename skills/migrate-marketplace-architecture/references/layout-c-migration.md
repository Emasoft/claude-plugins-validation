# Layout C Migration (Marketplace-in-Plugin / Self-Referential)

## Table of Contents

- [When to migrate to C](#when-to-migrate-to-c)
- [Pre-Flight Checks](#pre-flight-checks)
- [Migration paths (plugin-only or marketplace-only starting state)](#migration-paths-plugin-only-or-marketplace-only-starting-state)
- [Self-entry construction and version sync](#self-entry-construction-and-version-sync)
- [publish.py and atomic commit](#publishpy-and-atomic-commit)
- [Verification and Rollback](#verification-and-rollback)

## Checklist

- [ ] Pre-flight audit passed
- [ ] Both `.claude-plugin/plugin.json` AND `.claude-plugin/marketplace.json` present at repo root
- [ ] Self-entry in `marketplace.json.plugins[]` with `source: "./"` and matching `name` + `version`
- [ ] `publish.py` updated to bump BOTH manifests in one commit
- [ ] Single atomic commit for the whole migration
- [ ] Repository tagged at `v<version>`
- [ ] Verification: `validate_plugin.py --strict` AND `validate_marketplace.py --strict` both clean

---

## When to migrate to C

Layout C is the right fit when **all** of these are true:

- The repo packages **exactly one** plugin (not a set of related plugins).
- The user wants `claude plugin marketplace add <owner>/<repo>` followed by `claude plugin install <name>@<repo>` to work without a separate marketplace repo.
- The user is willing to accept that future second/third plugins under the same name would force a migration to Layout A or B.

If the user is publishing multiple plugins together, Layout B (monorepo) or Layout A (separate repos + hub) is correct — NOT Layout C. Layout C is **NOT** a way to host multiple plugins in one repo.

If the user is starting from:
- **A plugin-only repo** (has `plugin.json`, no `marketplace.json`) → migrate by adding `marketplace.json` with a single self-entry.
- **A marketplace-only repo** (has `marketplace.json` referencing one plugin via local path, no `plugin.json` at root) → migrate by adding `plugin.json` and rewriting the entry to `source: "./"`.
- **A Layout A or B configuration** → use `migrate-marketplace-architecture` to walk through the full restructure (out of scope for this reference).

## Pre-Flight Checks

1. **Clean working tree**

   ```bash
   [ -z "$(git status --porcelain)" ] \
     || { echo "BLOCKER: working tree is dirty"; exit 1; }
   ```

2. **Identify starting state** — exactly one of these:

   ```bash
   HAS_PLUGIN=$(test -f .claude-plugin/plugin.json && echo yes || echo no)
   HAS_MARKETPLACE=$(test -f .claude-plugin/marketplace.json && echo yes || echo no)
   echo "plugin.json=$HAS_PLUGIN  marketplace.json=$HAS_MARKETPLACE"
   ```

   - `yes/no` → plugin-only path
   - `no/yes` → marketplace-only path
   - `yes/yes` → already Layout C (verify cross-fields, exit if clean)
   - `no/no` → no manifests at all; not a migration, route to scaffolder

3. **Default branch detection**

   ```bash
   DEFAULT_BRANCH=$(git remote show origin 2>/dev/null | awk '/HEAD branch/ {print $NF}')
   ```

4. **Record the source-of-truth name and version** that will become the self-entry's identity. From `plugin.json` if present, otherwise from the existing single-plugin entry in `marketplace.json`.

## Migration paths (plugin-only or marketplace-only starting state)

### Plugin-only → Layout C (add marketplace.json)

Starting state: repo has `.claude-plugin/plugin.json`, ships one plugin, has no marketplace manifest.

1. **Read the plugin manifest** to extract `name`, `version`, and `description`.

2. **Create `.claude-plugin/marketplace.json`** with metadata mirroring the plugin's identity:

   ```json
   {
     "name": "<plugin-name>",
     "owner": {
       "name": "<author-name>",
       "email": "<author-email>"
     },
     "metadata": {
       "version": "<plugin-version>",
       "description": "<plugin-description>"
     },
     "plugins": [
       {
         "name": "<plugin-name>",
         "source": "./",
         "version": "<plugin-version>",
         "description": "<plugin-description>",
         "category": "<from AskUserQuestion>",
         "author": { "name": "<author-name>", "email": "<author-email>" },
         "homepage": "https://github.com/<owner>/<repo>",
         "license": "<MIT/Apache-2.0/etc>"
       }
     ]
   }
   ```

3. **Validate cross-references**: `name` is identical in both manifests; `version` is identical. The `source: "./"` is the canonical Layout C marker.

### Marketplace-only → Layout C (add plugin.json)

Starting state: repo has `.claude-plugin/marketplace.json` with a single entry pointing to a local path (e.g. `"./"` or `"./plugins/foo"`), but no `.claude-plugin/plugin.json` at the repo root.

1. **Read the existing plugin entry** to extract `name`, `version`, `description`, `author`. If multiple entries exist, this repo is NOT a Layout C candidate — abort and recommend Layout B or A instead.

2. **If the existing entry uses `source: "./plugins/<name>"`**: this is a Layout-B-with-one-plugin pattern. Layout C requires the plugin to live at the repo root. Two options:
   - Move the plugin contents up to the repo root (preserves git history via `git mv`), then change `source` to `"./"`.
   - Decline the migration and recommend Layout B instead (cleaner for repos that may grow).

3. **Create `.claude-plugin/plugin.json`** at the repo root mirroring the entry's identity:

   ```json
   {
     "name": "<plugin-name>",
     "version": "<plugin-version>",
     "description": "<plugin-description>",
     "author": { "name": "<author-name>", "email": "<author-email>" }
   }
   ```

4. **Update the marketplace entry** to `"source": "./"` (if it wasn't already).

## Self-entry construction and version sync

### Self-entry shape

The single entry in `marketplace.json.plugins[]` for Layout C MUST satisfy:

| Field | Required value |
|---|---|
| `name` | Same as `plugin.json.name` (CPV cross-validates). |
| `source` | `"./"` (the canonical self-reference). |
| `version` | Same as `plugin.json.version`. |
| `description` | Recommended. Usually mirrors `plugin.json.description`. |
| `category`, `homepage`, `license`, `author` | Encouraged for discovery. Use `AskUserQuestion` to gather. |

CPV's `validate_layout_c_consistency` checks rules 1, 2, and 3. A missing self-entry (rule 1) or a wrong `source` (rule 2) is a MAJOR finding (would break install); a version mismatch (rule 3) is a MINOR finding (soft drift). In `--strict` mode any of these block the run.

### Name and version synchronization

This is the single most fragile invariant in Layout C. Whenever EITHER manifest changes, BOTH must change in the same commit. The standard `publish.py` for Layout C handles this — but if a contributor edits one manifest by hand without the other, CPV flags the drift on the next validation run (MAJOR for a name/source mismatch, MINOR for a version mismatch).

To enforce the invariant:
- Add a pre-commit hook that aborts when `plugin.json` is staged without `marketplace.json` (or vice versa) when the changed field is `name` or `version`. The standard CPV pre-push hook also catches this on push.
- Document in the repo's CONTRIBUTING.md that Layout C requires lockstep edits to both manifests.

### Why Layout C Does Not Need Auto-Notification

Auto-notification (the `MARKETPLACE_PAT` + `notify-marketplace.yml` chain used in Layout A) exists to bridge a plugin repo and a SEPARATE marketplace repo. Layout C colocates both manifests in the SAME repo, so a single push updates both manifests atomically — there is no second repo to notify.

Skip the entire `setup-marketplace-auto-notification` flow for Layout C migrations.

## publish.py and atomic commit

### publish.py adaptation for Layout C

The Layout C `publish.py` differs from Layout A or B in two ways:
1. The bump function modifies BOTH `.claude-plugin/plugin.json::version` AND `.claude-plugin/marketplace.json::metadata.version` AND `.claude-plugin/marketplace.json::plugins[N].version` (the self-entry).
2. There is exactly ONE tag per release (matching the new version), and ONE git push that carries both manifests.

Scaffold the publish.py with `scripts/generate_plugin_repo.py` — its `gen_publish_py()` emits a single unified publish pipeline, and the Layout C three-location version sync is performed by `update_self_marketplace_json()` (bumps both `metadata.version` and the `source: "./"` self-entry's version) alongside `update_python_versions()` (bumps `plugin.json` + `pyproject.toml`). Pass `--self-marketplace` when generating so the Layout C marketplace.json is emitted too.

### Single Atomic Commit

The migration MUST land as one commit:

```bash
git add .claude-plugin/plugin.json .claude-plugin/marketplace.json scripts/publish.py CHANGELOG.md
git commit -m "chore: migrate to Layout C (marketplace-in-plugin)"
```

Never split the manifest creation from the publish.py update — a half-migrated repo trips CPV's `validate_layout_c_consistency` check (MAJOR for a missing self-entry or wrong `source`).

### Tag the Repository

```bash
NEW_VERSION=$(jq -r .version .claude-plugin/plugin.json)
git tag "v${NEW_VERSION}"
git push origin HEAD --tags
```

## Verification and Rollback

### Verification

```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_plugin.py" . --strict
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_marketplace.py" . --strict
```

Both must report zero CRITICAL/MAJOR/MINOR/NIT. Specifically check:
- `name` matches between `plugin.json` and `marketplace.json.plugins[N]` (where `source: "./"`)
- `version` matches in all three locations: `plugin.json.version`, `marketplace.json.metadata.version`, `marketplace.json.plugins[N].version`
- The self-entry `source` is exactly `"./"`

### Rollback Recipe

```bash
git reset --hard <pre-migration-sha>
git tag -d v<new-version>      # local tag
git push origin :refs/tags/v<new-version>  # remote tag, only if pushed
```

Always commit-tag-push as separate steps so a partial rollback is possible.
