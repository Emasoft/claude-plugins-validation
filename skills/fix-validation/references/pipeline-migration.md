# Pipeline migration to current standards

## Table of Contents

- [§1 — Fix dangling script references](#1--fix-dangling-scriptnamepy-references)
- [§2 — Migrate to whole-repo lint via cpv_lint_engine](#2--migrate-to-whole-repo-lint-via-cpv_lint_engine)
- [§3 — Make publish.py idempotent](#3--make-publishpy-idempotent-interrupted-publish-recovery)
- [Combined verification](#combined-verification)

How the plugin-fixer auto-migrates a legacy plugin's CI/CD + release
pipeline to the canonical current standards. Three independent
migrations: stale script refs, lint consolidation, and publish.py
idempotency.

---

## §1 — Fix dangling script references

CPV's `validate_pipeline_script_refs` rule (added v2.65.1) emits
**`[MAJOR] Dangling reference to scripts/<name>.py — file does not exist`**
with `file:line` for every stale reference in:

- `.github/workflows/*.{yml,yaml}` — CI / release / notify-marketplace workflows
- `.git/hooks/*` — locally-installed pre-push / pre-commit / post-merge hooks
- `scripts/setup_plugin_pipeline.py` — the PRE_PUSH_HOOK template literal
- `skills/plugin-validation-skill/references/*` — reference hooks copied into new plugins

### Fix recipe

For each finding `[MAJOR] Dangling reference to scripts/<old>.py — found at <file>:<line>`:

| `<old>` | Action |
|---|---|
| `lint_files.py` | Replace with `cpv_lint_engine.py` (CI workflow line) **or** drop entirely (pre-push hook — `validate_plugin.py` covers it) |
| `lint_validation.py` | Same as `lint_files.py` (older alias) |
| Any other removed script | Read the script's last-known purpose from `git log --diff-filter=D --name-only`. If it had a replacement, swap the reference; if it was deleted with no replacement, remove the call site. |

### Verify

```bash
uv run python scripts/validate_plugin.py . --strict
# expect: 0 MAJOR findings from validate_pipeline_script_refs
```

---

## §2 — Migrate to whole-repo lint via `cpv_lint_engine`

If the plugin still has a separate `scripts/lint_files.py`, or its CI
workflow runs ruff/eslint/shellcheck/etc. as separate steps, consolidate
to the unified engine.

### Detection signals

| Signal | Severity | Source |
|---|---|---|
| `scripts/lint_files.py` exists | INFO (legacy artefact) | filesystem |
| `.github/workflows/ci.yml` has separate `Ruff check` / `ESLint` / `ShellCheck` steps for project source | INFO | workflow file |
| Pre-push hook calls a per-language linter directly | INFO | `.git/hooks/pre-push` |

### Fix recipe

1. **Delete `scripts/lint_files.py`** (always — the engine owns this)
2. **Replace per-language steps in `.github/workflows/ci.yml`**:

```yaml
# Before
- name: Ruff check
  run: uv run ruff check scripts/ tests/
- name: ESLint
  run: bunx eslint .
- name: ShellCheck
  run: shellcheck **/*.sh

# After (single step covers ALL supported languages)
- name: Lint all source files (read-only)
  run: uv run python scripts/cpv_lint_engine.py .
```

3. **Pre-push hook**: drop any direct linter call. The hook calls
   `scripts/validate_plugin.py` which already invokes
   `cpv_lint_engine` internally.

The unified engine supports Python, JS/TS, Rust, Go, Bash, Markdown,
YAML, JSON, TOML, Dockerfile, HTML, CSS, SQL, Lua, R — and uses
`uvx`/`bunx`/`docker` fallback so missing local binaries do NOT silently
skip the language.

### Verify

```bash
uv run python scripts/cpv_lint_engine.py . --strict
# expect: every present language reports OK or fails with explicit findings
```

---

## §3 — Make `publish.py` idempotent (interrupted-publish recovery)

A non-idempotent `publish.py` reads LOCAL `plugin.json.version` as the
bump baseline. When a publish is interrupted between commit+tag and
push (transient network failure, pre-push hook reject, GitHub 503),
the local repo is at the bumped version while origin is one minor
behind. Re-running `publish.py --minor` then DOUBLE-BUMPS — local
went from 2.63.2 → 2.64.0 (interrupted), and the second attempt would
go 2.64.0 → 2.65.0, skipping 2.64.0 entirely. This actually happened
on CPV's own publish.py during the v2.64.0 ship attempt.

### Detection signal

Run:
```bash
grep -E "^def _read_remote_version|^def _infer_bump_type|^def _git_porcelain_clean" scripts/publish.py
```

If the helpers are absent, `publish.py` is non-idempotent and must be
upgraded.

### Fix recipe

The simplest path is to regenerate `publish.py` from
`generate_plugin_repo.py`'s `gen_publish_py()` — every newly-scaffolded
plugin since v2.65.1 ships with idempotency baked in.

For a surgical patch (preserves customizations), add these five helpers
before `stage_bump`:

- `_read_remote_version(plugin_root) -> str | None` — reads
  `.claude-plugin/plugin.json` from `origin/master`
- `_infer_bump_type(old, new) -> str | None` — classifies a semver delta
- `_git_porcelain_clean(root) -> bool` — true iff working tree clean
- `_head_commit_message(root) -> str` — HEAD subject line
- `_local_tag_exists(root, tag) -> bool` — local tag presence check

Then modify two stages:

**`stage_bump`** — read REMOTE plugin.json as baseline. If
`current == new_ver`, skip the bump. If `current != remote and current != new_ver`,
refuse and ask for manual intervention.

**`stage_commit_and_push`** — skip the commit when HEAD's subject
already matches `chore: bump version to <new_ver>` AND the tree is
clean. Skip the tag when it already exists locally. The push always runs.

Reference implementation: `scripts/generate_plugin_repo.py:gen_publish_py`
(canonical) and `scripts/publish.py` (CPV's own — same helpers).

### Verify

```bash
# Simulate the interrupted-publish state and re-run:
echo '{"version":"X.Y.Z"}' > .claude-plugin/plugin.json   # already bumped
git commit -am "chore: bump version to X.Y.Z"             # already committed
git tag vX.Y.Z                                            # already tagged
# (push would have failed)
uv run python scripts/publish.py --minor
# expect: "Local plugin.json is already at X.Y.Z — skipping bump",
#         "HEAD is already 'chore: bump version to X.Y.Z' — skipping commit",
#         "Tag vX.Y.Z already exists locally — skipping tag step",
#         then push proceeds normally
```

---

## Combined verification

After all three migrations, the plugin should:

```bash
uv run python scripts/validate_plugin.py . --strict
# expect: 0 CRITICAL/MAJOR/MINOR/NIT (only WARNINGs allowed)

uv run python scripts/cpv_lint_engine.py .
# expect: every language reports OK

# And the publish.py interrupted-recovery test from §3 should resume cleanly.
```

If any of these still fail after applying the recipes, surface the
remaining findings to the user — do NOT push a half-migrated state.
