---
trdd-id: H2WD3FN9
title: Lint extensionless shebang scripts — close the blind spot that shipped a broken git hook
column: complete
created: 2026-07-26T09:09:34+0200
updated: 2026-07-26T09:32:49+0200
current-owner: cpv-session
task-type: bugfix
scope: project
release-via: publish
relevant-rules: []
parent-trdd: 9XJTVI88
implementation-commits: []
---

# Lint extensionless shebang scripts — close the blind spot that shipped a broken git hook

## Problem

`cpv_lint_engine.detect_languages()` buckets files **purely by filename glob**
(`scripts/cpv_lint_engine.py:606-627`): `collect("python", ["*.py"])`,
`collect("shell", ["*.sh", "*.bash"])`, and so on. A file whose language is declared by a
**shebang** rather than a suffix therefore lands in no bucket and is **never linted**.

This is not hypothetical, and it is not confined to CPV:

1. **It already caused a real defect.** CPV's own `git-hooks/pre-push` shipped with a
   `NameError` (a `time` reference with no import, and a `DIM` constant that did not exist)
   in the one script that gates every push. It passed ruff, mypy, 11k tests, `--strict`
   self-validation, and a full publish pipeline — because no gate ever looked at it.
   `ruff check git-hooks/` printed `No Python files found` and then `All checks passed`:
   a checker inspecting **zero** files emits the same green as a clean one.
2. **CPV ships the same blind spot to every plugin it scaffolds.**
   `generate_plugin_repo.py` installs `git-hooks/pre-push` into generated repos
   (`:2159-2178`) and documents `git-hooks/` in the generated tree (`:1349`), while
   `gen_pyproject_toml` emits only `extend-exclude` (`:1097`) — no `extend-include`. So
   every scaffolded plugin carries an extensionless Python hook that neither CPV's lint
   gate nor the plugin's own `ruff check` will ever read.

The v3.19.2 `extend-include` entry in CPV's `pyproject.toml` fixed **CPV's own repo only**.
It is a per-repo workaround, not the product fix: CPV validates *other people's* plugins,
and it cannot rely on each of them having configured ruff correctly. The engine must
detect the language itself.

## Verified facts (not assumptions)

- ✓ `detect_languages` is glob-only — read at `cpv_lint_engine.py:589-629`.
- ✓ The only tracked extensionless shebang files in CPV are `git-hooks/pre-commit` and
  `git-hooks/pre-push`, both `#!/usr/bin/env python3` (enumerated over `git ls-files`).
- ✓ The v3.19.2 cold self-validate reported `[PYTHON] 562 file(s)` and `[SHELL] 1 file(s)`
  — neither hook is in those counts.
- ✓ `generate_plugin_repo.py` emits `extend-exclude` but **not** `extend-include`.
- ✓ `GitignoreFilter.rglob` (`gitignore_filter.py:330`) already prunes gitignored dirs and
  skips symlinks, so a `rglob("*")` pass inherits the existing trust boundary.

## Design

**Two complementary fixes — neither subsumes the other.**

### 1. Engine (universal; the product fix)

Add `_shebang_language(path) -> str | None` and one extra bucketing pass in
`detect_languages`:

- Read the first 256 bytes as **bytes**; require a literal `#!` prefix (binary-safe, no
  decode of arbitrary content).
- Parse the interpreter: split the shebang line; if the basename is `env`, take the next
  non-flag, non-`VAR=value` token.
- Strip trailing version digits/dots — `python3.12` → `python`.
- Map only into languages the engine already lints: `python` → `python`;
  `sh|bash|zsh|dash|ksh` → `shell`; `node` → `javascript`. Anything else → `None`
  (a `#!/usr/bin/perl` file stays unlinted rather than being mis-bucketed).

Scope the pass to **suffix-less files only** (`path.suffix == ""`). This is deliberate
conservatism: a `.txt` that happens to start with `#!` is not a Python file, and the
failure mode being fixed is specifically the extensionless one. Files already claimed by
another bucket (notably `Dockerfile`, which is suffix-less) are excluded so nothing is
double-linted.

### 2. Scaffolder parity

Emit `extend-include = ["git-hooks/pre-push", "git-hooks/pre-commit"]` from
`gen_pyproject_toml`, so a generated plugin's own `ruff check` — run by its CI and its
hooks, entirely outside CPV — sees them too.

## Why this TIGHTENS the gate (intended)

More files get linted, so a plugin with a dirty extensionless script that previously passed
will now correctly fail. That is the point: the gate was reporting green over unread code.
Per the standing directive, this is a strictness increase and is never to be softened into
an advisory.

## Test plan (two-sided, per detector behaviour)

- a dirty extensionless Python hook IS detected (the failing case must fail);
- a clean one passes;
- `LICENSE` / a no-shebang extensionless file is ignored;
- a binary extensionless file does not crash the reader;
- `#!/bin/bash` buckets to `shell`, `#!/usr/bin/env node` to `javascript`;
- `#!/usr/bin/env python3.12` normalizes to `python`;
- an unknown interpreter (`#!/usr/bin/perl`) is NOT bucketed;
- `Dockerfile` is not double-bucketed;
- the generated pyproject contains `extend-include` (scaffolder parity).

## Verification (measured, not asserted)

**End-to-end two-sided, on one identical fixture** (`/tmp/shebang-proof/git-hooks/pre-push`,
an extensionless `#!/usr/bin/env python3` file containing an undefined name):

| | plain `ruff check git-hooks/` | CPV engine, after this change |
|---|---|---|
| dirty hook | `No Python files found` → `All checks passed!` | `MAJOR — Ruff: 1 error(s) ... F821 git-hooks/pre-push:6:11 Undefined name` (`lint_python` → `False`) |
| clean hook | — | `PASSED — ruff check passed for 1 Python file(s)` (`lint_python` → `True`) |

The left column is the state every scaffolded plugin was in.

- 22 new unit tests, all two-sided in shape: each positive mapping is paired with a negative
  (`perl` NOT bucketed, `LICENSE` NOT bucketed, `#!` on line 2 NOT a shebang, `Dockerfile`
  not double-bucketed, binary files return `None` instead of raising).
- Dogfood test asserts CPV's OWN `pre-push` + `pre-commit` are discovered — if the engine
  ever regresses to glob-only, that test fails rather than quietly going green again.
- Bucket count on CPV's own tree moved 562 → 565 python files = 562 + 2 hooks + the new
  test file. Arithmetic reconciled rather than eyeballed.
- Full serial suite: 11350 passed, 3 skipped, `REAL_PYTEST_EXIT=0`, zero `FAILED`/`ERROR`.
- `ruff check` clean; `mypy scripts/` — no issues in 132 source files.

## Notes

Parent: TRDD-9XJTVI88, which found the blind spot while auditing idle-calibrated guards and
fixed it for CPV's own tree. This TRDD is the product-level half.

Lesson worth carrying: the v3.19.2 `extend-include` was a correct fix aimed at the wrong
scope. It made CPV's own repo green while leaving every plugin CPV *produces* and *validates*
in the failing state. When a defect is found in a tool's own tree, ask whether the tool
EMITS the same defect — a validator that fixes only itself has fixed the least important
instance of the bug.
