---
trdd-id: 08fecb37
title: TRDD-08fecb37 — Issue #23 `standardize --force-templates` clobbers notify-marketplace.yml
column: complete
updated: 2026-08-25T17:25:05+0200
---

# TRDD-08fecb37 — Issue #23: `standardize --force-templates` clobbers notify-marketplace.yml

**TRDD ID:** `08fecb37-37d3-40a7-a9e3-14ed007864fb`
**Filename:** `design/tasks/TRDD-08fecb37-37d3-40a7-a9e3-14ed007864fb-issue-23-force-templates-marketplace.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Done (shipped in v2.85.0)
**Date:** 2026-05-14
**Source:** https://github.com/Emasoft/claude-plugins-validation/issues/23

## Symptom

`standardize --fix --force-templates` (and the equivalent path via
`/cpv-upgrade-plugin`) replaces a working
`.github/workflows/notify-marketplace.yml` with a template that:

* **Bug A** — silently emits the literal placeholder
  `MARKETPLACE_REPO: 'my-plugins-marketplace'` over a real marketplace
  name (`ai-maestro-plugins` in the reported case).
* **Bug B** — hardcodes `secrets.MARKETPLACE_PAT` over the repo's
  configured secret (`MARKETPLACE_DISPATCH_TOKEN` in the reported case).

Both bugs are silent: nothing emits a warning at migration time, but
`publish.py` Stage 5 then blocks the next release with
`BLOCKED: notify-marketplace.yml has no MARKETPLACE_OWNER/MARKETPLACE_REPO`
or `MARKETPLACE_PAT secret not configured`. Issue #23 caught this in
`Emasoft/ai-maestro-plugin` v2.5.8.

## Root cause

`scripts/generate_plugin_repo.gen_notify_marketplace_yml` had two
hardcoded values that nothing in the migration path overrode:

```python
marketplace_repo = p.marketplace if p.marketplace else "my-plugins-marketplace"
# …
token: ${{ secrets.MARKETPLACE_PAT }}
```

The migration path is:

1. `standardize_plugin.fix_missing_files(force_templates=True)`
2. → `_params_from_manifest(manifest)` — reads `manifest.get("marketplace", "")`.
3. → `gen_notify_marketplace_yml(params)`

Step 2 only reads `manifest["marketplace"]`, which most plugin.json files
do NOT set. The pre-existing notify-marketplace.yml on disk — which DOES
contain the real values — was never consulted.

## Fix (v2.85.0)

Two new `PluginParams` fields, populated from a new detector run BEFORE
the template is overwritten:

| New field | Purpose |
|---|---|
| `marketplace_owner: str = ""` | Overrides `github_owner` for `MARKETPLACE_OWNER:` so a plugin whose marketplace lives under a different owner keeps the right value. |
| `marketplace_secret_name: str = "MARKETPLACE_PAT"` | Overrides the historical hardcoded secret name. Default preserves backward compatibility for fresh scaffolds. |

`generate_plugin_repo.gen_notify_marketplace_yml` now reads both fields.
The placeholder branch only triggers when `p.marketplace` is empty.

`standardize_plugin._detect_existing_notify_marketplace(plugin_path)`
parses the pre-existing YAML for `MARKETPLACE_OWNER`, `MARKETPLACE_REPO`,
and the first `secrets.<UPPER_SNAKE>` reference. The canonical placeholder
`my-plugins-marketplace` is filtered to `None` so a re-migration of a
previously-clobbered file does not "preserve" the placeholder.

`standardize_plugin._apply_notify_marketplace_overrides(params, path,
cli_marketplace)` mutates `params` with precedence
**CLI `--marketplace=` > detection > defaults**. Returns a change-record
dict so the caller can print a `[migration]` note when something differs
from the default — making any silent rotation visible to the operator.

`standardize_plugin.fix_missing_files`, in the force-overwrite branch for
`.github/workflows/notify-marketplace.yml`:

1. Calls `_apply_notify_marketplace_overrides` BEFORE the generator runs.
2. If `--force-templates` would still emit the placeholder over an
   existing file (no CLI flag AND nothing detectable in the YAML),
   **refuses** to ship the regeneration and prints a `REFUSED:` message
   directing the user to `--marketplace=owner/repo`. Other files still
   regenerate; the broken-on-purpose notify file is left untouched so a
   second pass can fix it.
3. When detection / CLI changed anything, prints a `[migration]` block
   listing `field: old → new`.

## Files changed

* `scripts/generate_plugin_repo.py` — 2 new PluginParams fields, generator
  reads them.
* `scripts/standardize_plugin.py` — `_detect_existing_notify_marketplace`,
  `_apply_notify_marketplace_overrides`, hook into `fix_missing_files`
  before the template-overwrite loop, refusal guard + `[migration]` echo.
* `tests/test_issue_23_force_templates_marketplace.py` — NEW, 17 tests
  covering detector quoting forms, placeholder filtering, generator
  output, precedence, end-to-end migration, refusal path, fresh-scaffold
  backward compat.

## Verification

```bash
uv run pytest tests/ -n auto --dist=worksteal --maxfail=3 -q
# 5091 passed, 1 skipped

CPV_SKIP_GITHUB_INTEGRITY=1 uv run python scripts/validate_plugin.py . --strict
# CRITICAL: 0  MAJOR: 0  MINOR: 0  NIT: 0  WARNING: 1 (pre-existing skill size)
```

## Backward compat

* `PluginParams(marketplace_owner="", marketplace_secret_name="MARKETPLACE_PAT")`
  defaults reproduce the pre-fix template byte-for-byte for any caller
  that does not opt into the new fields.
* Fresh-scaffold path (file does NOT yet exist) emits the placeholder
  exactly as before — there is no real value to detect, and the CLI flag
  is the documented way to override.

## Deferred / explicitly not implemented

* **`known_marketplaces.json` fallback** — issue #23 mentions it as one
  source. The file is keyed on marketplace name, not plugin name, so
  resolving "which marketplace hosts this plugin" requires loading every
  marketplace.json and scanning its plugin list. Detection of the
  existing YAML covers the actual reported case and is strictly local;
  the broader fallback can land in a follow-up if needed.
* **A new `--marketplace-secret-name` CLI flag** — not required for the
  reported failure (detection from the existing YAML covers it). Trivial
  to add later if a user wants to rotate the secret name via CLI.

## Approval log

* 2026-08-25T17:25:05+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED v2.85.0 — marketplace_owner override in generate_plugin_repo.py (batch_aa)
