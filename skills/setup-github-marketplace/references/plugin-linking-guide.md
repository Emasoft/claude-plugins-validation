# Plugin Linking Guide

Complete reference for linking, unlinking, and managing plugins within a Claude Code marketplace repository.

## Table of Contents

- [Adding a Plugin to the Marketplace](#adding-a-plugin-to-the-marketplace)
- [Removing a Plugin from the Marketplace](#removing-a-plugin-from-the-marketplace)
- [Updating a Plugin Version](#updating-a-plugin-version)
- [Configuring MARKETPLACE_PAT Secret](#configuring-marketplace_pat-secret)
- [Installing Notification Workflow](#installing-notification-workflow)
- [Testing the Notification Chain](#testing-the-notification-chain)
- [Migrating Plugins Between Marketplaces](#migrating-plugins-between-marketplaces)
- [Batch Operations](#batch-operations)

## Checklist

- [ ] Confirm plugin repo exists and has tagged releases
- [ ] Add plugin entry to marketplace.json with full metadata
- [ ] Configure `MARKETPLACE_PAT` secret on the plugin repo
- [ ] Install `notify-marketplace.yml` on the plugin's default branch
- [ ] Test the notification chain end-to-end
- [ ] Verify marketplace.json reflects the latest plugin version

---

## Adding a Plugin to the Marketplace

### Prerequisites

Before linking a plugin, verify that:

1. The plugin repository has a valid `.claude-plugin/plugin.json`
2. The marketplace repository exists and has a valid `.claude-plugin/marketplace.json`
3. You have push access to both repositories
4. The `gh` CLI is authenticated (`gh auth status`)

### Step 1: Verify Plugin Structure

```bash
# Clone or navigate to the plugin repository
gh repo clone OWNER/PLUGIN-REPO
cd PLUGIN-REPO

# Verify plugin.json exists and is valid JSON
cat .claude-plugin/plugin.json | jq .

# Confirm required fields exist
jq -e '.name, .version, .description' .claude-plugin/plugin.json
```

If `plugin.json` is missing or malformed, the plugin cannot be added to the marketplace.

### Step 2: Install the Notification Workflow

See [Installing Notification Workflow](#installing-notification-workflow) below for detailed steps.

### Step 3: Set the MARKETPLACE_PAT Secret

See [Configuring MARKETPLACE_PAT Secret](#configuring-marketplace_pat-secret) below for detailed steps.

### Step 4: Add the Plugin Entry to marketplace.json

The marketplace does not use git submodules. Instead, plugins are registered as entries in `.claude-plugin/marketplace.json` with `source.source: "github"`. Fetch the plugin metadata via the GitHub API and add it to the marketplace.

```bash
# Navigate to the marketplace repository
cd /path/to/marketplace-repo

# Fetch plugin.json from the plugin repo via GitHub API
gh api repos/OWNER/PLUGIN-REPO/contents/.claude-plugin/plugin.json \
  --jq '.content' | base64 -d | jq .

# Add a new entry to the plugins array in marketplace.json
# The entry should include name, version, description, and source info:
jq '.plugins += [{
  "name": "PLUGIN-NAME",
  "version": "1.0.0",
  "description": "Plugin description here",
  "source": {
    "source": "github",
    "repo": "OWNER/PLUGIN-REPO"
  }
}]' .claude-plugin/marketplace.json > tmp.json && mv tmp.json .claude-plugin/marketplace.json
```

### Step 5: Validate the Marketplace

```bash
# Run marketplace validation to catch any configuration errors
uv run python scripts/validate_marketplace.py /path/to/marketplace-repo --verbose
```

### Step 6: Regenerate the README

```bash
# Regenerate README to include the new plugin in the plugin table
uv run python scripts/generate-readme.py
```

### Step 7: Commit and Push

```bash
git add .claude-plugin/marketplace.json README.md
git commit -m "feat: add PLUGIN-NAME plugin to marketplace"
git push
```

---

## Removing a Plugin from the Marketplace

### Step 1: Remove the Plugin Entry from marketplace.json

```bash
cd /path/to/marketplace-repo

# Remove the plugin entry from the plugins array in marketplace.json
jq 'del(.plugins[] | select(.name == "PLUGIN-NAME"))' \
  .claude-plugin/marketplace.json > tmp.json && mv tmp.json .claude-plugin/marketplace.json
```

### Step 2: Regenerate the README

```bash
# Regenerate the README to remove the plugin from the plugin table
uv run python scripts/generate-readme.py
```

### Step 3: Delete the Notification Workflow from the Plugin Repo

In the plugin repository, remove `.github/workflows/notify-marketplace.yml` and the `MARKETPLACE_PAT` secret (Settings > Secrets and variables > Actions > delete the secret).

### Step 4: Validate and Commit

```bash
uv run python scripts/validate_marketplace.py /path/to/marketplace-repo --verbose
git add .claude-plugin/marketplace.json README.md
git commit -m "chore: remove PLUGIN-NAME from marketplace"
git push
```

---

## Updating a Plugin Version

### Automatic Updates (Recommended)

When the notification workflow is correctly configured, plugin version updates happen automatically:

1. Developer pushes a change to the plugin's `main` branch
2. `notify-marketplace.yml` fires in the plugin repo
3. A `repository_dispatch` event is sent to the marketplace repo
4. The marketplace's update workflow triggers and updates the plugin entry in `marketplace.json`

No manual intervention is required for this flow.

### Manual Update

If automatic updates fail or you need to force a sync:

```bash
cd /path/to/marketplace-repo

# Fetch the latest plugin.json from the plugin repo via GitHub API
LATEST_VERSION=$(gh api repos/OWNER/PLUGIN-REPO/contents/.claude-plugin/plugin.json \
  --jq '.content' | base64 -d | jq -r '.version')

# Update the version in marketplace.json for this plugin
jq --arg v "$LATEST_VERSION" \
  '(.plugins[] | select(.name == "PLUGIN-NAME")).version = $v' \
  .claude-plugin/marketplace.json > tmp.json && mv tmp.json .claude-plugin/marketplace.json

# Commit the updated entry
git add .claude-plugin/marketplace.json
git commit -m "chore: manually update PLUGIN-NAME to version $LATEST_VERSION"
git push
```

### Update All Plugins at Once

```bash
cd /path/to/marketplace-repo

# For each plugin in marketplace.json, fetch its latest version from GitHub
for PLUGIN in $(jq -r '.plugins[] | "\(.source.owner)/\(.source.repo)"' .claude-plugin/marketplace.json); do
  NAME=$(basename "$PLUGIN")
  LATEST=$(gh api "repos/$PLUGIN/contents/.claude-plugin/plugin.json" \
    --jq '.content' | base64 -d | jq -r '.version')
  echo "Updating $NAME to $LATEST"
  jq --arg name "$NAME" --arg v "$LATEST" \
    '(.plugins[] | select(.name == $name)).version = $v' \
    .claude-plugin/marketplace.json > tmp.json && mv tmp.json .claude-plugin/marketplace.json
done

git add .claude-plugin/marketplace.json
git commit -m "chore: update all plugins to latest versions"
git push
```

---

## Configuring MARKETPLACE_PAT Secret

The `MARKETPLACE_PAT` (Personal Access Token) allows the plugin repository's notification workflow to trigger a `repository_dispatch` event in the marketplace repository.

### Creating the Token

1. Go to **GitHub Settings > Developer settings > Personal access tokens > Fine-grained tokens**
   URL: `https://github.com/settings/tokens?type=beta`
2. Click **Generate new token**
3. Configure:
   - **Token name**: `marketplace-notify` (or any descriptive name)
   - **Expiration**: 90 days or longer (set a calendar reminder to rotate)
   - **Repository access**: Select **Only select repositories** and choose the marketplace repository
   - **Permissions**:
     - Repository permissions > **Contents**: Read and write
     - Repository permissions > **Metadata**: Read-only
4. Click **Generate token** and copy it immediately

### Setting the Secret on a Plugin Repository

Using the `gh` CLI:

```bash
# Set the secret on the plugin repository
gh secret set MARKETPLACE_PAT --repo OWNER/PLUGIN-REPO --body "ghp_YOUR_TOKEN_HERE"

# Verify the secret exists (does not reveal the value)
gh secret list --repo OWNER/PLUGIN-REPO
```

Using the GitHub web UI:

1. Navigate to the plugin repository on GitHub
2. Go to **Settings > Secrets and variables > Actions**
3. Click **New repository secret**
4. Name: `MARKETPLACE_PAT`
5. Value: paste the token
6. Click **Add secret**

### Sharing One Token Across Multiple Plugin Repos

A single fine-grained token scoped to the marketplace repository can be reused across all plugin repos that notify the same marketplace. Set the same secret value in each plugin repository.

---

## Installing Notification Workflow

### Step 1: Create the Workflow File

In the plugin repository, create `.github/workflows/notify-marketplace.yml`:

```yaml
name: Notify Marketplace

on:
  push:
    branches:
      - main
    paths:
      - '.claude-plugin/plugin.json'
      - 'commands/**'
      - 'agents/**'
      - 'skills/**'
      - 'hooks/**'
      - 'scripts/**'

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Get plugin info
        id: plugin
        run: |
          PLUGIN_NAME=$(jq -r '.name' .claude-plugin/plugin.json)
          PLUGIN_VERSION=$(jq -r '.version' .claude-plugin/plugin.json)
          echo "name=$PLUGIN_NAME" >> $GITHUB_OUTPUT
          echo "version=$PLUGIN_VERSION" >> $GITHUB_OUTPUT

      - name: Notify marketplace
        uses: peter-evans/repository-dispatch@v4
        with:
          token: ${{ secrets.MARKETPLACE_PAT }}
          repository: MARKETPLACE-OWNER/MARKETPLACE-REPO
          event-type: plugin-updated
          client-payload: >-
            {"plugin": "${{ steps.plugin.outputs.name }}",
             "version": "${{ steps.plugin.outputs.version }}"}
```

### Step 2: Customize the Workflow

Replace `MARKETPLACE-OWNER/MARKETPLACE-REPO` with the actual marketplace repository identifier (e.g., `<placeholder-for-github-repo-owner>/<placeholder-for-marketplace-repo-name>`).

### Step 3: Commit and Push

```bash
cd /path/to/plugin-repo
git add .github/workflows/notify-marketplace.yml
git commit -m "ci: add marketplace notification workflow"
git push
```

---

## Testing the Notification Chain

### Step 1: Make a Test Commit in the Plugin Repo

```bash
cd /path/to/plugin-repo

# Bump the patch version in plugin.json
jq '.version = "0.0.2"' .claude-plugin/plugin.json > tmp.json && mv tmp.json .claude-plugin/plugin.json

# Commit and push
git add .claude-plugin/plugin.json
git commit -m "test: bump version to verify marketplace notification"
git push
```

### Step 2: Check the Plugin Repo's Actions Tab

```bash
# List recent workflow runs for the plugin repo
gh run list --repo OWNER/PLUGIN-REPO --limit 5

# View the most recent run's logs
gh run view --repo OWNER/PLUGIN-REPO --log
```

Verify that the `Notify Marketplace` workflow ran successfully and the dispatch step completed without errors.

### Step 3: Check the Marketplace Repo's Actions Tab

```bash
# List recent workflow runs for the marketplace repo
gh run list --repo OWNER/MARKETPLACE-REPO --limit 5

# View the most recent run's logs
gh run view --repo OWNER/MARKETPLACE-REPO --log
```

Verify that the marketplace update workflow was triggered and completed successfully.

### Step 4: Verify marketplace.json Was Updated

```bash
# Check the latest marketplace.json for the updated version
gh api repos/OWNER/MARKETPLACE-REPO/contents/.claude-plugin/marketplace.json \
  --jq '.content' | base64 -d | jq '.plugins[] | select(.name == "PLUGIN-NAME")'
```

The version field should match the version you set in Step 1.

### Common Failures During Testing

| Symptom | Likely Cause |
|---------|--------------|
| Notification workflow did not run | Push was not to `main` branch, or paths filter excluded the changed file |
| Dispatch step failed with 404 | Marketplace repository does not exist or token lacks access |
| Dispatch step failed with 403 | Token expired or missing `contents: write` permission |
| Marketplace workflow did not trigger | Event type mismatch (must be `plugin-updated`) |
| Marketplace workflow ran but no commit | Plugin version in marketplace.json was already at latest, no changes detected |

---

## Migrating Plugins Between Marketplaces

### When to Use

- Moving plugins from one marketplace to another
- Consolidating multiple marketplaces into one
- Splitting a marketplace into specialized ones

### Step-by-Step Migration

1. **Identify plugins to migrate** -- read source marketplace.json and list the plugins to move.

   ```bash
   # Read source marketplace plugins
   gh api repos/OWNER/source-marketplace/contents/.claude-plugin/marketplace.json \
     --jq '.content' | base64 -d | jq '.plugins[].name'
   ```

2. **Clone or access both marketplace repos** -- source and target.

   ```bash
   gh repo clone OWNER/source-marketplace /tmp/source-marketplace
   gh repo clone OWNER/target-marketplace /tmp/target-marketplace
   ```

3. **For each plugin being migrated:**

   a. Read the plugin's current `notify-marketplace.yml` from the plugin repo:

   ```bash
   gh api repos/OWNER/PLUGIN-REPO/contents/.github/workflows/notify-marketplace.yml \
     --jq '.content' | base64 -d
   ```

   b. Update the `repository` field in `notify-marketplace.yml` to point to the TARGET marketplace repo. Clone the plugin repo, edit the workflow file, commit and push:

   ```bash
   gh repo clone OWNER/PLUGIN-REPO /tmp/PLUGIN-REPO
   cd /tmp/PLUGIN-REPO
   # Edit .github/workflows/notify-marketplace.yml:
   # Change "repository: OWNER/source-marketplace" to "repository: OWNER/target-marketplace"
   git add .github/workflows/notify-marketplace.yml
   git commit -m "ci: migrate notification to target marketplace"
   git push
   ```

   c. Copy the plugin entry from source `marketplace.json` to target `marketplace.json`:

   ```bash
   # Extract the plugin entry from source
   ENTRY=$(jq '.plugins[] | select(.name == "PLUGIN-NAME")' \
     /tmp/source-marketplace/.claude-plugin/marketplace.json)

   # Append to target
   jq --argjson entry "$ENTRY" '.plugins += [$entry]' \
     /tmp/target-marketplace/.claude-plugin/marketplace.json > tmp.json \
     && mv tmp.json /tmp/target-marketplace/.claude-plugin/marketplace.json
   ```

   d. Remove the plugin entry from source `marketplace.json`:

   ```bash
   jq 'del(.plugins[] | select(.name == "PLUGIN-NAME"))' \
     /tmp/source-marketplace/.claude-plugin/marketplace.json > tmp.json \
     && mv tmp.json /tmp/source-marketplace/.claude-plugin/marketplace.json
   ```

4. **Set MARKETPLACE_PAT on target marketplace** -- check if it exists on each migrated plugin repo, set if missing:

   ```bash
   gh secret list --repo OWNER/PLUGIN-REPO | grep MARKETPLACE_PAT
   # If missing or if the token needs to change for the new marketplace:
   gh secret set MARKETPLACE_PAT --repo OWNER/PLUGIN-REPO --body "ghp_TARGET_TOKEN_HERE"
   ```

5. **Regenerate README on both marketplaces:**

   ```bash
   cd /tmp/source-marketplace && uv run python scripts/generate-readme.py
   cd /tmp/target-marketplace && uv run python scripts/generate-readme.py
   ```

6. **Validate both marketplaces:**

   ```bash
   uv run python scripts/validate_marketplace.py /tmp/source-marketplace --verbose
   uv run python scripts/validate_marketplace.py /tmp/target-marketplace --verbose
   ```

7. **Test notification chain** -- push a test commit to a migrated plugin and verify the TARGET marketplace updates:

   ```bash
   cd /tmp/PLUGIN-REPO
   jq '.version = "0.0.99-migration-test"' .claude-plugin/plugin.json > tmp.json \
     && mv tmp.json .claude-plugin/plugin.json
   git add .claude-plugin/plugin.json
   git commit -m "test: verify migration notification chain"
   git push

   # Check that the target marketplace workflow was triggered
   gh run list --repo OWNER/target-marketplace --limit 3
   ```

### Rollback

- If migration fails: revert `notify-marketplace.yml` in plugin repos and restore `marketplace.json` from git in both source and target repos.
- Always validate both source and target after migration completes or after rollback.

```bash
# Rollback plugin notification workflow
cd /tmp/PLUGIN-REPO
git checkout HEAD~1 -- .github/workflows/notify-marketplace.yml
git commit -m "revert: restore original marketplace notification target"
git push

# Rollback marketplace.json on both repos
cd /tmp/source-marketplace
git checkout HEAD~1 -- .claude-plugin/marketplace.json
git commit -m "revert: restore marketplace.json before migration"
git push

cd /tmp/target-marketplace
git checkout HEAD~1 -- .claude-plugin/marketplace.json
git commit -m "revert: restore marketplace.json before migration"
git push
```

---

## Batch Operations

### Linking Multiple Plugins at Once

When setting up a marketplace with several plugins, loop over all of them to perform every linking step:

```bash
cd /path/to/marketplace-repo

# Define the plugins to add (owner/repo pairs)
PLUGINS=(
  "owner/plugin-a"
  "owner/plugin-b"
  "owner/plugin-c"
  "owner/plugin-d"
  "owner/plugin-e"
)

MARKETPLACE_REPO="OWNER/MARKETPLACE-REPO"
TOKEN="ghp_YOUR_TOKEN_HERE"

for PLUGIN in "${PLUGINS[@]}"; do
  NAME=$(basename "$PLUGIN")
  echo "=== Linking $NAME ==="

  # 1. Fetch plugin metadata via GitHub API
  PLUGIN_JSON=$(gh api "repos/$PLUGIN/contents/.claude-plugin/plugin.json" \
    --jq '.content' | base64 -d)
  VERSION=$(echo "$PLUGIN_JSON" | jq -r '.version')
  DESCRIPTION=$(echo "$PLUGIN_JSON" | jq -r '.description')
  OWNER_NAME=$(dirname "$PLUGIN")

  # 2. Add plugin entry to marketplace.json
  jq --arg name "$NAME" --arg ver "$VERSION" --arg desc "$DESCRIPTION" \
     --arg repo "$OWNER_NAME/$NAME" \
    '.plugins += [{
      "name": $name,
      "version": $ver,
      "description": $desc,
      "source": {"source": "github", "repo": $repo}
    }]' .claude-plugin/marketplace.json > tmp.json && mv tmp.json .claude-plugin/marketplace.json

  # 3. Set MARKETPLACE_PAT secret on the plugin repo
  gh secret set MARKETPLACE_PAT --repo "$PLUGIN" --body "$TOKEN"

  # 4. Install notification workflow on the plugin repo
  gh repo clone "$PLUGIN" "/tmp/$NAME"
  mkdir -p "/tmp/$NAME/.github/workflows"
  # Copy template and set the correct marketplace target
  sed "s|MARKETPLACE-OWNER/MARKETPLACE-REPO|$MARKETPLACE_REPO|g" \
    notify-marketplace-template.yml > "/tmp/$NAME/.github/workflows/notify-marketplace.yml"
  cd "/tmp/$NAME"
  git add .github/workflows/notify-marketplace.yml
  git commit -m "ci: add marketplace notification workflow"
  git push
  cd /path/to/marketplace-repo

  echo "=== Done linking $NAME ==="
done

# Validate the marketplace after all plugins are added
uv run python scripts/validate_marketplace.py /path/to/marketplace-repo --verbose

# Regenerate README
uv run python scripts/generate-readme.py

# Commit marketplace changes
git add .claude-plugin/marketplace.json README.md
git commit -m "feat: add plugins $(echo "${PLUGINS[@]}" | tr ' ' ', ')"
git push
```

### Setting MARKETPLACE_PAT on Multiple Plugin Repos

```bash
TOKEN="ghp_YOUR_TOKEN_HERE"

PLUGINS=(
  "owner/plugin-a"
  "owner/plugin-b"
  "owner/plugin-c"
  "owner/plugin-d"
  "owner/plugin-e"
)

for PLUGIN in "${PLUGINS[@]}"; do
  echo "Setting MARKETPLACE_PAT on $PLUGIN..."
  gh secret set MARKETPLACE_PAT --repo "$PLUGIN" --body "$TOKEN"
done
```

### Installing the Notification Workflow on Multiple Repos

```bash
# Assumes you have the workflow template at ./notify-marketplace-template.yml
MARKETPLACE_REPO="OWNER/MARKETPLACE-REPO"

PLUGINS=(
  "owner/plugin-a"
  "owner/plugin-b"
  "owner/plugin-c"
  "owner/plugin-d"
  "owner/plugin-e"
)

for PLUGIN in "${PLUGINS[@]}"; do
  NAME=$(basename "$PLUGIN")
  echo "Installing workflow on $PLUGIN..."
  gh repo clone "$PLUGIN" "/tmp/$NAME"
  mkdir -p "/tmp/$NAME/.github/workflows"
  sed "s|MARKETPLACE-OWNER/MARKETPLACE-REPO|$MARKETPLACE_REPO|g" \
    notify-marketplace-template.yml > "/tmp/$NAME/.github/workflows/notify-marketplace.yml"
  cd "/tmp/$NAME"
  git add .github/workflows/notify-marketplace.yml
  git commit -m "ci: add marketplace notification workflow"
  git push
  cd -
done
```

> **Note:** For batch workflow installation, ensure the template file `notify-marketplace-template.yml` uses `MARKETPLACE-OWNER/MARKETPLACE-REPO` as a placeholder so that `sed` can replace it with the actual marketplace repository for each plugin.
