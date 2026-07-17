---
name: bump-version
description: Bump plugin version and run the full publish pipeline (plugin.json, pyproject.toml, README badge, CHANGELOG, push, release). Use when bumping version + publishing the current plugin via publish.py. Used dynamically via the-skills-menu (TRDD-478d9687).
when_to_use: When the cpv-main-menu user picks Manage → Bump version, or any flow needs to bump version + publish the current plugin via publish.py
user-invocable: false
---

# bump-version

## Overview

Bumps the plugin version AND runs the full publish pipeline (TRDD-bbff5bc5 — single entry point). `publish.py` is the canonical entry point — it bumps the version in `plugin.json` + `pyproject.toml` + `__version__` vars, refreshes the README badge, regenerates the CHANGELOG, refreshes `.plugin-self-hashes.json` (if present), commits + tags + pushes, then creates the GitHub release. Every gate must pass before any push (no `--skip-*` flags exist). Loaded dynamically via the-skills-menu, reached via the Manage → Bump version menu branch.

## Prerequisites

- `uv` on PATH
- Clean git working tree (no uncommitted changes)
- `gh` CLI authenticated for GitHub release creation
- Network access for git push / GitHub API
- `PLUGIN_SKIP_GITHUB_INTEGRITY=1` NOT set unless intentionally skipping integrity gate

## Instructions

1. Confirm the working tree is clean (`git status`).
2. Choose a bump level: `--patch`, `--minor`, or `--major`.
3. Always preview first with `--dry-run` to confirm the new version, CHANGELOG entry, and gate-pass list.
4. Run the real invocation; `publish.py` enforces every quality gate before push.
5. If any gate fails, fix the underlying issue and re-run — `publish.py` is idempotent (interrupted runs resume cleanly).
6. After publish, confirm the GitHub release URL was printed.

Copy this checklist and track your progress:

- [ ] Working tree clean
- [ ] Bump level chosen
- [ ] `--dry-run` reviewed
- [ ] Real `publish.py` invocation executed
- [ ] All gates passed
- [ ] GitHub release URL captured

## Output

- Updated `plugin.json::version`, `pyproject.toml::version`, `__version__` vars
- Refreshed README version badge
- Regenerated `CHANGELOG.md` entry
- Git commit + tag + push
- GitHub release published

## Error Handling

| Error | Resolution |
|-------|------------|
| Gate failure (lint/test/validate) | Fix the underlying issue, re-run (publish.py resumes idempotently) |
| Dirty working tree | `git stash` or commit changes before bumping |
| `gh auth` not set up | Run `gh auth login` first |
| Network timeout on push | Re-run; publish.py picks up where it left off |
| Version conflict on push | Pull first, resolve, re-run |

## Examples

```bash
# Patch bump (1.2.0 -> 1.2.1)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/publish.py" --patch

# Minor bump (1.2.0 -> 1.3.0)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/publish.py" --minor

# Major bump (1.2.0 -> 2.0.0)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/publish.py" --major

# Preview only
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/publish.py" --dry-run --patch
```

### Local-only bump (no push)

For version-only bumps without push (rare — e.g. CI smoke tests):

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" --patch
```

`bump_version.py` is a thin wrapper around `publish.bump_semver` and exists only for callers that need the version number without triggering the full pipeline. New code should call `publish.py` directly.

## Resources

- `plugin-management` skill — version bump details
- `canonical-pipeline` skill — publish.py pipeline standards
- `publish-to-marketplace` skill — marketplace notification after publish
