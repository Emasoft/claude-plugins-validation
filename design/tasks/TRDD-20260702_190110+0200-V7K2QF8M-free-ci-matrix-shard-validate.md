---
trdd-id: V7K2QF8M
title: Free CI speedup — matrix-shard the Validate skillaudit scan + Test 4→8
column: dev
created: 2026-07-02T19:01:10+0200
updated: 2026-07-02T19:01:10+0200
current-owner: main-session
assignee: main-session
task-type: infra
priority: 4
severity: LOW
effort: M
labels: [ci, performance, sharding]
release-via: publish
delivery: direct-push
target-branch: master
must-pass-tests-before-merge: true
publish-target: claude-plugins-validation
test-requirements: [unit, lint, typecheck]
relevant-rules: []
impacts: [ci-pipeline]
implementation-commits: [c80b8c0, a2ebe4a]
external-refs: []
---

# TRDD-V7K2QF8M — Free CI speedup: matrix-shard Validate skillaudit + Test 4→8

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-07-02

**Goal:** cut total CI wall FREE (standard `ubuntu-latest` 4-vCPU public-repo runners + more parallel JOBS — NEVER a paid larger runner). Target 3m41s → ~2m06s (~1.7×).

**Current state:** code+ci.yml committed (c80b8c0, a2ebe4a); serial tests GREEN (10426). ✅ BLOCKER CLEARED: the RC-WORKFLOW-PATH-BROKEN FP on `reports-in/*.json` was a real detector gap (issue-#116 produced-path scan harvests only `run:` bodies → blind to the `actions/download-artifact` `uses:` step). FIXED in `validate_plugin.py` (`_collect_jobs_artifact_dirs` + `_is_downloaded_artifact_path` — suppress a token under a download-artifact `with.path:` dir in its SAME job; PER-JOB + explicit-non-`.`-path only, so broken-ref-outside-dir / no-download-job / cross-job / omitted-path all STILL fire). +7 two-sided tests (`tests/test_workflow_path_download_artifact.py`), ruff+mypy clean, cache-cold self-validate **0/0/0/0**. Remaining: docs done (CLAUDE.md v2.152.0) → regen self-hashes LAST → commit by name → `publish.py --minor` → watch CI green → MEASURE wall ≥1.3× vs 3m41s → correct memory `lesson_ci_validate_job_not_worth_optimizing` → `column: complete`.

**Durable evidence to read before acting:**
- `reports/validate-profiling/20260702_185714+0200-free-matrix-shard-plan.md` — the FULL plan (job graph, exact edits w/ file:line, parity proof, projections). THE spec.
- `reports/validate-profiling/20260702_181635+0200-validate-selfscan-profile.md` — the profile (skillaudit = 72% of the 209s self-scan, per-file-independent).

**NEXT ACTION:** await the background full-serial test (authoritative cross-shard gate; log at scratchpad/serial-test-run.log via bg task bxx8taaq1). On green → `publish.py --minor` → watch CI to green → MEASURE actual total-CI wall, gate ≥1.3× vs 3m41s → regen `.cpv-self-hashes.json` LAST → cache-cold `CPV_SCAN_CACHE=0 validate_plugin.py . --strict` self-validate = 0/0/0/0 → correction-protocol update to memory note `lesson_ci_validate_job_not_worth_optimizing` (free lever SUPERSEDES "not worth it") + update CLAUDE.md inventory + set `column: complete`.

**Load-bearing facts / gotchas:**
- skillaudit is per-file-independent (`cpv_skillaudit_native.py:4157-4189`); `scan_path` already delegates to `_scan_path_serial/_parallel(root, files)` which take explicit file lists → subset = sort-by-relative-posix + `files[k::N]`.
- PARITY HAZARD (handled): the RT4 exec-class merge (`validate_plugin.py:2450-2558`) dedups against existing skillaudit findings. So EACH shard must run BOTH skillaudit AND the exec-class merge over its subset (co-location guarantees a duplicate `(level,message,file,line)` is always in the same shard). The light job runs NEITHER security pass.
- rglob order is NOT stable across machines → MUST `sorted(..., key=relative_posix)` before slicing, else shards overlap/gap.
- Aggregate job MUST be named exactly `Validate` (branch ruleset required check); mirror the existing `Test` aggregate (ci.yml:107-127), fail-closed. Test aggregate name `Test` also preserved. NO ruleset change.
- Parity is GATED by a mandatory test: union(light + N shards) SUMMARY == single `validate_plugin.py . --strict` SUMMARY, byte-for-byte, on CPV-self AND a with-findings fixture. This test MUST be green before adoption — it catches any missed skillaudit interdependency.
- Ship the SAFE aggregate first (uv-sync + `ValidationReport.merge` → guaranteed identical counts); the lean stdlib-count aggregate is a later optimization once parity is locked.

**SUPERSEDED — do NOT carry forward:**
- ✗ "leave CI as-is / not worth optimizing" (memory note `lesson_ci_validate_job_not_worth_optimizing`) — that weighed ONLY the PAID larger-runner lever. This FREE matrix-shard lever clears the 1.3× gate (~1.7×). Update that note (correction protocol) after shipping.

## Change inventory (from the plan report §Change inventory)

| File | Edit | Size |
|---|---|---|
| `cpv_skillaudit_native.py` | `scan_path_subset(root,k,N)` (sort+slice, reuse `_scan_path_serial/_parallel`) + `run_skillaudit_scan_subset(...)` | +~25 |
| `validate_plugin.py` | `--skip-skillaudit` / `--skip-exec-class` guards @7436 + @2524 | +~6 |
| `validate_plugin.py` | `--security-shard k/N --json` emit-only mode (skillaudit + exec-merge over subset) | +~40 |
| `validate_plugin.py` | `--merge-report *.json` aggregate mode (load results, merge via `ValidationReport`, `exit_code_strict()`, print SUMMARY) | +~40 |
| `tests/` | MANDATORY parity test (union == single-run SUMMARY, CPV-self + with-findings fixture) | +1 file |
| `.github/workflows/ci.yml` | validate → {validate-light ∥ validate-shard[1..4] + `Validate` aggregate}; test matrix 4→8 | rewrite |

## Verification gate (measured, not assumed)
1. Parity test green (byte-for-byte union == single-run).
2. Cache-cold `CPV_SCAN_CACHE=0 validate_plugin.py . --strict` self-validate = 0/0/0/0 (unchanged).
3. Full LOCAL serial test run green (authoritative cross-shard gate).
4. `publish.py --minor`; watch CI to green; MEASURE actual total-CI wall; confirm ≥1.3× vs 3m41s (else the change is a no-op win → reconsider). regen `.cpv-self-hashes.json` LAST after CLAUDE.md/TRDD edits.

## Out of scope (phase-2, noted in report)
- Folding the other per-component-file validators into the shard matrix (only needed if the validate-light ~86s floor proves too high).
- Duration-based test split via `actions/cache` (committing `.test_durations` is blocked by CPV's own abs-path check on `/etc/passwd` test-ids).
- Mirroring the pattern into `generate_plugin_repo.py`'s emitted ci.yml (fleet template) — follow-up increment, same parity gate.
