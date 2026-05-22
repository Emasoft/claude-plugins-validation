---
trdd-id: 44d6b8c9-e26e-453c-b78f-d326eab4dc01
title: Canon generators lag CPV's own dogfooded pipeline — promote dogfood improvements into generate_plugin_repo.py
status: not-started
created: 2026-05-22T03:40:49+0200
updated: 2026-05-22T03:40:49+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-44d6b8c9 — Canon generator / dogfood lag

**Filename:** `design/tasks/TRDD-20260522_034049+0200-44d6b8c9-canon-generator-dogfood-lag.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

## Source

User observation (verbatim, condensed):

> What about the fact that the canon did not incorporate the additional
> features proposed by the visual communicator plugin? What kind of canon
> is fragmented to the point that every plugin has a different pipeline?

Surfaced while preparing to upgrade `ai-maestro-visual-communicator-plugin`
(a local checkout under `${HOME}/Code/`, v1.3.5), which carries
8 files in `plugin.json::cpv._intentional_pipeline_drift_files`, most
documented as "STRICTLY above canon".

## Root cause (✓ VERIFIED)

CPV improved its **own** dogfooded pipeline (`.github/workflows/*.yml`,
`scripts/publish.py`) over v2.94.0 → v2.101.4 but **never back-ported
those improvements into the canon template generators**
(`scripts/generate_plugin_repo.py`). The generators are the source of
truth for what `cpv create` and `cpv standardize --force-templates`
write into every plugin — so the canon a plugin receives drifted
**behind** the pipeline CPV itself runs.

Concrete, verified deltas (generator vs CPV's own dogfood):

| Surface | Canon generator emits | CPV's own dogfood runs |
|---|---|---|
| `gen_ci_yml` `timeout-minutes` | **0 (none)** | `timeout-minutes: 15` on every job |
| `gen_ci_yml` checkout pin | `actions/checkout@v4` (tag, stale) | `actions/checkout@de0fac… # v6.0.2` (SHA) |
| `gen_release_yml` `timeout-minutes` | **0** | present |
| `gen_release_yml` checkout pin | `@v4` | `@de0fac… # v6.0.2` |
| `gen_notify_marketplace_yml` `timeout-minutes` | **0** | present |
| `scripts/cpv_network_resilience.py` annotation | `Callable[[int, "subprocess.CompletedProcess[str]"], None]` (redundant string quote — ruff UP037) | (VC fixed: unquoted) |

So when VC migrated to canon (v1.3.x) and then **independently**
re-derived `timeout-minutes` + SHA-pinned `checkout@v6` + the UP037 fix,
it correctly flagged them as "above canon" — because the canon
generator genuinely lacked them. The fragmentation the user sees is
real and is CPV's bug: **the canon must never lag its own dogfood.**

## Re-evaluation of VC's 8 drift claims against CURRENT canon (v2.101.4)

VC's `plugin.json` drift rationale is pinned to its v1.3.x understanding
(it literally says `allow_pipeline_drift` is "not yet active in v2.94.0").
Several claims are now STALE because CPV's canon caught up:

| VC drifted file | VC claim | Reality vs v2.101.4 canon | Action |
|---|---|---|---|
| `scripts/publish.py` | atomic-push, dev-browser soft-pass, 3-manifest lockstep | **Canon CAUGHT UP**: `git push --atomic` + `_BROWSER_ORPHAN_SIGNATURES` cleanup shipped v2.98.0. No longer above canon. | none (VC plugin.json is stale) |
| `scripts/cpv_network_resilience.py` | UP037 unquoted annotation | **VC still ahead** — canon keeps the redundant string quote | **PROMOTE** |
| `.github/workflows/ci.yml` | SHA-pins + timeout-minutes | **Canon GENERATOR lags** (CPV dogfood has them; generator does not) | **PROMOTE dogfood→generator** |
| `.github/workflows/release.yml` | same | same | **PROMOTE** |
| `.github/workflows/notify-marketplace.yml` | same | same | **PROMOTE** |
| `.mega-linter.yml` | local drift | CPV ships no root `.mega-linter.yml`; plugin-specific | leave (plugin-specific) |
| `.markdownlint.json` | local `_comment` + MD012 disable | plugin-specific authoring choice | leave (legitimate drift; `allow_pipeline_drift`) |
| `git-hooks/pre-push` | drift | 480-line delta, mostly CPV-ahead; needs file-level analysis before any promotion | analyze (Phase 3) |

## Plan

### Phase 1 — workflow generator catch-up (the headline fix)

Bring `gen_ci_yml`, `gen_release_yml`, `gen_notify_marketplace_yml` in
`scripts/generate_plugin_repo.py` up to CPV's own dogfooded standard:

* Add `timeout-minutes:` to every job (match CPV's own values: 15 for
  CI test/lint jobs, 10–15 for release, 5 for the lightweight notify
  job — mirror the dogfood, do not invent new numbers).
* SHA-pin `actions/checkout` to the latest release
  (`de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2`) and
  `actions/upload-artifact` to `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1`,
  matching CPV's own dogfood (and per `~/.claude/rules/gh-actions.md`,
  pinning first-party actions to SHA is the stronger guarantee).
* Verify the latest SHAs at implementation time via
  `gh api repos/<a>/releases/latest` (the values above were captured
  2026-05-22; refresh before pinning).
* DERIVED: update `tests/test_generate_plugin_repo.py` /
  `test_new_pipeline_rules.py` to assert the generated workflows carry
  `timeout-minutes` on every job and SHA-pinned first-party actions, so
  the generator can never silently regress behind the dogfood again.
* DERIVED: add a CPV self-check rule (or extend
  `validate_canonical_pipeline_drift`) that FAILS when CPV's own
  `.github/workflows/*.yml` carry a feature the generator omits — a
  "generator must not lag dogfood" invariant. This is the structural
  guard that prevents the whole class of bug from recurring.

### Phase 2 — cpv_network_resilience.py UP037 fix

Remove the redundant string quote on the `on_retry` annotation in
`scripts/cpv_network_resilience.py` line 172
(`"subprocess.CompletedProcess[str]"` → `subprocess.CompletedProcess[str]`,
correct under `from __future__ import annotations`). The generator emits
this file verbatim (`gen_cpv_network_resilience_py` does
`src.read_text()`), so fixing CPV's copy fixes the canon for every
plugin. Confirm CPV's own ruff config (run `ruff check`) — if UP037 is
enabled, CPV's own copy was already a latent lint debt.

### Phase 3 — pre-push delta analysis (deferred judgment)

The `git-hooks/pre-push` 480-line delta needs a file-level diff to
separate CPV-ahead lines (do not back-port to plugins) from any
genuine VC-ahead improvement. Only promote VC-ahead, plugin-agnostic
improvements. Likely outcome: no promotion (CPV's pre-push is ahead),
but verify rather than assume.

### Phase 4 — document the already-shipped `allow_pipeline_drift`

`cpv.allow_pipeline_drift` shipped in v2.97.0 (TRDD-6edd2743) — the
consumer in `validate_canonical_pipeline_drift` reads the list and
skips the per-file drift WARNING for allow-listed `rel_path`s. Confirm
the README / canonical-pipeline skill documents this so plugin authors
stop hand-rolling `_intentional_pipeline_drift_files` workarounds and
use the supported field. (VC can then migrate its stale
`_intentional_pipeline_drift_files` → `allow_pipeline_drift` and drop
the now-closed upstream-bug citations.)

## Acceptance

* [ ] `gen_ci_yml` / `gen_release_yml` / `gen_notify_marketplace_yml`
      emit `timeout-minutes` on every job + SHA-pinned first-party
      actions matching CPV's own dogfood.
* [ ] A freshly-generated plugin's workflows are byte-compatible with
      CPV's own workflow standard on the timeout + pin axes.
* [ ] `cpv_network_resilience.py` UP037 fixed; canon ships the unquoted
      form.
* [ ] New invariant test: generator must not lag CPV's own dogfood.
* [ ] CPV self-scan stays 0/0/0/0 + WARNING-only.
* [ ] Full test suite green.
* [ ] CI ✓ + Release ✓ + Notify Marketplace ✓.

## Note on scope (NOT this TRDD)

* Fixing VC's stale `plugin.json` (closed-bug citations, v2.94.0
  pin) is a **visual-communicator-side** change — per
  `~/.claude/rules/how-to-fix-issues-of-other-projects.md` it is NOT
  done from CPV's session.
* This TRDD only promotes genuine, plugin-agnostic improvements into
  the canon generators. Legitimately plugin-specific drift
  (`.markdownlint.json` authoring choices, a plugin's bespoke
  `.mega-linter.yml`) stays in `allow_pipeline_drift`.
