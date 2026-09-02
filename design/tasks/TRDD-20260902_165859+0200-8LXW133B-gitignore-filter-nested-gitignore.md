---
trdd-id: 8LXW133B
title: GitignoreFilter honours nested .gitignore files so a sub-crate build tree is pruned at descent
column: dev
created: 2026-09-02T16:58:59+0200
updated: 2026-09-02T16:58:59+0200
current-owner: claude-plugins-validation session
task-type: bugfix
min-approval-requirement: none
external-refs: [https://github.com/Emasoft/claude-plugins-validation/issues/226]
---

# GitignoreFilter honours nested .gitignore files

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-09-02

- Issue #226 (reporter: the ai-maestro-janitor session; reproducer verified by them, root cause verified here first-hand): `scripts/gitignore_filter.py::GitignoreFilter.__init__` loads ONLY `root/.gitignore` (line 183); `_match` consults only that spec; `is_ignored` / `is_dir_ignored` early-return False when the root has no patterns. A nested `scripts/memgrep/.gitignore` containing `/target` is therefore invisible, `rglob` descends a 98k-file cargo tree on every call, and `validate` blows its 1800 s budget.
- NEXT ACTION: lean-worker implements per-directory nested spec lookup + two-sided tests (spec in the body); orchestrator verifies with `uv run pytest tests/test_gitignore_filter*.py tests/test_issue_67_external_scanner_gitignore.py tests/test_cpv_lint_engine.py`, ruff, mypy, then publishes `--patch`.
- Decision: implement git semantics (nested `.gitignore` patterns apply relative to their own directory) rather than hardcoding `target`/`node_modules`/`dist` into `_VCS_CACHE_DIR_NAMES` — a tracked `dist/` or `build/` is legitimate plugin content and a name blacklist would hide it.

## Fix spec

1. `GitignoreFilter` keeps `self._dir_specs: dict[Path, tuple[list[str], object | None]]`, keyed by resolved directory; entry for `d` is `_load_pathspec(d / ".gitignore")` computed once (existence check once per directory).
2. `_match(rel)` is replaced by an ancestor-chain check: for a candidate path `p` under root, test the root spec against `p.relative_to(root)`, then for each ancestor directory `d` strictly below root up to `p.parent`, test `d`'s spec (if any patterns) against `p.relative_to(d)`. Trailing-`/` handling for directories is preserved at every level. Ignored by ANY level ⇒ ignored.
3. The `if not self.patterns: return False` early returns in `is_ignored` / `is_dir_ignored` go away (a nested file can exist with no root file).
4. `walk`, `rglob`, `iterdir` need no change beyond using the new matcher.

## Acceptance

- [ ] Nested `sub/.gitignore` with `/target` prunes `sub/target/` from `rglob`, `walk`, `iterdir`; `is_dir_ignored(root/sub/target)` is True; `root/target/` (no rule) is NOT ignored (anchoring respected).
- [ ] Positive control in the same test: a sibling `sub/src/x.py` still yields.
- [ ] No root `.gitignore` + nested one still prunes.
- [ ] Root-only behaviour unchanged (existing test files green).
- [ ] Ruff + mypy clean; published as a patch release with #226 closed by a fix comment naming the release.
