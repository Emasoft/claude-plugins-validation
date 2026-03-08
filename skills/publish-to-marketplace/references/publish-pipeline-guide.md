# Publish Pipeline Guide

## Table of Contents
- [Section 1: PAT Setup](#section-1-pat-setup)
- [Section 2: notify-marketplace.yml](#section-2-notify-marketplaceyml)
- [Section 3: The Dispatch Chain](#section-3-the-dispatch-chain)
- [Section 4: publish.py Pipeline](#section-4-publishpy-pipeline)
- [Section 5: Pre-Push Hook Gates](#section-5-pre-push-hook-gates)
- [Section 6: marketplace.json Entry Format](#section-6-marketplacejson-entry-format)
- [Section 7: Troubleshooting](#section-7-troubleshooting)

---

## Section 1: PAT Setup

A Personal Access Token (PAT) is required for cross-repo dispatch. `GITHUB_TOKEN` cannot trigger workflows in other repos.

### Fine-Grained Token (Recommended)

1. GitHub Settings > Developer settings > Personal access tokens > Fine-grained tokens
2. Token name: `marketplace-notify`
3. Repository access: "Only select repositories" > select the **marketplace repo**
4. Permissions:
   - Contents: **Read and write**
   - Metadata: **Read-only**
5. Generate and copy immediately

### Classic Token (Alternative)

1. GitHub Settings > Developer settings > Personal access tokens > Tokens (classic)
2. Scope: `repo` (full control of private repositories)
3. Generate and copy

### Set the Secret

```bash
# Set on the PLUGIN repo (not marketplace)
gh secret set MARKETPLACE_PAT --repo <placeholder-for-github-repo-owner>/<placeholder-for-plugin-name>

# Verify
gh secret list --repo <placeholder-for-github-repo-owner>/<placeholder-for-plugin-name>
```

**One PAT can be shared across all plugin repos** notifying the same marketplace. The PAT owner must have **write access** to the marketplace repo.

### Required Secrets

| Secret | Set On | Description |
|---|---|---|
| `MARKETPLACE_PAT` | Plugin repo | PAT with `repo` scope for cross-repo dispatch |
| `MARKETPLACE_PAT` | Marketplace repo | Same PAT, for pushing past branch protection |
| `GITHUB_TOKEN` | Auto-provided | Only for GitHub Releases (release.yml) |

---

## Section 2: notify-marketplace.yml

Install this workflow in the **plugin repo** at `.github/workflows/notify-marketplace.yml`:

```yaml
name: Notify Marketplace

on:
  push:
    branches: [<placeholder-for-default-branch>]
    paths:
      - '.claude-plugin/plugin.json'
      - 'hooks/**'
      - 'commands/**'
      - 'agents/**'
      - 'skills/**'
      - 'scripts/**'

env:
  MARKETPLACE_OWNER: '<placeholder-for-marketplace-owner>'
  MARKETPLACE_REPO: '<placeholder-for-marketplace-repo-name>'

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - name: Get plugin info
        id: plugin
        run: |
          echo "name=${{ github.event.repository.name }}" >> $GITHUB_OUTPUT
          echo "ref=${{ github.sha }}" >> $GITHUB_OUTPUT

      - name: Trigger marketplace update
        uses: peter-evans/repository-dispatch@v4
        with:
          token: ${{ secrets.MARKETPLACE_PAT }}
          repository: ${{ env.MARKETPLACE_OWNER }}/${{ env.MARKETPLACE_REPO }}
          event-type: plugin-updated
          client-payload: |
            {
              "plugin": "${{ steps.plugin.outputs.name }}",
              "ref": "${{ steps.plugin.outputs.ref }}",
              "source_repo": "${{ github.repository }}",
              "triggered_by": "${{ github.actor }}"
            }

      - name: Summary
        run: |
          echo "## Marketplace Notification" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "Triggered update in ${{ env.MARKETPLACE_OWNER }}/${{ env.MARKETPLACE_REPO }}" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "- Plugin: ${{ steps.plugin.outputs.name }}" >> $GITHUB_STEP_SUMMARY
          echo "- Commit: ${{ steps.plugin.outputs.ref }}" >> $GITHUB_STEP_SUMMARY
```

### Key Configuration Points

- **`branches`**: Must match your default branch (`main` or `master`)
- **`paths`**: Only triggers on plugin-relevant file changes
- **`MARKETPLACE_OWNER`** and **`MARKETPLACE_REPO`**: Fill with your marketplace repo coordinates
- **`event-type: plugin-updated`**: Must match the marketplace's `update-submodules.yml` trigger

---

## Section 3: The Dispatch Chain

After `git push`, this automatic chain fires:

```
1. Push to plugin repo (default branch, plugin paths changed)
       |
       v
2. notify-marketplace.yml fires in plugin repo
       |
       v
3. peter-evans/repository-dispatch@v4 sends POST to GitHub API:
   POST /repos/{marketplace-owner}/{marketplace-repo}/dispatches
   Body: { "event_type": "plugin-updated", "client_payload": { ... } }
       |
       v
4. Marketplace repo receives repository_dispatch event
       |
       v
5. update-submodules.yml triggers in marketplace repo
       |
       v
6. Fetches plugin.json from plugin repo via gh api
   Extracts new version, updates marketplace.json
       |
       v
7. Commits and pushes updated marketplace.json
```

### Timing

- Step 1-3: Immediate (< 10 seconds after push)
- Step 4-7: Typically 30-60 seconds for marketplace workflow to complete
- Total: ~1-2 minutes from push to marketplace update

### Concurrency

The marketplace workflow uses `concurrency: { group: update-versions, cancel-in-progress: false }`. If two plugins update simultaneously, one queues behind the other. Push retries with `git pull --rebase` (up to 3 times) handle remaining conflicts.

---

## Section 4: publish.py Pipeline

The publish script is the **recommended way** to push plugin updates. It validates everything before pushing.

### CLI

```bash
uv run python scripts/publish.py --patch              # 1.0.0 -> 1.0.1
uv run python scripts/publish.py --minor              # 1.0.0 -> 1.1.0
uv run python scripts/publish.py --major              # 1.0.0 -> 2.0.0
uv run python scripts/publish.py --patch --dry-run    # preview only
uv run python scripts/publish.py --patch --skip-tests # skip pytest
```

### Pipeline Stages (all fail-fast)

```
Step 1: Check working tree     git status --porcelain (must be clean)
Step 2: Run tests              uv run pytest tests/ -x -q --tb=short
Step 3: Lint files             uv run python scripts/<lint-script> .
Step 4: Validate plugin        uv run python scripts/<validate-script> . --strict
Step 5: Version consistency    All version sources must match
Step 6: Bump version           Updates plugin.json, pyproject.toml, __version__
Step 7: Commit                 git add -A && git commit -m "chore: bump version to X.Y.Z"
Step 8: Push                   git push origin HEAD
```

### Version Update Targets

The bump step updates version in all of:
- `.claude-plugin/plugin.json` -> `"version"` field
- `pyproject.toml` -> `version` under `[project]`
- All `.py` files -> `__version__ = "X.Y.Z"` strings

### Template Location

The full `publish.py` template is in `setup-plugin-repo/references/plugin-hooks-and-scripts.md`.

---

## Section 5: Pre-Push Hook Gates

The pre-push hook runs automatically before every `git push` and enforces quality gates.

### Three Gates

| Gate | What It Checks | Blocks On |
|---|---|---|
| **Gate 1: Version Bump** | Local version vs remote version in plugin.json | Versions match (no bump) |
| **Gate 2: Lint** | Runs lint script on all source files | Any lint error |
| **Gate 3: Validate** | Runs plugin validator in strict mode | Any non-zero exit (CRITICAL, MAJOR, or MINOR) |

### Smart Triggering

Only runs when plugin-relevant files are staged:
```
.claude-plugin/*, agents/*, commands/*, skills/*, hooks/*,
scripts/*.py, scripts/*.sh, *.mcp.json
```

If no plugin files changed, the hook passes immediately.

### Installation

```bash
# The pre-push template goes in git-hooks/pre-push
# Then install with:
uv run python scripts/setup-hooks.py
# This copies git-hooks/* to .git/hooks/ and sets core.hooksPath
```

---

## Section 6: marketplace.json Entry Format

When a plugin is linked to the marketplace, its entry in `marketplace.json` looks like:

```json
{
  "plugins": [
    {
      "name": "<placeholder-for-plugin-name>",
      "version": "1.0.0",
      "description": "<placeholder-for-plugin-description>",
      "source": {
        "type": "github",
        "repo": "<placeholder-for-github-repo-owner>/<placeholder-for-plugin-name>"
      },
      "tags": ["validation", "quality"],
      "category": "development"
    }
  ]
}
```

### Required Fields

| Field | Description |
|---|---|
| `name` | Plugin name (must match `plugin.json`) |
| `version` | Current semver (auto-updated by dispatch chain) |
| `description` | One-line description |
| `source.type` | `"github"` |
| `source.repo` | `"owner/repo-name"` |

### Optional Fields

| Field | Description |
|---|---|
| `tags` | Array of keyword strings |
| `category` | Grouping category |
| `author` | Author name or GitHub username |

---

## Section 7: Troubleshooting

### Dispatch Not Received

1. Check the plugin repo's Actions tab — did `notify-marketplace.yml` run?
2. If it ran but marketplace didn't trigger:
   - Verify `MARKETPLACE_PAT` has `repo` scope
   - Verify PAT owner has write access to marketplace repo
   - Check `event-type` matches: `plugin-updated`
3. If it didn't run:
   - Verify workflow is on the default branch
   - Verify the push changed files matching the `paths` filter

### Pre-Push Hook Blocks Push

| Gate | Error | Fix |
|---|---|---|
| Gate 1 | "Version not bumped" | Run `publish.py --patch` instead of manual push |
| Gate 2 | "Lint failed" | Fix lint issues, `uv run ruff check --fix scripts/` |
| Gate 3 | "Validation failed" | Fix CRITICAL/MAJOR issues shown in output |

### Version Mismatch

If `plugin.json`, `pyproject.toml`, and `__version__` disagree:
```bash
# Check current versions
grep '"version"' .claude-plugin/plugin.json
grep 'version' pyproject.toml | head -1
grep '__version__' scripts/*.py
```
Use `publish.py --patch` to re-sync all version sources.

### Marketplace Not Updating

1. Check marketplace repo Actions > `update-submodules.yml` runs
2. Check the run logs for errors fetching `plugin.json`
3. Manual trigger: go to marketplace repo > Actions > "Update Versions" > "Run workflow"

---

## Placeholder Reference

| Placeholder | Description | Example |
|---|---|---|
| `<placeholder-for-default-branch>` | Plugin repo default branch | `main` |
| `<placeholder-for-github-repo-owner>` | GitHub username or org | `Emasoft` |
| `<placeholder-for-plugin-name>` | Plugin repository name | `claude-plugins-validation` |
| `<placeholder-for-plugin-description>` | One-line plugin description | `Validates Claude Code plugins` |
| `<placeholder-for-marketplace-owner>` | Marketplace repo owner | `Emasoft` |
| `<placeholder-for-marketplace-repo-name>` | Marketplace repo name | `emasoft-plugins` |
