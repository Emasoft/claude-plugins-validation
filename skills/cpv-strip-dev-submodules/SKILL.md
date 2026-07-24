---
name: cpv-strip-dev-submodules
description: Move dev-only folders or compile source out of a plugin's MAIN repo into a SEPARATE repo referenced by pinned URL + SHA, so the installed plugin stops shipping them. Use when shrinking a plugin install before publishing, or when creating the PUBLIC compile-source repo the ship-only-binary canon requires. Used dynamically via cpv-the-skills-menu (TRDD-478d9687).
when_to_use: When the cpv-main-menu user picks Manage → Strip dev parts, or any flow needs to move heavy dev folders (tests/, fixtures) or compile source (rust/, go/) out of the shipped plugin tree into a separate repository the manifest references by URL
user-invocable: false
---

# cpv-strip-dev-submodules

## Overview

Shrinks a plugin install by moving dev-only folders — or the compile source of a
compiled component — out of the plugin tree into a SEPARATE GitHub repository, and
recording a `{path, url, sha}` reference in `.claude-plugin/plugin.json` under
`cpv.strip.extract[]`. The extracted directory is REMOVED from the plugin tree and
no `.gitmodules` is written, so the content stops shipping. `--restore` re-clones
each reference pinned to its SHA for local development. Loaded dynamically via
cpv-the-skills-menu, reached via the Manage → Strip dev parts menu branch.

### Corrected threat model — read this before acting

**Claude Code RECURSIVELY FETCHES git-submodule CONTENT on install.** Verified
empirically on perfect-skill-suggester 3.10.8: its installed `rust/` submodule
carried 1.7 MB of Rust source into the plugin cache. A submodule pointer therefore
keeps NOTHING out of a user's install.

Earlier revisions of this skill asserted the opposite — that the installer skipped
submodule content, so only a tiny pointer file shipped. That claim is EMPIRICALLY
FALSE. An agent following it would ship compile source (and every other "stripped"
dev folder) to every user while believing it did not. The claim was corrected in
CPV v3.8.0 and the mechanism retargeted in v3.12.0: `cpv_strip_dev.py` no longer
runs `git submodule add` and never writes `.gitmodules`. This skill's directory
name is historical; there is ONE model — clone by URL.

## Prerequisites

- `uv`, `git`, `git-filter-repo`, `gh` on PATH; `trufflehog` for the secret gate
- Clean working tree, on a branch (not detached HEAD), no stash entries, no
  untracked files inside the extraction targets
- For real (non-dry) extraction: `gh auth` set up + write access to the target owner
- The extraction source must not be a reserved path (`.git`, `.gitmodules`,
  `.claude-plugin`, `scripts`, and the other component dirs)

## Instructions

1. Run `--dry-run` first to confirm the extraction plan + safety preflight, and to
   see the exact repo visibility the live run would use.
2. Run `--check` in CI to fail if stripped parts have leaked back into MAIN.
3. Choose the mode: `--dry-run` (preview), `--check` (CI gate), `--auto` (live
   execution), or `--restore` (re-clone every recorded reference). Running with NO
   mode flag is preview-only — the engine prints the plan and a NOTE telling you to
   pass `--auto`, then exits without touching GitHub or git history.
4. Choose the visibility: `--visibility private` (DEFAULT — a general dev-artefact
   strip must never auto-publicize) or `--visibility public`. The ship-only-binary
   migration passes `--visibility public` so the plugin's release CI can clone the
   source repo by URL tokenlessly.
5. A PUBLIC target is secret-gated FAIL-CLOSED before the push, because
   `git filter-repo` preserves the extracted subdirectory's FULL history — a secret
   buried in an old or deleted commit would leak the instant the repo goes public.
   `STRIP-S001` blocks on any trufflehog finding; `STRIP-S002` blocks when the scan
   cannot be trusted (trufflehog absent, timed out, or exited on an error). A
   PRIVATE target warns and proceeds. NEVER downgrade to `--visibility private` to
   get past a finding — purge the secret from history and retry.
6. Per-target overrides via `--extract <path>/` (default: `tests/`). A target under
   256 KB or under 20 files is skipped as not worth extracting; `--force-extract`
   bypasses that threshold (a small crate must still extract).
7. Each strip operation writes `.cpv-strip-state.json` for resume on crash.
8. After a successful strip, devs pull the content back with `--restore`, which
   clones each record at its pinned SHA and strips the nested `.git` so the restored
   files are plain files, not a gitlink.

Copy this checklist and track your progress:

- [ ] `--dry-run` reviewed (plan + resolved visibility)
- [ ] Safety preflight passes (working tree clean, on branch, no stash)
- [ ] Per-target paths confirmed (no reserved path)
- [ ] Visibility decided deliberately (`public` only when the source must be
      cloneable by CI without a token)
- [ ] Secret gate passed on its own merits — never bypassed
- [ ] Real run executed (`--auto`)
- [ ] `cpv.strip.extract[]` records verified in `.claude-plugin/plugin.json`
- [ ] Devs notified: `--restore` pulls the content back

## Output

- The selected folder(s) REMOVED from the plugin tree. No `.gitmodules` is created.
- A GitHub repo holding the extracted content WITH its history
  (`<owner>/<plugin>-<target>` by default), public or private per `--visibility`.
- A `{path, url, sha}` record appended to `cpv.strip.extract[]` in
  `.claude-plugin/plugin.json`.
- A new commit on the parent repo recording the removal + the reference.
- `.cpv-strip-state.json` written at each transition for crash recovery.

## Error Handling

| Error | Resolution |
|-------|------------|
| STRIP-W002 working tree dirty | Commit or stash changes, then re-run |
| STRIP-W003 inside linked worktree | Run from the main worktree |
| STRIP-E001..E007 invalid or reserved extraction path | Pick a non-reserved, in-tree, non-symlinked path |
| STRIP-G001 squat detected | Investigate the squatting repo; pick a different name |
| STRIP-S001 secrets found in the extracted history | Purge them from history, then retry. Do NOT switch to `--visibility private` to get past it |
| STRIP-S002 secret scan untrusted (tool absent, timeout, scan error) | Install or repair trufflehog and re-run; the PUBLIC push stays blocked until a scan completes |
| STRIP-R001..R020 malformed `cpv.strip.extract[]` record | Fix the `{path, url, sha}` record (HTTPS GitHub URL, full SHA) in plugin.json |
| Crash mid-run | Re-run — state machine resumes from `.cpv-strip-state.json` |

## Examples

```bash
# Preview a strip without doing anything
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" /path/to/my-plugin --dry-run

# Strip tests/ only, with explicit target
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" /path/to/my-plugin --dry-run \
  --extract tests/

# CI gate: fail if stripped parts leaked back into the MAIN repo
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" /path/to/my-plugin --check

# No mode flag — preview only (prints the plan, then exits with a NOTE
# to pass --auto; never creates repos or rewrites history)
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" /path/to/my-plugin

# Live execution — private source repo (the default)
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" /path/to/my-plugin --auto

# Ship-only-binary migration — PUBLIC compile-source repo, fail-closed secret gate.
# CONFIRM WITH THE USER before this: a public repo is immediately cloneable and
# indexable, and the extracted directory's FULL history becomes public.
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" /path/to/my-plugin --auto \
  --extract rust/ --force-extract --visibility public

# Restore every recorded reference for local development
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_strip_dev.py" /path/to/my-plugin --restore
```

### Default extraction target

ONE target per plugin by default. See [Security and State](references/security-and-state.md#default-extraction-target)
for the full table and the `cpv.strip.extract[]` schema.

## Resources

- [Security and State Machine](references/security-and-state.md)
  > Security model · Idempotent state machine · What is intentionally NOT in this skill · References · Default extraction target
- `cpv-canonical-pipeline` skill — the ship-only-binary canon for compiled components
- `cpv-fix-validation` skill — the RC-SHIP-BINARY-ONLY remediation recipe
- `cpv-plugin-management` skill — overall plugin lifecycle
