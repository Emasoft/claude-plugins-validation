---
trdd-id: 5bcfee1b-1249-437f-9774-531e7ca9b66d
title: Canon standardize/template defects failing an adopting plugin's CI — issue 142
column: complete
created: 2026-06-21T04:37:00+0200
updated: 2026-06-21T04:37:00+0200
current-owner: cpv-main-session
assignee: cpv-main-session
priority: 2
severity: HIGH
effort: M
labels: [canon-pipeline, standardize, ci, false-positive]
task-type: bugfix
parent-trdd: null
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
test-requirements: [unit, lint, typecheck]
impacts: [ci-pipeline]
external-refs: ["github.com/Emasoft/claude-plugins-validation/issues/142"]
implementation-commits: []
---

# TRDD-5bcfee1b — Canon standardize/template defects failing an adopting plugin's CI (issue 142)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-21

**Current state:** All four defects resolved; gates green; ready to publish v2.138.0.

- Defect 1 (publish.py mypy) — DONE. `generate_plugin_repo.py` publish.py template:
  both `gh_with_retry`/`git_with_retry` import-fallback shims now `# type: ignore[no-redef, misc]`
  with a WHY comment. Verified live `mypy --strict` on the generated publish.py is clean.
- Defect 2 (dev extra) — DONE. `standardize_plugin.py` `provision_dev_extra` auto-provisions
  `[project.optional-dependencies] dev = ["pytest","ruff","mypy"]` under `--fix` (create/augment,
  format-preserving, lockfile refresh); audit path WARN-only, never mutates. Generated-default
  template already declared it.
- Defect 3 (inverted `CLAUDE_PRIVATE_USERNAMES` env) — ALREADY FIXED in v2.137.1 (same root cause
  as #140; reporter was on 2.136.1). Verified `gen_ci_yml`/`gen_release_yml` bodies clean; standardize
  emits via those generators so it inherits the fix. No code change this TRDD.
- Defect 4 (superseded validate.yml) — DONE. `standardize_plugin.py` `remove_superseded_validate_yml`
  removes a CPV-shipped `validate.yml` (identity-guarded by command AND name markers, only when the
  replacement `ci.yml` exists), safe-deleted to the ADOPTING plugin's `scripts_dev/superseded-workflows/`,
  emits the branch-protection re-point note.

**NEXT ACTION:** run `uv run scripts/publish.py --minor` (2.137.1 → 2.138.0), watch CI to green,
then close issue 142 self-id'd. Self-hashes were regenerated AFTER the CLAUDE.md + this TRDD edit
(per the regen-LAST lesson) so Gate 3 self-scan keeps CLAUDE.md/TRDD skipped.

**Load-bearing facts:**
- CPV's OWN mypy gate is `uv run mypy scripts/ --ignore-missing-imports` (the pyproject `[tool.mypy]`
  config, NOT `--strict`). A bare `mypy --strict` reports 7 strict-only artifacts (type-arg / unused
  tomllib import-fallback ignores) that the real gate tolerates — not regressions.
- Defect 1's target gate is the ADOPTING plugin's `mypy --strict` (the generated publish.py), a
  different gate from CPV's own.
- The integrity self-check (`exit=2`, "local modifications") is expected during CPV development —
  bypass with `PLUGIN_SKIP_GITHUB_INTEGRITY=1`; it is NOT a validation finding.

**Durable artifacts:**
- reports/canon-142/20260621_044034+0200-genrepo.md (Agent A — defects 1 + 2-default)
- reports/canon-142/20260621_045427+0200-standardize.md (Agent B — defects 2-provision + 4)

## Background

Issue 142 (MANAGER field report): upgrading a plugin to the CPV canonical publish pipeline via
`remote_validation.py standardize . --fix --force-templates` surfaced four template defects, each of
which fails CI for the adopting plugin. None are caught by the local `publish.py --dry-run` /
`remote_validation.py plugin .` (they don't run the GitHub workflows) — only the real CI catches them.
This blocked an in-progress fleet-wide canon-pipeline upgrade (~8 plugins).

## Implementation

Delivered by two parallel opus subagents grouped by file (disjoint source trees → no edit conflict):
- Agent A owned `scripts/generate_plugin_repo.py` (defect 1 + the defect-2 generated-default check).
- Agent B owned `scripts/standardize_plugin.py` + `skills/standardize-plugin/references/pipeline-rules.md`
  (defect 2 provisioning + defect 4 removal).

New two-sided tests: `tests/test_canon_142_genrepo.py` (7) and `tests/test_canon_142_standardize.py` (23),
plus the pre-existing `tests/test_issue_25_publish_defects.py` `test_d_*` case re-pointed from the old
warn-only contract to the new auto-provision contract (asserts provisioning + existing-entry preservation).

## Verification (central, through the real gates)

- pytest 50/50 on the changed files (+ agents' 286 / 223 sibling sweeps, no regression).
- CI mypy gate (`uv run mypy scripts/ --ignore-missing-imports`): clean, 123 source files.
- ruff: clean.
- CPV self-validate `--strict` (`CPV_SCAN_CACHE=0 PLUGIN_SKIP_GITHUB_INTEGRITY=1`): CRITICAL=0 MAJOR=0
  MINOR=0 NIT=0, WARNING=5 (all pre-existing "agent body very long", untouched by this change).
- Defect 4's removal targets the ADOPTING plugin's tree (`plugin_path / …`), identity-guarded, with no
  CPV-tree pollution.

## Notes and lessons learned
