# TRDD-d47f5101 — Canonical pipeline hardening (issue #22) + canon-name policy (revert of v2.85.0)

**TRDD ID:** `d47f5101-e7a1-4e49-a85a-3b89e5e1fb6f`
**Filename:** `design/tasks/TRDD-d47f5101-e7a1-4e49-a85a-3b89e5e1fb6f-canon-pipeline-hardening.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Done (shipped in v2.86.0)
**Date:** 2026-05-14
**Source:**
* https://github.com/Emasoft/claude-plugins-validation/issues/22
* https://github.com/Emasoft/claude-plugins-validation/issues/23

## Decision

**Option A from issue #22 wins.** CPV absorbs the security hardening that
`ai-maestro-visual-communicator-plugin` had added above the canonical
templates, so every plugin migrating via `standardize --force-templates`
now lands on a strong-by-default baseline. No per-plugin opt-out
mechanism is needed because the canon now matches (or exceeds) every
hardened fork we've seen in the wild.

Additionally, the v2.85.0 `marketplace_secret_name` per-plugin override
is **reverted**. The user's directive: *"the marketplace secret must be
called MARKETPLACE_PAT everywhere"*. Single canonical name policy — the
migration normalizes deviations to canon and emits a loud
`[ACTION REQUIRED]` block telling the maintainer to rename their gh
secret, instead of silently preserving the deviation.

## Canon-name policy (revert of v2.85.0)

### What was wrong with v2.85.0

The v2.85.0 fix to issue #23 plumbed a `marketplace_secret_name` field
through `PluginParams` so a plugin with `MARKETPLACE_DISPATCH_TOKEN`
kept that name on regeneration. This *avoided* the silent breakage
from the pre-v2.85.0 behavior (which renamed to `MARKETPLACE_PAT` with
no warning), but it broke the single-canon-name principle: every plugin
could now have its own secret name, and `cpv_setup_auth` / `gh secret`
docs / the marketplace receiver webhook could no longer assume a stable
name.

### What v2.86.0 does instead

1. **Generator always emits `secrets.MARKETPLACE_PAT`** — the canonical
   name. Period.
2. **Detector still parses the pre-existing YAML** for the OLD secret
   name. But instead of plumbing it onto `PluginParams`, it records the
   deviation in the change-record dict under the key
   `marketplace_secret_name__DEVIATION`.
3. **Migration UX**: when a deviation is recorded,
   `fix_missing_files` emits a multi-line `[ACTION REQUIRED]` block to
   stdout — bold yellow header, the old secret name, the canonical
   name, and the exact `gh secret set` command (using `"$MARKETPLACE_PAT"`
   so the maintainer doesn't have to paste the PAT manually) plus the
   `gh secret delete` cleanup for after verification.
4. **Per-plugin VALUES still detected**: marketplace owner and repo are
   plugin-specific (different plugins live under different orgs). Those
   continue to be detected from the pre-existing YAML and plumbed into
   `PluginParams`.

`PluginParams.marketplace_secret_name` was removed; tests covering it
were rewritten to assert the canon-name policy.

## Canon hardening (issue #22)

Adopted from `ai-maestro-visual-communicator-plugin` v1.2.1's
TRDD-5f41ad36. Each item is now part of every plugin scaffolded or
migrated via CPV.

### `gen_publish_py` — `scripts/publish.py` template

* **Broadened bypass-guard (`stage_bypass_guard`)** — was an explicit
  forbidden-list of ~13 names; now prefix-pattern match against
  `PLUGIN_SKIP_*`, `PLUGIN_FORCE_*`, `PLUGIN_BYPASS_*`, `CPV_SKIP_*`,
  `SKIP_*`, plus exact match on `NO_VERIFY`. Closes the loophole where
  a fresh skip name (e.g. `CPV_SKIP_GATE7`) silently slipped past the
  fixed list. Two documented infrastructure exemptions retained:
  `CPV_SKIP_GITHUB_INTEGRITY`, `CPV_SKIP_GH_AUTH_CHECK`.
* **Atomic push** — replaces the previous two-call `git push origin HEAD`
  + `git push origin <tag>` form with a single
  `git push --atomic origin HEAD <tag>`. Commit + tag land in one
  wire-protocol transaction; the server rolls back if any ref-update
  fails. Eliminates the "branch pushed without tag" half-published
  state that was a flaky-network failure mode.

### `gen_ci_yml` — `.github/workflows/ci.yml` template

* **actionlint** as the first lint step — catches workflow-syntax
  regressions BEFORE the expensive Mega-Linter run. SHA-pinned to
  `rhysd/actionlint@914e7df21a07ef503a81201c76d2b11c789d3fca` # v1.7.12.
* **commitlint** as a new `commitlint` job, gated on `pull_request`
  events only (not on push to main) — rejects non-conventional commits
  at PR-merge time so git-cliff never sees junk subject lines.
  SHA-pinned to
  `wagoid/commitlint-github-action@6cf16efdf4da5277c791d335142c03a0bdf1766e` # v6.2.1.
* **macOS matrix** on the `test` job — `os: [ubuntu-latest,
  macos-latest]` with `fail-fast: false`. Catches darwin-specific
  regressions (pathlib casing, BSD `ps`, etc.).
* **SHA-pin** `oxsecurity/megalinter` and `astral-sh/setup-uv` to the
  current v8 / v4 major-tag SHA. First-party actions (`actions/*`,
  `github/*`) stay at `@v4` per gh-actions.md exemption.

### `gen_release_yml` — `.github/workflows/release.yml` template

* **CHANGELOG-section extraction** — release body is now the matching
  `## [X.Y.Z] — YYYY-MM-DD` block from CHANGELOG.md, not the entire
  file. Implementation: `awk` with a fallback chain (em-dash → hyphen
  → full CHANGELOG → git log).
* **SHA-pin** `astral-sh/setup-uv`.

### `gen_notify_marketplace_yml` — `.github/workflows/notify-marketplace.yml` template

* **`env:` sanitization** — every `${{ github.* }}` /
  `${{ steps.*.outputs.* }}` value consumed by a `run:` block is bound
  to an `env:` mapping; the shell sees `$VAR`, never raw expression
  interpolation. Prevents shell-injection if upstream repository
  metadata is ever crafted hostile (gh-actions.md
  §"Avoid expression injection").
* **SHA-pin** `peter-evans/repository-dispatch` to v4.0.1 SHA.
* **Header comment** documents the `gh secret set MARKETPLACE_PAT`
  setup recipe so a maintainer setting up a new plugin sees it without
  hunting through docs.

### `gen_cliff_toml` — `cliff.toml` template

* **Em-dash section separator** — `## [X.Y.Z] — YYYY-MM-DD`
  (was ` - `). Matches the typographic style of CPV docs and is what
  release.yml's section-extraction awk script looks for.
* **Drop scope display** — `*({{ commit.scope }})*` removed from the
  per-commit rendering. The group header already announces the kind.
* **Drop `striptags`** — conventional-commit group names never contain
  HTML.

### `validate_plugin.RC-PIPELINE-DRIFT-001`

* **Reworded** the doctor recommendation so plugins already at-or-above
  hardened canon don't get nagged. The warning now sniffs the diff for
  canon-hardening markers (SHA-pin comments, atomic push, actionlint,
  commitlint) and switches to a softer phrasing when those markers
  are present.

## Files changed

* `scripts/generate_plugin_repo.py` — PluginParams field reverted; all
  five generator functions hardened (publish, ci, release, notify, cliff).
* `scripts/publish.py` — CPV's own stage_bypass_guard + atomic push
  (dogfooding the new canon).
* `scripts/standardize_plugin.py` — `_apply_notify_marketplace_overrides`
  records secret-name deviation as `*__DEVIATION`; new `[ACTION REQUIRED]`
  block emitted from `fix_missing_files`.
* `scripts/validate_plugin.py` — RC-PIPELINE-DRIFT-001 wording softened
  for hardened forks.
* `tests/test_issue_23_force_templates_marketplace.py` — 4 tests
  rewritten for the canon-name policy; 1 new test asserts
  `marketplace_secret_name` is gone from PluginParams.
* `tests/test_v2_86_0_canon_hardening.py` — NEW, 12 tests covering all
  six hardening checkpoints.
* `tests/test_agent_marketplace_preflight.py` — updated for prefix-match
  bypass-guard.
* `tests/test_generate_plugin_repo.py` — updated for prefix-match
  bypass-guard.

## Verification

```bash
uv run pytest tests/ -n auto --dist=worksteal --maxfail=3 -q
# 5106 passed, 1 skipped, 0 failed

CPV_SKIP_GITHUB_INTEGRITY=1 uv run python scripts/validate_plugin.py . --strict
# CRITICAL: 0  MAJOR: 0  MINOR: 0  NIT: 0  WARNING: 1 (pre-existing skill size)
```

## SHA-pin reference (look up via `gh api`)

| Action | SHA | Version |
|---|---|---|
| `oxsecurity/megalinter` | `e08c2b05e3dbc40af4c23f41172ef1e068a7d651` | v8 (major) |
| `astral-sh/setup-uv` | `e4db8464a088ece1b920f60402e813ea4de65b8f` | v4 (major) |
| `rhysd/actionlint` | `914e7df21a07ef503a81201c76d2b11c789d3fca` | v1.7.12 |
| `wagoid/commitlint-github-action` | `6cf16efdf4da5277c791d335142c03a0bdf1766e` | v6.2.1 |
| `peter-evans/repository-dispatch` | `28959ce8df70de7be546dd1250a005dd32156697` | v4.0.1 |

To refresh: `gh api repos/<owner>/<action>/git/ref/tags/<tag> --jq .object.sha`.

## Migration UX (what a plugin maintainer sees)

When running `standardize --fix --force-templates` on a plugin with a
pre-existing `notify-marketplace.yml` that referenced
`secrets.MARKETPLACE_DISPATCH_TOKEN`:

```text
  [migration] notify-marketplace.yml derived from existing file:
    marketplace_owner: None → 'Emasoft'
    marketplace: None → 'ai-maestro-plugins'

  [ACTION REQUIRED] secret-name deviation detected
  The previous notify-marketplace.yml referenced secrets.MARKETPLACE_DISPATCH_TOKEN.
  CPV v2.86.0+ enforces the canonical secret name MARKETPLACE_PAT across all plugins —
  the regenerated YAML now references secrets.MARKETPLACE_PAT.

  Run (assumes $MARKETPLACE_PAT is exported):
    gh secret set MARKETPLACE_PAT --repo Emasoft/test-plugin --body "$MARKETPLACE_PAT"

  After the next push triggers a marketplace dispatch successfully:
    gh secret delete MARKETPLACE_DISPATCH_TOKEN --repo Emasoft/test-plugin
```

The `$MARKETPLACE_PAT` env-var assumption matches the documented
setup: maintainers export the PAT in their shell or `.env` file once;
every plugin uses the same canonical name.

## Deferred / explicitly not implemented

* **`cpv.allow_pipeline_drift` opt-out** — issue #22 proposed it as
  Option B if the hardening was deemed too plugin-specific to upstream.
  Now that everything was upstreamed (Option A), the opt-out is not
  needed. If a plugin author wants to deviate ABOVE canon in some other
  way in the future, we revisit.
* **CHANGELOG MIGRATION script** for v2.85.0 plugins** — plugins
  scaffolded against v2.85.0 with a custom `marketplace_secret_name`
  will hit the `[ACTION REQUIRED]` block on next migration. That's the
  documented upgrade path; no automated rewrite of their plugin.json is
  needed (the field was a generator input, never persisted to disk).
