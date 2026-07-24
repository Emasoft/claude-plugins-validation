# Ship-only-binary canon fixes (RC-SHIP-BINARY-ONLY family, issue #175)

## Table of Contents

- [Overview](#overview)
- [RC-SHIP-BINARY-ONLY — compile source ships in the plugin tree](#rc-ship-binary-only--compile-source-ships-in-the-plugin-tree)
- [RC-SUBMODULE-SHIPS — a non-build-source submodule ships its content](#rc-submodule-ships--a-non-build-source-submodule-ships-its-content)
- [RC-SHIP-BINARY-ONLY-STRICT — the opt-in escalation is blocking](#rc-ship-binary-only-strict--the-opt-in-escalation-is-blocking)
- [RC-MIXED-COMPILED — informational, no action](#rc-mixed-compiled--informational-no-action)

## Checklist

- [ ] Identify the RC-* rule from the validation message
- [ ] Confirm the plugin ships a compiled (non-script) component
- [ ] Apply the fix from the matching section below (extract source out-of-tree, or drop the opt-in)
- [ ] Re-run `validate_plugin.py --strict` to confirm
- [ ] Read the canon reference: `skills/cpv-canonical-pipeline/references/compiled-component-canon.md`

## Overview

A compiled-component plugin MUST ship ONLY its built binaries under `bin/` plus
a platform dispatcher. Compile source, build libraries, and build-source git
submodules must NOT ship — Claude Code recursively fetches submodule content on
install, so a submodule pointer excludes nothing. These findings come from
`validate_plugin.py::validate_cross_platform`. The full rationale is in the
canon reference above.

Three of the four findings are non-blocking WARN/INFO; only
`RC-SHIP-BINARY-ONLY-STRICT` (MAJOR) blocks a `--strict` publish, and it fires
only when the manifest opts in.

## RC-SHIP-BINARY-ONLY — compile source ships in the plugin tree

| Field | Value |
|---|---|
| **Severity** | WARNING |
| **Validator** | `validate_plugin.py` (`validate_cross_platform`) |
| **Triggered by** | A build-source git submodule in `.gitmodules`, OR in-tree committed compile-source files when a real compiled component is present (`bin/`, a build system, or a build script) |
| **Why it matters** | Every committed byte ships to every user on install. Compile source is dead weight the user never runs and forces a compile step the canon eliminates |

### Fix

1. Extract the compile source to a SEPARATE repository. Use
   `scripts/cpv_strip_dev.py`: it removes the source directory from the plugin
   tree and records a `{path, url, sha}` reference in `plugin.json` under
   `cpv.strip.extract[]`. The build CI clones that repo by the pinned URL/tag,
   builds the per-target binaries, and commits ONLY the binaries to `bin/`.
   `cpv_strip_dev.py --restore` re-clones each reference by url+sha for local
   development.
2. Do NOT convert the source directory into a git submodule — Claude Code ships
   submodule content on install, so a submodule is not a fix (it re-triggers
   `RC-SHIP-BINARY-ONLY` / `RC-SUBMODULE-SHIPS`).
3. Alternatively, package the binary as a separate binary-carrier plugin
   (`bin/` + `plugin.json` only) and depend on it via the `dependencies` array
   and the `{name}--v{version}` resolver tag.
4. Ensure `bin/` ships one binary per supported platform×arch plus a dispatcher
   that resolves `$CLAUDE_PLUGIN_ROOT/bin/<name>-<os>-<arch>` and fail-safes on
   an unsupported platform.

## RC-SUBMODULE-SHIPS — a non-build-source submodule ships its content

| Field | Value |
|---|---|
| **Severity** | WARNING |
| **Validator** | `validate_plugin.py` (`validate_cross_platform`) |
| **Triggered by** | Any non-build-source submodule in `.gitmodules` (non-hinted source dirs like `engine/`, a repo-named crate, OR dev/test tooling like `tests/`, `docs/`, `examples/`) |
| **Why it matters** | Claude Code recursively fetches submodule content on install, so every file in the submodule ships to every user — a submodule pointer keeps nothing out |

### Fix

1. Confirm the submodule content is genuinely meant to be distributed and is
   safe to ship.
2. If it is build/dev/test tooling (docs, tests, examples) or non-hinted
   compile source, it need NOT ship: reference it out-of-tree instead of linking
   a submodule — have the build CI clone it by a pinned URL/tag (see
   `scripts/cpv_strip_dev.py`), and remove the entry from `.gitmodules`.
3. If it is genuinely required at runtime, keep it — but be aware its full
   content ships to every user.

## RC-SHIP-BINARY-ONLY-STRICT — the opt-in escalation is blocking

| Field | Value |
|---|---|
| **Severity** | MAJOR (publish-blocking) |
| **Validator** | `validate_plugin.py` (`validate_cross_platform`) |
| **Triggered by** | The manifest declares `cpv.canon: ship-only-binary` AND the plugin links ANY `.gitmodules` entry (build-source or other) OR ships in-tree compile-source |
| **Why it matters** | The plugin opted into strict enforcement of the canon, so a shipped submodule or in-tree source is a hard violation that blocks the publish |

### Fix

1. Migrate the plugin to the canon: apply the `RC-SHIP-BINARY-ONLY` /
   `RC-SUBMODULE-SHIPS` fixes above — extract all compile source to a separate
   repo cloned by URL/tag in CI, remove every `.gitmodules` entry, and ship only
   the built binaries under `bin/`.
2. OR remove the `cpv.canon: ship-only-binary` opt-in from `plugin.json` until
   the plugin is migrated. Dropping the opt-in reverts the finding to the
   non-blocking `RC-SHIP-BINARY-ONLY` / `RC-SUBMODULE-SHIPS` WARN (the
   never-retro-break guarantee — a plugin that does not opt in never blocks on
   this canon). Prefer option 1; drop the opt-in only as a temporary measure.
3. Note: renaming the submodule path (e.g. `rust/` → `tests/rust/`) does NOT
   clear the STRICT block — it escalates on ANY `.gitmodules` entry regardless of
   path.

## RC-MIXED-COMPILED — informational, no action

| Field | Value |
|---|---|
| **Severity** | INFO (non-blocking) |
| **Validator** | `validate_plugin.py` (`validate_cross_platform`) |
| **Triggered by** | A script-primary plugin (pipeline profile `standard`) that also ships a compiled component |
| **Why it matters** | Purely informational — it confirms CPV recognized the mixed-language shape and that the compiled part is already covered |

### Fix

No action required. This INFO simply surfaces that the plugin ships a compiled
component alongside a script-primary pipeline. The compiled build is already
gated by `RC-SHIP-BINARY-ONLY` and the generated `publish.py` G2e build gate
regardless of profile — no separate compiled pipeline profile is needed. If
`RC-SHIP-BINARY-ONLY` also fired, act on that; `RC-MIXED-COMPILED` alone needs
nothing.
