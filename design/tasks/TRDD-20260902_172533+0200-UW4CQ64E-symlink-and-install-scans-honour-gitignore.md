---
trdd-id: UW4CQ64E
title: Symlink scan and install-combo scan prune gitignored directories through GitignoreFilter
column: testing
created: 2026-09-02T17:25:33+0200
updated: 2026-09-02T17:33:00+0200
current-owner: claude-plugins-validation session
task-type: bugfix
min-approval-requirement: none
external-refs: [https://github.com/Emasoft/claude-plugins-validation/issues/227]
---

# Symlink scan and install-combo scan honour .gitignore

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-09-02

- Issue #227 (found by the review fork of the #226 fix, verified first-hand): two `os.walk` walkers in `scripts/validate_plugin.py` are gitignore-blind — `_iter_declared_component_symlinks` (line ~3075, prunes only `.git/.trashcan/node_modules/.venv/*_dev`) and `_check_unauthorized_install_combo` (line ~4381, prunes `_INSTALL_SCAN_SKIP_DIRS` by name). Same descend-then-filter shape #226 fixed in `GitignoreFilter`.
- Decision: keep `os.walk` in both (the symlink scan must SEE symlinks and hidden dirs, which `GitignoreFilter.walk` drops/skips) and add pruning via a local `gi = _gi or GitignoreFilter(plugin_root)` — `is_dir_ignored` on subdirs, `is_ignored` on files. A local instance, not the module global, because tests call these functions directly with `_gi` unset and the `if _gi else os.walk` convention elsewhere silently degrades to blind in exactly that case.
- DONE (17:33): both walkers pruned; `tests/test_issue_227_symlink_scan_gitignore.py` (4 tests, two-sided). Verified first-hand: 309 passed across the symlink/combo test files, ruff + mypy clean. CLAUDE.md v5.16.2 entry written.
- NEXT ACTION: `uv run python scripts/publish.py --patch` (v5.16.2); on CI green, fix comment on #227, card → `complete` + archive.

## Acceptance

- [ ] Symlink under a nested-ignored dir is neither reported nor walked; a symlink in a tracked sibling dir is still reported (positive control).
- [ ] Install-combo split across a tracked file and a nested-ignored file does NOT fire; the same pair in tracked files still fires (control).
- [ ] Existing symlink + combo tests green; ruff + mypy clean; published as v5.16.2 with #227 closed by a fix comment naming the release.
