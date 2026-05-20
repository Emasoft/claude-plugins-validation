---
trdd-id: dce5f014-1e59-433e-a732-31c96bf0afdd
title: v2.98.0 close issues 30 31 — lower batch thresholds, auto-dispatch from doctor & upgrade, faster tests
status: completed
created: 2026-05-20T13:34:03+0200
updated: 2026-05-20T14:11:33+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-dce5f014 — v2.98.0 multi-fix release

**Filename:** `design/tasks/TRDD-20260520_133403+0200-dce5f014-v298-issues-30-31-batch-thresholds-test-speed.md`

## Source

Five concurrent concerns from the v2.97.0 → v2.98.0 cycle:

1. **Issue #30** — `validate_plugin` emits WARNING "Possible broken backtick path" for backtick-quoted sibling-skill names that exist on disk.
2. **Issue #31** — `publish.py` G4 (test gate) auto-launches `dev-browser`/Chrome with no `plugin.json` opt-out.
3. **Test speed** — full suite is 26s wall (`-n auto`) but the slowest 15 tests account for ~110s of CPU time. publish.py needed a retry on v2.96.0 + v2.97.0 from an xdist isolation flake.
4. **Auto-dispatch batch** — `/cpv-batch-fix` currently requires manual invocation. Single-fixer returns `[BATCH_REQUIRED]` when above safe-ceiling and the user has to manually re-dispatch. User wants automatic batch dispatch from the main session.
5. **Lower thresholds + doctor/upgrade integration** — drop batch safe-ceiling so batch mode triggers earlier; wire the batch protocol into the `/cpv-doctor` fix flow and the `/cpv-upgrade-plugin` migration flow.

## Phase 1 — Issue fixes (mechanical)

### Issue #30 fix

`scripts/cpv_validation_common.py:6850` backtick path resolver currently tries
two locations:
1. `md_file.parent / clean_path`
2. `plugin_root / clean_path`

A path like `amvcp-modal-comments/SKILL.md` doesn't match either; the WARNING
fires even though `plugin_root / "skills" / clean_path` resolves. Fix: add
the `plugin_root / "skills" / clean_path` resolution AND
`plugin_root / "skills" / clean_path / "SKILL.md"` (when clean_path is a
bare slug) before falling through.

### Issue #31 fix (revised — iron-rule-preserving)

**Original proposal in the issue:** `cpv.gates_to_skip: ["G4"]` to skip
the test gate.

**Rejected** because it violates the iron rule "no plugin with issues
must be pushed on GitHub EVER". Skipping tests defeats the publish
pipeline's purpose.

**Actual root cause** the issue describes: a pytest run that uses
`dev-browser` / Playwright spawns `Chrome for Testing` processes. If
the test code or fixtures forget to close pages, the browser
processes leak and accumulate — eventually exhausting resources and
crashing either the browser or the machine.

**Fix (Layer 1 — safety net):** add a browser-orphan cleanup wrapper
around the pytest invocation in both `scripts/publish.py::stage_run_tests`
(CPV's own) and `scripts/generate_plugin_repo.py::gen_publish_py`
(consumer-plugin template).

Mechanics:
1. Before pytest: snapshot the set of PIDs whose command line matches a
   narrow browser signature (`Chrome for Testing`, `headless_shell`,
   `Chromium.app/Contents`, `chromium-browser`, `/playwright/`,
   `playwright-core`). This is the **baseline**.
2. Run pytest unconditionally (no skip, no opt-out — iron rule honored).
3. After pytest returns (success OR failure path): snapshot again. Any
   PID in the post-snapshot but NOT the baseline is an orphan spawned
   during the test run. SIGTERM, wait 1.5s, SIGKILL stragglers.

Why baseline-diff: the maintainer's own daily browser is in the baseline
and therefore NEVER killed, even if its command line accidentally
matches a signature. Only NEW processes that came into existence during
pytest are candidates.

The implementation lives in `_snapshot_browser_pids()` and
`_cleanup_browser_orphans(baseline)` helpers shared between
`publish.py` and the template.

## Phase 2 — Test speed

### Slow-test marker

Add `@pytest.mark.slow` to the 15 slowest tests (mostly URL retry, security
scanner, scoring). Default `pytest` skips slow tests; CI runs with
`-m "not slow or slow"` (no filter) for full coverage. publish.py's Gate 2
runs without slow tests by default; the `--full-tests` flag opts in for
release verification.

### xdist flake fix

`tests/test_cpv_lint_engine.py::TestLintRepoOrchestration::test_language_subset_filter`
flaked under xdist load on v2.96.0 + v2.97.0 publishes. Likely cause: a
sibling test in the same worker mutates the `_DISPATCH` global state and
the `patch.dict(... clear=False)` doesn't restore it before this test
runs. Fix: switch to `clear=True` for the duration of this test's patch
(or use a fresh dict copy).

## Phase 3 — Batch threshold + auto-dispatch + doctor/upgrade wire-in

### Lower safe-ceilings

Drop per-model defaults from the current heuristic (30-40 for bare
opus/sonnet, 100-150 for [1m]) to ~15-25 and ~50-75 respectively. Lower
ceilings mean batch mode kicks in earlier — better context safety, finer
parallelism, faster wall-clock for medium-finding-count plugins.

Implementation:
- `agents/plugin-fixer.md:106-110` routing table updated
- `scripts/cpv_batch_planner.py::DEFAULT_SHARD_SIZE` lowered (currently 30 → 15)

### Auto-dispatch batch from orchestrators

When a `plugin-fixer` dispatch returns the `[BATCH_REQUIRED]` line, the
calling orchestrator (the main session running `/cpv-main-menu`,
`/cpv-doctor`, or `/cpv-upgrade-plugin`) automatically:

1. Reads the `[BATCH_REQUIRED] <N> findings exceed single-agent capacity (safe-ceiling=<C>). Run /cpv-batch-fix <plugin-root>` line
2. Runs the batch planner via Bash (`scripts/cpv_batch_planner.py <plugin>`)
3. Dispatches N parallel `plugin-fixer` agents in `batch_shard` mode in a single message
4. Runs the aggregator after all shards return
5. Reports the consolidated outcome

User no longer has to type `/cpv-batch-fix` — it's automatic when the threshold is crossed.

### Doctor + upgrade integration

- `agents/cpv-doctor-agent.md` "Fix all findings" follow-up: when the dispatched plugin-fixer returns `[BATCH_REQUIRED]`, orchestrator auto-routes to batch-fix protocol.
- `/cpv-upgrade-plugin` flow: same auto-route.

## Acceptance criteria

- Issue #30 fixed + regression test
- Issue #31 fixed in BOTH CPV publish.py AND gen_publish_py template + regression test
- 15 slowest tests marked `@pytest.mark.slow`
- xdist flake fixed; publish.py Gate 2 robust under load
- DEFAULT_SHARD_SIZE lowered
- plugin-fixer.md safe-ceiling table lowered
- cpv-main-menu, cpv-doctor-agent, cpv-upgrade-plugin all auto-dispatch batch when threshold crossed
- Full suite still passes (5421+ tests)
- Self-scan 0/0/0/0
- v2.98.0 published, both issues closed

## File touch list

| File | Action |
|---|---|
| `scripts/cpv_validation_common.py` | Add `skills/<slug>` + `skills/<slug>/SKILL.md` resolution before WARNING |
| `scripts/publish.py` | Add `--skip-gate` CLI + `_gate_is_skipped` helper |
| `scripts/generate_plugin_repo.py` | Same shape in gen_publish_py template |
| `tests/test_validate_md_urls.py` + 14 others | Add `@pytest.mark.slow` markers |
| `tests/test_cpv_lint_engine.py` | Switch to `clear=True` in patch.dict |
| `pytest.ini` or `pyproject.toml` | Register `slow` marker + default filter |
| `scripts/cpv_batch_planner.py` | DEFAULT_SHARD_SIZE 30 → 15 |
| `agents/plugin-fixer.md` | Safe-ceiling table lowered |
| `agents/cpv-doctor-agent.md` | Auto-batch dispatch flow |
| `commands/cpv-doctor.md` | Same |
| `commands/cpv-upgrade-plugin.md` (NEW or existing) | Same |
| `tests/test_issues_30_31_batch.py` | NEW — regression coverage for all 5 phases |
| `design/tasks/TRDD-...md` | THIS file |
