# Marketplace Error-to-Fix Index

## Table of Contents

- [1. validate_marketplace.py](#1-validate_marketplacepy)
- [2. validate_marketplace_pipeline.py](#2-validate_marketplace_pipelinepy)
- [3. Architecture / Layout Migration Warnings (7 signals)](#3-architecture--layout-migration-warnings-7-signals)

## Checklist

- [ ] Identify the validator that produced the finding
- [ ] Distinguish MECHANICAL findings (route to `fix-marketplace-validation`) from ARCHITECTURE signals (route to `migrate-marketplace-architecture`)
- [ ] Jump to the matching section below
- [ ] Apply the fix from the referenced guide
- [ ] Re-validate — marketplace findings often cascade, so loop until clean

---

Maps each **marketplace-scope** CPV validator to its fix reference guide with section numbers. This index covers the 2 validators that operate on a marketplace repository. For plugin-level validators see [plugin-error-index.md](plugin-error-index.md).

Entries tagged `[NEW]` were added in recent releases (v2.11.x / v2.12.x) and correspond to items tracked in `docs_dev/validator_error_inventory_20260412.md`.

---

## 1. validate_marketplace.py

Primary fix guide: [marketplace-fixes.md](marketplace-fixes.md)

| Error topic | Fix guide section |
|---|---|
| marketplace.json not found / JSON parse error / not an object | marketplace-fixes §1 |
| Marketplace `name` (type, length, pattern, reserved, trailing hyphen) | marketplace-fixes §1 |
| Marketplace `owner` (object with `name`, optional `email`) | marketplace-fixes §1 |
| Marketplace `metadata` (description, version, pluginRoot) | marketplace-fixes §1 |
| Marketplace impersonation of official Anthropic marketplaces | marketplace-fixes §1 |
| `plugins` field (must be array, non-empty recommended) | marketplace-fixes §2 |
| Plugin entry required fields (`name`, `source`) | marketplace-fixes §2 |
| Plugin `name` (pattern, length, trailing hyphen) | marketplace-fixes §2 |
| Plugin `version` (semver) | marketplace-fixes §2 |
| Plugin `tags`, `keywords`, `dependencies`, `author`, `strict` | marketplace-fixes §2 |
| Duplicate plugin names | marketplace-fixes §2 |
| Unknown plugin fields (INFO) | marketplace-fixes §2 |
| Plugin source type `github` (requires `repo: owner/name`) | marketplace-fixes §3 |
| Plugin source type `url` (requires `url` string) | marketplace-fixes §3 |
| Plugin source type `npm` (requires `package`) | marketplace-fixes §3 |
| Plugin source type `git-subdir` (requires `url` + `path`, v2.1.69+) | marketplace-fixes §3 |
| Plugin source type `directory` (Layout B: requires `path`) **[NEW]** | marketplace-fixes §3 |
| Source: `..` path traversal BLOCKED | marketplace-fixes §3 |
| Source: `sha` 40-char hex, `ref` string | marketplace-fixes §3 |
| Absolute path in `path` BLOCKED (CRITICAL) | marketplace-fixes §3 |
| Plugin uses remote source but exists as local submodule | marketplace-fixes §3 |
| Plugin local-path does not exist / not a directory / missing plugin.json | marketplace-fixes §2 (local path subsections) |
| Nested plugin recursive validation **[NEW]** (could not validate nested plugin recursively — WARNING) | marketplace-fixes §2 |
| `.gitmodules` parsing and submodule-URL drift | marketplace-fixes §4 |
| Submodule initialization (`git submodule update --init --recursive`) | marketplace-fixes §4 |
| Marketplace README.md existence and required sections | marketplace-fixes §1 (README subsection) |
| Marketplace placeholder content / Troubleshooting topics | marketplace-fixes §1 (README subsection) |
| Marketplace private-info scan (leaked paths) **[NEW]** (L1554–L1692) | marketplace-fixes §7 |
| Dangerous inline Python in workflows **[NEW]** (L1866–L1884) | marketplace-fixes §5 |
| GitHub-source validation — plugin `repository` field required **[NEW]** (L1732–L1790) | marketplace-fixes §2 |
| Architecture / nested-monorepo restructure 7-signal WARNING **[NEW]** (L2299) | See §3 below |

---

## 2. validate_marketplace_pipeline.py

Primary fix guide: [marketplace-fixes.md](marketplace-fixes.md)

| Error topic | Fix guide section |
|---|---|
| `.github/workflows/` directory not found | marketplace-fixes §5.1 |
| `validate.yml` workflow missing | marketplace-fixes §5 |
| `update-submodules.yml` workflow missing | marketplace-fixes §5 |
| `repository_dispatch` trigger check | marketplace-fixes §5 |
| `workflow_dispatch` trigger check | marketplace-fixes §5 |
| Notify-marketplace workflow push trigger | marketplace-fixes §5 |
| Sync-script execution step | marketplace-fixes §5 |
| `scripts/` directory not found | marketplace-fixes §5.13 |
| `scripts/sync_marketplace_versions.py` missing / not executable / invalid Python | marketplace-fixes §5 |
| Plugins missing `.github/workflows/` directory | marketplace-fixes §5.9 |
| Plugin submodule README presence | marketplace-fixes §8 |
| Plugin submodule README installation instructions | marketplace-fixes §8.9 |
| Plugin submodule README architecture diagram | marketplace-fixes §8 |
| Cascade failures (parent failure → dependent checks become MAJOR/MINOR) | marketplace-fixes Appendix |

---

## 3. Architecture / Layout Migration Warnings (7 signals)

Primary fix guide: [marketplace-fixes.md](marketplace-fixes.md) **§9 — Architecture / Marketplace Layout Migration**

When a Layout-B marketplace (nested plugins via `source: directory` or `source: "./path"`) has at least 3 of the following 7 non-CPV signals, `validate_marketplace.py` emits a single WARNING at line 2299 with `category: architecture`. Each signal has its own per-warning mechanical fix in marketplace-fixes §9.

| # | Signal | Fix guide section |
|---|---|---|
| 1 | No git tags | marketplace-fixes §9.1 |
| 2 | No `CHANGELOG.md` at repo root | marketplace-fixes §9.2 |
| 3 | No `cliff.toml` (git-cliff configuration) | marketplace-fixes §9.3 |
| 4 | No `.github/workflows/` (no automated validation) | marketplace-fixes §9.4 |
| 5 | No `scripts/publish.py` for atomic tagged releases | marketplace-fixes §9.5 |
| 6 | Mixed authorship across plugin entries (>1 distinct author) | marketplace-fixes §9.6 |
| 7 | Version drift (>3 distinct major.minor versions across plugins) | marketplace-fixes §9.7 |

Each fix guide section tells you:
- Why CPV flags the signal
- What the non-CPV pattern looks like
- How to fix the signal **mechanically** if you do NOT want a full migration

For a full interactive Layout A (hub-and-spoke: git subtree split each plugin to its own repo) or Layout B (nested with CPV discipline) migration, use the `migrate-marketplace-architecture` skill invoked by the `marketplace-fixer` agent. The per-warning mechanical fixes in marketplace-fixes §9 handle the simpler case where you just want to bring an existing layout into compliance without moving repositories around.

Trigger condition recap (from `validate_marketplace.py::_recommend_cpv_restructure`):

1. Marketplace has ≥2 plugins using `source: "./path"` string form OR `{"source": {"source": "directory", "path": "./path"}}` object form.
2. At least 3 of the 7 signals above are detected.
3. If both conditions are met, a single `WARNING` result is appended with `category="architecture"` and a multi-paragraph message listing each detected signal, its cost, and CPV's recommended approach.

The message text itself points users at:
- `skills/create-plugin/references/marketplace-layouts.md` for manual migration
- The `marketplace-fixer` agent with `/cpv-fix-marketplace-validation <report.json>` for automated conversion
