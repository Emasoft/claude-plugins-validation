# TRDD-20108ab7 — Cross-marketplace dependency allowlist

**TRDD ID:** `20108ab7-3fb1-4d20-8959-700992e9ee7c`
**Filename:** `design/tasks/TRDD-20108ab7-3fb1-4d20-8959-700992e9ee7c-cross-marketplace-deps.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Not started
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

## Blocking questions

1. What is the exact name of the allowlist key in `marketplace.json`?
2. Is it TOP-LEVEL or nested under `metadata`?
3. Can plugins themselves declare the allowlist, or is it marketplace-only?
4. Does the allowlist use bare marketplace names or `owner/repo` form?

Until these are answered by a concrete spec reference, CPV should continue
to WARN on any cross-marketplace dependency resolution attempt rather than
enforce an allowlist it cannot locate.

## Interim behavior (already in v2.22.0)

CPV's `dependencies` validator already accepts the `marketplace` sub-field
and flags it as "cross-marketplace dependency — resolution subject to root
marketplace allowlist (not yet enforced by CPV)". This is fine as a
placeholder until the spec is confirmed.

## Success criteria

- `validate_marketplace.py` recognizes the new allowlist field.
- `validate_plugin.py` emits MAJOR on cross-marketplace deps that violate the
  root marketplace's allowlist.
- Five regression tests covering the behavior matrix above all pass.
- Documentation (command docs + README) mention the new validation.
