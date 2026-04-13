# End-to-End Verification Playbook

## Table of Contents
- [Dry Run Without Cutting a Release](#dry-run-without-cutting-a-release)
- [Observe the Plugin Side](#observe-the-plugin-side)
- [Observe the Marketplace Side](#observe-the-marketplace-side)
- [Confirm marketplace.json Changed](#confirm-marketplacejson-changed)
- [Confirm Claude Code Sees the New Version](#confirm-claude-code-sees-the-new-version)
- [Troubleshooting](#troubleshooting)
- [Rollback Procedure](#rollback-procedure)

This playbook proves the full chain works: plugin push → notify workflow →
repository_dispatch → receiver workflow → marketplace.json bump → Claude
Code sees the new version. Run it once after installing the notify and
receiver templates, and again after rotating the `MARKETPLACE_PAT` secret.

---

## Dry Run Without Cutting a Release

You can exercise the whole chain without a real version bump in two ways:

**Option 1: manual dispatch on the marketplace receiver.**
This skips the plugin-side notify step and goes straight to the receiver.
Best for verifying the marketplace-side PAT and jq/python logic.

```bash
gh workflow run "Update plugin version" \
  --repo <MARKETPLACE_OWNER>/<MARKETPLACE_REPO> \
  --field plugin=<PLUGIN_NAME>
```

**Option 2: manual dispatch on the plugin-side notify workflow.**
This adds a `workflow_dispatch:` trigger temporarily and kicks it from
the plugin repo. Exercises the PAT on the plugin side and proves the
dispatch can cross the boundary.

Edit `.github/workflows/notify-marketplace.yml` to add:

```yaml
on:
  push:
    branches: [main]
    paths: [...]
  workflow_dispatch:      # add this block temporarily
```

Then:

```bash
gh workflow run "Notify Marketplace" \
  --repo <PLUGIN_OWNER>/<PLUGIN_REPO>
```

**Option 3: fake tag on a test branch.**
If you use Template B (release tag trigger), push a throwaway tag on a
throwaway branch of the plugin repo:

```bash
git checkout -b verify/notify-chain
git commit --allow-empty -m "verify: notify chain"
git tag v0.0.0-verify
git push origin verify/notify-chain v0.0.0-verify
# When done:
git push origin --delete v0.0.0-verify verify/notify-chain
```

The marketplace receiver is idempotent: if `plugin.json` on the default
branch has not changed, the receiver commits nothing.

---

## Observe the Plugin Side

Watch the notify workflow run on the plugin repo:

```bash
gh run list --repo <PLUGIN_OWNER>/<PLUGIN_REPO> \
  --workflow "Notify Marketplace" --limit 5

# Follow the most recent run
gh run watch --repo <PLUGIN_OWNER>/<PLUGIN_REPO> $(gh run list \
  --repo <PLUGIN_OWNER>/<PLUGIN_REPO> \
  --workflow "Notify Marketplace" \
  --limit 1 --json databaseId --jq '.[0].databaseId')
```

A passing run logs a final "Dispatch plugin-updated to marketplace" step
with status `success`. A failing run with `Bad credentials` means the
`MARKETPLACE_PAT` secret is missing, wrong, or expired.

---

## Observe the Marketplace Side

Within ~5 seconds of a successful plugin dispatch, a new run should appear
on the marketplace repo:

```bash
gh run list --repo <MARKETPLACE_OWNER>/<MARKETPLACE_REPO> \
  --workflow "Update plugin version" --limit 5

gh run view --repo <MARKETPLACE_OWNER>/<MARKETPLACE_REPO> \
  $(gh run list --repo <MARKETPLACE_OWNER>/<MARKETPLACE_REPO> \
      --workflow "Update plugin version" \
      --limit 1 --json databaseId --jq '.[0].databaseId') --log
```

Look for the `Update marketplace.json` step's log line
`updated <plugin>: <old> -> <new>`. That line is the proof the chain closed.

If the run is missing entirely, the dispatch was accepted by GitHub but the
receiver workflow does not exist on the default branch of the marketplace
repo. Push the receiver workflow to `<DEFAULT_BRANCH>` and re-run the
dispatch.

---

## Confirm marketplace.json Changed

```bash
gh api "repos/<MARKETPLACE_OWNER>/<MARKETPLACE_REPO>/contents/.claude-plugin/marketplace.json" \
  --jq '.content' | base64 -d | python3 -c "
import sys, json
data = json.load(sys.stdin)
for p in data['plugins']:
    if p['name'] == '<PLUGIN_NAME>':
        print(p['name'], p.get('version', 'unset'))
        break
"
```

The printed version should match the value in the plugin repo's
`.claude-plugin/plugin.json`. If it does not, the receiver ran but failed
silently on the update step — inspect the receiver job log for a traceback.

---

## Confirm Claude Code Sees the New Version

Claude Code caches marketplace metadata. After the marketplace repo commit
lands, users still need to refresh their local cache. There are three
supported ways:

```bash
# CLI (recommended for CI verification)
claude plugin marketplace update <MARKETPLACE_NAME>
claude plugin list
```

Inside Claude Code interactive sessions:

- Run `/plugin` to open the plugin picker and press the refresh key.
- Or run the marketplace update command provided by the marketplace plugin
  (varies per marketplace — CPV ships `/cpv-manage`).

After the refresh, `claude plugin list` must show the new version next to
the plugin name. If it still shows the old version, Claude Code is reading
from a stale cache; delete the marketplace cache dir listed by
`claude plugin marketplace info <MARKETPLACE_NAME>` and retry.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Notify run never fires | Wrong branch or `paths` filter | Push to default branch; check YAML |
| Notify run fails `401` | Expired/missing PAT | See `pat-secret-setup.md` section "Renewal When Expired" |
| Notify run passes but no receiver run | Receiver not on default branch | Push receiver to `<DEFAULT_BRANCH>` |
| Receiver run fails `403` on push | Branch protection on marketplace | PAT owner must be admin |
| Receiver run commits wrong version | Plugin bumped tag but not default branch | Always commit to default branch FIRST, then tag |
| Receiver loops forever | Commit does not include `[skip ci]` | Ensure commit message has `[skip ci]` |
| Claude Code still old version | Local cache stale | `claude plugin marketplace update <name>` |
| Env var `MARKETPLACE_PAT` is set but `gh secret set` fails `401/403` | Stored PAT expired, revoked, or missing scopes | `unset MARKETPLACE_PAT` and re-run the skill — it will trigger the manual creation walkthrough. Generate a new token with `repo` scope (classic) or Contents=RW + Actions=RW + Metadata=R (fine grained), then re-export and re-run. See [pat-secret-setup.md Auto Detect From Environment](pat-secret-setup.md#auto-detect-from-environment) for the full recovery flow. |

---

## Rollback Procedure

If the auto-notification caused a bad update to `marketplace.json` (wrong
version, corrupted entry, deleted field), roll back on the marketplace repo
BEFORE another dispatch re-overwrites it:

```bash
# 1. Revert the specific commit
gh repo clone <MARKETPLACE_OWNER>/<MARKETPLACE_REPO> /tmp/mkt
cd /tmp/mkt
git log --oneline -5 -- .claude-plugin/marketplace.json
git revert <BAD_SHA>    # creates a new revert commit
git push

# 2. Temporarily disable the receiver workflow to stop further autobumps
gh workflow disable "Update plugin version" \
  --repo <MARKETPLACE_OWNER>/<MARKETPLACE_REPO>

# 3. Fix the underlying cause (bad plugin.json, bad payload, ...) on the
#    plugin side.

# 4. Re-enable the receiver
gh workflow enable "Update plugin version" \
  --repo <MARKETPLACE_OWNER>/<MARKETPLACE_REPO>

# 5. Push a fresh plugin commit to re-drive the chain to the correct version
```

After rollback, run the full verification again to confirm the chain is
healthy before closing the incident.
