---
name: cpv-setup-branch-rules
description: Create/update the GitHub branch-protection ruleset on a plugin or marketplace repo — enforces CI as a required status check, preserves bypass for trusted bots, idempotent.
user-invocable: true
argument-hint: <owner/repo> [--dry-run] [--list-apps] [--reset-bypass]
---

Create or update the `cpv-branch-rules` ruleset on the target repo so that CI must pass before any PR can be merged. This is the **server-side** gate that the local pre-push hook alone cannot provide — a dev can always `git push --no-verify` to bypass a local hook, but the ruleset is enforced by GitHub itself.

## What the ruleset enforces

- **Required status checks**: `CI / Lint`, `CI / Validate`, `CI / Test` must all pass
- **Block deletion**: the default branch cannot be deleted
- **Block force-push**: non-fast-forward pushes rejected
- **Require PR**: every change goes through a pull request (but **no** manual approval required by default)
- **Allow auto-merge**: GitHub merges the PR as soon as CI turns green
- **Bypass for trusted bots**: Dependabot, GitHub Actions, and any integration already on the repo (Claude, Copilot, Renovate, etc.) can merge without review

## Usage

**Preview only (no changes):**
```bash
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_branch_rules.py" Emasoft/my-plugin --dry-run
```

**Apply to a repo:**
```bash
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_branch_rules.py" Emasoft/my-plugin
```

**List installed GitHub Apps on the owner** (to decide which to trust):
```bash
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_branch_rules.py" Emasoft/my-plugin --list-apps
```

**Add extra bypass apps** (e.g., Copilot with actor_id 852577):
```bash
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_branch_rules.py" Emasoft/my-plugin \
    --add-bypass-app-id 852577
```

**Reset bypass list to defaults** (removes any manually configured trust — careful):
```bash
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_branch_rules.py" Emasoft/my-plugin --reset-bypass
```

## Behavior

- **Idempotent**: running twice is a no-op — the second run finds the existing `cpv-branch-rules` and updates it in place.
- **Legacy adoption**: on first run, if the repo already has a non-CPV ruleset (e.g., a manually-created branch protection), its `bypass_actors` list is adopted automatically so you don't lose any trust already configured. A warning is printed with the `gh api DELETE` command for the legacy ruleset so you can clean up.
- **Auto-merge friendly**: the ruleset does NOT require a branch to be up-to-date (`strict_required_status_checks_policy: false`), so GitHub's auto-merge works without forcing a rebase loop.
- **Solo-project friendly**: `required_approving_review_count: 0` means you don't need to approve your own PRs. Teams can raise this to 1 later via the GitHub UI or by running the script with `--required-reviews 1` (not yet supported — manual UI change for now).

## Requirements

- `gh` CLI authenticated with a token that has `repo` + `admin:repo_hook` scopes on the target repo
- The target repo must already have the consolidated `ci.yml` workflow (from the CPV plugin/marketplace scaffolding) so the required check contexts actually exist

## Quick CI + branch-rules setup (typical new-plugin flow)

```bash
# 1. Push your first commit so the CI job names register with GitHub
git push -u origin master

# 2. Wait for the first CI run to complete (the job names must exist on GitHub
#    before the ruleset can reference them)
gh run watch

# 3. Apply the ruleset
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_branch_rules.py" Emasoft/my-plugin
```

See the **setup-plugin-repo** and **setup-github-marketplace** skills for the full end-to-end workflow.
