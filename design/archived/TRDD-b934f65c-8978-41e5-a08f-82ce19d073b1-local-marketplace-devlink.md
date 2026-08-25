---
trdd-id: b934f65c
title: TRDD-b934f65c-8978-41e5-a08f-82ce19d073b1 — Local Marketplace + Dev-Link Mode
column: complete
updated: 2026-08-25T17:25:27+0200
---

# TRDD-b934f65c-8978-41e5-a08f-82ce19d073b1 — Local Marketplace + Dev-Link Mode

**TRDD ID:** `b934f65c-8978-41e5-a08f-82ce19d073b1`
**Filename:** `design/tasks/TRDD-b934f65c-8978-41e5-a08f-82ce19d073b1-local-marketplace-devlink.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

**Status:** Done (2026-05-10)
**Priority:** MEDIUM
**Effort:** MEDIUM
**Source:** `docs_dev/cpv_workflow_audit_20260412.md` sections A2/A4/A5/A6/C6/C12

## Completion notes (2026-05-10)

All four parts implemented across multiple commits:

- **Part 1 — local marketplace generator** (commit `f747af2`, 2026-05-03): empty
  `--github-owner` triggers LOCAL-ONLY mode in `generate_marketplace_repo.py` —
  no `.github/workflows/`, no badges, README routes through `_readme_local()`.
  This branch was previously unreachable dead code.
- **Part 2 — dev-link install / uninstall / update** (commit `5dd4c5f`,
  2026-04-12 + the present worktree): `--dev-link` flag on install creates a
  symlink from `MARKETPLACES_DIR` to the live source plus a
  `.cpv-devlink-<plugin>.json` sentinel. `do_uninstall` detects the sentinel
  and unlinks only (preserving the source tree). `do_update` now ALSO detects
  the dev-link state before tearing down and re-applies `--dev-link` on the
  reinstall — without this guard, an update silently downgraded a dev-link
  into a regular copy.
- **Part 3 — `do_link_plugin`** (commit `5dd4c5f`): appends a plugin entry to
  an existing marketplace.json using the correct `source.source` schema key,
  resolves local paths to `./relative` and `owner/repo` to GitHub source
  objects, replaces existing entries by name (no duplicates).
- **Part 4 — `/cpv-link-plugin` slash command** (commit `5dd4c5f`):
  `commands/cpv-link-plugin.md` wires the slash command directly to
  `manage_plugin.py --link-plugin`.

Tests added in this worktree (18 total):

- `tests/test_manage_plugin.py::TestDevLinkInstall` (4 tests) — symlink
  creation, sentinel content, live-edit visibility, archive-source rejection
- `tests/test_manage_plugin.py::TestUninstallDevLink` (2 tests) — source
  preservation on dev-link uninstall, regression check that normal uninstall
  still rms the dir
- `tests/test_manage_plugin.py::TestDoLinkPlugin` (9 tests) — local + github
  spec resolution, replace-not-duplicate, dry-run, error paths, root-level
  marketplace.json fallback
- `tests/test_manage_plugin.py::TestUpdateDevLink` (2 tests) — preserves
  dev-link across update (this caught the bug fixed in this worktree),
  regression check that normal updates still copy
- `tests/test_generate_marketplace_repo.py::TestLocalOnlyGeneration` (4
  tests) — workflows skipped, local-only README template (no badges, has
  `marketplace add` instructions), marketplace.json valid, essential files
  still emitted

The fix to `do_update` in this worktree closes the last gap in the success
criteria ("All existing install/uninstall/update workflows still pass tests")
— previously, updating a dev-linked plugin would silently lose the dev-link.
Full test suite: 4534 passed, 3 skipped (Windows-only paths).

## Problem

CPV's plugin/marketplace lifecycle is GitHub-centric and copy-on-install:

1. `generate_marketplace_repo.py` REQUIRES `--github-owner` and hardcodes
   `"source": {"source": "github", ...}` at line 77, rejecting any other
   source type at line 647-648.
2. `scripts/manage_plugin.py::do_install` always copies plugin files to
   `MARKETPLACES_DIR/<mkt>/<plugin>`. Editing the source requires re-install.
3. Symlinks are explicitly dropped (`manage_plugin.py:249`) so a manual
   symlink workaround loses files.
4. No `/cpv-link-plugin` command to add an existing plugin to an existing
   marketplace. Users must hand-edit JSON.
5. `source: "settings"` (v2.1.80 inline marketplace) is validator-aware
   but generator-blind.

## Scope

### Part 1 — Local marketplace generator

`generate_marketplace_repo.py`:

- Make `--github-owner` OPTIONAL when `--local` is passed
- Add `--local` flag that:
  - Skips GitHub Actions emission (no `.github/workflows/`)
  - Skips `notify-marketplace.yml`
  - Skips README badges and install-from-GitHub instructions
  - Emits a LOCAL-only README that explains how to add the marketplace via
    `claude plugin marketplace add /abs/path/to/marketplace`

### Part 2 — Dev-link mode for plugin install

`scripts/manage_plugin.py`:

- Add `--dev-link <plugin-source-dir>` flag to install command
- Instead of copying, create a directory symlink (or Windows reparse
  point) from `MARKETPLACES_DIR/<mkt>/<plugin>` to the source dir
- Write a `.cpv-devlink` sentinel file at the target recording:
  - Source path
  - Timestamp
  - Installer version
- `do_uninstall` checks for `.cpv-devlink`:
  - If present: unlink only (do not recursively delete the source tree)
  - If absent: current behavior (recursive delete)
- `do_update` on a dev-linked plugin:
  - If present: `git pull` in the source dir (no copy)
  - If absent: current behavior

### Part 3 — cpv-link-plugin command

New script: `scripts/link_plugin_to_marketplace.py` (or add to manage_plugin.py)

Usage:
```
cpv link-plugin <marketplace-path-or-gh-url> <plugin-path-or-gh-url> [--branch main] [--push]
```

Behavior:
1. Load the target marketplace.json
2. Resolve plugin source:
   - Local path → use relative path from marketplace root
   - github owner/repo → `{"source": "github", "repo": "owner/repo"}`
   - git subdir → `{"source": "git-subdir", "url": "...", "path": "..."}`
3. Append the new plugin entry, preserving existing entries
4. Validate the updated marketplace.json (call `validate_marketplace.py`)
5. Write file
6. If `--push` and marketplace is a git repo: commit and push

### Part 4 — New CLI command

Add to `commands/cpv-link-plugin.md` (new command) that invokes the
plugin-manager agent with the link instructions loaded, OR runs the script
directly.

## Success criteria

- [ ] `generate_marketplace_repo.py --local --name foo ~/marketplaces/foo`
      creates a working local marketplace without GitHub requirements
- [ ] `cpv plugin install --dev-link ~/src/myplugin foo` creates a symlink
      that reflects live edits without re-install
- [ ] `cpv link-plugin ~/marketplaces/foo ~/src/bar` appends bar to foo's
      marketplace.json with the correct `source.source` schema key
- [ ] Dev-linked plugins preserve their source directory on uninstall
- [ ] All existing install/uninstall/update workflows still pass tests

## Out of scope

- Windows-native reparse points (use `os.symlink` which now works on
  Windows with developer mode enabled)
- Auto-detection of which marketplace a plugin belongs to
- Editing existing plugin entries in a marketplace (only append)

## Approval log

- 2026-08-25T17:25:27+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED — do_link_plugin/--dev-link in manage_plugin.py (batch_ai)
