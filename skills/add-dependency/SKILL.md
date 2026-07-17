---
name: add-dependency
description: Add plugin dependencies to a target plugin (explicit --add specs or --from copy from another plugin's plugin.json). Use when adding/copying plugin.json::dependencies entries with atomic rollback on regression. Used dynamically via the-skills-menu (TRDD-478d9687).
when_to_use: When the cpv-main-menu user picks Create → Add dependencies, or any flow needs to add/copy plugin.json::dependencies entries with atomic rollback on regression
user-invocable: false
---

# add-dependency

## Overview

Adds one or more plugin dependencies to a target plugin's `plugin.json::dependencies` array. Two input modes that can be combined; the engine deduplicates by name (last-write-wins), sorts the result alphabetically, writes atomically, and rolls back from a `.bak` if the post-write validation introduces any new CRITICAL/MAJOR finding. Loaded dynamically via the-skills-menu, reached via the Create → Add dependencies menu branch.

## Prerequisites

- `uv` on PATH
- Target plugin path with `.claude-plugin/plugin.json`
- For `--from` git URLs: network access and `git` on PATH
- For cross-marketplace deps: the hosting marketplace's `marketplace.json::allowCrossMarketplaceDependenciesOn` allowlist updated

## Instructions

1. Always start with `--dry-run` to preview the diff against `plugin.json`.
2. Choose `--add` (explicit specs) and/or `--from` (copy from another plugin).
3. Use kebab-case names; versions accept any semver-range expression (`~1.2.0`, `^2.0`, `>=1.4`).
4. Run the real invocation; the script writes atomically (tmp + rename) and re-runs `validate_plugin --strict`.
5. If exit code is 3 (rollback), inspect the diff, narrow the spec, and re-try.
6. For unversioned bare-string adds, `validate_plugin --strict` emits a WARNING that the dependency auto-tracks the latest upstream tag. The only valid resolution is to pin a semver range (`name@@~1.2.0`); there is no config opt-out (`cpv.allow_unversioned_dependencies` was removed in TRDD-02e1672b and is now ignored with a `[RC-DEPRECATED-OPTOUT]` deprecation WARNING if present).

Copy this checklist and track your progress:

- [ ] Dry-run reviewed
- [ ] Explicit specs or `--from` source confirmed
- [ ] Real invocation run
- [ ] Exit code 0 (or 3 → analyze rollback reason)
- [ ] `validate_plugin --strict` re-run on target

## Output

- Updated `plugin.json::dependencies` array, sorted alphabetically, deduplicated by name.
- Atomic write via tmp + rename.
- `.bak` left next to `plugin.json` after a successful update so a human can roll back manually if needed.
- Exit code 0 on success, 1 on bad args, 2 on `--from` source unreadable, 3 on rollback after regression, 4 on atomic-write failure.

## Error Handling

| Error | Resolution |
|-------|------------|
| Exit 1: invalid args | Check `--add` spec syntax (see [Spec Syntax](references/spec-syntax.md)) |
| Exit 2: `--from` source unreachable | Verify path or git URL, check network |
| Exit 3: rollback after regression | Inspect new CRITICAL/MAJOR finding, narrow the spec, retry |
| Exit 4: atomic write failed | Disk full or permission denied — fix and re-run |
| WARNING: unversioned dependency auto-tracks latest tag | Pin a semver range (`name@@~1.2.0`) — the only valid resolution; there is no config opt-out |
| MAJOR cross-marketplace blocked | Add marketplace name to `marketplace.json::allowCrossMarketplaceDependenciesOn` |

## Examples

```bash
# Explicit single dep, version-pinned
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/add_dependencies.py" /path/to/my-plugin \
  --add dev-browser@@~1.2.0

# Always preview first with --dry-run
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/add_dependencies.py" /path/to/my-plugin \
  --add dev-browser \
  --dry-run
```

See [Spec Syntax](references/spec-syntax.md) for the full add-spec table and additional examples (cross-marketplace, --from URL, combined).

## Resources

- [Spec Syntax](references/spec-syntax.md) — add-spec syntax table + worked examples
  > add spec syntax · Full examples
- Spec reference: official `plugin-dependencies.md` docs
- `plugin-management` skill — show dependency tree + runtime errors before adding
- `plugin-validation-skill` — run `validate_plugin --strict` on the target after adding
