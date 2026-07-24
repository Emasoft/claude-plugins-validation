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

### Per-target path validation (STRIP-E001..E007)

- Whitelist regex `^[a-z][a-z0-9_-]*(/[a-z][a-z0-9_-]*)*/?$`
- Reject `..` traversal, `/` prefix, symlinks
- Reject reserved paths (`.git`, `.gitmodules`, `.claude-plugin`, `scripts`, `agents`, `commands`, `skills`, `hooks`, `templates`)

### GitHub repo safety (STRIP-G001..G003)

- `gh repo view <owner>/<plugin>-<target>` pre-flight — abort if the repo exists with non-trivial content (race or squat detected)
- If the repo exists AND is empty → re-use it
- If the repo doesn't exist → `gh repo create` with the visibility resolved from `--visibility` (DEFAULT `private`; `public` is the ship-only-binary compile-source case)
- Post-create empty-state verification before pushing the migration commit

### Secret gate before a push (STRIP-S001 / STRIP-S002)

`git filter-repo` PRESERVES history, so the extracted subdirectory's every past commit rides along to the new repo. A PUBLIC target is therefore FAIL-CLOSED:

- `trufflehog git file://<clone>` scans the filtered clone's FULL history after `git remote add` and BEFORE the push (30-minute timeout)
- Any finding on a PUBLIC target → `STRIP-S001`, push refused
- trufflehog absent, timed out, or a non-`{0,183}` exit on a PUBLIC target → `STRIP-S002`, push refused (a scan that cannot be trusted is not a clean scan)
- A PRIVATE target warns and proceeds — it is not the leak surface the public compile-source case introduces

NEVER downgrade a PUBLIC run to `--visibility private` to clear a finding. Purge the secret from history (or make the tool available) and retry.

### Recorded-reference validation (STRIP-R001..R020)

The strip records a `{path, url, sha}` reference in `.claude-plugin/plugin.json` under `cpv.strip.extract[]`. No `.gitmodules` is written, so there is no submodule URL to allowlist. Instead, each record is fail-fast validated before `--restore` acts on it:

- `url` must match the strict HTTPS GitHub URL pattern (no userinfo, no alternate host, no scheme substitution)
- `git clone -- <url>` is used so a URL can never be read as an option
- `path` must resolve inside the plugin tree; `sha` must be a full commit SHA, and `--restore` checks out that SHA detached, then strips the nested `.git` so the restored files are plain files

`scripts/cpv_validate_gitmodules.py` still guards any `.gitmodules` a plugin carries for its own reasons, but this engine no longer creates one.

## Idempotent state machine

Each strip operation writes `.cpv-strip-state.json` at the plugin root after each major transition. A crashed run can resume from the last successful state:

```text
INIT -> REPO_VERIFIED -> CONTENT_PUSHED ->
        REFERENCE_RECORDED -> COMMITTED -> DONE
```

`REFERENCE_RECORDED` replaces the former `SUBMODULE_ADDED`: the step records the `{path, url, sha}` reference instead of adding a gitlink. Re-run the skill to resume.

## What is intentionally NOT in this skill

- Interactive mode. The engine ships `--dry-run`, `--check`, `--auto`, and `--restore`; a run with no mode flag is preview-only.
- Any submodule mechanism. `git submodule add` was removed in v3.12.0 because Claude Code recursively fetches submodule content on install, so a submodule pointer excludes nothing from a user's install.
- Automatic visibility escalation. `--visibility public` is always an explicit, deliberate choice by the caller.

## References

- `scripts/cpv_strip_dev.py` — engine
- `scripts/cpv_validate_gitmodules.py` — `.gitmodules` allowlist helper (no longer written by this engine)
- `tests/test_cpv_strip_dev_unit.py` — engine tests
- `tests/test_cpv_validate_gitmodules.py` — allowlist tests
- TRDD-793ac32a — original design rationale + risk register (its no-recurse premise is superseded)

## Default extraction target

ONE extraction target per plugin by default:

| Target | Source repo (auto-named) | Recorded reference |
|--------|--------------------------|--------------------|
| `tests/` | `<owner>/<plugin>-tests` | `{"path": "tests/", "url": "https://github.com/<owner>/<plugin>-tests", "sha": "<commit>"}` |

The extracted directory is removed from the plugin tree, so it no longer ships to end users. Developers get it back with `--restore`, which re-clones each record at its pinned SHA to the SAME path the original folder occupied — so references in CI / scripts / README keep working.

`design/` and `git-hooks/` are tiny (<300 KB combined) and intentionally stay in the main repo. Add more entries to `cpv.strip.extract[]` in `plugin.json` if your plugin has additional heavy dev folders worth stripping, or pass `--extract <path>/` for a one-off (for example the compile-source directory of a compiled component). See §5 of TRDD-793ac32a for the declaration schema.
