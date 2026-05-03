---
name: cpv-strip-dev-parts
description: |
  Move dev-only artefacts (default: tests/) from a plugin's MAIN repo
  into a per-plugin git submodule. Implements TRDD-793ac32a — exploits
  Claude Code's no-recurse-submodules shallow clone (PSS pattern).
  ONE submodule per plugin by default; add more to cpv.strip.extract[]
  in plugin.json if your plugin has additional heavy dev folders.
allowed-tools: Bash(uv:*), Bash(git:*), Bash(gh:*), Read, Write, Edit
user-invocable: true
---

# /cpv-strip-dev-parts

## Overview

`cpv strip-dev-parts` shrinks every CPV-style plugin install by moving
dev-only folders into per-plugin git submodules. Claude Code's plugin
installer does NOT pass `--recurse-submodules`, so the submodule content
never reaches the user — only the .gitmodules pointer (~86 bytes) does.

Verified empirically against PSS (`perfect-skill-suggester`): the
gigabytes of Rust source that lives in PSS's `rust/` submodule never
ship to end users. This command generalises the pattern to N submodules
per plugin.

## Modes

```bash
# Interactive (default) — prompts at each major step
cpv strip-dev-parts <plugin-path>

# Non-interactive — apply the standard rules with no prompts
cpv strip-dev-parts <plugin-path> --auto

# Preview only — no GH repos created, no commits made
cpv strip-dev-parts <plugin-path> --dry-run

# Per-target overrides
cpv strip-dev-parts <plugin-path> --extract tests/ --extract design/

# CI gate — exit 1 if any dev parts still in MAIN repo
cpv strip-dev-parts <plugin-path> --check

# Pull submodule content back into the working tree (devs)
cpv strip-dev-parts <plugin-path> --restore
```

## Default extraction target

Per the PSS pattern (verified empirically) — ONE submodule per plugin:

| Target | Submodule (auto-named) | Submodule path |
|---|---|---|
| `tests/` | `<owner>/<plugin>-tests` | `tests/` (same path) |

The submodule mounts at the SAME path as the original folder, so all
references in CI / scripts / README continue to work unchanged for devs
(after `git submodule update --init`). End-user installs get only the
.gitmodules pointer (no recurse-submodules in Claude Code's installer).

design/ and git-hooks/ are tiny (<300 KB combined) and intentionally
stay in the main repo. Add more entries to `cpv.strip.extract[]` in
`plugin.json` if your plugin has additional heavy dev folders worth
stripping. See §5 of TRDD-793ac32a for the full schema.

## Security model

This command performs DESTRUCTIVE operations (creates GitHub repos,
rewrites git history). The engine refuses to operate unless ALL of
these checks pass first:

1. Plugin is a git working tree (STRIP-W001)
2. Working tree is clean — no staged/unstaged/untracked changes (STRIP-W002)
3. Not running inside a linked git worktree (STRIP-W003)
4. No git stash entries (STRIP-W004)
5. No untracked files inside extraction targets (STRIP-W005)
6. No unmerged paths in progress (STRIP-W006)
7. HEAD is on a branch, not detached (STRIP-W007)

Per-target path validation (STRIP-E001..E006):

* Whitelist regex `^[a-z][a-z0-9_-]*(/[a-z][a-z0-9_-]*)*/?$`
* Reject `..` traversal, `/` prefix, symlinks
* Reject reserved paths (`.git`, `scripts`, `agents`, `commands`,
  `skills`, `hooks`, `templates`, `.claude-plugin`, `.gitmodules`)

GitHub repo safety (STRIP-G001..G003):

* `gh repo view <owner>/<plugin>-tests` pre-flight — abort if repo
  exists with non-trivial content (race or squat detected)
* If repo exists AND empty → re-use it
* If repo doesn't exist → `gh repo create --private` (always private)
* Post-create empty-state verification before pushing migration commit

`.gitmodules` URL allowlist (STRIP-G010..G015) — enforced both at
publish-time (validate_plugin.py) AND at strip-time:

* Per-plugin allowlist via `cpv.strip.allowed_submodule_urls`
* Default rule: same owner as parent OR `Emasoft`
* Optional opt-out: `cpv.strip.require_url_allowlist=false`

## Idempotent state machine

Each strip operation writes `.cpv-strip-state.json` at the plugin root
after each major transition. A crashed run can resume from the last
successful state:

```
INIT → REPO_VERIFIED → REPO_CREATED → CONTENT_PUSHED →
       SUBMODULE_ADDED → COMMITTED → DONE
```

Re-run the command to resume.

## Examples

```bash
# Preview a strip without doing anything
uv run python scripts/cpv_strip_dev.py /path/to/my-plugin --dry-run

# Strip tests/ only, with explicit target
uv run python scripts/cpv_strip_dev.py /path/to/my-plugin --dry-run \
  --extract tests/

# CI gate: fail if dev parts leaked back into MAIN repo
uv run python scripts/cpv_strip_dev.py /path/to/my-plugin --check
```

## What is intentionally NOT in this command

* The full live extraction flow (`--auto` real-execution) lands in
  Sprint 2 rc3 once the engine is battle-tested. This RC ships
  `--dry-run` and `--check` only, plus the publish-time allowlist
  enforcement.
* `--restore` is not implemented yet (devs use `git submodule update
  --init --recursive dev/<target>/` directly).

## References

* `scripts/cpv_strip_dev.py` — engine
* `scripts/cpv_validate_gitmodules.py` — security helper
* `tests/test_cpv_strip_dev_unit.py` — engine tests
* `tests/test_cpv_validate_gitmodules.py` — allowlist tests
* TRDD-793ac32a — full design rationale + risk register
* `~/.claude/plans/delegated-chasing-bentley.md` — sprint plan
