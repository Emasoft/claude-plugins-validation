# Layout A Migration (Hub-and-Spoke)

## Table of Contents

- [Pre-Flight Checks](#pre-flight-checks)
- [Per-Plugin Subtree Split](#per-plugin-subtree-split)
- [Per-Plugin Repo Creation](#per-plugin-repo-creation)
- [CPV Canonicalization](#cpv-canonicalization)
- [Per-Plugin Tagging](#per-plugin-tagging)
- [Per-Plugin Auto-Notify Setup](#per-plugin-auto-notify-setup)
- [Marketplace.json Rewrite](#marketplacejson-rewrite)
- [Cleanup Commit](#cleanup-commit)
- [Verification](#verification)
- [Rollback Recipe](#rollback-recipe)

## Checklist

- [ ] Pre-flight audit passed (no uncommitted changes, backup tag made)
- [ ] Each plugin subtree split via `git subtree split` or equivalent
- [ ] Per-plugin GitHub repo created with `gh repo create`
- [ ] Each plugin canonicalized (CI, hooks, publish.py) — fixer may be invoked
- [ ] Each plugin tagged with initial version
- [ ] Auto-notify wired per-plugin (notify-marketplace.yml + MARKETPLACE_PAT)
- [ ] marketplace.json rewritten with github-source entries
- [ ] Cleanup commit removes the old per-plugin subfolders from the hub repo
- [ ] Verification: `marketplace.json` validates; dispatch round-trips

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

## Per-Plugin Auto-Notify Setup

After tagging, every new plugin repo must be wired to notify the migrated
marketplace via `repository_dispatch` so Claude Code auto-picks up future
releases. This is Layout A-specific — skip entirely for Layout B.

Do NOT inline the workflow templates here — they live under
`skills/setup-marketplace-auto-notification/references/` and that skill is the
canonical source of truth. This section only describes the per-plugin
migrator's sequence.

For each extracted plugin repo at `<owner>/<plugin-repo>`:

1. **Check the shell env for a PAT** and set the marketplace secret via the
   dedicated helper script — **never improvise `gh secret set`**. The helper
   reads `$MARKETPLACE_PAT` from the environment, calls the only correct form
   (`gh secret set NAME --repo OWNER/REPO --body "$VALUE"` — equivalent to
   `-b`), never prints the token value (so it will never appear in the Claude
   transcript, shell history, or log files), and verifies the secret
   afterwards.

   ```bash
   if [ -n "${MARKETPLACE_PAT:-}" ]; then
     echo "reusing env PAT (${#MARKETPLACE_PAT} chars)"
     uv run python scripts/set_marketplace_pat.py "<owner>/<plugin-repo>"
   else
     echo "MARKETPLACE_PAT not set — see pat-secret-setup.md"
     # Walk the user through creating a classic PAT with `repo` scope,
     # exporting it, and re-running this step. Never echo the value.
     exit 1
   fi
   ```

   **Manual fallback** (only if the helper is unavailable): the only correct
   form of `gh secret set` uses the `--body` (or short `-b`) flag to pass the
   value as a command-line argument, never via stdin or a pipe:

   ```bash
   set +x  # silence xtrace around the secret set call
   gh secret set MARKETPLACE_PAT --repo "<owner>/<plugin-repo>" --body "$MARKETPLACE_PAT" >/dev/null
   set -x 2>/dev/null || true
   ```

   **FORBIDDEN patterns** — reject these on sight, do not emit them:
   - `echo "$MARKETPLACE_PAT" | gh secret set ...` (trailing newline → 401)
   - `gh secret set MARKETPLACE_PAT <<< "$MARKETPLACE_PAT"` (same problem)
   - `printf "$MARKETPLACE_PAT" | gh secret set ...` (same category)
   - Any form that would cause the PAT to appear in the shell history, log
     files, stdout, stderr, or Claude's conversation transcript.

   Full PAT creation procedure (token scopes, auto-detect, rotation) lives in
   `../../../setup-marketplace-auto-notification/references/pat-secret-setup.md`.

2. **Scaffold `.github/workflows/notify-marketplace.yml`** on the plugin repo
   using the template at
   `../../../setup-marketplace-auto-notification/references/notify-workflow-template.md`
   with the marketplace owner/repo substituted for the `MARKETPLACE_OWNER` /
   `MARKETPLACE_REPO` placeholders.

3. **Commit and push** the new workflow to the plugin's default branch:

   ```bash
   cd "/tmp/$name"
   git add .github/workflows/notify-marketplace.yml
   git commit -m "ci: add marketplace auto-notify workflow"
   git push origin main
   ```

4. **Verify the secret** is set on the plugin repo using the helper:

   ```bash
   uv run python scripts/set_marketplace_pat.py --verify-only "<owner>/<plugin-repo>"
   ```

5. **Trigger a dry-run** to confirm the dispatch fires. Push a no-op commit
   (e.g., a whitespace change in `README.md`) and watch the workflow run via
   `gh run list --repo "<owner>/<plugin-repo>" --workflow notify-marketplace.yml --limit 1`.
   Abort the migration if the dispatch step fails — it usually means the PAT
   lacks cross-repo `repo` scope.

The marketplace repo must also have the receiver workflow installed once
(not per-plugin). See
`../../../setup-marketplace-auto-notification/references/receiver-workflow-template.md`
for the Layout A receiver. If that receiver is not yet present, install it
BEFORE wiring the first plugin.

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

Record every rollback step in `reports/migration-log_<marketplace>_<date>.md`
at the project root (gitignored, worktree-aware — resolve via
`${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}`)
so the audit trail is complete.
