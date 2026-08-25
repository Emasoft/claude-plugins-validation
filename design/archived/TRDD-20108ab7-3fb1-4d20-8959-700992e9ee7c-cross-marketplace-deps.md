---
trdd-id: 20108ab7
title: TRDD-20108ab7 — Cross-marketplace dependency allowlist
column: complete
updated: 2026-08-25T17:25:05+0200
---

# TRDD-20108ab7 — Cross-marketplace dependency allowlist

**TRDD ID:** `20108ab7-3fb1-4d20-8959-700992e9ee7c`
**Filename:** `design/tasks/TRDD-20108ab7-3fb1-4d20-8959-700992e9ee7c-cross-marketplace-deps.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Done — 2026-05-10. Spec field name confirmed (`allowCrossMarketplaceDependenciesOn`, plugin-dependencies.md:54-79); validator wired in v2.22.3 with library-level enforcement; on-disk auto-discovery (Layout C / Layout B / cache layout) added 2026-05-10 so `validate_plugin <path>` enforces the allowlist without explicit context; CLI `--marketplace-context PATH` flag added for CI / out-of-tree validation; documentation updated in README.md, commands/cpv-validate-plugin.md, skills/fix-validation/references/{plugin-error-index.md, plugin-structure-fixes.md §17}; 15/15 regression tests pass (5 from v2.22.3 + 10 added 2026-05-10).
**Deferred from:** TRDD-479cde0c §v2.22.1 "NEXT-RELEASE"
**Parent audit report:** `docs_dev/spec-audit-2-plugins-20260417-163141.md` §5 item 4

## Problem

plugin-dependencies.md (v2.1.110+) defines a new top-level `dependencies` field on
`plugin.json`. Each entry is `"<name>"` or `{name, version?, marketplace?}`.

The `marketplace` sub-field lets a plugin pull a dependency from a *different*
marketplace than the one that hosts the declaring plugin. Per the spec:

> Cross-marketplace deps are **blocked unless the target marketplace is
> allowlisted in the root marketplace's `marketplace.json`** (new allowlist
> mechanism).

The spec does not explicitly name the allowlist key yet (as of 2026-04-17).
The research agent flagged this in the audit:

> "implies a new field in marketplace.json: allowed-dependency-marketplaces
> (spec doesn't explicitly name the key — NEED TO INVESTIGATE FURTHER)"

## What CPV needs to do once the spec is nailed down

1. **Discover the actual field name** — check the newest plugin-dependencies.md
   revision and the plugin-marketplaces.md for the allowlist key name.
   Candidates: `allowedDependencyMarketplaces`, `trustedMarketplaces`,
   `crossMarketplaceAllowlist`. Confirm via the spec, NOT by guessing.
2. **Add the allowlist-key to marketplace.json schema** in `validate_marketplace.py`
   — accept the new top-level key, validate it is an array of marketplace name
   strings.
3. **When validating a plugin.json with cross-marketplace deps**:
   - Resolve the declaring plugin's root marketplace.
   - Read its `marketplace.json`.
   - For each `dependencies[i]` with a `marketplace` sub-field different from
     the root:
     - If the root marketplace's allowlist is absent → MAJOR: "cross-marketplace
       dependency on `<target>` but root marketplace does not allowlist any
       foreign marketplaces".
     - If the target isn't in the allowlist → MAJOR: "cross-marketplace dependency
       on `<target>` is blocked — root marketplace's allowlist is <list>".
     - If the target IS in the allowlist → clean, or INFO acknowledging the
       cross-marketplace resolution.
4. **Marketplace.json schema validation**: reject malformed allowlist entries
   (non-string items, empty strings, names that don't exist as installed
   marketplaces per `extraKnownMarketplaces`).

## Files

- `scripts/validate_plugin.py` — extend `validate_dependencies` to resolve
  root-marketplace context (passed in from the caller).
- `scripts/validate_marketplace.py` — add the allowlist field to the
  accepted top-level marketplace.json keys + new `_flag_cross_marketplace_*`
  helper.
- `scripts/cpv_management_common.py` — may need a helper that locates the
  "root marketplace" for a given plugin directory (walking up `~/.claude/
  plugins/cache/<marketplace>/` structure).

## Tests

- `test_cross_marketplace_dep_with_allowlist_accepted`
- `test_cross_marketplace_dep_without_allowlist_major`
- `test_cross_marketplace_dep_not_in_allowlist_major`
- `test_marketplace_json_malformed_allowlist_rejected`
- `test_same_marketplace_dep_no_cross_check_needed`

## Blocking questions — RESOLVED 2026-04-XX (v2.22.3 era)

1. **What is the exact name of the allowlist key in `marketplace.json`?**
   `allowCrossMarketplaceDependenciesOn` (plugin-dependencies.md:54-79).
   The earlier conjectural name `allowedDependencyMarketplaces` was wrong;
   CPV honours it as a legacy alias with a NIT nudge.
2. **Is it TOP-LEVEL or nested under `metadata`?** Top-level. Added to
   `OPTIONAL_MARKETPLACE_TOP_LEVEL_FIELDS` in `validate_marketplace.py:180`.
3. **Can plugins themselves declare the allowlist, or is it marketplace-only?**
   Marketplace-only. The allowlist is the marketplace owner's explicit
   consent for foreign-marketplace deps; a plugin cannot grant itself
   foreign-marketplace permission.
4. **Does the allowlist use bare marketplace names or `owner/repo` form?**
   Bare marketplace names (the `name` field of the foreign marketplace's
   `marketplace.json`). Same identifier shape used by
   `claude plugin install <plugin>@<marketplace>`.

## Interim behavior (already in v2.22.0)

CPV's `dependencies` validator already accepts the `marketplace` sub-field
and flags it as "cross-marketplace dependency — resolution subject to root
marketplace allowlist (not yet enforced by CPV)". This is fine as a
placeholder until the spec is confirmed.

## Success criteria — STATUS

- `validate_marketplace.py` recognizes the new allowlist field — DONE
  (v2.22.3, `validate_marketplace.py:180`).
- `validate_plugin.py` emits MAJOR on cross-marketplace deps that violate the
  root marketplace's allowlist — DONE (v2.22.3, `validate_plugin.py:295-322`).
- Five regression tests covering the behavior matrix above all pass — DONE
  (v2.22.3, `TestV223CrossMarketplaceDeps`, 5 tests).
- Documentation (command docs + README) mention the new validation — DONE
  (v2.22.3 partial in `cpv-add-dependency.md`; completed 2026-05-10 in
  README.md "Cross-marketplace dependency allowlist" + commands/cpv-validate-plugin.md
  Options table + "What Gets Validated" §1 + skills/fix-validation/references/
  {plugin-error-index.md, plugin-structure-fixes.md §17}).

## 2026-05-10 follow-up — auto-discovery + CLI override

The library-level enforcement landed in v2.22.3 was correct but the
ORCHESTRATION layer never threaded the hosting marketplace into
`validate_manifest`, so end-users running `validate_plugin <path>` got
INFO instead of MAJOR. 2026-05-10 closes that gap:

1. `discover_hosting_marketplace(plugin_root: Path) -> dict | None` — new
   helper in `validate_plugin.py:180-232`. Walks three on-disk shapes in
   priority order: Layout C (own `.claude-plugin/marketplace.json`),
   Layout B (parent `.claude-plugin/marketplace.json`, walked up to 3
   levels), cache layout (parent `marketplace.json` without the wrapper).
   Returns None on standalone plugins. Malformed marketplace.json yields
   None gracefully (validation owned by `validate_marketplace.py`, not us).
2. `validate_manifest()` auto-calls `discover_hosting_marketplace()` when
   `hosting_marketplace=` is None AND the manifest has a `dependencies`
   field. Explicit context always wins.
3. CLI `--marketplace-context PATH` flag for CI / out-of-tree validation
   (worktrees, extracted tarballs, fresh PR clones).
4. Tests: 10 new tests under `TestCrossMarketplaceHostingDiscovery` —
   layout discovery (4), priority order (1), graceful malformed handling
   (1), library auto-discovery integration (1), CLI flag round-trip (2),
   explicit override precedence (1).

## Approval log

- 2026-08-25T17:25:05+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED v2.22.3 — allowCrossMarketplaceDependenciesOn + discover_hosting_marketplace live (batch_aa)
