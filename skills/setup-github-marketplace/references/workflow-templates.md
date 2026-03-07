# Workflow Templates

## Table of Contents

- [Placeholder Reference](#placeholder-reference)
- [notify-marketplace.yml (Plugin Side)](#notify-marketplaceyml-plugin-side)
- [sync-plugins.yml (Marketplace Side)](#sync-pluginsyml-marketplace-side)
- [validate-marketplace.yml (Marketplace CI)](#validate-marketplaceyml-marketplace-ci)
- [Plugin CI Workflow (Optional)](#plugin-ci-workflow-optional)

Reusable GitHub Actions workflow templates for the Claude Plugin Marketplace ecosystem.
Each template is a complete, copy-pasteable YAML file. Replace all `{{PLACEHOLDER}}`
values with your actual configuration before committing.

---

## Placeholder Reference

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `{{MARKETPLACE_OWNER}}` | GitHub username or org that owns the marketplace repo | `acme-org` |
| `{{MARKETPLACE_REPO}}` | Marketplace repository name | `claude-plugins-marketplace` |
| `{{PLUGIN_NAME}}` | Plugin name from plugin.json | `my-awesome-plugin` |
| `{{PYTHON_VERSION}}` | Python version for CI jobs | `3.12` |
| `{{VALIDATION_PKG}}` | PyPI or Git URL for the validator package | `claude-plugins-validation` |

### Required Secrets

| Secret | Where to Add | Description |
|--------|-------------|-------------|
| `MARKETPLACE_PAT` | Plugin repo **and** marketplace repo | A GitHub PAT (classic) with `repo` scope, owned by a marketplace repo admin. Required for cross-repo dispatch and for pushing past branch protection. |

---

## notify-marketplace.yml (Plugin Side)

This workflow is installed in each **plugin repository**. It fires when plugin
files change on the default branch and sends a `repository_dispatch` event to
the marketplace repo so it can pull the latest plugin metadata.

**Install location:** `.github/workflows/notify-marketplace.yml`

### Setup Instructions

1. **Create a Personal Access Token (PAT):**
   - GitHub Settings > Developer settings > Personal access tokens > Tokens (classic)
   - Scope: `repo` (full control of private repositories)
   - Copy the token immediately -- it is shown only once.

2. **Add the PAT as a repository secret** in the plugin repo:
   - Settings > Secrets and variables > Actions > New repository secret
   - Name: `MARKETPLACE_PAT`
   - Value: the PAT from step 1.

3. **Replace the placeholders** `{{MARKETPLACE_OWNER}}` and `{{MARKETPLACE_REPO}}` below.

> **Important:** Do NOT use `GITHUB_TOKEN` -- it lacks permission to trigger
> workflows in other repositories.

### Template

```yaml
# Notify marketplace repo when this plugin is updated
# Place this in each plugin repo: .github/workflows/notify-marketplace.yml
#
# Required secret: MARKETPLACE_PAT (PAT with 'repo' scope)
# Required placeholders: {{MARKETPLACE_OWNER}}, {{MARKETPLACE_REPO}}

name: Notify Marketplace

on:
  push:
    branches: [main, master]
    paths:
      - '.claude-plugin/**'
      - 'hooks/**'
      - 'commands/**'
      - 'agents/**'
      - 'skills/**'
      - 'scripts/**'

env:
  MARKETPLACE_OWNER: '{{MARKETPLACE_OWNER}}'
  MARKETPLACE_REPO: '{{MARKETPLACE_REPO}}'

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
          echo "Triggered update in **${{ env.MARKETPLACE_OWNER }}/${{ env.MARKETPLACE_REPO }}**" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "| Field | Value |" >> $GITHUB_STEP_SUMMARY
          echo "|-------|-------|" >> $GITHUB_STEP_SUMMARY
          echo "| Plugin | ${{ steps.plugin.outputs.name }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Commit | \`${{ steps.plugin.outputs.ref }}\` |" >> $GITHUB_STEP_SUMMARY
          echo "| Actor | ${{ github.actor }} |" >> $GITHUB_STEP_SUMMARY
```

---

## sync-plugins.yml (Marketplace Side)

This workflow is installed in the **marketplace repository**. It receives the
`plugin-updated` dispatch event and updates `marketplace.json` by fetching the
plugin's `plugin.json` directly via the GitHub API. This avoids the fragility
of git submodules -- the API approach is stateless and more reliable in CI.

**Install location:** `.github/workflows/sync-plugins.yml`

### How It Works

1. Receives `repository_dispatch` with `plugin` name and `source_repo`.
2. Uses `gh api` to fetch `.claude-plugin/plugin.json` from the plugin repo.
3. Merges the plugin metadata into `marketplace.json`.
4. Optionally runs `sync_marketplace_versions.py` and `generate-readme.py`.
5. Commits and pushes via `MARKETPLACE_PAT` (to bypass branch protection).

### Template

```yaml
# Auto-update marketplace metadata when plugins are pushed
# This workflow is triggered by repository_dispatch from plugin repos
#
# Required secret: MARKETPLACE_PAT (PAT with 'repo' scope, owned by a repo admin)
#
# Setup:
#   1. Add this workflow to your marketplace repo
#   2. Add notify-marketplace.yml to each plugin repo
#   3. Create a PAT with 'repo' scope and add as MARKETPLACE_PAT secret in:
#      - Each plugin repo (for triggering this workflow)
#      - This marketplace repo (for checkout + push past branch protection)

name: Update Marketplace

on:
  repository_dispatch:
    types: [plugin-updated]

  workflow_dispatch:
    inputs:
      plugin:
        description: 'Plugin repo name to update (owner/repo or just repo name)'
        required: false
        type: string
      source_repo:
        description: 'Full source repo (owner/repo). Required if plugin is just a name.'
        required: false
        type: string

permissions:
  contents: write

jobs:
  update-marketplace:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout marketplace
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
          token: ${{ secrets.MARKETPLACE_PAT }}

      - name: Resolve plugin info
        id: info
        env:
          GH_TOKEN: ${{ secrets.MARKETPLACE_PAT }}
        run: |
          # Determine plugin name and source repo from dispatch or manual input
          if [ "${{ github.event_name }}" = "repository_dispatch" ]; then
            PLUGIN="${{ github.event.client_payload.plugin }}"
            SOURCE_REPO="${{ github.event.client_payload.source_repo }}"
            TRIGGERED_BY="${{ github.event.client_payload.triggered_by }}"
          else
            PLUGIN="${{ inputs.plugin }}"
            SOURCE_REPO="${{ inputs.source_repo }}"
            TRIGGERED_BY="${{ github.actor }}"
          fi

          # If source_repo is empty, assume same owner as this marketplace repo
          if [ -z "$SOURCE_REPO" ] && [ -n "$PLUGIN" ]; then
            SOURCE_REPO="${{ github.repository_owner }}/$PLUGIN"
          fi

          echo "plugin=$PLUGIN" >> $GITHUB_OUTPUT
          echo "source_repo=$SOURCE_REPO" >> $GITHUB_OUTPUT
          echo "triggered_by=$TRIGGERED_BY" >> $GITHUB_OUTPUT

          echo "Plugin: $PLUGIN"
          echo "Source repo: $SOURCE_REPO"
          echo "Triggered by: $TRIGGERED_BY"

      - name: Fetch plugin.json from source repo
        id: fetch
        env:
          GH_TOKEN: ${{ secrets.MARKETPLACE_PAT }}
        run: |
          SOURCE_REPO="${{ steps.info.outputs.source_repo }}"
          PLUGIN="${{ steps.info.outputs.plugin }}"

          if [ -z "$SOURCE_REPO" ] || [ -z "$PLUGIN" ]; then
            echo "::error::Missing plugin name or source_repo. Nothing to update."
            exit 1
          fi

          echo "Fetching .claude-plugin/plugin.json from $SOURCE_REPO ..."

          # Use GitHub API to fetch the file content (base64-decoded)
          PLUGIN_JSON=$(gh api \
            "repos/$SOURCE_REPO/contents/.claude-plugin/plugin.json" \
            --jq '.content' | base64 --decode 2>/dev/null) || {
            echo "::error::Failed to fetch plugin.json from $SOURCE_REPO"
            exit 1
          }

          echo "$PLUGIN_JSON" > /tmp/plugin-metadata.json

          # Extract key fields
          PLUGIN_VERSION=$(echo "$PLUGIN_JSON" | python3 -c \
            "import sys,json; print(json.load(sys.stdin).get('version','0.0.0'))")
          PLUGIN_DISPLAY=$(echo "$PLUGIN_JSON" | python3 -c \
            "import sys,json; print(json.load(sys.stdin).get('name','$PLUGIN'))")

          echo "version=$PLUGIN_VERSION" >> $GITHUB_OUTPUT
          echo "display_name=$PLUGIN_DISPLAY" >> $GITHUB_OUTPUT

          echo "Fetched plugin: $PLUGIN_DISPLAY v$PLUGIN_VERSION"

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '{{PYTHON_VERSION}}'

      - name: Update marketplace.json
        run: |
          PLUGIN="${{ steps.info.outputs.plugin }}"
          SOURCE_REPO="${{ steps.info.outputs.source_repo }}"
          VERSION="${{ steps.fetch.outputs.version }}"
          DISPLAY_NAME="${{ steps.fetch.outputs.display_name }}"

          MARKETPLACE_FILE=".claude-plugin/marketplace.json"

          if [ ! -f "$MARKETPLACE_FILE" ]; then
            echo "::error::$MARKETPLACE_FILE not found in marketplace repo"
            exit 1
          fi

          # Merge the plugin entry into marketplace.json
          python3 << 'PYEOF'
          import json, os, datetime

          marketplace_file = os.environ.get("MARKETPLACE_FILE", ".claude-plugin/marketplace.json")
          plugin_name = os.environ.get("PLUGIN", "")
          source_repo = os.environ.get("SOURCE_REPO", "")
          version = os.environ.get("VERSION", "0.0.0")
          display_name = os.environ.get("DISPLAY_NAME", plugin_name)

          with open(marketplace_file) as f:
              marketplace = json.load(f)

          # Ensure plugins list exists
          if "plugins" not in marketplace:
              marketplace["plugins"] = []

          # Find existing entry or create new one
          entry = None
          for p in marketplace["plugins"]:
              if p.get("name") == plugin_name or p.get("repo") == source_repo:
                  entry = p
                  break

          if entry is None:
              entry = {"name": plugin_name}
              marketplace["plugins"].append(entry)

          # Update fields
          entry["name"] = plugin_name
          entry["display_name"] = display_name
          entry["version"] = version
          entry["repo"] = source_repo
          entry["updated_at"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

          # Merge any extra metadata from the fetched plugin.json
          try:
              with open("/tmp/plugin-metadata.json") as f:
                  meta = json.load(f)
              for key in ("description", "author", "license", "tags", "min_claude_version"):
                  if key in meta:
                      entry[key] = meta[key]
          except Exception:
              pass

          with open(marketplace_file, "w") as f:
              json.dump(marketplace, f, indent=2)
              f.write("\n")

          print(f"Updated {plugin_name} to v{version} in {marketplace_file}")
          PYEOF
        env:
          MARKETPLACE_FILE: .claude-plugin/marketplace.json
          PLUGIN: ${{ steps.info.outputs.plugin }}
          SOURCE_REPO: ${{ steps.info.outputs.source_repo }}
          VERSION: ${{ steps.fetch.outputs.version }}
          DISPLAY_NAME: ${{ steps.fetch.outputs.display_name }}

      - name: Run sync script (if exists)
        run: |
          if [ -f "scripts/sync_marketplace_versions.py" ]; then
            echo "Running sync_marketplace_versions.py ..."
            python3 scripts/sync_marketplace_versions.py
          else
            echo "No sync_marketplace_versions.py found -- skipping."
          fi

      - name: Run readme generator (if exists)
        run: |
          if [ -f "scripts/generate-readme.py" ]; then
            echo "Running generate-readme.py ..."
            python3 scripts/generate-readme.py
          else
            echo "No generate-readme.py found -- skipping."
          fi

      - name: Configure git
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"

      - name: Check for changes
        id: changes
        run: |
          if git diff --quiet && git diff --staged --quiet; then
            echo "has_changes=false" >> $GITHUB_OUTPUT
            echo "No changes detected."
          else
            echo "has_changes=true" >> $GITHUB_OUTPUT
            echo "Changes detected:"
            git diff --stat
          fi

      - name: Commit and push
        if: steps.changes.outputs.has_changes == 'true'
        run: |
          git add .

          PLUGIN="${{ steps.info.outputs.plugin }}"
          VERSION="${{ steps.fetch.outputs.version }}"

          git commit -m "chore: update $PLUGIN to v$VERSION"

          # Push uses MARKETPLACE_PAT configured at checkout to bypass branch protection
          git push

      - name: Summary
        run: |
          echo "## Marketplace Update Summary" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "| Field | Value |" >> $GITHUB_STEP_SUMMARY
          echo "|-------|-------|" >> $GITHUB_STEP_SUMMARY
          echo "| Trigger | \`${{ github.event_name }}\` |" >> $GITHUB_STEP_SUMMARY
          echo "| Plugin | ${{ steps.info.outputs.plugin }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Version | ${{ steps.fetch.outputs.version }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Source | ${{ steps.info.outputs.source_repo }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Changes | ${{ steps.changes.outputs.has_changes }} |" >> $GITHUB_STEP_SUMMARY
```

---

## validate-marketplace.yml (Marketplace CI)

Standard CI workflow for marketplace repositories. Runs on every push and pull
request to the default branch. Validates the marketplace structure, each plugin
entry in `marketplace.json`, and optionally lints any helper scripts.

**Install location:** `.github/workflows/validate-marketplace.yml`

### Template

```yaml
# CI validation for the marketplace repository
# Runs on push and PR to default branches

name: Marketplace Validation

on:
  push:
    branches: [main, master]
  pull_request:
    branches: [main, master]

jobs:
  validate:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '{{PYTHON_VERSION}}'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install ruff pyyaml

      - name: Validate marketplace.json exists and is valid
        run: |
          echo "=== Validating marketplace.json ==="

          MARKETPLACE_FILE=".claude-plugin/marketplace.json"

          if [ ! -f "$MARKETPLACE_FILE" ]; then
            echo "::error::$MARKETPLACE_FILE not found"
            exit 1
          fi
          echo "Found $MARKETPLACE_FILE"

          # Validate JSON syntax
          python3 -c "import json; json.load(open('$MARKETPLACE_FILE'))" || {
            echo "::error::$MARKETPLACE_FILE is not valid JSON"
            exit 1
          }
          echo "JSON syntax is valid."

      - name: Validate plugin entries
        run: |
          echo "=== Validating plugin entries ==="

          python3 << 'PYEOF'
          import json, sys

          REQUIRED_FIELDS = ["name", "version", "repo"]

          with open(".claude-plugin/marketplace.json") as f:
              data = json.load(f)

          plugins = data.get("plugins", [])
          if not plugins:
              print("WARNING: marketplace.json has no plugins listed.")
              sys.exit(0)

          errors = []
          for i, plugin in enumerate(plugins):
              for field in REQUIRED_FIELDS:
                  if field not in plugin or not plugin[field]:
                      errors.append(f"Plugin #{i} ({plugin.get('name','UNNAMED')}): missing '{field}'")

          if errors:
              print("=== Validation Errors ===")
              for e in errors:
                  print(f"  - {e}")
              sys.exit(1)

          print(f"All {len(plugins)} plugin(s) have required fields.")
          PYEOF

      - name: Run validate_marketplace.py (if available)
        run: |
          VALIDATOR=""
          for candidate in \
            "scripts/validate_marketplace.py" \
            "claude-plugins-validation/scripts/validate_marketplace.py"; do
            if [ -f "$candidate" ]; then
              VALIDATOR="$candidate"
              break
            fi
          done

          if [ -n "$VALIDATOR" ]; then
            echo "Running $VALIDATOR ..."
            python3 "$VALIDATOR" --verbose || {
              echo "::warning::Validator reported issues (see above)"
            }
          else
            echo "No validate_marketplace.py found -- basic checks only."
          fi

      - name: Lint marketplace scripts
        run: |
          echo "=== Linting scripts/ ==="
          if [ -d "scripts" ]; then
            ruff check scripts/ --select=E,F,W --ignore=E501 || \
              echo "::warning::Lint warnings found (non-blocking)"
          else
            echo "No scripts/ directory to lint."
          fi

      - name: Summary
        run: |
          echo "## Marketplace Validation" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          PLUGIN_COUNT=$(python3 -c \
            "import json; print(len(json.load(open('.claude-plugin/marketplace.json')).get('plugins',[])))" \
            2>/dev/null || echo "?")
          echo "| Metric | Value |" >> $GITHUB_STEP_SUMMARY
          echo "|--------|-------|" >> $GITHUB_STEP_SUMMARY
          echo "| Plugins | $PLUGIN_COUNT |" >> $GITHUB_STEP_SUMMARY
          echo "| Status | Passed |" >> $GITHUB_STEP_SUMMARY
```

---

## Plugin CI Workflow (Optional)

An optional CI workflow for **individual plugin repositories** that validates
the plugin structure on every push. Uses `uv` to install the validator package
and runs `validate_plugin.py` against the repo root.

**Install location:** `.github/workflows/validate-plugin.yml`

### Template

```yaml
# Optional CI for individual plugin repos
# Validates plugin structure on every push and PR

name: Validate Plugin

on:
  push:
    branches: [main, master]
    paths:
      - '.claude-plugin/**'
      - 'hooks/**'
      - 'commands/**'
      - 'agents/**'
      - 'skills/**'
      - 'scripts/**'
  pull_request:
    branches: [main, master]

jobs:
  validate:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          version: "latest"

      - name: Set up Python
        run: uv python install {{PYTHON_VERSION}}

      - name: Install validator
        run: |
          uv venv
          # Install from PyPI (preferred) or from Git
          # Option A: PyPI
          # uv pip install {{VALIDATION_PKG}}
          # Option B: Git
          uv pip install "git+https://github.com/{{MARKETPLACE_OWNER}}/claude-plugins-validation.git"

      - name: Validate plugin
        run: |
          echo "=== Validating Plugin Structure ==="

          # Find the validate_plugin.py script
          VALIDATOR=$(find .venv -name "validate_plugin.py" -path "*/scripts/*" 2>/dev/null | head -1)

          if [ -z "$VALIDATOR" ]; then
            echo "::error::validate_plugin.py not found in installed package"
            exit 1
          fi

          uv run python "$VALIDATOR" . --verbose
          echo "Plugin validation passed."

      - name: Validate plugin.json schema
        run: |
          python3 << 'PYEOF'
          import json, sys

          REQUIRED_FIELDS = ["name", "version", "description"]

          try:
              with open(".claude-plugin/plugin.json") as f:
                  data = json.load(f)
          except FileNotFoundError:
              print("::error::.claude-plugin/plugin.json not found")
              sys.exit(1)
          except json.JSONDecodeError as e:
              print(f"::error::plugin.json is not valid JSON: {e}")
              sys.exit(1)

          errors = []
          for field in REQUIRED_FIELDS:
              if field not in data or not data[field]:
                  errors.append(f"Missing required field: '{field}'")

          if "version" in data:
              parts = data["version"].split(".")
              if len(parts) < 2:
                  errors.append(f"Version '{data['version']}' should be semver (e.g. 1.0.0)")

          if errors:
              for e in errors:
                  print(f"::error::{e}")
              sys.exit(1)

          print(f"plugin.json valid: {data['name']} v{data['version']}")
          PYEOF

      - name: Lint plugin scripts
        run: |
          if [ -d "scripts" ]; then
            uv pip install ruff
            uv run ruff check scripts/ --select=E,F,W --ignore=E501 || \
              echo "::warning::Lint warnings (non-blocking)"
          fi

      - name: Summary
        run: |
          PNAME=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json')).get('name','unknown'))" 2>/dev/null || echo "unknown")
          PVER=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json')).get('version','?'))" 2>/dev/null || echo "?")
          echo "## Plugin Validation" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "| Field | Value |" >> $GITHUB_STEP_SUMMARY
          echo "|-------|-------|" >> $GITHUB_STEP_SUMMARY
          echo "| Plugin | $PNAME |" >> $GITHUB_STEP_SUMMARY
          echo "| Version | $PVER |" >> $GITHUB_STEP_SUMMARY
          echo "| Status | Passed |" >> $GITHUB_STEP_SUMMARY
```

---

## Action Version Reference

All templates in this document use the following action versions:

| Action | Version | Notes |
|--------|---------|-------|
| `actions/checkout` | `v4` | Node 20 runtime |
| `actions/setup-python` | `v5` | Supports Python 3.8 -- 3.13 |
| `peter-evans/repository-dispatch` | `v4` | Node 20, supports fine-grained PATs |
| `astral-sh/setup-uv` | `v4` | Installs the `uv` package manager |

---

## Troubleshooting

### `repository_dispatch` not firing

- Verify the PAT has `repo` scope (not just `public_repo`).
- The PAT must belong to a user with **write** access to the target repo.
- `GITHUB_TOKEN` cannot trigger `repository_dispatch` in another repo -- use a PAT.

### Push rejected by branch protection

- The `MARKETPLACE_PAT` must belong to a **repository admin**.
- Branch protection must have "Allow administrators to bypass" enabled.
- Both `checkout` and `push` must use the same PAT (set `token:` in the checkout step).

### `gh api` returns 404 for plugin.json

- Ensure the plugin repo has `.claude-plugin/plugin.json` committed on the default branch.
- If the repo is private, the PAT must have `repo` scope.
- Check the `source_repo` payload is in `owner/repo` format.

### marketplace.json merge conflicts

- The update workflow always reads the current marketplace.json from the checked-out branch.
- If two plugins update simultaneously, one push may fail. Re-run the failed workflow manually.
