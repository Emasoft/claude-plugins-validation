---
trdd-id: VWSEG7D7
title: CPVPPC P2 - Config schema, lock, init, pin
column: backburner
status: tasked
created: 2026-09-24T20:00:35+0200
updated: 2026-09-24T20:03:59+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:00:35+0200
---

# CPVPPC P2 - Config schema, lock, init, pin

Files: config_schema.json, init.py, pin.py, lock read/write in verify.py. Key tests: anti-bypass property names; closed enums; floors; unknown key; out-of-scope features (containers, npm/PyPI/Homebrew) rejected; support-window computation; init on PSS-, janitor-, llm-ext-, plain-Python-shaped fixtures reproduces section 3.3; pin refuses an unreachable SHA. Done-when: fixtures validate. Plan: design/specs/cpvppc-plan.md section 6, row P2. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:00:35+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.

## Plan excerpt: section 3.1 (verbatim)

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

## Plan excerpt: section 3.3 (verbatim)

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
