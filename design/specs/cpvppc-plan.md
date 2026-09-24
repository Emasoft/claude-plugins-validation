# Plan — CPVPPC: a flexible, deterministic, bypass-proof publishing canon

## 0. Context

CPV scaffolds and publishes plugins and marketplaces through its publishing pipeline canon (CPVPPC).
Today the canon is only the combined behaviour of generators (`scripts/generate_plugin_repo.py`,
34 `gen_*` emitters; `scripts/generate_marketplace_repo.py`, 12; `scripts/setup_marketplace_automation.py`),
templates, skill docs and the 87-item prose checklist `references/canonical-pipeline-migration-checklist.md`.
Nothing can say whether a repo implements the canon, which version, or whether its pipeline is intact,
which is why the v5.18.0 README-table canon never reached either marketplace (TRDD-8W80DZHI, 14 defects).

User requirements (verbatim on TRDD-DFRPRZYD): deterministic "does this plugin / plugin repo /
marketplace repo implement canon version X"; spec entries that are testable assertions; agents that
detect, deploy, upgrade and repair; auto-generated plugin and marketplace README sections; the canon
has its own version; a lock file; ONE simple editable config file adapting to any project (languages,
mixed compilers, platforms, binaries-only shipping, dependencies, vendored tools, installers, git root
in a subfolder, local vs CI linting, extra linters/docgen); nothing in it may bypass or lower a
validation, security or privacy gate; no exceptions; maximum strictness; publishing and marketplace
creation must work flawlessly.

## 1. What real plugins need (studies of 2026-09-24, read-only)

"ppt plugin" was taken to mean perfect-skill-suggester (PSS), the plugin with the ~40 GB Rust build
tree that ships only binaries.

| # | Project | How it works today | Gaps the canon must close |
|---|---|---|---|
| 1 | PSS | Rust workspace (2 crates) in submodule `rust/`; built LOCALLY with cargo/cross/zigbuild for 5 targets; 10 binaries committed to `bin/` + release assets; `bin/manifest.json` sha256; `sh` dispatcher maps `uname` to a binary | no toolchain pin; no `cargo test/clippy` gate; `build.rs` downloads a model at compile time; submodule source ships (Claude Code recurses submodules); dispatcher fails SILENTLY on a missing binary; wrong-arch lookup on Intel Mac; unused fetcher; CI job commits binaries back and swallows push errors; pre-push gate skipped for `bin/`/`.github/`/submodule-only pushes, `--no-verify`, process-tree regex, stamp file; CPV from unpinned `main`; NIT not blocking; `cpv.canon: none` + `intentional_divergence`; dev folders ship |
| 2 | janitor memgrep | Rust (+ C via rusqlite `cc`) built in CI on 4 native runners; assets + per-file `.sha256` + `SHA256SUMS`; per-binary provenance; `permissions: {}`; SHA-pinned actions | no Rust pin; three release creators race; only linux-x64 built on push (tag-only matrix); `SHA256SUMS` not attested; users must `cargo install`; hook runner exits 0 on a missing script |
| 3 | tldr-code fork | 5 targets incl. Windows; ~27 C grammars via `cc`; archives + sha256; installer maps `uname` → triple, verifies sha | no CI on push; checksum from the same origin; `contents: write` workflow-wide; `\|\| echo` hides release errors |
| 4 | SKIA player (C++) | clang++ Makefile; Skia submodule via gn/ninja, cached; tag matrix; `SHA256SUMS` re-checked; Homebrew formula | retired runner label; unpinned action; no attestation |
| 5 | llm-externalizer | TS bundled by esbuild; `tsc --noEmit` + eslint `--max-warnings 0`; `dist/` committed; native addon `better-sqlite3` installed on first use outside `CLAUDE_PLUGIN_DATA`; PATH shim in `~/.local/bin` | committed `dist/` never diffed against a rebuild; install scripts forced on; no reinstall on Node ABI change; writes into the plugin cache |
| 6 | jgrep | Bun-built, Node-run; npm publish via OIDC with provenance | no type checking; unpinned `npm@latest` |
| 7 | dev-browser | `npm install` with caret ranges at every start; downloads Chromium; writes to the plugin cache | unpinned install-time downloads; state lost on update |
| 8 | visual-comunicator | 10 MB committed minified bundles from vendored sources | vendor pinned by date, not commit; bundles never rebuilt/compared |
| 9 | token-reporter, menu-system | Python hooks | unpinned `uv --with tiktoken` at every hook; unquoted `${CLAUDE_PLUGIN_ROOT}` |

## 2. Design principles

1. **Two layers.** The author configures the SHAPE of the project in `cpvppc.yaml`. Nobody configures
   the TRUST CORE: what is scanned, how strictly, by which CPV, in which order, whether it gates the
   release. The trust core reads nothing from the repo except the config's shape fields.
2. **Scan what ships.** Gates run on the assembled release artifact, never on a file list the repo
   can influence.
3. **One writer per repo, and it is CI.** Binaries are CI-built, or locally built and signed with a
   key declared in the config; either way only CI writes them into a release. A plugin repo's release (version bump, built binaries,
   README sections, self-hosted `marketplace.json` for Layout C, tags, GitHub release, marketplace
   notifications) is produced by the canon release workflow only; a marketplace repo's updates by the
   canon update workflow only. Humans push source; they never push tags or releases. The one allowed human write is the
   `staging-v<version>` draft that carries signed `local_release` binaries (no tag, collaborators
   only, consumed and deleted by the release workflow). The GitHub
   Actions app is the only bypass actor on the default branch and tags (user decision); because
   GitHub cannot restrict that bypass to one workflow, G-CONFIG proves that no other workflow has
   `contents: write` and every workflow file is hash-locked.
4. **Trust is anchored on the artifact.** Every release carries build provenance from the canon release
   workflow and an attested scan manifest (sha256 of everything scanned). Rulesets and hooks are
   defense in depth, re-checked by verify, never the anchor. Private repos without Enterprise
   attestations: every other check applies and the verdict reads `COMPLIANT (no provenance: private
   repo, self-attested scan manifest)`, with the scan manifest's sha256 recorded in the release commit
   (user decision). A Layout C repo (`plugin+marketplace`) lists only itself, so its release workflow is
   its only writer. GitLab or
   repos with no CI → `UNKNOWN`.
5. **Every rule is a checkable fact**; "could not check" is `UNKNOWN` and never passes.
6. **Variation is added to the canon, never excepted from it.** A shape the config cannot express is
   a canon change request; there is no exception list, divergence marker or opt-out.
7. **Simple by default.** `cpvppc init` infers the shape and writes a commented config; a plain plugin's
   config is ~6 lines; everything inferred is written explicitly into the lock.
8. **Scope.** CPVPPC rules bind repos that carry a CPVPPC lock. CPV's universal validator behaviour for
   everyone else is unchanged (#170: no retro-break).

## 3. The config file `cpvppc.yaml` (author-editable)

Validated against `scripts/cpvppc/config_schema.json`: `additionalProperties: false` at every level,
closed enums, numeric floors, `schema_version`. Unknown key → invalid config → every gate fails.

### 3.1 Sections and fields

| # | Section | Fields (types, defaults) | Rules |
|---|---|---|---|
| 1 | `canon` | `version` (semver) | inside the support window |
| 2 | `repo` | `kind` (`plugin` \| `marketplace` \| `plugin+marketplace`), `plugin_root` (path inside the git root, default `.`; the release artifact is this subtree plus its declared units), `host` (`github`) | one plugin per config; a monorepo has one config per plugin root |
| 3 | `preflight` | `local` (`full` \| `minimal`, default `full`) | only affects the local advisory preflight; CI always runs every gate; formatters always run in check mode, never auto-fix |
| 4 | `units[]` | `name`, `path`, `adapter` (catalog), `adapter_options` (typed per adapter), `source` (`in-tree` \| `external-repo: {url, ref: <full sha>}`) | every shipped executable-type file belongs to a unit; JS/TS hooks and scripts must be in a `node-ts` unit, so they are type-checked |
| 5 | `units[].toolchain` | `version` (required), `system_packages` per target (catalog names) | versions pinned; the adapter writes/verifies the pin file (`rust-toolchain.toml`, `.nvmrc`, `.python-version`) |
| 6 | `units[].build` | `kind` (`none` \| `bundle` \| `compiled`), `outputs[]` (`{name, kind: bin \| cdylib \| node-addon \| wheel \| wasm \| bundle}`), `targets[]` (`{os, arch, triple?, runner, method: native \| cross \| zigbuild \| docker, c_compiler?: clang \| gcc \| msvc \| zig, node_abi?: [majors]}`), `network_hosts[]` (default none), `local_release` (`{signing_key: <public key id>}` or absent) | CI builds: dependencies are fetched first, then the build runs offline (`cargo fetch --locked` → `cargo build --offline`; `npm ci` → offline bundle), the offline build is the guarantee on every OS, and a SHA-pinned egress blocker (`step-security/harden-runner`, block mode) is added as defense in depth on the runners it supports (P6 verifies which); files fetched during a build must match a declared `external_artifacts` hash. `node-addon` outputs are either N-API (one build per os×arch) or carry a `node_abi` axis. `local_release` (user decision): a binary built on the author's machine may ship if hash-pinned and signed with the declared key; `publish.py` uploads it to a separate DRAFT release `staging-v<version>` (drafts create no tag and are visible only to repo collaborators; the only human write the canon allows; never the real release), which must hold exactly one asset plus its `.sig` per `local_release` target and nothing else. Drafts are visible only to tokens with write access, so the release workflow's single write job fetches the draft assets first and hands them to the read-only build/verify jobs as workflow artifacts; G-BUILD verifies signature and hash as that target's build step; G-ASSEMBLE includes it; the release workflow deletes the draft after publishing. At the start of the write job a draft for the same version is reused only if its assets verify, and a leftover draft for another version fails the release with the exact cleanup command. On ordinary push/PR runs these targets are reported "verified at release time" (neither missing nor passed); `verify --remote` reports a stale draft; a change of `signing_key` is reported in verify and in the release notes, never silent, it is still binary-scanned and its source scanned at the recorded commit, and every verdict and README shows "locally built: N" |
| 7 | `units[].deps` | `lockfile` (required when anything is installed), `strategy` (`bundled` \| `install-on-first-use` \| `system-prerequisite`) | first-use installs go into `${CLAUDE_PLUGIN_DATA}` only, from the lockfile, hash-verified, with install scripts disabled; native addons are never compiled on the user's machine: they are `node-addon` outputs prebuilt per target in CI and shipped or fetched hash-pinned |
| 8 | `artifacts` | `naming` (template of `{name} {os} {arch} {triple} {ext} {version}`), `archive` per OS (`raw` \| `tar.gz` \| `zip`), `delivery` (`committed-bin` \| `release-asset-fetch` \| `both`), `manifest_path` (default `bin/manifest.json`), `signing` (`{macos: {identity_secret, notarize: bool}, windows: {cert_secret}}` or absent) | `committed-bin`: the release workflow commits the built binaries into the release commit; `release-asset-fetch`: fetched at first use into `${CLAUDE_PLUGIN_DATA}/bin/<sha16>/`, verified against the git-tracked manifest, fails closed with a clear message when offline; for a private repo the fetcher uses `GITHUB_TOKEN` or, if absent, `gh auth token`, and fails closed without either. Signing is optional (user decision): unsigned binaries are allowed; for `release-asset-fetch` the fetcher clears the macOS quarantine flag only after the hash matches (`committed-bin` files arrive through git and are never quarantined); the README says "unsigned" |
| 9 | `runtime` | `launchers[]` (`{name, output}`), `arch_aliases[]` (catalog, e.g. `windows-arm64-to-x86_64`), `path_shim` (`{dir: ~/.local/bin}` or absent), `network_hosts[]` (hosts the plugin contacts at runtime, default none) | launcher lookup order and failure behaviour are canon; the PATH shim is idempotent, never overwrites a file it did not create, removable; declared hosts are shown in the README section |
| 10 | `vendor[]` | `name`, `upstream`, `commit` (full SHA), `license_file`, `build` (argv), `output`, `output_sha256`, `reproducible` (`bool`) | reproducible → rebuilt and compared in CI; not reproducible → hash-pinned, reported "unattested" |
| 11 | `external_artifacts[]` | `name`, `source`, `version`, `sha256` (`{<os>-<arch>: …}` or `{any: …}` for a platform-independent file), `provenance` (`{signer}` \| `none`) | `none` → "unattested", visible in README and every verify verdict |
| 12 | `checks` | extra catalog checks (`markdownlint`, `shellcheck`, `cspell`, `docgen: {tool}` …) and `custom[]` (`{name, stage: pre-lint \| post-test \| docgen, argv[], network_hosts[]}`) | can only add failures; run before assembly |
| 13 | `quality` | `coverage_min`, `test_platforms[]`, `max_test_minutes` | may only be raised above canon floors |
| 14 | `hooks` | per hook script: `interpreter` (`python-uv` \| `node` \| `bun` \| `sh`) | deps from the unit lockfile only; `${CLAUDE_PLUGIN_ROOT}` quoted; network only to `runtime.network_hosts` |
| 15 | `targets.marketplaces[]` | `{owner, repo, entry_name, visibility: public (default) \| private}`; may be empty | one notification and one remote check per target |
| 16 | `release` | `changelog` (`git-cliff`), `prerelease_channel` (`bool`) | tags are canon: `v{v}` and `{name}--v{v}` |
| 17 | `docs` | README sections (`install`, `platforms`, `components`, `badges`, `network`, `unattested`) | |
| 18 | `writes_outside_data[]` | runtime writes outside `${CLAUDE_PLUGIN_DATA}` (e.g. the PATH shim target) | undeclared writes fail the static scan; each is listed in the README section |

Out of scope for canon 1.0 (rejected by the schema, not silently ignored): container images, and
distribution through npm, PyPI or Homebrew. Every release ships a CycloneDX SBOM.

### 3.2 Adapter catalog (CPV code; commands run by CI with CPV-controlled arguments)

| # | Adapter | lint / typecheck | test | build | install |
|---|---|---|---|---|---|
| 1 | `python-uv` | `ruff check`, `mypy` | `pytest -o addopts="" -p no:cacheprovider --junitxml=…` plus a closed list of allowed `adapter_options` (`import_mode`, `plugins[]`); collected ≥ discovered test files | `uv build` when a wheel output is declared | `uv sync --locked` |
| 2 | `node-ts` | `tsc --noEmit`, `eslint --max-warnings 0` | `vitest run` \| `node --test` \| `bun test` | `esbuild` \| `bun build` \| `vite build` \| `tsdown` with an entry→output map and externals; `node-addon` outputs via `prebuildify`/napi-rs per target | `npm ci --ignore-scripts` \| `pnpm install --frozen-lockfile --ignore-scripts` \| `bun install --frozen-lockfile --ignore-scripts` |
| 3 | `rust-cargo` | `cargo fmt --check`, `cargo clippy --all-targets --locked -- -D warnings` | `cargo test --locked` | `cargo build --release --locked --target T` (native \| `cross` \| `cargo zigbuild`); musl targets need `musl-tools` or zigbuild; `-Werror` is never forced on C built by `cc` | `--locked` |
| 4 | `c-cpp` | compiler warnings as errors, `clang-tidy` (option) | `ctest` \| `make test` (option) | `cmake --build` \| `make` \| `meson compile` \| `gn gen && ninja` (option) | submodules pinned by SHA |
| 5 | `go` | `go vet`, `staticcheck` | `go test ./...` | `GOOS/GOARCH go build -mod=readonly` | `go mod verify` |
| 6 | `shell` | `shellcheck`, `shfmt -d` | `bats` (option) | — | — |
| 7 | `dotnet`, `swift`, `zig`, `java-gradle` | as today's `publish.py` G2e gates | idem | idem | idem |

### 3.3 Worked examples (canon 1.0.0 end state)

Plain Python plugin (written by `cpvppc init`):

```yaml
canon: {version: "1.0.0"}
repo: {kind: plugin}
units:
  - {name: scripts, path: scripts, adapter: python-uv, toolchain: {version: "3.12"}}
targets:
  marketplaces: [{owner: Emasoft, repo: emasoft-plugins, entry_name: my-plugin}]
```

PSS (Rust in a separate repo, 5 targets, binaries only, fetched at first use):

```yaml
canon: {version: "1.0.0"}
repo: {kind: plugin}
units:
  - name: engine
    path: rust                     # local clone for development, gitignored; never ships
    source: {external-repo: {url: "https://github.com/Emasoft/pss-rust-engine", ref: "<full sha>"}}
    adapter: rust-cargo
    toolchain: {version: "1.82.0"}
    build:
      kind: compiled
      outputs: [{name: pss, kind: bin}, {name: pss-nlp, kind: bin}]
      targets:
        - {os: darwin, arch: arm64, runner: macos-14, method: native, c_compiler: clang}
        - {os: darwin, arch: x86_64, runner: macos-15-intel, method: native, c_compiler: clang}
        - {os: linux, arch: x86_64, triple: x86_64-unknown-linux-musl, runner: ubuntu-24.04, method: zigbuild, c_compiler: zig}
        - {os: linux, arch: arm64, triple: aarch64-unknown-linux-musl, runner: ubuntu-24.04-arm, method: zigbuild, c_compiler: zig}
        - {os: windows, arch: x86_64, runner: windows-2022, method: native, c_compiler: msvc}
    deps: {lockfile: rust/Cargo.lock, strategy: bundled}
  - {name: scripts, path: scripts, adapter: python-uv, toolchain: {version: "3.12"}}
artifacts: {naming: "{name}-{os}-{arch}{ext}", delivery: release-asset-fetch}
runtime:
  launchers: [{name: pss, output: pss}, {name: pss-nlp, output: pss-nlp}]
  arch_aliases: [windows-arm64-to-x86_64]
external_artifacts:
  - {name: nlprule-en-model, source: "https://…", version: "0.6.4", sha256: {any: "<sha>"}, provenance: none}
targets:
  marketplaces: [{owner: Emasoft, repo: emasoft-plugins, entry_name: perfect-skill-suggester}]
```

The build tree never enters the plugin: CI clones the engine at the pinned SHA inside each build job
(a submodule would ship its source, because Claude Code recurses submodules); `target/` exists only on
the runner. The model `build.rs` downloads becomes a declared external artifact: the build job
pre-fetches and hash-checks it and passes its path to `build.rs` (one-time engine change), with the
build network otherwise denied. Engine development: work in the gitignored local clone, push the
engine repo, then `cpvppc pin engine` updates `ref` to the new SHA (refusing a SHA not reachable on the
engine remote).

TypeScript CLI with a native addon (llm-externalizer-style):

```yaml
canon: {version: "1.0.0"}
repo: {kind: plugin}
units:
  - name: cli
    path: scripts/llm-ext
    adapter: node-ts
    adapter_options: {bundler: esbuild, entries: {"src/cli/main.ts": "dist/llm-ext.js"}, externals: [better-sqlite3], test: vitest}
    toolchain: {version: "20.18"}
    build:
      kind: bundle
      outputs: [{name: llm-ext, kind: bundle}, {name: better-sqlite3, kind: node-addon}]
      targets:                       # better-sqlite3 is not N-API, so each target carries the Node ABI axis
        - {os: darwin, arch: arm64, runner: macos-14, method: native, node_abi: [20, 22]}
        - {os: linux, arch: x86_64, runner: ubuntu-24.04, method: native, node_abi: [20, 22]}
    deps: {lockfile: scripts/llm-ext/package-lock.json, strategy: bundled}
artifacts: {naming: "{name}-{os}-{arch}{ext}", delivery: release-asset-fetch}
runtime:
  launchers: [{name: llm-ext, output: llm-ext}]
  path_shim: {dir: "~/.local/bin"}
writes_outside_data: ["~/.local/bin/llm-ext"]
targets:
  marketplaces: [{owner: Emasoft, repo: emasoft-plugins, entry_name: llm-externalizer}]
```

`dist/` is produced by the release workflow (if kept committed, G-BUILD rebuilds and diffs it on every
push); the SQLite addon is built in CI per target and Node ABI (`npm rebuild better-sqlite3
--build-from-source`), fetched hash-pinned, and loaded through better-sqlite3's `nativeBinding`
option, so no install script runs on a user's machine.

## 4. The trust core (not configurable)

### 4.1 Gate sequence

CI runs gates 1-9 on every push and PR (every declared target builds as a dry run: "test parity").
The release workflow (`workflow_dispatch`, triggered by `publish.py`) first runs step 0 — version bump
in every declared version file, the project's OWN version inside lockfiles only (e.g. `cargo update -p
<self> --offline`, `uv lock` for the root version), CHANGELOG by git-cliff, README sections,
`bin/manifest.json` — in its workspace; G-CONFIG asserts the step-0 diff touches only version fields
(a dependency change fails, so `--locked` / `npm ci` / `--frozen-lockfile` keep working); then gates
1-12 run on exactly that tree, and G-RELEASE commits exactly that tree. The latest-CPV re-scan makes a
release depend on the newest rules: a retry of the same release may need fixes if CPV changed. Local `publish.py` runs 1-9 as an advisory preflight
(`preflight.local`). Because pushes made with the workflow token trigger no other workflow, the
"green run" for a release commit is the release workflow run that produced it, and marketplace
notification is a job inside the release workflow (cross-repo dispatch uses `MARKETPLACE_PAT`).

| # | Gate | What it runs | Blocks on |
|---|---|---|---|
| 1 | G-CONFIG | config schema; lock consistent with config; managed files match their render; workflows free of bypass constructs (`continue-on-error` on a gate, `if: false`, `\|\| true`, `\|\| echo`, skip inputs, repo-variable reads); only the canon writer workflow has `contents: write`; `curl \| sh` absent from every executable and canon-managed file, including vendored ones | any failure |
| 2 | G-CPV | CPV pinned by commit SHA in the lock, installed from the official repo; `validate_plugin --strict` (marketplace: `validate_marketplace --strict`) on the plugin subtree; in the release workflow also a re-scan with the latest CPV release, which must be clean for a new release (so `publish.py` users and agents hit it alike) | CRITICAL/MAJOR/MINOR/NIT |
| 3 | G-UNITS | each unit's adapter lint, typecheck, test with CPV-controlled args; `quality` floors | any failure; skips/deselection above threshold |
| 4 | G-CUSTOM | `checks.custom[]` and docgen in a sandboxed job (argv only, read-only token, no secrets, network denied except declared hosts); managed files re-hashed after | non-zero exit; any managed-file change |
| 5 | G-BUILD | every target by its method on GitHub-hosted runners, network denied except declared hosts, fetched files hash-checked; outputs named by template; committed `dist/` rebuild-and-diff; reproducible vendor rebuild-and-compare; `local_release` targets are fetched from the staging draft and signature/hash-verified in the release run, and reported "verified at release time" on push/PR runs | any failure; any missing CI-built target |
| 6 | G-ASSEMBLE | release artifact = plugin subtree + built outputs + vendor outputs + external artifacts (hash-checked) + generated README sections; submodules recursed | hash mismatch; undeclared content; executable-type file outside every unit |
| 7 | G-SECURITY | CPV security scan (skillaudit, RC rules, external scanners) on the assembled artifact AND on the sources of `external-repo` units, reading no repo ignore/config; supply-chain and runtime-download checks; SBOM generated | any blocking finding |
| 8 | G-PRIVACY | home-directory paths (`/Users/…`, `/home/…`, `C:\Users\…`), git author names/emails of the release range, hostnames, email addresses; runtime hosts ⊆ declared; generated text (README sections, release notes, scan manifest) scanned; private targets never rendered into a public README; no allowlist from the repo | any finding |
| 9 | G-SECRETS | trufflehog, all result buckets, on the release range of history and the assembled artifact | any finding |
| 10 | G-ATTEST (release) | `SHA256SUMS`, scan manifest, SBOM; build provenance for every artifact and for those three files | failure |
| 11 | G-RELEASE (release) | the workflow commits the version bump + `bin/` (if `committed-bin`) + manifest + README sections as ONE release commit whose tree equals the scanned artifact, creates both tags, creates the GitHub release, uploads assets, sends notifications; idempotent on re-run | any error |
| 12 | G-POST (release) | remote verify: tags, assets hash-match, provenance, install smoke from each target marketplace, listing matches manifest | any failure (job red) |

Writers per repo kind: plugin repo → the release workflow (the only job with `contents: write`);
marketplace repo → the update workflow. Rulesets: CPV's ratified `baseline-*` trio on the default
branch plus tag protection (`v*`, `*--v*`: no update, no deletion), with the GitHub Actions app as the
only bypass actor.

### 4.2 Canon-owned runtime behaviour

- **Launcher** `bin/<name>` + `bin/<name>.cmd`: `plugin bin/<name>-<os>-<arch>` → `${CLAUDE_PLUGIN_DATA}/bin/<sha16>/`
  (fetched on first use, verified against `bin/manifest.json`) → fail CLOSED with a clear message
  (never empty output, never exit 0); arch from the OS, aliases only from the catalog; `--version` must
  equal the plugin version.
- **Hook runner**: quoted `${CLAUDE_PLUGIN_ROOT}`; a missing script fails loudly; deps only from the lockfile.
- **First-use installer**: `${CLAUDE_PLUGIN_DATA}` only (plus declared `writes_outside_data`); lockfile;
  hash-verified; install scripts disabled; reinstall on lockfile-hash or runtime-ABI change;
  cross-process lock.

### 4.3 Threat table (every row is an assertion; a violation is `FAIL`)

| # | Trick | Control |
|---|---|---|
| 1 | Config knob that skips or weakens a gate | schema has none; a test fails if any schema property matches `skip\|allow\|ignore\|disable\|bypass\|except\|divergen\|optional`; floors on numbers |
| 2 | Repo ignore/config files hiding content (`.gitignore`, `.gitattributes export-ignore/linguist-*`, `.trufflehogignore`, `.semgrepignore`, `cpv.exclude_paths`, linter excludes, Mega-Linter `DISABLE`) | security/privacy/secret gates read none of them; they scan the assembled artifact |
| 3 | Shipped but unscanned content (outputs, bundles, assets, first-use downloads, submodules, history) | part of the assembled artifact or the release-range history scan; first-use downloads hash-pinned |
| 4 | Editing a canon-managed file | hash-locked to the render of the lock's config at the lock's canon version |
| 5 | Custom step neutering or editing the pipeline | G-CUSTOM sandbox, before assembly; re-hash after; can only add failures |
| 6 | Releasing around the pipeline (local tag, `gh release create`, `--no-verify`, another workflow with write) | only the release workflow writes, apart from the `staging-v<version>` draft of signed `local_release` binaries, which it consumes and deletes; tag rulesets; every release needs provenance from that workflow and the green release-workflow run that produced its commit |
| 7 | Weakening a step inside a workflow | workflows hash-locked; G-CONFIG rejects bypass constructs; no gate reads repo variables |
| 8 | Old or fake CPV | CPV pinned by commit SHA per canon version, installed from the official repo; vendored CPV copies fail |
| 9 | Downgrading the declared canon version | support window; a lock whose canon version decreased in history fails |
| 10 | Test tricks (`addopts`, `testpaths`, `conftest` hooks, deselection, all skipped) | adapters override config args; collected ≥ discovered; skip ratio from JUnit |
| 11 | Mutable or untrusted actions, `pull_request_target`, broad permissions | SHA pins from the canon allowlist; `pull_request_target` forbidden; `permissions: {}` default; `persist-credentials: false` |
| 12 | Marketplace update silently doing nothing | receiver fails on an unknown plugin; remote listing check |
| 13 | Building a different feature set than shipped | only G-BUILD outputs and signature-verified `local_release` binaries ship, all hashed into the scan manifest |
| 14 | Code outside every unit | G-ASSEMBLE rejects executable-type files outside units and canon-managed paths |
| 15 | Content produced after the scan | nothing runs after G-ASSEMBLE except scan, attest, release; release commit tree = scanned artifact |
| 16 | Per-platform asset swapped or different | each asset hashed, scanned, attested; immutable; verify re-checks |
| 17 | Runtime code downloading more code | supply-chain/taint scanners with no repo-controlled demotion; only the canon first-use installer and fetcher may fetch, hash-verified |
| 18 | Self-hosted or retired runners | canon jobs only on GitHub-hosted runners from a maintained label list; `runs-on` hash-locked |
| 19 | Bot or human token as a standing bypass | the GitHub Actions app is the only bypass actor; no human token; `gh_ruleset_matches` checks the bypass list; G-CONFIG proves no other workflow can write |
| 20 | Admin disabling rulesets, deleting attestations, re-tagging | trust verdict rests on provenance + scan manifest matching the tag commit; missing provenance = `NON-COMPLIANT` |
| 21 | Shrinking scope in the config | allowed, never silent: verify and release notes report "scope reduced" with the diff |
| 22 | Private data in shipped files or generated text | G-PRIVACY |
| 23 | Hidden telemetry or undeclared runtime network use | runtime hosts must be declared and shown in the README |
| 24 | Secrets in logs or artifacts | masked logs, no `set -x` near secrets, G-SECRETS |
| 25 | Unpinned toolchains, lockfile-less installs, `npm install` instead of `npm ci`, `curl \| sh`, install scripts on user machines, build-time downloads | each is an assertion that fails |

## 5. Canon definition and verifier

- `scripts/cpvppc/` (in the wheel): `canon.json` (own semver: `0.1.0` now, `1.0.0` at P15),
  `config_schema.json`, `adapters/`, `templates/<canon-version>/`, `verify.py`, `render_spec.py`,
  `init.py`, `pin.py`. `design/specs/cpvppc.md` is generated from `canon.json` (a test fails on drift).
- Assertion `{id, since, until?, scope, applies_when, fact: {type, args}, fix}`; IDs stable; scopes
  `plugin-source`, `plugin-repo`, `marketplace-source`, `marketplace-repo`, `listing`.
- Fact types (closed set, each a pure tested function): `file_present`, `file_sha_in_set`,
  `file_matches_render`, `json_path_equals`, `workflow_step`, `workflow_no_bypass`,
  `readme_section_current`, `config_valid`, `lock_consistent`, `actions_pinned`,
  `unit_covers_executables`, `artifact_privacy_clean`, `runtime_hosts_declared`,
  `generated_text_privacy_clean`; remote: `gh_ruleset_matches`, `gh_tag_exists`,
  `gh_run_success_for_commit`, `gh_secret_present`, `gh_release_assets_match_manifest`,
  `gh_provenance_verified`, `listing_matches_manifest`.
- Verdicts: `COMPLIANT <v>` (exit 0; `(static)` without `--remote`, rejected by agent gates;
  qualifiers `unattested: N`, `locally built: N`, `unsigned`, `no provenance: private repo`, always
  printed when they apply), `NON-COMPLIANT <v>` (1), `UNKNOWN` (5), `NOT-DECLARED` (6, plus the highest
  canon version whose static assertions pass).
- Support window: the current and previous minor versions of the current canon major, plus the last
  minor of the previous major for 6 months after a new major ships; outside it, `NON-COMPLIANT`.
- The verdict is for the DECLARED version, with templated values taken from the lock (so a CPV release
  that does not change the canon changes no hash). A re-scan with the current CPV is reported as a
  separate section ("findings under CPV X"); it does not change the verdict of an already-published
  release, but agent gates require it clean before a NEW release. An older supported canon version gets
  a non-blocking "canon X.Y available" WARNING.
- Never executes target code; static checks work on uninstalled checkouts.
- `validate_plugin` / `validate_marketplace`: repo with a lock → failing assertions as MAJOR; without
  a lock → one INFO line.

## 6. Implementation phases

Each phase: its own TRDD card; one `lean-worker` builds it from the card; I read every diff and re-run
every check; commits by file name; no push or release without asking. "Done" = the listed tests pass,
the full serial suite passes (exit code captured to a file), self-hash manifests regenerated last,
cache-cold strict self-validate 0/0/0/0.

| # | Phase | Files | Key tests (each positive + negative) | Done when |
|---|---|---|---|---|
| P0 | Cards | epic + one card per phase; TRDD-DFRPRZYD = P1 (approval note reworded); TRDD-8W80DZHI closed → epic | — | cards committed |
| P1 | Framework + marketplace README assertions (no emitted change) | `scripts/cpvppc/{__init__,verify,render_spec}.py`, `canon.json` 0.1.0 with `CPVPPC-MKT-001..007`, `remote_validation.py` mode `cpvppc`, `design/specs/cpvppc.md` | `test_cpvppc_framework.py` (verdicts, exit codes 0/1/5/6, UNKNOWN never 0, spec equality); `test_cpvppc_marketplace_readme.py` (real-generator scaffold passes all but MKT-007, tied to P4; one mutation per assertion; dated renderer; hand-edited table; never-execute sentinel; PyYAML `on:`; CRLF) | investigation clones give: ai-maestro main all fail (table-current n/a), PR #18 only `version`, emasoft-plugins renderer sha + table-current |
| P2 | Config schema, lock, `init`, `pin` | `config_schema.json`, `init.py`, `pin.py`, lock read/write in `verify.py` | anti-bypass property names; closed enums; floors; unknown key; out-of-scope features (containers, npm/PyPI/Homebrew) rejected; support-window computation; `init` on PSS-, janitor-, llm-ext-, plain-Python-shaped fixtures reproduces section 3.3; `pin` refuses an unreachable SHA | fixtures validate |
| P3 | Adapter catalog | `scripts/cpvppc/adapters/{python_uv,node_ts,rust_cargo,c_cpp,go,shell}.py` | exact argv per command and option; config-file args overridden; pytest collected-vs-discovered check on a fixture with `addopts=-k nothing` | argv snapshots match section 3.2 |
| P4 | Marketplace templates | `templates/0.x/marketplace/{update.yml,validate-readme-table.yml,render_readme_table.py}`; `generate_marketplace_repo.py`, `standardize_marketplace.py` write config + lock, insert missing markers, never overwrite a non-canon file without a diff | Layout A/B/C fixtures verify COMPLIANT (static); unknown plugin dispatch fails loudly; D5/D6/D7/D8 regressions | MKT-007 green on the scaffold |
| P5 | Notify job | notify as a job template included in the release workflow (not a separate workflow, since token pushes trigger none); migrator in `standardize_plugin.py` removing the old separate `notify-marketplace.yml` | one job per target, `fail-fast: false`; payload `entry_name` with plugin.json name fallback; receiver accepts both | D9 regression: repo name ≠ entry name updates the listing |
| P6 | CI template | `templates/0.x/plugin/ci.yml` | gates 1-9 as jobs; per-target build on push/PR with SHA-pinned caches; `permissions: {}`; runner-label check; custom sandbox (a custom step writing `publish.py` fails) | actionlint clean; fixture matrix green |
| P7 | Release template | `templates/0.x/plugin/release.yml` | gates 1-12; release commit tree = scanned artifact; provenance on assets + SUMS + manifest + SBOM; idempotent re-run | throwaway-repo e2e (with approval) publishes and verifies COMPLIANT |
| P8 | `publish.py` | `templates/0.x/plugin/publish.py` | local preflight = gates 1-9; triggers the release workflow via `gh workflow run` and watches it; no stamp files, no process-tree checks, no tag pushes | preflight on the fixture matrix; dry-run dispatch |
| P9 | Runtime templates | `templates/0.x/plugin/{launcher.sh,launcher.cmd,fetch.py,install_first_use.py,hook_runner.sh,path_shim.sh}` | missing binary fails closed; wrong-arch never chosen; fetch hash mismatch refused; quarantine cleared only after a hash match; private-repo fetch without a token fails closed; node addon chosen by `process.versions.modules`, unknown ABI fails closed; offline message; ABI change reinstalls; shim never overwrites a foreign file; a locally built binary with a bad signature refused | tests on macOS, Linux, Windows runners |
| P10 | README sections | plugin section renderer + `--check`; marketplace canon line | section current after release; hand edit fails; private target not rendered publicly | both READMEs regenerate |
| P11 | Privacy gate | `scripts/cpvppc/privacy.py` reusing CPV's path/username/email detectors | home paths, emails, author names, undeclared hosts, private target names all fail; clean fixture passes | fixture matrix clean |
| P12 | Strictness audit (lock repos only) | report of every escape hatch in the canon and CPV's pipeline paths; removals in canon templates; lock repos reject `cpv.canon: none`, `intentional_divergence`, skip env vars, non-failing smoke, NIT-not-blocking; timeout knobs raise-only | one mutation per hatch fails verify | the user rules on the #101 sentinel and #194 consent registry |
| P13 | Remote facts, rulesets, checklist fold | remote fact types; ruleset setup script; 87-item checklist folded into `canon.json` | tamper tests on the throwaway repo (asset swap, ruleset disabled, bypass actor added) | each tamper caught |
| P14 | Agents | `cpv-the-skills-menu` routes; `cpv-plugin-creator-agent`, `cpv-plugin-fixer-agent`, `cpv-marketplace-fixer-agent` gates (`verify --remote` COMPLIANT + current-CPV rescan clean + CI green); migrations `migrations/<from>-<to>.py`; repair flow (re-render, or stay NON-COMPLIANT) | agent contract tests; migration round-trips 0.x → 1.0 | an agent run on a fixture reaches COMPLIANT unattended |
| P15 | Canon 1.0.0 + rollout | CPV release; then via `cpv-agent`, each push approved: emasoft-plugins, ai-maestro-plugins, PSS, janitor, llm-externalizer, the rest | verify `--remote` COMPLIANT per repo | every listed repo COMPLIANT |

## 7. Verification strategy

1. Every fact type and assertion: a positive and a negative test.
2. A CPV-generated fixture matrix (plain Python; PSS-shaped Rust external-repo with 5 targets; Rust + C
   via `cc`; TS bundle + prebuilt node addon; C++ CMake; marketplaces Layout A/B/C) verifies
   `COMPLIANT (static)`; one mutation per threat-table row turns it `NON-COMPLIANT` on exactly that
   assertion.
3. Remote end-to-end on a throwaway GitHub repo, created only with the user's approval (P7, P13).
4. Per phase: `ruff` + `mypy`; full serial suite with the exit code captured to a file; hashes last;
   cache-cold strict self-validate 0/0/0/0.
