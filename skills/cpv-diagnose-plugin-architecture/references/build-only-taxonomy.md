# Build-only taxonomy — 4 categories, per-language patterns, remediation

Every non-runtime path the engine surfaces is classified into exactly ONE
of four categories. Each category has a distinct remediation that points at
EXISTING machinery — the diagnostic recommends; it never moves, deletes, or
mutates anything.

This doc is the canonical taxonomy + pattern reference. The engine emits
the `category` field on each finding; map it to the remediation here.

## Table of Contents

- [Category 1 — BUILD_SOURCE](#category-1--build_source)
- [Category 2 — RUNTIME_DEP](#category-2--runtime_dep)
- [Category 3 — DEV_ONLY](#category-3--dev_only)
- [Category 4 — BUILD_CACHE](#category-4--build_cache)
- [FN-safety rules every recommendation obeys](#fn-safety-rules-every-recommendation-obeys)
- [Category to remediation quick map](#category-to-remediation-quick-map)

## Category 1 — BUILD_SOURCE

Source code and build manifests that only PRODUCE the `bin/` binaries —
they are never executed at runtime. The compiled output in `bin/` ships;
the source does not.

**Remediation:** strip the source into a per-plugin git submodule (the PSS
model — Claude Code's shallow clone does NOT `--recurse-submodules`, so the
submodule content never reaches the user, only an ~86-byte `.gitmodules`
pointer). Add a `cpv.strip.extract[]` entry to `plugin.json` and run
`cpv strip-dev-parts`. The engine emits a ready-to-paste
`strip_extract_entry` on each `BUILD_SOURCE` finding.

**Per-language signals:**

- **Rust** — a crate dir (`rust/`, `crates/`, any dir with `Cargo.toml` +
  `src/*.rs`) whose output lands in `bin/`; `**/*.rs`, the `Cargo.toml` /
  `Cargo.lock` of a build crate.
- **Go** — `*.go` packages compiled to `bin/` (not the plugin's own
  runtime), `go.mod` / `go.sum` of a build module.
- **C / C++ / Zig** — `*.c *.cc *.cpp *.h *.hpp *.zig` plus a `Makefile` /
  `CMakeLists.txt` / `build.zig` that produces `bin/`.
- **Swift** — `Package.swift` + `Sources/` producing `bin/`.
- **JVM (Java / Kotlin / Scala)** — `*.java *.kt *.scala` plus `pom.xml` /
  `build.gradle*` producing `bin/`.
- **.NET** — `*.cs *.fs` plus `*.csproj` / `*.sln`.
- **Other native** — Ruby native extensions; Haskell `*.hs` + `*.cabal` /
  `stack.yaml`; Deno / Bun build sources.
- **Generic build systems** — `Bazel` / `BUILD`, `meson.build`, autotools
  (`configure.ac`, `Makefile.am`).

## Category 2 — RUNTIME_DEP

Dependency trees that the Anthropic plugins-reference says to install at
RUNTIME into `${CLAUDE_PLUGIN_DATA}` rather than ship in the plugin.

**Remediation:** recommend the install-on-first-use pattern — a
`SessionStart` (or `Setup`) hook that populates `${CLAUDE_PLUGIN_DATA}` on
first run (cite the docs' npm example in `anthropic-plugin-components.md`)
— AND gitignore the shipped copy so it stops being tracked. The engine
lists each `RUNTIME_DEP` path in both `recommendations.claude_plugin_data`
and `recommendations.gitignore_add`.

**Per-ecosystem signals:**

- **Node** — `node_modules/`, `.pnp.*` (Yarn PnP).
- **Python** — `.venv/`, `venv/`, `site-packages/`, `__pypackages__/`.
- **Ruby** — `.bundle/`, `vendor/bundle/`.
- **Generic** — `vendor/` WHEN it holds installed dependencies (not
  first-party vendored source). The engine reuses `is_vendored_path` /
  `VENDORED_DIR_NAMES` from `cpv_validation_common` to decide.

## Category 3 — DEV_ONLY

Present for development, not needed to run the installed plugin.

**Remediation:** strip into a submodule via `cpv.strip.extract[]` (the
engine's DEFAULT already extracts `tests/`), OR gitignore if the folder is
regenerable. `.github/` workflows are repo-needed but not install-needed —
they are flagged INFO-priority only, never recommended for removal.

**Signals:**

- `tests/`, `test/` (the strip-dev DEFAULT extract target).
- `design/`, `docs/` (dev docs — note a root `CLAUDE.md` is NOT loaded by
  Claude Code, but is tiny and not flagged as mass).
- `examples/`, `samples/`, `benchmarks/`, `bench/`.
- `fixtures/` when large.
- `.github/` workflows — LOW priority, INFO only (repo needs them; the
  install does not).

## Category 4 — BUILD_CACHE

Regenerable build output and caches that must be **gitignored** — never
tracked, never shipped. A build cache that is currently TRACKED ships to
every user and is INVALID.

**Remediation:** emit the `.gitignore` lines (the engine lists them in
`recommendations.gitignore_add`). If a cache is currently TRACKED (its
finding carries `"tracked": true`), surface it as a BUILD_CACHE-invalid
condition that must be `git rm --cached`'d and gitignored — this ties into
CPV's gitignore-enforcement work. A gitignored + untracked cache is already
not-shipped; the engine does not double-flag it as shippable mass.

**Per-toolchain signals:**

- **Rust** — `target/`, `.cargo/`.
- **JS** — `dist/` (when regenerable), `.next/`, `.turbo/`,
  `.parcel-cache/`.
- **Python** — `__pycache__/`, `*.pyc`, `.tox/`, `.pytest_cache/`,
  `*.egg-info/`, `.mypy_cache/`, `.ruff_cache/`.
- **C / C++** — `build/`, `*.o`, `*.obj`, `*.a`, `*.so` (the non-`bin/`
  ones).
- **JVM** — `target/`, `build/`, `.gradle/`, `*.class`.
- **Swift** — `.build/`, `DerivedData/`.
- **General** — `*.log`, coverage output, `.DS_Store`.

## FN-safety rules every recommendation obeys

These are hard rules — the diagnostic must never produce a destructive or
wrong recommendation:

1. NEVER recommend stripping / removing a RUNTIME-ESSENTIAL path (see
   `anthropic-plugin-components.md`), anything in `_RESERVED_SRCS`, or
   anything referenced via `${CLAUDE_PLUGIN_ROOT}`. When in doubt →
   ship-always.
2. `bin/` (compiled binaries) is ALWAYS runtime — even though the source
   that builds it is `BUILD_SOURCE`.
3. A `scripts/foo.py` referenced by a hook / skill is runtime; a
   `scripts/build_*.py` referenced by nothing at runtime is a `DEV_ONLY`
   strip candidate. Decide by REFERENCE, not by name alone.
4. Git-accurate: a gitignored + untracked file is already not-shipped — do
   not flag it as shippable mass. A TRACKED build cache DOES ship, so
   surface it as `BUILD_CACHE` invalid.
5. The diagnostic is read-only / advisory. It NEVER writes to the target
   plugin. Every remediation hands the user a SEPARATE command or config
   edit to run themselves.

## Category to remediation quick map

| Category | Recommend | Machinery |
|---|---|---|
| `BUILD_SOURCE` | Strip into a git submodule | `cpv.strip.extract[]` + `cpv strip-dev-parts` |
| `RUNTIME_DEP` | Install on first use; gitignore the shipped copy | `${CLAUDE_PLUGIN_DATA}` `SessionStart` hook + `.gitignore` |
| `DEV_ONLY` | Strip into a submodule, or gitignore if regenerable (`.github/` = INFO only) | `cpv.strip.extract[]` (default already does `tests/`) + `.gitignore` |
| `BUILD_CACHE` | Gitignore; if tracked, `git rm --cached` then gitignore | `.gitignore` |
| `RUNTIME_ESSENTIAL` | Leave it alone | n/a — ship-always |
| `UNKNOWN` | Investigate manually before acting | n/a — never auto-recommended for removal |
