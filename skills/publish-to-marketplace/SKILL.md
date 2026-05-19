---
name: publish-to-marketplace
description: >
  Use when publishing a plugin to a GitHub-hosted marketplace.
  Used dynamically via the-skills-menu (TRDD-478d9687).
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

- Plugin repo with valid plugin.json (name, version, description)
- `gh` CLI authenticated (`gh auth status`)
- For Layout A: marketplace repo with marketplace.json
- For Layout C: same repo also has marketplace.json with self-entry (`source: "./"`)
- `uv` on PATH, plugin has `pyproject.toml`

## Instructions

### Phase 0: Detect layout and discover marketplace

- Plugin root has marketplace.json → **Layout C** (skip Phase 1)
- Parent has marketplace.json → **Layout B** (skip Phase 1)
- Otherwise → **Layout A**: ask the user for `<owner>/<marketplace-repo>`, verify with `gh repo view`.

### Phase 1: Configure Notification Pipeline (Layout A only)

**Skip for Layout B/C** — both manifests live in the same repo.

1. **Create PAT**: GitHub PAT with `repo` scope (publish-pipeline-guide §1)
2. **Set secret**: `gh secret set MARKETPLACE_PAT --repo <owner>/<plugin-repo> --body "$MARKETPLACE_PAT"` (MUST use `--body`)
3. **Install notify-marketplace.yml**: Copy from publish-pipeline-guide §2 → `.github/workflows/`, fill `MARKETPLACE_OWNER`/`MARKETPLACE_REPO`
4. **Verify CI**: `ci.yml` consolidated (lint + validate + test) per `canonical-pipeline`

### Phase 2: Configure Publish Pipeline

5. **Install publish.py**: `test -f scripts/publish.py`
6. **Install pre-push hook**: `uv run python scripts/publish.py --install-hook`
7. **Verify hooks**: `git config core.hooksPath` shows `git-hooks`

### Phase 3: Publish

8. **Gate check**: `uv run python scripts/publish.py --gate`
9. **Run publish**: `uv run python scripts/publish.py` — bump auto-detected from git-cliff. Override with `--patch`/`--minor`/`--major`.
   - **Layout C**: publish.py MUST bump version in plugin.json AND marketplace.json (metadata + self-entry) atomically.
10. **Verify dispatch** (Layout A only): marketplace repo `update-submodules.yml` triggers within 30s. Skip for B/C.
11. **Verify marketplace.json sync**:
    - Layout A: plugin version appears in the SEPARATE marketplace repo via dispatch
    - Layout B/C: plugin version updates in the SAME repo (already pushed)

### Phase 4: Enforce CI on GitHub (first publish only)

12. **Apply branch-rules ruleset**:
    ```bash
    uvx --from git+https://github.com/Emasoft/claude-plugins-validation \
        cpv-setup-branch-rules <owner>/<plugin-repo>
    ```
    Idempotent. `--dry-run` to preview.

Copy this checklist and track your progress:
- [ ] Layout detected (A / B / C)
- [ ] (Layout A only) PAT secret set
- [ ] (Layout A only) notify-marketplace.yml installed
- [ ] Consolidated ci.yml present
- [ ] publish.py + pre-push hook installed
- [ ] (Layout C) publish.py bumps both manifests atomically
- [ ] First publish succeeded
- [ ] Marketplace sync verified
- [ ] cpv-setup-branch-rules applied

## Output

Report: plugin name, old/new version, push status, dispatch status. On failure, name the failed phase.

## Error Handling

| Error | Resolution |
|-------|------------|
| `MARKETPLACE_PAT` missing | `gh secret set MARKETPLACE_PAT` |
| Dispatch not received | Check PAT scope, workflow on default branch |
| Pre-push blocks | Fix validation/lint, bump version |
| Version mismatch | `publish.py` auto-bumps all sources |
| Push rejected | Check branch protection; PAT owner must be admin |

## Examples

**Input:** `publish my-plugin to marketplace`
**Output:** `[DONE] Plugin: my-plugin 1.0.0->1.0.1 | Push: ok | Dispatch: triggered`

## Resources

- [Publish Pipeline Guide](references/publish-pipeline-guide.md)
  > Section 1: PAT Setup · Section 2: notify-marketplace.yml · Section 3: The Dispatch Chain · Section 4: publish.py Pipeline · Section 5: Pre-Push Hook Gates · Section 6: marketplace.json Entry Format · Section 7: Troubleshooting
- `canonical-pipeline` skill — publish.py, pre-push hook, CI workflows

## Token Optimization

Prefer LLM Externalizer MCP for bounded file analysis.
