# Security Model and Idempotent State Machine

## Table of Contents

- [Security model](#security-model)
- [Idempotent state machine](#idempotent-state-machine)
- [What is intentionally NOT in this skill](#what-is-intentionally-not-in-this-skill)
- [References](#references)
- [Default extraction target](#default-extraction-target)

## Security model

This skill performs DESTRUCTIVE operations (creates GitHub repos, rewrites git history). The engine refuses to operate unless ALL of these checks pass first:

1. Plugin is a git working tree (STRIP-W001)
2. Working tree is clean — no staged/unstaged/untracked changes (STRIP-W002)
3. Not running inside a linked git worktree (STRIP-W003)
4. No git stash entries (STRIP-W004)
5. No untracked files inside extraction targets (STRIP-W005)
6. No unmerged paths in progress (STRIP-W006)
7. HEAD is on a branch, not detached (STRIP-W007)

### Per-target path validation (STRIP-E001..E006)

- Whitelist regex `^[a-z][a-z0-9_-]*(/[a-z][a-z0-9_-]*)*/?$`
- Reject `..` traversal, `/` prefix, symlinks
- Reject reserved paths (`.git`, `scripts`, `agents`, `commands`, `skills`, `hooks`, `templates`, `.claude-plugin`, `.gitmodules`)

### GitHub repo safety (STRIP-G001..G003)

- `gh repo view <owner>/<plugin>-tests` pre-flight — abort if repo exists with non-trivial content (race or squat detected)
- If repo exists AND empty → re-use it
- If repo doesn't exist → `gh repo create --private` (always private)
- Post-create empty-state verification before pushing migration commit

### `.gitmodules` URL allowlist (STRIP-G010..G015)

Enforced both at publish-time (`validate_plugin.py`) AND at strip-time:

- Per-plugin allowlist via `cpv.strip.allowed_submodule_urls`
- Default rule: same owner as parent OR `Emasoft`
- Optional opt-out: `cpv.strip.require_url_allowlist=false`

## Idempotent state machine

Each strip operation writes `.cpv-strip-state.json` at the plugin root after each major transition. A crashed run can resume from the last successful state:

```text
INIT -> REPO_VERIFIED -> CONTENT_PUSHED ->
        SUBMODULE_ADDED -> COMMITTED -> DONE
```

Re-run the skill to resume.

## What is intentionally NOT in this skill

- The full live extraction flow (`--auto` real-execution) lands in Sprint 2 rc3 once the engine is battle-tested. The current RC ships `--dry-run` and `--check` only, plus the publish-time allowlist enforcement.
- `--restore` is not implemented yet (devs use `git submodule update --init --recursive dev/<target>/` directly).

## References

- `scripts/cpv_strip_dev.py` — engine
- `scripts/cpv_validate_gitmodules.py` — security helper
- `tests/test_cpv_strip_dev_unit.py` — engine tests
- `tests/test_cpv_validate_gitmodules.py` — allowlist tests
- TRDD-793ac32a — full design rationale + risk register

## Default extraction target

Per the PSS pattern (verified empirically) — ONE submodule per plugin:

| Target | Submodule (auto-named) | Submodule path |
|--------|------------------------|----------------|
| `tests/` | `<owner>/<plugin>-tests` | `tests/` (same path) |

The submodule mounts at the SAME path as the original folder, so all references in CI / scripts / README continue to work unchanged for devs (after `git submodule update --init`). End-user installs get only the `.gitmodules` pointer.

`design/` and `git-hooks/` are tiny (<300 KB combined) and intentionally stay in the main repo. Add more entries to `cpv.strip.extract[]` in `plugin.json` if your plugin has additional heavy dev folders worth stripping. See §5 of TRDD-793ac32a for the full schema.
