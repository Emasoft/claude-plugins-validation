---
name: setup-marketplace-auto-notification
description: >
  Use when configuring a plugin to notify its marketplace after each release
  so Claude Code can auto-update users. Universal CI recipe for any
  plugin/marketplace pair. Loaded by plugin-creator agent.
tags:
  - marketplace
  - notification
  - ci-cd
  - github-actions
  - release
allowed-tools: Read, Write, Edit, Bash(gh:*,git:*,jq:*,yq:*), Glob, Grep, AskUserQuestion
agent: plugin-creator
context: fork
user-invocable: false
---

# Setup Marketplace Auto Notification

## Overview

Configures the plugin side of the auto-notify chain: plugin push → notify
workflow → `repository_dispatch` → marketplace receiver → `marketplace.json`
bump → Claude Code sees the new version on next refresh. Works for any
plugin/marketplace pair with placeholder substitution only.

## Prerequisites

- `gh auth status` green for the owner of BOTH repos
- PAT with `repo` scope (or fine grained RW) — see
  [pat-secret-setup.md](references/pat-secret-setup.md)
- Marketplace has a `repository_dispatch: plugin-updated` receiver (create
  from [receiver-workflow-template.md](references/receiver-workflow-template.md))
- Plugin has a valid `.claude-plugin/plugin.json` with `name` + `version`

## Instructions

1. **Check env for `MARKETPLACE_PAT`** first. If set, reuse it and skip
   manual PAT creation. If unset, walk the user through creating one and
   exporting it. Never echo the value — use `${#VAR}` for length checks.
2. Run `gh secret set MARKETPLACE_PAT --body "$MARKETPLACE_PAT"` against
   both repos. Verify with `gh secret list`, not by printing the value.
3. Install `notify-marketplace.yml` on the plugin repo from the template.
4. Verify the marketplace receiver exists on its default branch — create
   it from `receiver-workflow-template.md` if missing.
5. Dry run the chain without cutting a real release.
6. Verify end to end: plugin run → marketplace run → `marketplace.json`
   bumped → Claude Code refresh sees the new version.

Copy this checklist and track your progress:

- [ ] Check env for `MARKETPLACE_PAT`
- [ ] If absent, create PAT and export it
- [ ] Run `gh secret set` using the env var
- [ ] Verify secret exists
- [ ] Scaffold `notify-marketplace.yml`
- [ ] Verify marketplace receiver exists
- [ ] Dry-run end-to-end

## Output

- Plugin repo: `.github/workflows/notify-marketplace.yml` + `MARKETPLACE_PAT` secret
- Marketplace repo: `.github/workflows/update-plugin-version.yml` + same secret
- Auto-commit on marketplace default branch bumping the plugin's `version`
  in `.claude-plugin/marketplace.json`

## Error Handling

| Symptom | Fix |
|---------|-----|
| `Bad credentials` / 401 | Rotate PAT; re-run `gh secret set --body` on both repos |
| Env var set but `gh secret set` fails 401/403 | `unset MARKETPLACE_PAT`, re-run skill, create fresh token |
| Dispatch never fires | Push to default branch; check `paths:` filter |
| 404 from dispatch API | Fix `MARKETPLACE_OWNER` / `MARKETPLACE_REPO` |
| Receiver never runs | Commit receiver workflow to marketplace default branch |
| Push rejected by branch protection | PAT owner must be a repo admin |
| Infinite loop | Add `[skip ci]` to receiver commit; exclude `marketplace.json` from notify `paths` |

## Examples

Plugin `claude-plugins-validation` → marketplace `Emasoft/emasoft-plugins`:

```bash
# Auto-detect: reuse shell env token if already set
[ -n "${MARKETPLACE_PAT:-}" ] && echo "reusing (${#MARKETPLACE_PAT} chars)"
gh secret set MARKETPLACE_PAT --repo Emasoft/claude-plugins-validation --body "$MARKETPLACE_PAT"
gh secret set MARKETPLACE_PAT --repo Emasoft/emasoft-plugins           --body "$MARKETPLACE_PAT"
gh secret list --repo Emasoft/emasoft-plugins | grep MARKETPLACE_PAT
# Then copy references/notify-workflow-template.md -> .github/workflows/notify-marketplace.yml,
# set MARKETPLACE_OWNER=Emasoft, MARKETPLACE_REPO=emasoft-plugins, commit, push.
```

Any plugin-file push on default branch dispatches `plugin-updated`; the
receiver bumps `marketplace.json` with `[skip ci]`.

## Resources

- [Notify Workflow Template](references/notify-workflow-template.md)
  > Placeholders · Template A Push Trigger · Template B Release Tag Trigger · Why repository_dispatch · Curl Fallback · Troubleshooting
- [Receiver Workflow Template](references/receiver-workflow-template.md)
  > Placeholders · Layout A Receiver · Layout B Receiver · Payload Contract · Concurrency and Loops · Troubleshooting
- [PAT Secret Setup](references/pat-secret-setup.md)
  > Auto Detect From Environment · Why a PAT · Classic PAT Scopes · Fine Grained PAT · Creating the PAT · Storing the Secret · Verifying the Secret · Rotation · Renewal When Expired
- [End to End Verification](references/end-to-end-verification.md)
  > Dry Run Without Cutting a Release · Observe the Plugin Side · Observe the Marketplace Side · Confirm marketplace.json Changed · Confirm Claude Code Sees the New Version · Troubleshooting · Rollback Procedure
