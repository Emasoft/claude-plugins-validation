# Parallel scanning in the canonical pipeline (v2.103.0+)

## Table of contents

- [Performance summary](#performance-summary)
- [Environment knobs (disable selectively for debugging)](#environment-knobs-disable-selectively-for-debugging)
- [Scaffolded plugins (created via `create-plugin` / `setup-plugin-repo`)](#scaffolded-plugins-created-via-create-plugin--setup-plugin-repo)
- [Batch commands (`cpv-batch-*`)](#batch-commands-cpv-batch-)
- [Remote validation (`cpv` remote-mode + scaffolded `publish.py`)](#remote-validation-cpv-remote-mode--scaffolded-publishpy)
- [When to disable parallelism](#when-to-disable-parallelism)
- [See also](#see-also)

Every CPV validator and the orchestrator that drives them runs file scans
in parallel by default. Scaffolded plugins inherit this for free because
their `ci.yml`, `release.yml`, and `publish.py` G3 gate all call CPV
remotely via `uvx --from git+https://github.com/Emasoft/claude-plugins-validation`,
which always resolves to master and therefore always has the latest
parallelism active.

## Performance summary

`validate_plugin .` against the CPV repo itself (≈ 600 files):

| Configuration | Wall time | Speedup |
|---|---|---|
| Pre-v2.103.0 baseline (cProfile-measured) | 197.9 s | 1.00× |
| v2.103.x (parallel default) | ≈ 17 s | **~11.6×** |

Component breakdown of the 180 s saved:

| Bottleneck | Pre | Post | Mechanism |
|---|---|---|---|
| `cpv_skillaudit_native.scan_content` | 151.6 s (76 %) | ~13 s | `ProcessPoolExecutor` per-file fan-out |
| `cpv_lint_engine.lint_repo` | 29.1 s (15 %) | ~6 s | `ThreadPoolExecutor` over 15 languages |
| `gitignore_filter.from_lines` (1.7 M calls) | 32.4 s (16 %) | ~0 s | LRU-bounded `PathSpec` cache keyed by `(path, mtime_ns, size)` |
| Per-validator scans (security/skill/hook/…) | ~10 s (3-5 %) | ~3 s | `parallel_scan` harness in `cpv_parallel_runner` |
| Orchestrator dispatch | < 1 % | ~1 s | `ThreadPoolExecutor` over independent validator phases |

Local benchmark script (records 3-run median + writes a markdown report
under `reports/menu-migration-planning/`):

```bash
uv run python scripts/cpv_validate_benchmark.py --runs 3 --clear-cache
```

## Environment knobs (disable selectively for debugging)

Every parallel path has a `CPV_*_PARALLEL` opt-out env var. Setting it
to `0` falls back to the deterministic serial code path while preserving
identical output. Useful when:

- A stack trace's traceback is opaque because it crossed worker boundaries
- You want to bisect a finding to its emitting file in source order
- A CI runner with < 4 cores would otherwise spend more on spawn overhead
  than it saves on parallelism

| Variable | Default | Effect when set to `0` |
|---|---|---|
| `CPV_ORCHESTRATOR_PARALLEL` | parallel | `validate_plugin.py` runs every sibling validator serially in source order |
| `CPV_SECURITY_PARALLEL` | parallel | `validate_security.py` scans every file serially (≈ 36× slower on large repos) |
| `CPV_SKILLAUDIT_PARALLEL_THRESHOLD` | `8` | Forces serial when scan size < threshold; set `1` to always parallelize |
| `CPV_HOOK_PARALLEL` | parallel | `validate_hook.py` per-hook workers run serially |
| `CPV_CACHE_PARALLEL` | parallel | `validate_cache.py` per-component workers run serially |
| `CPV_XREF_PARALLEL` | parallel | `validate_xref.py` per-file extractors run serially |
| `CPV_LINT_PARALLEL` | parallel | `cpv_lint_engine.lint_repo` runs each language linter serially |

None of these knobs need to be set in normal use — the parallel default
is correct on every host CPV supports (macOS / Linux / GH Actions
ubuntu-latest 4-core / dev boxes with 8+ cores). The knobs exist for
debug-time triage, not steady-state operation.

## Scaffolded plugins (created via `create-plugin` / `setup-plugin-repo`)

New plugins emit `ci.yml` + `release.yml` that pin
`actions/checkout@<sha>  # v6.0.2` and `astral-sh/setup-uv@<sha>  # v8.1.0`,
then invoke CPV remotely:

```yaml
- name: Run plugin validation (strict)
  run: |
    uvx --from git+https://github.com/Emasoft/claude-plugins-validation \
      validate_plugin . --verbose --strict
```

Because the `uvx --from git+…` resolver always pulls master, every
scaffolded plugin's CI inherits the latest CPV parallelism without any
template re-scaffold. The 4-core ubuntu-latest GitHub Actions runner hits
the 1.3× per-component minimum-speedup floor the parallelism tests pin
(`test_parallel_is_at_least_2x_faster_on_multi_core` tiers by cpu_count:
≥ 4 → 1.3×; ≥ 8 → 2.0×; ≥ 12 → 3.0×).

## Batch commands (`cpv-batch-*`)

The 10 batch commands (`cpv-batch-validate`, `cpv-batch-fix`,
`cpv-batch-security-audit`, `cpv-batch-caching-optimize`, etc.) run
`cpv_batch_orchestrator.py` which dispatches one specialised work agent
per shard (default shard size 15 plugins). Inside each shard the work
agent calls the per-plugin validator (e.g. `validate_plugin.py` for
batch-validate, `validate_security.py` for batch-security-audit), which
itself is parallelized. The two layers compose: N shards × per-validator
ProcessPool fan-out → effectively `(host_cores) × (shard_count)` workers
contributing simultaneously.

This is why the v2.91.0 batch-fix architecture (dispatching parallel
plugin-fixer agents from a single main-session message) survives the
v2.103.x parallelism rewrite unchanged — the per-plugin validator under
each fixer just got 11.6× faster, and the outer fan-out it was always
designed for still works.

## Remote validation (`cpv` remote-mode + scaffolded `publish.py`)

`scripts/publish.py` G3 (validate gate) and the GitHub Actions Validate
job both call CPV's validator. Both code paths invoke `validate_plugin`
through the same Python entry-point — the parallelism is on by default
in both. No per-call configuration needed; the worker pool is sized to
`os.cpu_count() - 1` automatically (1 core left for the orchestrator).

For Layout A (separate marketplace repo) the marketplace's
`receive-notification.yml` workflow re-validates the incoming plugin via
the same remote-uvx entrypoint, so the marketplace gate ALSO benefits.

## When to disable parallelism

In production: never. The 11.6× speedup is the point of the rewrite.

In debugging / triage:

- `CPV_ORCHESTRATOR_PARALLEL=0` if you want a stable findings order to
  bisect against (serial → source-order; parallel → input-order
  preserved but interleaved across siblings).
- A specific `CPV_<X>_PARALLEL=0` to isolate one validator's contribution
  to a wall-time regression.

Always re-run with parallelism back ON before publishing — the CI gates
expect the production speedups.

## See also

- `scripts/cpv_parallel_runner.py` — the shared `ProcessPoolExecutor`
  harness (`parallel_scan`, `parallel_scan_aggregated`, `ScanResult`).
- `scripts/cpv_validate_benchmark.py` — A10's three-phase benchmark
  (all-serial / inner-parallel-only / fully-parallel).
- `tests/test_skillaudit_native_parallelism.py` — pins the per-tier
  speedup floors so a future refactor can't silently regress them.
- The 13-agent task #384 history: harness (A1) + per-validator parity
  (A2-A9) + orchestrator + benchmark (A10) + hot-path agents (B1
  skillaudit, B2 lint_engine, B3 gitignore cache).
