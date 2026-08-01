# Compiled-component canon (ship only the binary)

## Table of Contents

- [Overview](#overview)
- [The canon shape — bin/ per platform×arch + a dispatcher](#the-canon-shape--bin-per-platformarch--a-dispatcher)
- [Two compliant source-hosting options](#two-compliant-source-hosting-options)
- [Why a git submodule is non-compliant](#why-a-git-submodule-is-non-compliant)
- [The findings](#the-findings)
- [The canon is UNIVERSAL (v5.0.0) — and the one declared exception](#the-canon-is-universal-v500--and-the-one-declared-exception)
- [The generated publish.py build gates (G2e / G2f)](#the-generated-publishpy-build-gates-g2e--g2f)

## Overview

A plugin that ships a compiled (non-script) component — Rust, Go, C, C++,
C#, Swift, Zig, and the like — MUST build it to per-platform × per-arch
binaries and ship ONLY those binaries under `bin/`, plus a platform
dispatcher. NO compile source, NO build libraries, and NO build-source git
submodule may ship inside the installed plugin. The compile source lives in
a SEPARATE repository the build CI clones by pinned URL/tag, or the binary
is packaged as a separate binary-carrier plugin.

CPV enforces this canon with findings emitted by
`validate_plugin.py::validate_cross_platform`. **As of v5.0.0 enforcement is
UNIVERSAL**: a compiled plugin blocks on publish unless it declares the
`cpv.canon: none` exception.

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

1. **Separate PUBLIC repo, cloned by URL/tag in CI.** The compile source lives
   in its own repository, and that repository is PUBLIC — the release CI must
   be able to clone it by URL tokenlessly, and the binary built from it ships
   publicly anyway, so public source is transparency, not exposure. The build
   CI clones it by a PINNED URL and tag (NOT a submodule), builds the
   per-target binaries, and commits ONLY the binaries to `bin/`. CPV's
   `scripts/cpv_strip_dev.py` operationalizes the extraction: it removes the
   source directory from the plugin tree and records a `{path, url, sha}`
   reference in `plugin.json` under `cpv.strip.extract[]`; `--restore`
   re-clones each reference pinned to its SHA and strips the nested `.git` so
   the restored files are plain files, not a gitlink. One command creates the
   repo end-to-end:

   ```bash
   uv run python scripts/cpv_strip_dev.py <plugin> --auto \
     --extract <source-dir>/ --force-extract --visibility public
   ```

   The push is gated by a FAIL-CLOSED secret scan of the extracted history
   (`git filter-repo` preserves every past commit, so an old secret would go
   public with it): a finding raises `STRIP-S001` and an untrusted scan raises
   `STRIP-S002`, both refusing the push. Purge the secret and retry — never
   downgrade the visibility to get past the gate. Confirm with the user before
   creating a public repository.
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

## The findings

Emitted by `validate_plugin.py::validate_cross_platform` (issue #175), with the
attestation rules from `cpv_binary_attestation.py` (issue #185 §2/§3).

| Identifier | Severity | Trigger |
|---|---|---|
| `RC-SHIP-BINARY-ONLY` | WARNING | A build-source git submodule in `.gitmodules`, OR in-tree committed compile-source files — the in-tree case gated on a real compiled component (a `bin/`, a build system, or a build script). |
| `RC-SUBMODULE-SHIPS` | WARNING | Any non-build-source submodule in `.gitmodules` (its content ships on install anyway; closes the v3.8.0 hint-whitelist false negative). |
| `RC-SHIP-BINARY-ONLY-STRICT` | MAJOR (publish-blocking) | **v5.0.0: fires for EVERY compiled plugin** unless it declares `cpv.canon: none`. Blocks on CONTENT not names: ANY `.gitmodules` entry (build-source OR other) or in-tree compile-source. |
| `RC-ATTEST-MISSING` | MAJOR (publish-blocking) | **v5.0.0** (WARNING in 4.3.0): a binary in `bin/` with no `cpv.attest[]` record. Run `cpv-remote-validate attest --emit .`. |
| `RC-SHIP-BINARY-ONLY-OPTOUT` | WARNING | The manifest declares `cpv.canon: none`. The findings above stay advisory — a declared exception, never a clean bill of health. |
| `RC-MIXED-COMPILED` | INFO | A script-primary plugin (pipeline profile `standard`) that ALSO ships a compiled component. Its build is covered by `RC-SHIP-BINARY-ONLY` + the generated `publish.py` G2e gate regardless of profile. |

**Why this matters:** `RC-SUBMODULE-SHIPS` exists because the earlier
detector only fired for a build-source-hint whitelist, so a non-hinted source
submodule (`engine/`, a repo-named crate) or a dev/test submodule drew zero
finding despite Claude Code shipping its content. Every submodule now draws a
finding. `RC-MIXED-COMPILED` is purely informational — it tells the author
CPV recognized the mixed-language shape and that the compiled part is already
gated; it invents no new pipeline profile.

## The canon is UNIVERSAL (v5.0.0) — and the one declared exception

Through v4.3.0 the blocking escalation required an opt-in
(`cpv.canon: ship-only-binary`). In practice that enforced almost nothing: the
plugins most in need of migrating were exactly the ones that never opted in.
**v5.0.0 makes enforcement the default.** `RC-SHIP-BINARY-ONLY-STRICT` and
`RC-ATTEST-MISSING` are publish-blocking MAJORs for every compiled plugin.

The escalation blocks on CONTENT, not names: it fires on ANY `.gitmodules`
entry — build-source OR other — which kills the rename-downgrade gaming vector
(moving `rust/` under `tests/rust/` would flip `RC-SHIP-BINARY-ONLY` to the
softer `RC-SUBMODULE-SHIPS`, but both escalate identically).

**Why attestation had to ship first.** Enforcing "ship only the binary" before
`cpv.attest[]` existed would have forced the fleet to ship binaries that
nothing could tie to a source revision — CPV's five scanners cannot read native
code, so the record IS the audit trail. An opaque blob is a worse outcome than
shipped source, which is the opposite of what a security validator is for.
v4.3.0 shipped the record; v5.0.0 turns on the requirement that makes it
checkable. That ordering is the whole design decision, not a scheduling detail.

### The exception: `cpv.canon: none`

```json
{ "cpv": { "canon": "none" } }
```

This is the ONLY escape hatch, and it withholds the BLOCK, never the finding —
every advisory still fires, plus an `RC-SHIP-BINARY-ONLY-OPTOUT` WARNING naming
the exception. It exists because the flip **deliberately retro-breaks plugins
that are green today** (consciously overriding #170 rather than quietly bending
it), and some authors cannot migrate on this release's schedule — a source repo
they do not control, a build they cannot yet reproduce in CI. Without a declared
exception their only recourse would be to stop upgrading CPV, which would cost
them every OTHER security fix too. A visible, greppable exception is strictly
better than that.

**Fail-safe direction is INVERTED from the old opt-in, and this is the
security-relevant part.** `cpv_pipeline_profile.ship_canon_opted_out()` returns
False on a missing, malformed, or unreadable manifest — i.e. **ENFORCED**. Under
the opt-in a broken manifest degraded to "advisory"; under a mandatory canon it
must degrade to "enforced", or a corrupt manifest would buy an exemption.

A legacy `cpv.canon: ship-only-binary` declaration is now redundant rather than
wrong: it is honoured, reported as `RC-SHIP-BINARY-ONLY-DECLARED` (INFO), and
may be removed.

## The generated publish.py build gates (G2e / G2f)

> **Where the canon actually lives — read this before diffing anything.**
> A plugin's canonical `publish.py` is the output of **`gen_publish_py()` in
> `scripts/generate_plugin_repo.py`**. That function is the canon; the gates
> below exist only there.
>
> **`<cpv>/scripts/publish.py` is CPV's OWN release pipeline, not a template.**
> Diffing your plugin's `publish.py` against it is the natural move and it is
> wrong: you will find zero build gates and a large apparent gap (one report
> measured 1805 vs 3236 lines with 40+ functions seemingly "missing"), which
> reads as catastrophic drift and invites a destructive wholesale port. To see
> the real canon for your plugin, generate it:
>
> ```bash
> uv run python -c "import sys; sys.path.insert(0,'scripts'); \
>   import generate_plugin_repo as g; \
>   print(g.gen_publish_py(g.PluginParams(name='<your-plugin>', description='x', \
>     author='x', author_email='x@example.com'), profile='<your-profile>'))" > /tmp/canon-publish.py
> diff /tmp/canon-publish.py scripts/publish.py
> ```
>
> And determine your PROFILE first — `gen_publish_py` is profile-aware, so a
> diff taken against the wrong profile is meaningless. See
> "Profile-aware" in `cpv-canonical-pipeline/SKILL.md`.

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
