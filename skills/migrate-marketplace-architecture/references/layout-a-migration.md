# Layout A Migration (Hub-and-Spoke)

## Table of Contents

- [Pre-Flight Checks](#pre-flight-checks)
- [Per-Plugin Subtree Split](#per-plugin-subtree-split)
- [Per-Plugin Repo Creation](#per-plugin-repo-creation)
- [CPV Canonicalization](#cpv-canonicalization)
- [Per-Plugin Tagging](#per-plugin-tagging)
- [Marketplace.json Rewrite](#marketplacejson-rewrite)
- [Cleanup Commit](#cleanup-commit)
- [Verification](#verification)
- [Rollback Recipe](#rollback-recipe)

---

## Purpose

Full step-by-step procedure for migrating a nested marketplace (the
`wshobson/agents` pattern — plugins as subdirectories under `plugins/`) into
CPV's Layout A: one standalone GitHub repository per plugin, with the original
marketplace repo reduced to a thin `marketplace.json` pointing at the new
repos. Preserves per-plugin git history via `git subtree split`.

This reference is loaded by the `migrate-marketplace-architecture` skill after
the pre-migration audit is READY and the user has selected Layout A in the
interrogation playbook.

## Pre-Flight Checks

Run these in order BEFORE the first `git subtree split` command. Any failure
aborts the migration cleanly — no partial state.

1. **gh authentication**

   ```bash
   gh auth status 2>&1 | grep -q "Logged in" \
     || { echo "BLOCKER: gh not authenticated"; exit 1; }
   ```

2. **Clean working tree** — `git subtree split` refuses to run on a dirty tree.

   ```bash
   [ -z "$(git status --porcelain)" ] \
     || { echo "BLOCKER: working tree is dirty"; exit 1; }
   ```

3. **Disk space** — subtree split duplicates objects. Require at least 2x the
   current `.git` size as free space.

   ```bash
   git_size=$(du -sk .git | cut -f1)
   free=$(df -k . | awk 'NR==2 {print $4}')
   [ "$free" -lt "$((git_size * 2))" ] \
     && { echo "BLOCKER: insufficient disk"; exit 1; }
   ```

4. **Default branch detection** — capture once so every command uses the same
   target.

   ```bash
   DEFAULT_BRANCH=$(git remote show origin | awk '/HEAD branch/ {print $NF}')
   echo "Default branch: $DEFAULT_BRANCH"
   ```

5. **Record current marketplace version** from `.claude-plugin/marketplace.json`
   `metadata.version`. The cleanup commit later will tag the marketplace at
   the next patch version.

## Per-Plugin Subtree Split

For each plugin under `plugins/<name>/`, run `git subtree split` to create a
branch containing only that plugin's history.

```bash
for plugin in plugins/*/; do
  name=$(basename "$plugin")
  git subtree split --prefix="plugins/$name" -b "extract-$name"
done
```

The resulting `extract-<name>` branches contain the plugin's files at the repo
root (no `plugins/<name>/` prefix). Verify each branch with:

```bash
git log --oneline "extract-$name" | head -5
git ls-tree -r "extract-$name" | head -20
```

## Per-Plugin Repo Creation

Create one GitHub repository per plugin under the target owner. The interrogation
playbook already captured `{owner}` and `{visibility}`.

```bash
gh repo create "$owner/$name" "--$visibility" \
  --description "$(jq -r '.description' "plugins/$name/.claude-plugin/plugin.json")"
```

Add the new repo as a remote and push the extracted branch as `main`:

```bash
git remote add "ext-$name" "https://github.com/$owner/$name.git"
git push "ext-$name" "extract-$name:main"
```

Do this sequentially, not in parallel — parallel `gh repo create` calls trip
GitHub's secondary rate limits and auth throttling.

## CPV Canonicalization

Clone each new repo into a scratch location and add the canonical CPV files
(pyproject.toml, cliff.toml, .github/workflows/, git-hooks/, scripts/publish.py,
scripts/validate_plugin.py symlinks, README badges).

```bash
for plugin in plugins/*/; do
  name=$(basename "$plugin")
  git clone "https://github.com/$owner/$name.git" "/tmp/$name"
  uv run --with pyyaml python scripts/standardize_plugin.py "/tmp/$name" --fix
done
```

The `standardize_plugin.py --fix` command adds every missing canonical file
without modifying existing plugin code. Any remaining issues (README badges,
MINOR findings) must be fixed manually in a follow-up commit.

## Per-Plugin Tagging

Tag each new repo at the exact version recorded in its original
`.claude-plugin/plugin.json`. Tagging at the current version — not `v0.1.0` —
preserves the release history the plugin carried inside the nested marketplace.

```bash
cd "/tmp/$name"
version=$(jq -r '.version' .claude-plugin/plugin.json)
git add .
git commit -m "chore: add CPV canonical files"
git tag "v$version" -m "Migrated from $original_marketplace at v$version"
git push origin main --tags
```

## Marketplace.json Rewrite

Back in the original marketplace repo, rewrite each extracted plugin entry in
`.claude-plugin/marketplace.json` from a local path reference to a github
source reference. Preserve every other field (author, homepage, license,
category, description).

Before:

```json
{
  "name": "foo",
  "source": "./plugins/foo",
  "author": "jsmith",
  "category": "development"
}
```

After:

```json
{
  "name": "foo",
  "source": {
    "source": "github",
    "repo": "jsmith/foo"
  },
  "author": "jsmith",
  "category": "development"
}
```

Use `jq` to do this mechanically; never hand-edit:

```bash
jq --arg name "$name" --arg repo "$owner/$name" '
  .plugins |= map(
    if .name == $name then
      .source = {"source": "github", "repo": $repo}
    else . end
  )' .claude-plugin/marketplace.json > tmp.json \
    && mv tmp.json .claude-plugin/marketplace.json
```

## Cleanup Commit

Once every plugin is extracted, tagged, and pushed, delete the `plugins/<name>/`
directories from the marketplace repo, commit the marketplace.json rewrite,
and bump the marketplace version by one patch.

```bash
for plugin in plugins/*/; do
  git rm -rf "$plugin"
done
git add .claude-plugin/marketplace.json
next=$(python3 -c "v='$current_version'.split('.'); v[2]=str(int(v[2])+1); print('.'.join(v))")
git commit -m "chore: migrate to Layout A (extract $N plugins to standalone repos)"
git tag "v$next" -m "Layout A migration"
git push origin main --tags
```

Do NOT rebase or amend history. All changes are forward-only commits.

## Verification

Run validation against the cleaned marketplace AND every new plugin repo.
Every report must be VALID with zero MAJOR findings.

```bash
uv run --with pyyaml python scripts/validate_marketplace.py . --strict
for plugin in plugins/*/; do
  name=$(basename "$plugin")
  uv run --with pyyaml python scripts/validate_plugin.py "/tmp/$name" --strict
done
```

If any report is INVALID, fix the finding in the appropriate repo and re-run
the specific validator. Never claim the migration is done with unresolved
MAJOR or CRITICAL findings outstanding.

## Rollback Recipe

If the migration fails partway through, the rollback is mechanical because
every operation was additive:

- **New repos are in place but the marketplace.json rewrite never ran**:
  delete the new repos via `gh repo delete` and leave the nested marketplace
  untouched.
- **Marketplace.json was rewritten but not yet committed**:
  `git checkout -- .claude-plugin/marketplace.json`.
- **Everything committed and tagged**: do NOT force-push. Instead, open a
  revert commit on the marketplace and ask the user whether to keep or
  delete the new repos.

Record every rollback step in `docs_dev/migration-log_<marketplace>_<date>.md`
so the audit trail is complete.
