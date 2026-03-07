# Workflow Templates

## Table of Contents
- [Placeholder Reference](#placeholder-reference)
- [validate.yml (Marketplace CI)](#validateyml-marketplace-ci)
- [update-submodules.yml (Dispatch Receiver)](#update-submodulesyml-dispatch-receiver)
- [notify-marketplace.yml.template (Plugin Side)](#notify-marketplaceymltemplate-plugin-side)

Reusable GitHub Actions workflow templates for the Claude Plugin Marketplace
ecosystem. Based on production workflows from the Emasoft marketplace. Replace
all `{{PLACEHOLDER}}` values with your actual configuration before committing.

---

## Placeholder Reference

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `{{MARKETPLACE_OWNER}}` | GitHub username or org that owns the marketplace repo | `acme-org` |
| `{{MARKETPLACE_REPO}}` | Marketplace repository name | `my-plugins-marketplace` |
| `{{PYTHON_VERSION}}` | Python version for CI jobs | `3.12` |

### Required Secrets

| Secret | Where to Add | Description |
|--------|-------------|-------------|
| `MARKETPLACE_PAT` | Plugin repo **and** marketplace repo | A GitHub PAT (classic) with `repo` scope, owned by a marketplace repo admin. Required for cross-repo dispatch and for pushing past branch protection. |

---

## validate.yml (Marketplace CI)

CI workflow for marketplace repositories. Runs on every push and pull request
to the default branch. Validates marketplace structure, plugin entries
(required fields, valid sources), and lints helper scripts.

**Install location:** `.github/workflows/validate.yml`

### Template

```yaml
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
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '{{PYTHON_VERSION}}'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install ruff mypy pyyaml

      - name: Validate marketplace structure
        run: |
          echo "=== Validating Marketplace Structure ==="

          # Check marketplace.json exists and is valid JSON
          if [ -f ".claude-plugin/marketplace.json" ]; then
            echo "OK marketplace.json exists"
            python -c "import json; json.load(open('.claude-plugin/marketplace.json'))" && echo "OK marketplace.json is valid JSON"
          else
            echo "FAIL marketplace.json not found"
            exit 1
          fi

          # Validate all plugin entries have required fields and valid sources.
          #
          # IMPORTANT: This is inline Python inside a YAML double-quoted shell string.
          # Shell quoting rules apply -- the shell strips inner double quotes before
          # Python sees the code. Therefore:
          #   - NEVER use dict["key"] inside f-strings (shell eats the quotes,
          #     Python sees bare `key` as an undefined variable -> NameError)
          #   - ALWAYS extract dict values into local variables first, then
          #     reference those variables in f-strings
          #   - Use only single quotes inside f-strings
          # See CLAUDE.md "CI/CD Pitfalls" section for full explanation.
          python3 -c "
          import json, sys
          with open('.claude-plugin/marketplace.json') as f:
              data = json.load(f)
          if not data.get('name'):
              print('FAIL: missing marketplace name')
              sys.exit(1)
          plugins = data.get('plugins', [])
          if not plugins:
              print('FAIL: no plugins defined')
              sys.exit(1)
          for p in plugins:
              name = p.get('name', '?')
              if not p.get('name'):
                  print(f'FAIL: plugin entry missing name')
                  sys.exit(1)
              source = p.get('source')
              if not source:
                  print(f'FAIL: plugin {name} missing source')
                  sys.exit(1)
              if isinstance(source, dict):
                  src_type = source.get('source')
                  # Extract values into locals -- do NOT use source['repo'] in
                  # f-strings because shell quoting will mangle the double quotes
                  repo = source.get('repo', '')
                  url = source.get('url', '')
                  if src_type == 'github' and repo:
                      print(f'OK {name}: GitHub source -> {repo}')
                  elif src_type == 'url' and url:
                      print(f'OK {name}: URL source -> {url}')
                  else:
                      print(f'FAIL: plugin {name} invalid source object (needs source+repo or source+url)')
                      sys.exit(1)
              elif isinstance(source, str):
                  print(f'OK {name}: path source -> {source}')
              else:
                  print(f'FAIL: plugin {name} invalid source type')
                  sys.exit(1)
          print(f'')
          print(f'=== All {len(plugins)} plugin entries validated ===')
          "

      - name: Lint marketplace scripts
        run: |
          echo "=== Linting marketplace scripts ==="
          if [ -d "scripts" ]; then
            ruff check scripts/ --select=E,F,W --ignore=E501 || echo "WARNING Some lint warnings (non-blocking)"
          else
            echo "No scripts/ directory to lint"
          fi
```

---

## update-submodules.yml (Dispatch Receiver)

Marketplace-side workflow that receives `plugin-updated` dispatch events from
plugin repos and updates plugin versions in `marketplace.json`. Fetches each
plugin's `plugin.json` from GitHub via the API, updates the version, commits,
and pushes. Uses concurrency serialization to prevent race conditions when
multiple plugins update simultaneously.

**Install location:** `.github/workflows/update-submodules.yml`

### How It Works

1. Receives `repository_dispatch` with `plugin` name (or manual `workflow_dispatch`).
2. Uses `gh api` to fetch `.claude-plugin/plugin.json` from each plugin repo.
3. Updates the version in `marketplace.json`.
4. Commits and pushes via `MARKETPLACE_PAT` (to bypass branch protection).
5. Retries push with `pull --rebase` on concurrent update conflicts.

### Template

```yaml
# Auto-update marketplace versions when plugins are pushed
# This workflow is triggered by repository_dispatch from plugin repos
#
# Setup:
#   1. Add this workflow to your marketplace repo
#   2. Add notify-marketplace.yml to each plugin repo
#   3. Create a PAT with 'repo' scope and add as MARKETPLACE_PAT secret in plugin repos

name: Update Versions

on:
  # Triggered by plugin repos when they push updates
  repository_dispatch:
    types: [plugin-updated]

  # Manual trigger for testing or recovery
  workflow_dispatch:
    inputs:
      plugin:
        description: 'Specific plugin name to update (leave empty for all)'
        required: false
        type: string

permissions:
  contents: write

# Serialize dispatch events to prevent race conditions on git push
concurrency:
  group: update-versions
  cancel-in-progress: false

jobs:
  update-versions:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout marketplace
        uses: actions/checkout@v4
        with:
          fetch-depth: 1
          token: ${{ secrets.MARKETPLACE_PAT }}

      - name: Show trigger info
        run: |
          echo "Triggered by: ${{ github.event_name }}"
          if [ "${{ github.event_name }}" = "repository_dispatch" ]; then
            echo "Plugin: ${{ github.event.client_payload.plugin }}"
            echo "Ref: ${{ github.event.client_payload.ref }}"
            echo "Actor: ${{ github.event.client_payload.triggered_by }}"
          fi

      - name: Configure git
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '{{PYTHON_VERSION}}'

      - name: Fetch and update plugin version
        env:
          GH_TOKEN: ${{ secrets.MARKETPLACE_PAT }}
        run: |
          PLUGIN="${{ github.event.client_payload.plugin || inputs.plugin }}"
          MARKETPLACE_JSON=".claude-plugin/marketplace.json"

          if [ -z "$PLUGIN" ]; then
            echo "No specific plugin specified, updating all..."
            # Read all plugin names from marketplace.json
            PLUGINS=$(python3 -c "
          import json
          with open('$MARKETPLACE_JSON') as f:
              data = json.load(f)
          for p in data.get('plugins', []):
              name = p.get('name', '')
              source = p.get('source', {})
              if isinstance(source, dict) and source.get('source') == 'github':
                  repo_val = source.get('repo', '')
                  print(f'{name}|{repo_val}')
                  continue
              if isinstance(source, dict) and source.get('source') == 'url':
                  repo = source.get('url', '').rstrip('/').removesuffix('.git')
                  owner_repo = '/'.join(repo.split('/')[-2:])
                  print(f'{name}|{owner_repo}')
          ")
          else
            # Single plugin - extract repo from marketplace.json
            REPO_INFO=$(python3 -c "
          import json
          with open('$MARKETPLACE_JSON') as f:
              data = json.load(f)
          for p in data.get('plugins', []):
              if p.get('name') == '$PLUGIN':
                  source = p.get('source', {})
                  if isinstance(source, dict) and source.get('source') == 'github':
                      repo_val = source.get('repo', '')
                      print(f'$PLUGIN|{repo_val}')
                  elif isinstance(source, dict) and source.get('source') == 'url':
                      repo = source.get('url', '').rstrip('/').removesuffix('.git')
                      owner_repo = '/'.join(repo.split('/')[-2:])
                      print(f'$PLUGIN|{owner_repo}')
                  break
          ")
            PLUGINS="$REPO_INFO"
          fi

          UPDATED=0
          echo "$PLUGINS" | while IFS='|' read -r name owner_repo; do
            if [ -z "$name" ] || [ -z "$owner_repo" ]; then
              continue
            fi
            echo "Fetching version for $name from $owner_repo..."

            # Fetch plugin.json from the plugin repo via GitHub API
            VERSION=$(gh api "repos/$owner_repo/contents/.claude-plugin/plugin.json" \
              --jq '.content' 2>/dev/null | base64 -d 2>/dev/null | \
              python3 -c "import sys,json; print(json.load(sys.stdin).get('version',''))" 2>/dev/null || echo "")

            if [ -n "$VERSION" ]; then
              echo "  $name: found version $VERSION"
              # Update marketplace.json
              python3 -c "
          import json
          with open('$MARKETPLACE_JSON') as f:
              data = json.load(f)
          for p in data.get('plugins', []):
              if p.get('name') == '$name':
                  old = p.get('version', 'unknown')
                  if old != '$VERSION':
                      p['version'] = '$VERSION'
                      pname = p.get('name', '?')
                      print(f'  Updated {pname}: {old} -> $VERSION')
                  else:
                      pname = p.get('name', '?')
                      print(f'  {pname}: already at $VERSION')
                  break
          with open('$MARKETPLACE_JSON', 'w') as f:
              json.dump(data, f, indent=2)
              f.write('\n')
          "
            else
              echo "  WARNING: Could not fetch version for $name"
            fi
          done

      - name: Check for changes
        id: changes
        run: |
          if git diff --quiet; then
            echo "has_changes=false" >> $GITHUB_OUTPUT
            echo "No changes detected"
          else
            echo "has_changes=true" >> $GITHUB_OUTPUT
            echo "Changes detected:"
            git diff --stat
          fi

      - name: Commit and push
        if: steps.changes.outputs.has_changes == 'true'
        run: |
          git add .claude-plugin/marketplace.json

          # Build commit message
          PLUGIN="${{ github.event.client_payload.plugin || inputs.plugin || 'all' }}"

          if [ "$PLUGIN" != "all" ]; then
            NEW_VERSION=$(python3 -c "
          import json
          with open('.claude-plugin/marketplace.json') as f:
              data = json.load(f)
          for p in data.get('plugins', []):
              if p.get('name') == '$PLUGIN':
                  print(p.get('version', 'unknown'))
                  break
          " 2>/dev/null || echo "unknown")
            git commit -m "chore: update $PLUGIN to $NEW_VERSION"
          else
            git commit -m "chore: sync marketplace.json plugin versions"
          fi

          # Retry push with pull-rebase in case of concurrent updates
          for attempt in 1 2 3; do
            if git push; then
              echo "Push succeeded on attempt $attempt"
              break
            fi
            echo "Push failed (attempt $attempt), pulling and retrying..."
            git pull --rebase origin main
          done

      - name: Summary
        run: |
          echo "## Version Update Summary" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "**Trigger:** ${{ github.event_name }}" >> $GITHUB_STEP_SUMMARY
          if [ "${{ github.event_name }}" = "repository_dispatch" ]; then
            echo "**Plugin:** ${{ github.event.client_payload.plugin }}" >> $GITHUB_STEP_SUMMARY
          fi
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "### marketplace.json" >> $GITHUB_STEP_SUMMARY
          echo '```json' >> $GITHUB_STEP_SUMMARY
          python3 -c "
          import json
          data = json.load(open('.claude-plugin/marketplace.json'))
          for p in data.get('plugins', []):
              name = p.get('name', '?')
              ver = p.get('version', '?')
              print(f'  {name}: {ver}')
          " >> $GITHUB_STEP_SUMMARY
          echo '```' >> $GITHUB_STEP_SUMMARY
```

---

## notify-marketplace.yml.template (Plugin Side)

This template is installed in each **plugin repository**. It fires when
plugin files change on the default branch and sends a `repository_dispatch`
event to the marketplace repo so it can pull the latest plugin metadata.

**Install location:** `scripts/notify-marketplace.yml.template`
(Copy to each plugin repo as `.github/workflows/notify-marketplace.yml`)

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
# Requirements:
#   - Create a PAT (Personal Access Token) with 'repo' scope
#   - Add it as a secret named MARKETPLACE_PAT in the plugin repo
#   - Update MARKETPLACE_OWNER and MARKETPLACE_REPO below

name: Notify Marketplace

on:
  push:
    branches: [main, master]
    paths:
      - '.claude-plugin/plugin.json'
      - 'hooks/**'
      - 'commands/**'
      - 'agents/**'
      - 'skills/**'
      - 'scripts/**'

env:
  # Update these to match your marketplace repo
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
        uses: peter-evans/repository-dispatch@v2
        with:
          token: ${{ secrets.MARKETPLACE_PAT }}
          repository: ${{ env.MARKETPLACE_OWNER }}/${{ env.MARKETPLACE_REPO }}
          event-type: plugin-updated
          client-payload: |
            {
              "plugin": "${{ steps.plugin.outputs.name }}",
              "ref": "${{ steps.plugin.outputs.ref }}",
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

---

## Action Version Reference

All templates in this document use the following action versions:

| Action | Version | Notes |
|--------|---------|-------|
| `actions/checkout` | `v4` | Node 20 runtime |
| `actions/setup-python` | `v5` | Supports Python 3.8 -- 3.13 |
| `peter-evans/repository-dispatch` | `v2` | Cross-repo dispatch |

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

- The update workflow uses `concurrency` serialization to prevent simultaneous pushes.
- If two plugins update simultaneously, one job will queue behind the other.
- If a push still fails, the workflow retries with `pull --rebase` up to 3 times.

### Shell quoting in inline Python (CI/CD pitfall)

- When embedding Python inside YAML shell steps, the shell strips double quotes.
- Never use `dict["key"]` in f-strings -- extract to local variables first.
- Use only single quotes inside f-strings in inline Python blocks.
