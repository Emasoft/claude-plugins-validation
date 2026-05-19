---
name: strip-dev-submodules
description: Move dev-only artefacts (default tests/) from a plugin's MAIN repo into a per-plugin git submodule. Use when shrinking end-user installs via shallow-clone trick. Used dynamically via the-skills-menu (TRDD-478d9687).
when_to_use: When the cpv-main-menu user picks Manage → Strip dev parts, or any flow needs to move heavy dev folders (tests/, etc) into git submodules so end-user installs stay small
user-invocable: false
allowed-tools: Bash(uv:*), Bash(git:*), Bash(gh:*), Read, Write, Edit
---

# strip-dev-submodules

## Overview

Shrinks every CPV-style plugin install by moving dev-only folders into per-plugin git submodules. Claude Code's plugin installer does NOT pass `--recurse-submodules`, so the submodule content never reaches the user — only the `.gitmodules` pointer (~86 bytes) does. Verified empirically against PSS (`perfect-skill-suggester`): the gigabytes of Rust source that lives in PSS's `rust/` submodule never ship to end users. This skill generalises the pattern to N submodules per plugin. Loaded by `cpv-main-menu-agent` via the Manage → Strip dev parts menu branch.

Implements TRDD-793ac32a — exploits Claude Code's no-recurse-submodules shallow clone (PSS pattern).

## Prerequisites

- `uv`, `git`, `gh` on PATH
- Clean working tree, on a branch (not detached HEAD), no stash entries, no untracked files
- For real (non-dry) extraction: `gh auth` set up + write access to the target GitHub owner
- Per-plugin allowlist via `cpv.strip.allowed_submodule_urls` if not extracting under the parent owner or `Emasoft`

## Instructions

1. Run `--dry-run` first to confirm extraction plan + safety preflight passes.
2. Run `--check` in CI to fail if dev parts have leaked back into MAIN.
3. Choose extraction mode: interactive (default), `--auto`, or `--dry-run`.
4. Per-target overrides via `--extract <path>/` (default: `tests/`).
5. For each strip operation, the engine writes `.cpv-strip-state.json` for resume on crash.
6. After successful strip, devs use `git submodule update --init` to pull content back.

Copy this checklist and track your progress:

- [ ] `--dry-run` reviewed
- [ ] Safety preflight passes (working tree clean, on branch, no stash)
- [ ] Per-target paths confirmed
- [ ] Real run executed (`--auto` or interactive)
- [ ] `.gitmodules` URL allowlist satisfied
- [ ] Devs notified: `git submodule update --init` needed

## Output

- The selected dev folder(s) replaced by a `.gitmodules` pointer (~86 bytes).
- A per-plugin private GitHub repo holding the moved content (`<owner>/<plugin>-tests` by default).
- A new commit on the parent repo wiring up the submodule.
- `.cpv-strip-state.json` written at each transition for crash recovery.

## Error Handling

| Error | Resolution |
|-------|------------|
| STRIP-W002 working tree dirty | Commit or stash changes, then re-run |
| STRIP-W003 inside linked worktree | Run from the main worktree |
| STRIP-G001 squat detected | Investigate the squatting repo; pick a different name |
| STRIP-G010 URL not in allowlist | Add to `cpv.strip.allowed_submodule_urls` in plugin.json |
| Crash mid-run | Re-run — state machine resumes from `.cpv-strip-state.json` |

## Examples

```bash
# Preview a strip without doing anything
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" /path/to/my-plugin --dry-run

# Strip tests/ only, with explicit target
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" /path/to/my-plugin --dry-run \
  --extract tests/

# CI gate: fail if dev parts leaked back into MAIN repo
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" /path/to/my-plugin --check

# Interactive (default) — prompts at each major step
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" /path/to/my-plugin

# Non-interactive — apply the standard rules with no prompts
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" /path/to/my-plugin --auto
```

### Default extraction target

Per the PSS pattern (verified empirically) — ONE submodule per plugin. See [Security and State](references/security-and-state.md#default-extraction-target) for the full table and `cpv.strip.extract[]` schema.

## Resources

- [Security and State Machine](references/security-and-state.md)
  > Security model · Idempotent state machine · What is intentionally NOT in this skill · References · Default extraction target
- `plugin-management` skill — overall plugin lifecycle
- `canonical-pipeline` skill — pipeline standards for the plugin shape
