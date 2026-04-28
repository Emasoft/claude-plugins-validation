---
name: publish-to-marketplace
description: >
  Use when publishing a plugin to a GitHub-hosted marketplace.
  Loaded by plugin-creator agent.
tags:
  - marketplace
  - publish
  - ci-cd
  - plugin
allowed-tools: Read, Bash(git:*,gh:*,uv:*), Write, Edit, Glob, Grep, AskUserQuestion
user-invocable: false
---

# Publish Plugin to Marketplace

## Overview

Publishes a validated Claude Code plugin to a GitHub-hosted marketplace repo. Configures notification workflow, PAT secret, and publish pipeline.

Handles all three CPV layouts:
- **Layout A** (separate plugin and marketplace repos) — full notify chain with `MARKETPLACE_PAT` and dispatch
- **Layout B** (nested monorepo) — single repo, single tag, no cross-repo dispatch
- **Layout C** (marketplace-in-plugin self-referential) — single repo with both manifests, single tag, no cross-repo dispatch (skips Phase 1 entirely)

## Prerequisites

- Plugin repo with valid `.claude-plugin/plugin.json` (name, version, description)
- `gh` CLI authenticated (`gh auth status`)
- For Layout A: marketplace repo exists with `marketplace.json` (see `canonical-pipeline` skill or use `/cpv-create` to set one up)
- For Layout C: same repo also has `.claude-plugin/marketplace.json` with self-entry
- `uv` on PATH, plugin has `pyproject.toml`

## Instructions

### Phase 0: Detect layout and discover marketplace

Detect the layout first:
- `.claude-plugin/marketplace.json` exists in the plugin repo root → **Layout C** (skip Phase 1, proceed to Phase 2 directly)
- Plugin repo is a subdir of a marketplace repo (`../.claude-plugin/marketplace.json` exists) → **Layout B** (no notify chain, skip Phase 1)
- Otherwise → **Layout A** — ask the user for marketplace coordinates (`<owner>/<marketplace-repo>`). Verify it exists: `gh repo view <owner>/<marketplace-repo> --json name`. All subsequent placeholders use these values.

### Phase 1: Configure Notification Pipeline (Layout A only)

**SKIP this phase entirely for Layout B and Layout C** — both manifests live in the same repo, so a single push handles both. Go directly to Phase 2.

1. **Create PAT**: Ask user for a GitHub PAT with `repo` scope. See publish-pipeline-guide (Resources) Section 1
2. **Set secret**: `gh secret set MARKETPLACE_PAT --repo <owner>/<plugin-repo> --body "$MARKETPLACE_PAT"` (MUST use `--body` flag)
3. **Install notify-marketplace.yml**: Copy from publish-pipeline-guide (Resources) Section 2 into `.github/workflows/`. Fill `MARKETPLACE_OWNER` and `MARKETPLACE_REPO`
4. **Verify CI workflow**: Ensure `ci.yml` exists with jobs `lint`, `validate`, `test` (single consolidated workflow — see `canonical-pipeline` skill). `validate.yml` is no longer used.

### Phase 2: Configure Publish Pipeline

5. **Install publish.py**: Should already exist from standardize. Verify with `test -f scripts/publish.py`
6. **Install pre-push hook**: `uv run python scripts/publish.py --install-hook` (PSS pattern)
7. **Verify hooks**: `git config core.hooksPath` should show `git-hooks`

### Phase 3: Publish

8. **Run gate check**: `uv run python scripts/publish.py --gate` (verify quality gates pass)
9. **Run publish**: `uv run python scripts/publish.py` — the bump type is auto-detected from git-cliff (feat → minor, fix → patch, BREAKING CHANGE → major). Force with `--patch`/`--minor`/`--major` only when the auto-detection picks the wrong level.
   - **Layout C**: publish.py MUST bump BOTH `.claude-plugin/plugin.json::version` AND `.claude-plugin/marketplace.json::metadata.version` AND the self-entry's `version` in one atomic commit. Verify by reading both files post-bump.
10. **Verify dispatch** (Layout A only): Check marketplace repo Actions tab — `update-submodules.yml` should trigger within 30s. Skip for Layout B/C.
11. **Verify marketplace.json**:
    - Layout A: plugin version updates in the SEPARATE marketplace repo via dispatch
    - Layout B: plugin version updates in the SAME repo (the marketplace's own marketplace.json was edited as part of the push)
    - Layout C: plugin version updates in the SAME repo's `.claude-plugin/marketplace.json` self-entry (verify name + version sync between both manifests)

### Phase 4: Enforce CI on GitHub (first publish only)

12. **Apply branch-rules ruleset**: Once the first CI run completes, run the branch-rules script so future PRs cannot merge without the CI going green. This is the server-side enforcement — the local pre-push hook is bypassable with `--no-verify`, so this is the real gate.
    ```bash
    uvx --from git+https://github.com/Emasoft/claude-plugins-validation \
        cpv-setup-branch-rules <owner>/<plugin-repo>
    ```
    Add `--dry-run` to preview. The script is idempotent — re-running is a no-op.

Copy this checklist and track your progress:
- [ ] Layout detected (A / B / C)
- [ ] (Layout A only) PAT created and secret set
- [ ] (Layout A only) notify-marketplace.yml installed
- [ ] Consolidated ci.yml workflow present (lint + validate + test jobs)
- [ ] publish.py + pre-push hook installed
- [ ] (Layout C only) publish.py confirmed to bump both manifests atomically
- [ ] First publish successful
- [ ] Marketplace sync verified (Layout A: cross-repo dispatch; Layout B/C: same-repo update)
- [ ] cpv-setup-branch-rules applied (CI required on GitHub)

## Output

Report: plugin name, old/new version, push status, marketplace dispatch status (triggered/failed/not_configured). On failure, report which phase failed with error.

## Error Handling

| Error | Resolution |
|-------|------------|
| `MARKETPLACE_PAT` missing | `gh secret set MARKETPLACE_PAT` |
| Dispatch not received | Check PAT scope, verify workflow is on default branch |
| Pre-push blocks | Fix validation/lint issues, bump version |
| Version mismatch | Run `publish.py` which auto-bumps all sources |
| Push rejected | Check branch protection; PAT owner must be admin |

## Examples

**Input:** `publish my-plugin to marketplace`
**Output:**
```
[DONE] Plugin: my-plugin 1.0.0->1.0.1 | Push: ok | Dispatch: triggered
```

## Resources

- [Publish Pipeline Guide](references/publish-pipeline-guide.md)
  > Section 1: PAT Setup · Section 2: notify-marketplace.yml · Section 3: The Dispatch Chain · Section 4: publish.py Pipeline · Section 5: Pre-Push Hook Gates · Section 6: marketplace.json Entry Format · Section 7: Troubleshooting
- `canonical-pipeline` skill — publish.py, pre-push hook, CI workflows

## Token Optimization

Prefer LLM Externalizer MCP for bounded file analysis to save context tokens.
