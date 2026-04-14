---
name: cpv-setup-branch-rules-generic
description: Project-agnostic branch-protection ruleset installer — works on ANY GitHub repo (not just CPV plugins/marketplaces). Requires explicit --check contexts. Idempotent, auto-merge friendly, preserves existing bot bypass actors.
user-invocable: true
argument-hint: <owner/repo> --check "CI / job-name" [--check ...] [--ruleset-name NAME] [--dry-run]
---

Create or update a branch-protection ruleset on any GitHub repository. This is the project-agnostic variant of `/cpv-setup-branch-rules` — it has no CPV-specific defaults, doesn't assume plugin vs marketplace, and requires you to spell out every required status check context explicitly.

## What the ruleset enforces

- **Required status checks**: every `--check` flag becomes a required status context (no defaults — you must know the check names)
- **Block deletion**: the default branch cannot be deleted
- **Block force-push**: non-fast-forward pushes rejected
- **Require PR**: every change goes through a pull request (but **no** manual approval required by default)
- **Allow auto-merge**: GitHub merges the PR as soon as CI turns green (strict policy off — no forced rebases)
- **Bypass for admins**: admin role (actor_id 5) pre-seeded; add specific bots via `--add-bypass-app-id <id>` after running `--list-apps`

## Why no hardcoded bot defaults

GitHub's Rulesets API rejects bypass actors whose app isn't installed on the target owner's account with HTTP 422. Every owner has a different set of installed apps, so the only portable defaults are (a) the admin role and (b) any bypass_actors already present on a pre-existing legacy ruleset (auto-adopted on first run).

## Usage

**Preview only (recommended first run):**
```bash
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_branch_rules_generic.py" \
    Emasoft/my-project \
    --check "CI / build" --check "CI / test" \
    --dry-run
```

On dry-run the script prints a **diagnostic** showing the actual check-run names currently reported on the target repo's HEAD, so you can sanity-check that your `--check` values match what GitHub is actually reporting.

**Apply (once you've confirmed the check names):**
```bash
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_branch_rules_generic.py" \
    Emasoft/my-project \
    --check "CI / build" --check "CI / test"
```

**List installed GitHub Apps (to find app_ids for bypass):**
```bash
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_branch_rules_generic.py" \
    Emasoft/my-project --list-apps
```

**Add specific bot apps to bypass (after `--list-apps` shows you the IDs):**
```bash
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_branch_rules_generic.py" \
    Emasoft/my-project \
    --check "CI / test" \
    --add-bypass-app-id 29110  # Dependabot
    --add-bypass-app-id 852577 # (example)
```

**Use a custom ruleset name** (default: `branch-rules`):
```bash
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/setup_branch_rules_generic.py" \
    Emasoft/my-project \
    --check "CI / test" \
    --ruleset-name "my-project-main-protection"
```

## As a reusable CLI via uvx (no local install)

```bash
uvx --from git+https://github.com/Emasoft/claude-plugins-validation \
    branch-rules-install Emasoft/my-project \
    --check "CI / build" --check "CI / test"
```

## Idempotent

Running twice is a no-op — the script looks up the ruleset by name and updates it in place on the second run.

## Requirements

- `gh` CLI authenticated with a token that has `repo` + `admin:repo_hook` scopes on the target repo

## Difference vs `/cpv-setup-branch-rules`

| | `/cpv-setup-branch-rules` | `/cpv-setup-branch-rules-generic` |
|---|---|---|
| Target repo | CPV plugins & marketplaces | Any GitHub repo |
| Default check contexts | Auto-detected (plugin → CI / Lint, CI / Validate, CI / Test; marketplace → Marketplace Validation / Validate) | None — must pass `--check` explicitly |
| Ruleset name | `cpv-branch-rules` | `branch-rules` (overridable) |
| Plugin/marketplace detection | Yes | No |
| Everything else | Same | Same |
