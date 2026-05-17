---
name: deterministic-codemod
description: Deterministic codemod CLI — bulk-fix backtick-path → markdown-link, add TOC stubs, dedup blank lines, and other mechanical text transforms (issue #17). Zero LLM cost.
when_to_use: When the cpv-main-menu user picks Fix → Deterministic codemod, or any flow needs zero-LLM-cost mechanical transforms (backtick→link, TOC stubs, blank-line dedup) instead of dispatching the plugin-fixer agent
user-invocable: false
allowed-tools: Read, Bash, Glob, Grep
---

# deterministic-codemod

Bulk-applies the inverse of CPV's detection regexes — read-only audit
becomes read-write fix at zero LLM cost. Designed for high-volume
mechanical fixes where the plugin-fixer agent is the wrong tool because
the work is line-local and predictable.

Addresses [GitHub issue #17](https://github.com/Emasoft/claude-plugins-validation/issues/17)
and the high-volume Categories C and D of [issue #16](https://github.com/Emasoft/claude-plugins-validation/issues/16).

## Subcommands

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Subcommand                    ┃ What it does                                                                  ┃ Validator findings cleared      ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ backtick-to-link              │ ``path/file.md`` in prose → ``[file](path/file.md)``                          │ Issue #16 C — ~1240 MINORs      │
├───┼───────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┤
│ 2 │ add-toc                       │ Prepend ``## Table of Contents`` from existing ``##`` headings (≥50 lines)    │ Issue #16 D — 473 MINOR + 782 NIT │
├───┼───────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┤
│ 3 │ wrap-placeholder-paths        │ Wrap unresolved placeholder-shaped prose paths in ``<...>``                   │ MINORs across plugins           │
├───┼───────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┤
│ 4 │ add-standard-sections         │ Insert missing ``## Overview`` / ``## Examples`` / ``## Output`` in SKILL.md  │ Structural MAJORs               │
├───┼───────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┤
│ 5 │ dedup-trailing-blanks         │ Collapse runs of 3+ newlines into exactly 2                                   │ NITs                            │
├───┼───────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┤
│ 6 │ external-skip-list            │ Add ``external/``, ``vendor/``, submodule paths to ``cpv.exclude_paths``      │ Issue #16 F — vendored MINORs   │
├───┼───────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┼─────────────────────────────────┤
│ 7 │ all                           │ Run every applicable subcommand in safe order                                 │ All of the above                │
└───┴───────────────────────────────┴───────────────────────────────────────────────────────────────────────────────┴─────────────────────────────────┘
```

## Safety contract (mandatory)

1. **Dry-run is the default.** `--apply` is opt-in and always pairs with
   a per-file backup under `.cpv-codemod-backup/<timestamp>/<rel-path>`.
2. **Backups are atomic** — a single timestamped directory mirrors the
   plugin layout for instant rollback (`mv .cpv-codemod-backup/<ts>/* .`).
3. **Per-edit transparency** — every change is shown as a unified diff
   with file path + line numbers in dry-run, so the maintainer can review
   before applying.
4. **Skip vendored subtrees** — `external/`, `vendor/`, `vendored/`,
   `third_party/`, `node_modules/`, `.venv/`, `dist/`, `build/`,
   `__pycache__/`, AND any path declared in `.gitmodules`.
5. **Skip the npm-package shape.** `@scope/name`, `name@version`, and
   `id/version` are NOT paths — left alone.
6. **Idempotency.** Running the codemod twice on the same plugin produces
   no further changes.
7. **No commit.** The codemod never invokes git. The maintainer reviews
   diffs and commits.

## Usage

```bash
# Dry run (default) — show diff for review without writing
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_codemod.py" backtick-to-link \
  --plugin /path/to/plugin

# Apply with backup — only writes after diff preview is confirmed
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_codemod.py" backtick-to-link \
  --plugin /path/to/plugin --apply

# Add TOC stubs only to files with ≥80 lines
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_codemod.py" add-toc \
  --plugin /path/to/plugin --apply --min-lines 80

# Run every codemod in safe order
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_codemod.py" all \
  --plugin /path/to/plugin --apply
```

## Recommended workflow

```bash
# 1. Audit first — capture baseline finding counts
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  plugin /path/to/plugin --report /tmp/before.md

# 2. Dry-run codemod — review the proposed diff
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_codemod.py" all \
  --plugin /path/to/plugin

# 3. Apply
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_codemod.py" all \
  --plugin /path/to/plugin --apply

# 4. Re-audit — confirm finding counts dropped
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  plugin /path/to/plugin --report /tmp/after.md

# 5. Diff the two reports to see what was actually fixed
diff /tmp/before.md /tmp/after.md | head -100
```

## When the codemod is the WRONG tool

Use the **plugin-fixer agent** (not this codemod) when:

- The fix requires reading the file's semantics (e.g. "rewrite the
  `description:` to add trigger phrases").
- The fix touches frontmatter `description:` rewrites that need to
  preserve trigger phrases.
- The fix requires deciding which content moves to references (judgment).
- The fix requires resolving a cross-skill conflict (judgment).

The codemod handles ONLY line-local mechanical transforms. Everything
else stays with the agent.

## Recovery

If something goes wrong, every `--apply` run leaves a per-file backup:

```bash
ls .cpv-codemod-backup/
# 20260502_193015+0200/
# 20260502_194022+0200/

# Roll the most recent run back
LATEST=$(ls -1t .cpv-codemod-backup/ | head -n1)
cd .cpv-codemod-backup/"$LATEST" && cp -r ./* ../../
```

## Related

- `plugin-validation-skill` — find what to fix (read-only)
- `fix-validation` — agent-driven fixes for findings that
  require judgment
