# Compiled-component canon (ship only the binary)

## Table of Contents

- [Overview](#overview)
- [The canon shape — bin/ per platform×arch + a dispatcher](#the-canon-shape--bin-per-platformarch--a-dispatcher)
- [Two compliant source-hosting options](#two-compliant-source-hosting-options)
- [Why a git submodule is non-compliant](#why-a-git-submodule-is-non-compliant)
- [The four findings](#the-four-findings)
- [The cpv.canon opt-in — WARN escalates to a blocking MAJOR](#the-cpvcanon-opt-in--warn-escalates-to-a-blocking-major)
- [The generated publish.py build gates (G2e / G2f)](#the-generated-publishpy-build-gates-g2e--g2f)

## Overview

A plugin that ships a compiled (non-script) component — Rust, Go, C, C++,
C#, Swift, Zig, and the like — MUST build it to per-platform × per-arch
binaries and ship ONLY those binaries under `bin/`, plus a platform
dispatcher. NO compile source, NO build libraries, and NO build-source git
submodule may ship inside the installed plugin. The compile source lives in
a SEPARATE repository the build CI clones by pinned URL/tag, or the binary
is packaged as a separate binary-carrier plugin.

CPV enforces this canon with four findings emitted by
`validate_plugin.py::validate_cross_platform`. Three are advisory WARNs for
every plugin; a plugin that opts in via `cpv.canon: ship-only-binary` gets
them escalated to a publish-blocking MAJOR.

## The canon shape — bin/ per platform×arch + a dispatcher

The installed plugin ships:

- `bin/<name>-<os>-<arch>` — one prebuilt binary per supported target
  (e.g. `bin/mytool-darwin-arm64`, `bin/mytool-linux-amd64`,
  `bin/mytool-windows-amd64.exe`). The build CI produces these from a matrix
  over targets.
- A platform dispatcher — a small script that resolves
  `$CLAUDE_PLUGIN_ROOT/bin/<name>-<os>-<arch>` at runtime and fail-safes
  with a clear error on an unsupported platform. The dispatcher (and the
  `bin/` binaries) are the ONLY runtime artifacts; the plugin runs from
  `bin/` alone.

Nothing else compiled ships: no `.rs` / `.go` / `.c` / `.cs` source, no
`Cargo.toml` / `go.mod` / build scripts, no `target/` or `build/` output.

**Why this matters:** every byte a plugin commits ships to every user's
machine on install. Compile source, build toolchains, and intermediate
artifacts are dead weight the end user never runs — they bloat the install,
widen the attack surface, and force a compile step the canon exists to
eliminate. Shipping only the binary keeps the installed plugin minimal and
runnable with no toolchain.

## Two compliant source-hosting options

There are exactly two compliant ways to host the compile source:

1. **Separate repo, cloned by URL/tag in CI.** The compile source lives in
   its own repository. The build CI clones it by a PINNED URL and tag (NOT a
   submodule), builds the per-target binaries, and commits ONLY the binaries
   to `bin/`. CPV's `scripts/cpv_strip_dev.py` operationalizes the
   extraction: it removes the source directory from the plugin tree and
   records a `{path, url, sha}` reference in `plugin.json` under
   `cpv.strip.extract[]`; `--restore` re-clones each reference pinned to its
   SHA and strips the nested `.git` so the restored files are plain files,
   not a gitlink.
2. **Binary-carrier plugin.** The binary is packaged as a separate plugin
   that ships `bin/` + `plugin.json` only. The consuming plugin depends on it
   via the `dependencies` array and the `{name}--v{version}` dependency
   resolver tag.

**Why this matters:** both options keep the compile source out of the
installed artifact while keeping the build reproducible — a pinned URL/tag
or a version-pinned dependency is as reproducible as an in-tree copy, without
shipping the source to every user.

## Why a git submodule is non-compliant

A build-source git submodule is NOT a way to keep source out of installs.
**Claude Code recursively fetches git-submodule CONTENT on install** — this
was verified empirically on the reference compliant plugin
perfect-skill-suggester (PSS) 3.10.8: its installed `rust/` submodule shipped
1.7 MB of Rust source (8 `.rs` files, 3 `Cargo.toml`, `Cargo.lock`) into the
plugin cache. The submodule is a bare pointer in the source repo, yet the
installed plugin carried the full checked-out content.

So a submodule POINTER excludes nothing: the source ships anyway. That is why
the canon forbids a build-source submodule and why CPV's detectors are
checkout-independent — they read `.gitmodules` directly, so they fire even
when CPV validates a source repo where the submodule is an unpopulated
pointer.

**Why this matters:** the intuition that "a submodule pointer keeps the
content out of the install" is empirically false for Claude Code. Relying on
it ships exactly the source the canon means to exclude.

## The four findings

All four are emitted by `validate_plugin.py::validate_cross_platform`
(issue #175).

| Identifier | Severity | Trigger |
|---|---|---|
| `RC-SHIP-BINARY-ONLY` | WARNING | A build-source git submodule in `.gitmodules`, OR in-tree committed compile-source files — the in-tree case gated on a real compiled component (a `bin/`, a build system, or a build script). |
| `RC-SUBMODULE-SHIPS` | WARNING | Any non-build-source submodule in `.gitmodules` (its content ships on install anyway; closes the v3.8.0 hint-whitelist false negative). |
| `RC-SHIP-BINARY-ONLY-STRICT` | MAJOR (publish-blocking) | Fires ONLY when the manifest opts in via `cpv.canon: ship-only-binary`. Blocks on CONTENT not names: ANY `.gitmodules` entry (build-source OR other) or in-tree compile-source. |
| `RC-MIXED-COMPILED` | INFO | A script-primary plugin (pipeline profile `standard`) that ALSO ships a compiled component. Its build is covered by `RC-SHIP-BINARY-ONLY` + the generated `publish.py` G2e gate regardless of profile. |

**Why this matters:** `RC-SUBMODULE-SHIPS` exists because the earlier
detector only fired for a build-source-hint whitelist, so a non-hinted source
submodule (`engine/`, a repo-named crate) or a dev/test submodule drew zero
finding despite Claude Code shipping its content. Every submodule now draws a
finding. `RC-MIXED-COMPILED` is purely informational — it tells the author
CPV recognized the mixed-language shape and that the compiled part is already
gated; it invents no new pipeline profile.

## The cpv.canon opt-in — WARN escalates to a blocking MAJOR

`RC-SHIP-BINARY-ONLY` and `RC-SUBMODULE-SHIPS` are non-blocking WARNs for
every plugin. A plugin that declares `cpv.canon: ship-only-binary` in its
manifest additionally gets those findings escalated to a publish-blocking
`RC-SHIP-BINARY-ONLY-STRICT` MAJOR. The opt-in reader is
`cpv_pipeline_profile.opts_into_ship_only_binary_canon(plugin_root)`, which
returns True iff `plugin.json` has `cpv.canon == "ship-only-binary"`.

The escalation blocks on CONTENT, not names: it fires on ANY `.gitmodules`
entry — build-source OR other — which kills the rename-downgrade gaming vector
(moving `rust/` under `tests/rust/` would flip `RC-SHIP-BINARY-ONLY` to the
softer `RC-SUBMODULE-SHIPS`, but under the opt-in both escalate identically).

**Never-retro-break guarantee (#170):** the opt-in is a selector, not a
suppressor, and is fail-safe. A plugin that does NOT opt in keeps exactly its
current WARN — nothing green starts failing unbidden. A missing, malformed, or
non-`"ship-only-binary"` value reads False, so only the WARN fires. The WARN
is ALWAYS emitted first; the MAJOR is only ever ADDED on top, so the opt-in
can never silence a finding — a broken manifest degrades to today's behavior,
never to a silenced violation.

**Why this matters:** a blanket WARN→MAJOR flip would retro-break every plugin
that ships a compiled submodule and is green today (WARNING is the only
non-blocking tier). The opt-in makes the canon enforceable without breaking a
single green plugin — the plugin (or a successful migration) declares
readiness before the block turns on.

## The generated publish.py build gates (G2e / G2f)

The canonical pipeline's generated `publish.py` carries per-plugin,
self-detecting build gates that run before the release is cut:

- **G2e — compiled build gate** (table-driven, self-detecting per language):
  Rust (`clippy` with `-D warnings` + test), Go (vet + build + test), .NET
  (`dotnet build`), Swift (`swift build`), Zig (`zig build`). C/C++ is
  detect-and-note (no false-block-safe universal local build command — CI's
  controlled toolchain builds it; `RC-SHIP-BINARY-ONLY` still enforces the
  canon). Each gate self-detects its manifest, degrades to a WARNING when the
  toolchain is absent (never a false block), and blocks on a real failure.
- **G2f — shell gate:** `shellcheck` over `*.sh` / `*.bash`.

**Why this matters:** the compiled component must build clean (lints + tests)
before it ships as a binary. G2e/G2f enforce that locally at the pre-push
gate, so a real clippy defect or shell bug can never ship inside the binary —
independent of the plugin's pipeline profile.
