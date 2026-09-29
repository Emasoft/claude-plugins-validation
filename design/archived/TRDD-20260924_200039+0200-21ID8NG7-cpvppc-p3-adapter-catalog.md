---
trdd-id: 21ID8NG7
title: CPVPPC P3 - Adapter catalog
column: complete
status: archived
created: 2026-09-24T20:00:39+0200
updated: 2026-09-29T13:52:07+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:00:39+0200
implementation-commits: [36627f1f]
---

# CPVPPC P3 - Adapter catalog

Files: scripts/cpvppc/adapters/{python_uv,node_ts,rust_cargo,c_cpp,go,shell}.py. Key tests: exact argv per command and option; config-file args overridden; pytest collected-vs-discovered check on a fixture with addopts=-k nothing. Done-when: argv snapshots match section 3.2. Plan: design/specs/cpvppc-plan.md section 6, row P3. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:00:39+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- 2026-09-29T02:53:45+0200 — column → dev by main-agent@claude-plugins-validation. P2 landed; P3 is next per plan sequencing
- 2026-09-29T13:52:07+0200 — COMPLETE by main-agent@claude-plugins-validation. P3 verified centrally: 141 tests, ruff/mypy clean, bare-CLI smoke OK, argv snapshots match plan §3.2.

## Plan excerpt: section 3.2 (verbatim)

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

## Problem

Review R12 findings carried forward: (a) no test pins that every cpvppc entry module (init/pin/verify + P3 adapters) stays bare-CLI importable — the P2 ModuleNotFoundError was masked by the suite and found only by ad-hoc smoke; add a parametrized import test. (b) The sidecar hooks-path gate: does it resolve symlinks (an in-plugin symlink pointing outside would read/lint an out-of-tree file — the v5.16.2 resolved-path lesson)? Verify, and fix if unresolved. (c) Record the interpreter-form relative-token MINOR as a retro-break in the release notes (new finding surface). (d) Q1 doc cites recorded at the change site (hooks.md:416/:601) — done.

## Acceptance checklist

- [x] 6 adapter modules + registry exist, exactly the plan §3.2 names
- [x] argv snapshots match §3.2 exactly (141-test two-sided suite, literal lists not substrings)
- [x] config-override rule tested (adapter_options cannot swap lint/typecheck/test argv)
- [x] pytest collected>=discovered gate on addopts=-k nothing fixture (hermetic probe)
- [x] schema adapter enum closed to exactly the 6 names, two-sided
- [x] bare-CLI importable + smoke __main__ on every adapter (P2 lesson)
- [x] centrally verified: 141 pass / ruff clean / mypy clean (repo config) / smoke exit 0
- [x] implementation-commits recorded: 36627f1f
