---
name: deterministic-codemod
description: "Deterministic codemod CLI — bulk-fix backtick-path to markdown-link, add TOC stubs, dedup blank lines, and other mechanical text transforms (issue 17). Zero LLM cost. Use when mechanical fixes outnumber semantic ones. Used dynamically via the-skills-menu (TRDD-478d9687)."
when_to_use: When the cpv-main-menu user picks Fix → Deterministic codemod, or any flow needs zero-LLM-cost mechanical transforms (backtick->link, TOC stubs, blank-line dedup) instead of dispatching the plugin-fixer agent
user-invocable: false
---

# deterministic-codemod

## Overview

Bulk-applies the inverse of CPV's detection regexes — read-only audit becomes read-write fix at zero LLM cost. Designed for high-volume mechanical fixes where the plugin-fixer agent is the wrong tool because the work is line-local and predictable. Addresses [GitHub issue #17](https://github.com/Emasoft/claude-plugins-validation/issues/17) and the high-volume Categories C and D of [issue #16](https://github.com/Emasoft/claude-plugins-validation/issues/16). Loaded dynamically via the-skills-menu, reached via the Fix → Deterministic codemod menu branch.

See [subcommands](references/subcommands.md) for detail.
> Subcommand table · Safety contract · Recommended workflow · When the codemod is the WRONG tool · Recovery

## Prerequisites

- `uv` on PATH
- Target plugin path
- (Optional) `git` for reviewing diffs before commit

## Instructions

1. Choose a subcommand: `backtick-to-link`, `add-toc`, `wrap-placeholder-paths`, `add-standard-sections`, `dedup-trailing-blanks`, `external-skip-list`, or `all`.
2. Run dry-run first (default — no `--apply`). Inspect the diff output.
3. Re-run with `--apply` to write. A per-file backup is created under `.cpv-codemod-backup/<timestamp>/<rel-path>`.
4. Re-validate via `plugin-validation-skill` and confirm finding counts dropped.
5. If something went wrong, restore from `.cpv-codemod-backup/<timestamp>/`.

Copy this checklist and track your progress:

- [ ] Subcommand chosen
- [ ] Dry-run output reviewed
- [ ] `--apply` run (backup created)
- [ ] `validate_plugin` re-run, counts dropped
- [ ] Backup deleted only after commit

## Output

- Modified `.md` files at the chosen plugin path.
- Per-file backups under `.cpv-codemod-backup/<timestamp>/<rel-path>` (atomic, mirrors the plugin layout).
- A unified-diff print of every change.

## Error Handling

| Error | Resolution |
|-------|------------|
| Unknown subcommand | See the subcommands reference (linked above in Overview) |
| No diff produced | Codemod already idempotent on this tree |
| Permission denied on write | Check file permissions, or run from a writable workspace |
| Backup dir already exists | Re-runs create new timestamped dirs — old ones are kept until manual cleanup |

## Examples

```bash
# Dry run (default) — show diff for review without writing
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_codemod.py" backtick-to-link \
  --plugin /path/to/plugin

# Apply with backup — only writes after diff preview is confirmed
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_codemod.py" backtick-to-link \
  --plugin /path/to/plugin --apply

# Add TOC stubs only to files with >=80 lines
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_codemod.py" add-toc \
  --plugin /path/to/plugin --apply --min-lines 80

# Run every codemod in safe order
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_codemod.py" all \
  --plugin /path/to/plugin --apply
```

## Resources

- [Subcommands](references/subcommands.md)
  > Subcommand table · Safety contract · Recommended workflow · When the codemod is the WRONG tool · Recovery
- `plugin-validation-skill` — find what to fix (read-only)
- `fix-validation` skill — agent-driven fixes for findings that require judgment
