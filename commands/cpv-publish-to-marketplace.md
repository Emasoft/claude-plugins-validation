---
name: cpv-publish-to-marketplace
allowed-tools: Read, Bash(git:*,gh:*,uv:*), Write, Edit, Glob, Grep, AskUserQuestion
description: Publish a plugin to a GitHub-hosted marketplace with PAT setup and notification workflow
skill: publish-to-marketplace
user-invocable: true
---

# /cpv-publish-to-marketplace

Configure the notification pipeline and publish a plugin to a GitHub-hosted marketplace.

## Usage

```
/cpv-publish-to-marketplace <owner/marketplace-repo>
```

## What It Does

1. **Discover marketplace**: Verify `<owner/marketplace-repo>` exists via `gh repo view`
2. **Configure PAT**: Ask user for a GitHub PAT with `repo` scope, set as secret: `gh secret set MARKETPLACE_PAT --repo <owner/plugin-repo>`
3. **Install notify-marketplace.yml**: Add notification workflow to `.github/workflows/` if missing
4. **Verify CI workflows**: Ensure ci.yml, validate.yml, release.yml exist
5. **Install publish pipeline**: Add `scripts/publish.py` and `.githooks/pre-push` if missing
6. **Run publish**: `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" --patch && git push`
7. **Verify dispatch**: Check marketplace repo Actions tab for triggered workflow

## Checklist

- [ ] Marketplace repo verified
- [ ] PAT created and secret set
- [ ] notify-marketplace.yml installed
- [ ] CI workflows present (ci.yml, validate.yml, release.yml)
- [ ] publish.py + pre-push hook installed
- [ ] First publish successful
- [ ] Marketplace sync verified

This command delegates to the `publish-to-marketplace` skill. See the skill documentation for detailed instructions on each phase.
