# Notify Marketplace Workflow Template

## Table of Contents
- [Placeholders](#placeholders)
- [Template A Push Trigger](#template-a-push-trigger)
- [Template B Release Tag Trigger](#template-b-release-tag-trigger)
- [Why repository_dispatch](#why-repository_dispatch)
- [Curl Fallback](#curl-fallback)
- [Troubleshooting](#troubleshooting)

## Checklist

- [ ] Decide trigger: A (push) or B (release tag)
- [ ] Copy the chosen template into plugin's `.github/workflows/notify-marketplace.yml`
- [ ] Replace placeholders (MARKETPLACE_OWNER, MARKETPLACE_REPO)
- [ ] Ensure `MARKETPLACE_PAT` secret is set on the plugin repo
- [ ] Commit + push; verify the workflow runs successfully

Copy one of the two templates below into `.github/workflows/notify-marketplace.yml`
in the plugin repository. Replace the placeholders, add the `MARKETPLACE_PAT`
secret, then push. The CPV reference implementation lives at
`.github/workflows/notify-marketplace.yml` in this repo and uses Template A.

---

## Placeholders

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `<MARKETPLACE_OWNER>` | GitHub user or org owning the marketplace repo | `acme-inc` |
| `<MARKETPLACE_REPO>`  | Marketplace repository name                   | `acme-plugins` |
| `<PAT_SECRET_NAME>`   | Secret name holding the PAT                   | `MARKETPLACE_PAT` |
| `<DEFAULT_BRANCH>`    | Plugin default branch                         | `main` |

The PAT is NOT carried in `plugin.json` or in the dispatch payload — it is a
repo **secret** named `MARKETPLACE_PAT` (see `pat-secret-setup.md`) that the
notify workflow reads as `${{ secrets.MARKETPLACE_PAT }}` to authenticate the
cross-repo dispatch. The `version` field is what the receiver can recover from
the plugin's own `plugin.json` (via the GitHub API) when a dispatch omits it;
see the Payload Contract in `receiver-workflow-template.md`. Templates A and B
below send `version` explicitly so the receiver never needs that fallback.

---

## Template A Push Trigger

This is the template used by `generate_plugin_repo.py` and by CPV's own
`notify-marketplace.yml`. It fires on any push to the default branch that
touches plugin files. It is the simplest, most reliable trigger — you do not
need to cut a release tag for it to work. `scripts/publish.py` produces a
normal push alongside the tag, so Template A also fires on release.

```yaml
# .github/workflows/notify-marketplace.yml
# Plugin-side notifier: tells the marketplace repo to refresh this plugin
# whenever plugin files change on the default branch.
#
# SETUP:
#   1. Create a PAT with 'repo' scope owned by a marketplace admin.
#   2. Add it as repo secret MARKETPLACE_PAT on THIS plugin repo.
#   3. Edit MARKETPLACE_OWNER and MARKETPLACE_REPO below.

name: Notify Marketplace

on:
  push:
    branches: [<DEFAULT_BRANCH>]
    paths:
      - '.claude-plugin/plugin.json'
      - 'hooks/**'
      - 'commands/**'
      - 'agents/**'
      - 'skills/**'
      - 'scripts/**'

env:
  MARKETPLACE_OWNER: '<MARKETPLACE_OWNER>'
  MARKETPLACE_REPO: '<MARKETPLACE_REPO>'

permissions:
  contents: read

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout plugin
        uses: actions/checkout@v4

      - name: Extract plugin name and version
        id: plugin
        run: |
          set -euo pipefail
          NAME=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['name'])")
          VERSION=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])")
          # Reject anything shaped to break out of the downstream env/JSON
          # context before it ever reaches an env: var or a dispatch payload.
          if ! [[ "$NAME" =~ ^[a-z0-9][a-z0-9._-]*$ ]]; then
            echo "::error::plugin.json name does not match the expected slug pattern: $NAME" >&2
            exit 1
          fi
          if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+][A-Za-z0-9.]+)?$ ]]; then
            echo "::error::plugin.json version does not match the expected semver pattern: $VERSION" >&2
            exit 1
          fi
          echo "name=$NAME" >> "$GITHUB_OUTPUT"
          echo "version=$VERSION" >> "$GITHUB_OUTPUT"
          echo "ref=${GITHUB_SHA}" >> "$GITHUB_OUTPUT"

      - name: Dispatch plugin-updated to marketplace
        uses: peter-evans/repository-dispatch@v4
        with:
          token: ${{ secrets.MARKETPLACE_PAT }}
          repository: ${{ env.MARKETPLACE_OWNER }}/${{ env.MARKETPLACE_REPO }}
          event-type: plugin-updated
          client-payload: |
            {
              "plugin": "${{ steps.plugin.outputs.name }}",
              "version": "${{ steps.plugin.outputs.version }}",
              "ref": "${{ steps.plugin.outputs.ref }}",
              "source_repo": "${{ github.repository }}",
              "triggered_by": "${{ github.actor }}"
            }

      - name: Job summary
        # Read values from the environment rather than interpolating ${{ }}
        # directly into the run-script — a value from plugin.json must never be
        # spliced into the shell. MARKETPLACE_OWNER/REPO come from the workflow
        # env: block above.
        env:
          PLUGIN_NAME: ${{ steps.plugin.outputs.name }}
          PLUGIN_VERSION: ${{ steps.plugin.outputs.version }}
          PLUGIN_REF: ${{ steps.plugin.outputs.ref }}
        run: |
          {
            echo "## Marketplace Notification"
            echo ""
            echo "- Plugin: $PLUGIN_NAME"
            echo "- Version: $PLUGIN_VERSION"
            echo "- Ref: $PLUGIN_REF"
            echo "- Target: $MARKETPLACE_OWNER/$MARKETPLACE_REPO"
          } >> "$GITHUB_STEP_SUMMARY"
```

---

## Template B Release Tag Trigger

Use this variant if you only want to notify on semver tag pushes (or GitHub
Releases). It avoids noisy dispatches on doc-only pushes. Requires that your
release process actually creates and pushes a `v*` tag — CPV's `publish.py`
does this in Step 9.

```yaml
# .github/workflows/notify-marketplace.yml
# Alternative notifier: fires only on semver tag pushes.

name: Notify Marketplace

on:
  push:
    tags: ['v*']
  # Alternate: uncomment to fire on published GitHub Release instead
  # release:
  #   types: [published]

env:
  MARKETPLACE_OWNER: '<MARKETPLACE_OWNER>'
  MARKETPLACE_REPO: '<MARKETPLACE_REPO>'

permissions:
  contents: read

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout plugin
        uses: actions/checkout@v4
        with:
          # We want the tag ref, not the tag commit, so fetch full history
          fetch-depth: 0

      - name: Resolve version from tag or plugin.json
        id: plugin
        run: |
          set -euo pipefail
          TAG="${GITHUB_REF_NAME:-}"
          if [[ "$TAG" == v* ]]; then
            VERSION="${TAG#v}"
          else
            VERSION=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['version'])")
          fi
          NAME=$(python3 -c "import json; print(json.load(open('.claude-plugin/plugin.json'))['name'])")
          # Reject anything shaped to break out of the downstream env/JSON
          # context before it ever reaches an env: var or a dispatch payload.
          if ! [[ "$NAME" =~ ^[a-z0-9][a-z0-9._-]*$ ]]; then
            echo "::error::plugin.json name does not match the expected slug pattern: $NAME" >&2
            exit 1
          fi
          if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+][A-Za-z0-9.]+)?$ ]]; then
            echo "::error::resolved version does not match the expected semver pattern: $VERSION" >&2
            exit 1
          fi
          echo "name=$NAME"       >> "$GITHUB_OUTPUT"
          echo "version=$VERSION" >> "$GITHUB_OUTPUT"
          echo "ref=${GITHUB_SHA}" >> "$GITHUB_OUTPUT"

      - name: Dispatch plugin-updated to marketplace
        uses: peter-evans/repository-dispatch@v4
        with:
          token: ${{ secrets.MARKETPLACE_PAT }}
          repository: ${{ env.MARKETPLACE_OWNER }}/${{ env.MARKETPLACE_REPO }}
          event-type: plugin-updated
          client-payload: |
            {
              "plugin": "${{ steps.plugin.outputs.name }}",
              "version": "${{ steps.plugin.outputs.version }}",
              "ref": "${{ steps.plugin.outputs.ref }}",
              "source_repo": "${{ github.repository }}",
              "triggered_by": "${{ github.actor }}"
            }
```

---

## Why repository_dispatch

- **Cross-repo**: `workflow_dispatch` only triggers a workflow in the SAME
  repository. To kick a workflow in the marketplace repo from the plugin
  repo you MUST use `repository_dispatch`.
- **Custom payload**: `repository_dispatch` accepts a `client-payload` JSON
  object, so the marketplace receiver can read plugin name and version
  without fetching `plugin.json` twice.
- **No GITHUB_TOKEN**: The default `GITHUB_TOKEN` is scoped to the current
  repository and cannot dispatch events to other repos. You MUST use a PAT
  stored as a repo secret (`MARKETPLACE_PAT`).

---

## Curl Fallback

If you cannot use `peter-evans/repository-dispatch@v4` (air-gapped runners,
actions policy), you can call the REST API directly:

```yaml
      - name: Dispatch via curl
        env:
          GH_TOKEN: ${{ secrets.MARKETPLACE_PAT }}
          OWNER: ${{ env.MARKETPLACE_OWNER }}
          REPO: ${{ env.MARKETPLACE_REPO }}
          # Step outputs are untrusted (they carry whatever plugin.json held).
          # Bind them here and let `jq` build the JSON from the environment —
          # splicing `${{ }}` into the heredoc would make a crafted name or
          # version part of the shell/JSON source (RC-WORKFLOW-EXPR-INJECT).
          PLUGIN_NAME: ${{ steps.plugin.outputs.name }}
          PLUGIN_VERSION: ${{ steps.plugin.outputs.version }}
          PLUGIN_REF: ${{ steps.plugin.outputs.ref }}
          SOURCE_REPO: ${{ github.repository }}
          TRIGGERED_BY: ${{ github.actor }}
        run: |
          jq -n \
            --arg plugin "$PLUGIN_NAME" \
            --arg version "$PLUGIN_VERSION" \
            --arg ref "$PLUGIN_REF" \
            --arg source_repo "$SOURCE_REPO" \
            --arg triggered_by "$TRIGGERED_BY" \
            '{event_type: "plugin-updated", client_payload: {plugin: $plugin, version: $version, ref: $ref, source_repo: $source_repo, triggered_by: $triggered_by}}' \
          | curl -fsSL -X POST \
              -H "Accept: application/vnd.github+json" \
              -H "Authorization: Bearer $GH_TOKEN" \
              -H "X-GitHub-Api-Version: 2022-11-28" \
              "https://api.github.com/repos/$OWNER/$REPO/dispatches" \
              -d @-
```

A `204 No Content` response means the dispatch was accepted. Any 4xx is a
PAT scope or repo-name error; 5xx means GitHub is having a bad day — the
next push will retry automatically with Template A.

If `gh` CLI is available on the runner, an even shorter form works:

```yaml
      - name: Dispatch via gh cli
        env:
          GH_TOKEN: ${{ secrets.MARKETPLACE_PAT }}
          OWNER: ${{ env.MARKETPLACE_OWNER }}
          REPO: ${{ env.MARKETPLACE_REPO }}
          # Same rule as the curl form: every event/step value is read from the
          # environment, never interpolated into the run-script.
          PLUGIN_NAME: ${{ steps.plugin.outputs.name }}
          PLUGIN_VERSION: ${{ steps.plugin.outputs.version }}
          PLUGIN_REF: ${{ steps.plugin.outputs.ref }}
          SOURCE_REPO: ${{ github.repository }}
        run: |
          gh api "repos/$OWNER/$REPO/dispatches" \
            -f event_type=plugin-updated \
            -f "client_payload[plugin]=$PLUGIN_NAME" \
            -f "client_payload[version]=$PLUGIN_VERSION" \
            -f "client_payload[ref]=$PLUGIN_REF" \
            -f "client_payload[source_repo]=$SOURCE_REPO"
```

---

## Troubleshooting

- **Dispatch returns 404** — wrong owner or repo in `MARKETPLACE_OWNER` or
  `MARKETPLACE_REPO`, or the PAT user has no access to the target repo.
- **Dispatch returns 401** — PAT expired or not stored correctly in the
  `MARKETPLACE_PAT` secret; see `pat-secret-setup.md`.
- **Dispatch returns 422** — `event_type` must be a string <= 100 chars and
  `client_payload` must be valid JSON <= 64KB.
- **Workflow never runs** — the notify workflow was pushed to a branch that
  does not match the `on.push.branches` filter. Push to the default branch.
- **Double firings** — Template A and Template B are mutually exclusive;
  pick one. Keeping both causes two dispatches per release.
