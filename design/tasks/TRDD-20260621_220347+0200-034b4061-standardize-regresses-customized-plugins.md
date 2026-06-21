---
trdd-id: 034b4061-d92f-451d-832e-7f45bd8f8a0d
title: Upgrade flow regresses customized + ahead-of-canon plugins — canon template quality + standardize profile-awareness (issues 144 + 145, FM6)
column: dev
created: 2026-06-21T22:03:47+0200
updated: 2026-06-21T22:03:47+0200
current-owner: cpv-main-session
assignee: cpv-main-session
priority: 1
severity: HIGH
effort: L
labels: [upgrade-agents, standardize, canon-template, drift, regression]
task-type: bugfix
parent-trdd: null
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
test-requirements: [unit, lint, typecheck]
impacts: [ci-pipeline]
external-refs: ["github.com/Emasoft/claude-plugins-validation/issues/144", "github.com/Emasoft/claude-plugins-validation/issues/145"]
implementation-commits: []
---

# TRDD-034b4061 — Upgrade flow regresses customized + ahead-of-canon plugins (issues 144 + 145)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-21

**Current state:** All 3 agents DONE + central-verified — shipping in v2.140.0. C1 (canon: MD024→false +MD025,
cliff scope+short-hash, em-dash kept) verified with REAL markdownlint + git-cliff; C2 (force-templates skips
at/AHEAD-of-canon + intentional_divergence files); C3 (intentional_divergence manifest + softened nudge,
`_classify_drift_direction` signature stable for C2). ruff + mypy(125) clean; the 4 new test files interoperate
(78 pass); +78 two-sided tests. NOTE: Fix 1 landed as `MD024: false` (not a bare drop) — the agent claim-verified
that strict-default MD024 is MORE hostile, so `false` is the defensible form per #144's own recommendation.
HIGH priority — user reports "many projects are stuck due to the cpv agents ... creating a pipeline that does not
work / regressing".

**The bug (FM6 — upgrade regresses customization):** `standardize --fix --force-templates` (the prescribed
upgrade path) (1) ships canon `.markdownlint.json` / `cliff.toml` defaults that REGRESS a hand-tuned plugin,
and (2) force-overwrites files the VALIDATOR itself labels "at or AHEAD of canon — do NOT --force-templates"
— so an at/above-canon plugin can't run the prescribed upgrade without regressing. Both #144 (CAA) and #145
(maintainer-agent) are clean-validating plugins (0/0/0/0) blocked by this. NOT a security/--strict issue.

**VERIFIED current state (claim-checked against master):**
- `gen_markdownlint_json` (generate_plugin_repo.py): canon has `"MD024": { "siblings_only": true }` (changelog-hostile — flags recurring per-release CHANGELOG headings) and NO `MD025` (so a frontmatter-titled TRDD doc — YAML `title:` + body `# H1` — trips MD025 → --strict BLOCKS).
- `gen_cliff_toml`: the em-dash `— ` section separator is DELIBERATE (release.yml's awk section-extractor keys on it — do NOT revert it); drop-scope is intentional but loses changelog traceability.
- `standardize_plugin.py`: imports `resolve_pipeline_profile` (L1575) for the PSS submodule case, but `_FORCE_TEMPLATE_FILES` (L795: publish.py, ci.yml, release.yml, notify-marketplace.yml, cliff.toml, .mega-linter.yml, .markdownlint.json) are force-overwritten with NO per-file ahead/behind check.
- The detection EXISTS: `validate_plugin._classify_drift_direction(diff_lines)` (L5570) + `validate_canonical_pipeline_drift` (L5623) emit "at or AHEAD of canon … Do NOT run --force-templates" (L5925). The standardizer just doesn't consult it.

**THE FIX — 5 parts across 3 file-disjoint agents:**

**Agent C1 — `generate_plugin_repo.py` (canon template quality + templated-publish.py cleanliness):**
- Fix 1 [#144 + #145a] `gen_markdownlint_json`: DROP `"MD024": {"siblings_only": true}` (recurring per-release CHANGELOG headings are valid); ADD `"MD025": {"front_matter_title": ""}` (frontmatter-titled docs need it; harmless otherwise). Two-sided test: generated config does NOT flag a recurring-heading CHANGELOG nor a frontmatter-titled doc; still flags a genuine duplicate-H1.
- Fix 2 [#144] `gen_cliff_toml`: RESTORE commit scope + short-hash in per-commit rendering (changelog traceability — compatible with the em-dash awk, which extracts by SECTION header not per-commit format). KEEP the em-dash `— ` header (release.yml needs it) + document WHY inline. Verify a `release:`/`chore(release)` commit does NOT render as a `### Release` noise group (canon already `skip = true` for `^chore\(release\)`; extend if a bare `release:` slips through). Two-sided test.
- Fix 5 [#145c] verify the GENERATED `publish.py` passes `ruff check` (E302 blank-lines between top-level funcs) AND `mypy` (the import-fallback shims already carry `[no-redef, misc]` from v2.138.0 — confirm). Fix any E302 in the template. Add a test that the generated publish.py is ruff-clean.

**Agent C2 — `standardize_plugin.py` (--force-templates profile-awareness):**
- Fix 3 [#145b + #144Bb] When `--force-templates` is on, for EACH file in `_FORCE_TEMPLATE_FILES`, compute its drift direction vs the profile-appropriate canon (REUSE `validate_plugin._classify_drift_direction` on a diff of the plugin file vs the freshly-generated canon for that file — import read-only; do NOT modify it). If the plugin's file is **at/AHEAD of canon** (the validator's "do NOT --force-templates / would downgrade" case), **SKIP** force-overwriting it (leave the plugin's version) and emit a clear line: "skipped force-overwrite of <file> — at/AHEAD of canon (would downgrade); see RC-PIPELINE-DRIFT-001." A BEHIND/plain file is overwritten as before. ALSO honor the new `intentional_divergence` manifest list from C3 (skip those too). Two-sided test: ahead-of-canon file → skipped; behind file → overwritten.

**Agent C3 — `validate_plugin.py` (drift section) + `cpv_pipeline_profile.py` (mark-divergent + nudge):**
- Fix 4 [#144Ba] Add a manifest mechanism — `plugin.json` `cpv.pipeline.intentional_divergence: ["cliff.toml", …]` (a list of repo-relative paths) — that SUPPRESSES the RC-PIPELINE-DRIFT-001 *recommendation* for those files (still NOTE the divergence as informational, but drop the "run --force-templates" nudge). Soften the general drift nudge wording: when recommending an upgrade, warn that force-templating a deliberately-customized shared-canon file may regress it. Keep `_classify_drift_direction` + `validate_canonical_pipeline_drift` signatures STABLE (additive only — C2 imports them). Two-sided test: a marked file → no upgrade nudge (still noted); an unmarked drifted file → nudge as before.

**Load-bearing facts:**
- The em-dash cliff.toml separator is REQUIRED by release.yml's awk — never revert it (a reverting "fix" of #144's `-`→`—` complaint would break section extraction). #144's real wins are the .markdownlint MD024-drop + the cliff scope+hash restore.
- The MD025-strip (#145a) is resolved two ways together: Fix 1 ADDS MD025 to canon (so force-templating no longer strips it) AND Fix 3 skips force-overwriting an ahead-of-canon .markdownlint.json.
- This is NOT a security/--strict change — both reporters verified --strict is correct. Do NOT touch the scanner or --strict.
- CPV's OWN mypy gate is `mypy scripts/ --ignore-missing-imports`. Regen self-hashes LAST after CLAUDE.md/TRDD edits; self-validate `CPV_SCAN_CACHE=0 PLUGIN_SKIP_GITHUB_INTEGRITY=1`. No line-start `#`/`+ `/`* ` poison.
- Ship target: v2.140.0 alongside TRDD-8eee537a Phase-1 ci-preflight (already built). The agent-wiring (TRDD-8eee537a Phase-2) ships next as v2.141.0.

## Background

Two clean-validating ecosystem plugins (CAA #144, maintainer-agent #145) cannot adopt the prescribed
canonical-pipeline upgrade because `standardize --force-templates` regresses their hand-tuned / ahead-of-canon
files. This blocks the fleet-wide "upgrade to CPV canon" directive. The validator became profile-aware in #118;
this TRDD makes the STANDARDIZER honor the same profile + raises canon template quality.

## Notes and lessons learned
