# Marketplace Receiver Workflow Template

## Table of Contents
- [Placeholders](#placeholders)
- [Layout A Receiver](#layout-a-receiver)
- [Layout B Receiver](#layout-b-receiver)
- [Payload Contract](#payload-contract)
- [Concurrency and Loops](#concurrency-and-loops)
- [Troubleshooting](#troubleshooting)

The receiver workflow lives in the **marketplace** repo and listens for the
`plugin-updated` dispatch events that plugin repos send. It updates
`marketplace.json` and commits the change. CPV's marketplace uses the Layout
A variant. Both templates are drop-in and work with the notify workflow in
`notify-workflow-template.md`.

---

## Placeholders

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `<PAT_SECRET_NAME>`  | Secret name holding the PAT | `MARKETPLACE_PAT` |
| `<DEFAULT_BRANCH>`   | Marketplace default branch | `main` |
| `<PYTHON_VERSION>`   | Python runtime for jq/python steps | `3.12` |
| `<PLUGIN_SUBDIR>`    | For Layout B: directory under `plugins/` | `plugins/my-plugin` |

The PAT stored here must belong to a user with **write** access to the
marketplace repo and must be able to bypass branch protection.

---

## Layout A Receiver

Use this when each plugin lives in its own GitHub repo and the marketplace
references it as `{"source": "github", "repo": "owner/plugin"}`. The
receiver fetches `.claude-plugin/plugin.json` from the plugin repo via the
GitHub API and writes the `version` field into `marketplace.json`.

Install at `.github/workflows/update-plugin-version.yml` in the marketplace repo.

```yaml
name: Update plugin version

on:
  repository_dispatch:
    types: [plugin-updated]
  workflow_dispatch:
    inputs:
      plugin:
        description: Plugin name to refresh (leave blank for all)
        required: false

permissions:
  contents: write

# Serialize concurrent dispatches so two plugins updating at once do not
# fight over marketplace.json.
concurrency:
  group: update-plugin-version
  cancel-in-progress: false

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout marketplace
        uses: actions/checkout@v4
        with:
          token: ${{ secrets.MARKETPLACE_PAT }}
          fetch-depth: 1

      - name: Configure git
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"

      - name: Setup python
        uses: actions/setup-python@v5
        with:
          python-version: '<PYTHON_VERSION>'

      - name: Resolve target plugin
        id: target
        env:
          PLUGIN_FROM_DISPATCH: ${{ github.event.client_payload.plugin }}
          VERSION_FROM_DISPATCH: ${{ github.event.client_payload.version }}
          PLUGIN_FROM_MANUAL: ${{ inputs.plugin }}
        run: |
          set -euo pipefail
          PLUGIN="${PLUGIN_FROM_DISPATCH:-$PLUGIN_FROM_MANUAL}"
          VERSION="${VERSION_FROM_DISPATCH:-}"
          if [[ -z "$PLUGIN" ]]; then
            echo "No plugin name in payload; aborting." >&2
            exit 1
          fi
          echo "plugin=$PLUGIN"   >> "$GITHUB_OUTPUT"
          echo "version=$VERSION" >> "$GITHUB_OUTPUT"

      - name: Fetch version from plugin repo if missing
        id: fetch
        if: steps.target.outputs.version == ''
        env:
          GH_TOKEN: ${{ secrets.MARKETPLACE_PAT }}
          PLUGIN: ${{ steps.target.outputs.plugin }}
        run: |
          set -euo pipefail
          # Find the plugin entry to get owner/repo
          OWNER_REPO=$(python3 - <<'PY'
          import json, os
          data = json.load(open('.claude-plugin/marketplace.json'))
          plugin = os.environ['PLUGIN']
          for p in data.get('plugins', []):
              if p.get('name') == plugin:
                  src = p.get('source', {})
                  if isinstance(src, dict) and src.get('source') == 'github':
                      print(src.get('repo', ''))
                  break
          PY
          )
          if [[ -z "$OWNER_REPO" ]]; then
            echo "Plugin $PLUGIN not found as github source; nothing to do."
            echo "version=" >> "$GITHUB_OUTPUT"
            exit 0
          fi
          VERSION=$(gh api "repos/$OWNER_REPO/contents/.claude-plugin/plugin.json" --jq '.content' \
            | base64 -d \
            | python3 -c "import sys,json; print(json.load(sys.stdin).get('version',''))")
          echo "version=$VERSION" >> "$GITHUB_OUTPUT"

      - name: Update marketplace.json
        env:
          PLUGIN: ${{ steps.target.outputs.plugin }}
          VERSION: ${{ steps.target.outputs.version || steps.fetch.outputs.version }}
        run: |
          set -euo pipefail
          if [[ -z "$VERSION" ]]; then
            echo "No version resolved; skipping."
            exit 0
          fi
          python3 - <<'PY'
          import json, os
          path = '.claude-plugin/marketplace.json'
          data = json.load(open(path))
          plugin  = os.environ['PLUGIN']
          version = os.environ['VERSION']
          for p in data.get('plugins', []):
              if p.get('name') == plugin:
                  old = p.get('version', '')
                  if old != version:
                      p['version'] = version
                      print(f'updated {plugin}: {old} -> {version}')
                  else:
                      print(f'{plugin} already at {version}')
                  break
          with open(path, 'w') as f:
              json.dump(data, f, indent=2)
              f.write('\n')
          PY

      - name: Commit and push if changed
        run: |
          set -euo pipefail
          if git diff --quiet -- .claude-plugin/marketplace.json; then
            echo "No changes."
            exit 0
          fi
          git add .claude-plugin/marketplace.json
          git commit -m "chore: bump ${{ steps.target.outputs.plugin }} to ${{ steps.target.outputs.version || steps.fetch.outputs.version }} [skip ci]"
          for attempt in 1 2 3; do
            if git push; then exit 0; fi
            git pull --rebase origin <DEFAULT_BRANCH>
          done
          exit 1
```

---

## Layout B Receiver

Use this when plugins live as subdirectories inside the marketplace repo
(Layout B nested single-repo). Each plugin has its own
`plugins/<name>/.claude-plugin/plugin.json`. The receiver reads the local
file instead of calling the GitHub API.

Install at `.github/workflows/update-plugin-version.yml` in the marketplace repo.

```yaml
name: Update plugin version (layout B)

on:
  repository_dispatch:
    types: [plugin-updated]
  workflow_dispatch:
    inputs:
      plugin:
        description: Plugin name to refresh
        required: false

permissions:
  contents: write

concurrency:
  group: update-plugin-version
  cancel-in-progress: false

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout marketplace
        uses: actions/checkout@v4
        with:
          token: ${{ secrets.MARKETPLACE_PAT }}

      - name: Configure git
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"

      - name: Resolve version from local plugin.json
        id: resolve
        env:
          PLUGIN: ${{ github.event.client_payload.plugin }}
        run: |
          set -euo pipefail
          if [[ -z "$PLUGIN" ]]; then
            echo "No plugin name; aborting." >&2
            exit 1
          fi
          FILE="plugins/$PLUGIN/.claude-plugin/plugin.json"
          if [[ ! -f "$FILE" ]]; then
            echo "Plugin file $FILE not found; aborting." >&2
            exit 1
          fi
          VERSION=$(python3 -c "import json; print(json.load(open('$FILE'))['version'])")
          echo "plugin=$PLUGIN"   >> "$GITHUB_OUTPUT"
          echo "version=$VERSION" >> "$GITHUB_OUTPUT"

      - name: Update marketplace.json entry
        env:
          PLUGIN: ${{ steps.resolve.outputs.plugin }}
          VERSION: ${{ steps.resolve.outputs.version }}
        run: |
          set -euo pipefail
          python3 - <<'PY'
          import json, os
          path = '.claude-plugin/marketplace.json'
          data = json.load(open(path))
          plugin  = os.environ['PLUGIN']
          version = os.environ['VERSION']
          for p in data.get('plugins', []):
              if p.get('name') == plugin:
                  if p.get('version') != version:
                      p['version'] = version
                      print(f'updated {plugin} -> {version}')
                  break
          with open(path, 'w') as f:
              json.dump(data, f, indent=2)
              f.write('\n')
          PY

      - name: Commit if changed
        run: |
          set -euo pipefail
          if git diff --quiet -- .claude-plugin/marketplace.json; then
            echo "No change."
            exit 0
          fi
          git add .claude-plugin/marketplace.json
          git commit -m "chore: sync ${{ steps.resolve.outputs.plugin }} to ${{ steps.resolve.outputs.version }} [skip ci]"
          git push
```

---

## Payload Contract

Both receivers read these fields from `github.event.client_payload`:

| Field | Required | Source | Used for |
|-------|----------|--------|----------|
| `plugin`       | yes | plugin's `plugin.json` `name`    | marketplace.json lookup |
| `version`      | yes | plugin's `plugin.json` `version` | marketplace.json write |
| `ref`          | no  | `${{ github.sha }}`              | commit message, audit log |
| `source_repo`  | no  | `${{ github.repository }}`       | audit log |
| `triggered_by` | no  | `${{ github.actor }}`            | audit log |

The notify workflow in `notify-workflow-template.md` emits all five fields.
If `version` is missing (older templates), Layout A receiver falls back to
fetching it from the plugin repo's `plugin.json` via the GitHub API.

---

## Concurrency and Loops

- `[skip ci]` in the commit subject prevents the marketplace's own CI from
  treating the auto-bump as a real change and firing another dispatch.
- `concurrency.group: update-plugin-version` with `cancel-in-progress: false`
  serializes simultaneous dispatches from different plugins, so two plugins
  releasing at the same second do not race on `git push`.
- If marketplace.json has branch protection requiring approvals, the PAT
  must belong to a user allowed to bypass protection, AND you must set
  `permissions: contents: write`.

---

## Troubleshooting

- **Receiver fires but nothing changes** — the plugin's version in
  `marketplace.json` already matches `plugin.json`. That is the idempotent
  no-op case; it is intentional.
- **Push rejected** — branch protection on the marketplace default branch.
  Either grant the PAT user admin bypass, or switch to a PR-based flow that
  opens a PR against `main` and auto-merges.
- **Receiver never fires** — check the marketplace repo Actions tab for any
  `repository_dispatch` event. If none arrives, the PAT is wrong or the
  notify workflow is on a non-default branch.
- **Wrong version written** — Layout A fetches from the default branch of
  the plugin repo. If you push a tag but not the bumped `plugin.json` to
  the default branch, the fetched version is stale. CPV's `publish.py`
  commits to default branch FIRST, then tags, so this is already correct.
- **Infinite loop** — receiver commit re-triggers the notify workflow on
  the marketplace. Fix: use `[skip ci]` AND exclude marketplace.json from
  the notify's `paths:` filter (see `notify-workflow-template.md`).
