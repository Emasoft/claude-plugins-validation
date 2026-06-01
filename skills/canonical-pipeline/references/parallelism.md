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

## v2.104.0 perf layers: content-hash cache, binary scanning, RE2 hybrid matcher

The v2.103.0 fan-out (this doc's primary subject) gave an ~11.6×
win by parallelising work. v2.104.0 adds three orthogonal wins
layered on top — they compose with the fan-out (cache check happens
BEFORE worker dispatch; RE2.Set and binary scanner both run INSIDE
the worker). The three subsections below summarise them; the full
specs live in `scan-cache.md` and `binary-scanning.md`.

### Content-hash cache (v2.104.0+)

Repeat runs of `validate_plugin` against the same source tree do not
need to re-scan files that have not changed. The v2.104.0 content-hash
cache (`scripts/cpv_scan_cache.py`) skips the entire SkillAudit
pipeline for any file whose `(SHA-256, catalog_hash, scanner_version)`
tuple is already on disk. Expected effect on the CPV repo: a warm
second run drops from ~17 s to < 0.5 s — roughly a 50× speedup on
top of the v2.103.0 parallelism win.

Storage path is resolved via a 5-level fallback chain (CLI flag →
`CPV_SCAN_CACHE_DIR` env → `XDG_CACHE_HOME` → `~/.cache/cpv/scan-cache/`
on Unix → `%LOCALAPPDATA%\cpv\scan-cache\` on Windows). See
`scan-cache.md` for the full chain, the SQLite schema, the WAL
concurrency mode, and the `chmod 0700` security stance.

The triple-key invalidation is the entire reason a content-hash cache
is correctness-safe rather than just a performance hack:

| Key | Invalidates when… | Why required |
|---|---|---|
| `content_hash` | file bytes change | same file, different content → different result |
| `catalog_hash` | `scripts/rules/skillaudit_patterns.json` changes | new patterns → must re-scan |
| `scanner_version` | `cpv_skillaudit_native` semver bumps | logic change (e.g. new context classifier) → must re-scan |

Env-var knobs:

| Variable | Default | Effect |
|---|---|---|
| `CPV_SCAN_CACHE` | `1` (on) | Set `0` to disable both lookup and store; every run is cold |
| `CPV_SCAN_CACHE_DEEP` | `0` (off) | Set `1` to force re-scan AND compare against cached result; logs a WARNING on disagreement (cache-correctness audit) |
| `CPV_SCAN_CACHE_DIR` | unset | Override the path-resolution chain |
| `CPV_SCAN_CACHE_MAX_BYTES` | `500 MB` | LRU eviction triggers above this size |

Scaffolded plugins inherit the cache for free via `actions/cache@v4`
in `ci.yml`, keyed on `hashFiles('plugin.json', 'commands/**',
'skills/**', 'agents/**', 'hooks/**', '.mcp.json')` with a partial
`restore-keys` fallback. See `scan-cache.md` for the full GitHub
Actions integration recipe.

Full reference (table of contents):

- Storage path — 5-level resolution chain
- SQLite schema and triple-key PK
- Concurrency under `ProcessPoolExecutor` (WAL mode, `INSERT OR IGNORE`
  on race)
- Eviction policy (LRU by `cached_at`)
- CI integration (`actions/cache@v4` recipe for scaffolded plugins)
- Troubleshooting (clearing the cache, deep-mode audit, doctor
  hit-rate report)
- TRDD-40f46a83 — the v2.104.0 design spec

### Binary scanning strategy (v2.104.0+)

Through v2.103.0, SkillAudit explicitly skipped any file detected as
binary. That was the textbook bury-your-head-in-the-sand security
stance — real malicious skills routinely embed payloads in PNG
metadata, ZIP comments, WASM data sections, PDF streams, etc.
v2.104.0 replaces "is binary → skip" with "is binary → extract
strings → scan extracted strings through the same matcher pipeline".

The **never-skip principle**: every file lands on the matcher
eventually. Binary files take a different extraction path on the way
in, but the pattern catalog and severity rollup are identical. A
malicious string embedded in a PNG `tEXt` chunk fires the same
findings it would fire in a `.sh` script.

Detection + pipeline:

```text
file_bytes
   │
   ▼
is_binary?  (UTF-8/UTF-16 BOM → text; else null byte in first 8 KB → binary)
   ├── no  ──→ text-path (unchanged from v2.103.0)
   │
   └── yes ──→ extract_ascii(min_run=6) + extract_utf16(min_run=6)
                        │
                        ▼
                  scan_content over each extracted string
                        │
                        ▼
                  decode_chain (depth ≤ 3; base64 / hex / gzip / zlib → re-scan;
                                bounded at 100 MB per decode output
                                to defuse decode-bombs)
                        │
                        ▼
                  scan_content over each decoded string
                        │
                        ▼
                  findings prefixed "[extracted from binary] "
```

Coverage gain vs the v2.103.0 text-only path: every embedded payload
in PNG / ZIP / WASM / PDF / ICO / WOFF2 / TAR / etc. is now subject
to the same pattern sweep that text files always were. Wall-clock
cost: a small regression on plugins containing many binaries (an
extra few hundred ms on the CPV repo). Net release-time benchmark:
still well under the 7 s cold-run target because the RE2 win swamps
the binary-scan cost.

Env-var knob:

| Variable | Default | Effect |
|---|---|---|
| `CPV_BINARY_SCAN` | `1` (on) | Set `0` to revert to v2.103.0 skip-binaries behaviour; **debug-only**, NEVER for production — leaves the embedded-payload hole open |

Full reference (table of contents):

- Detection heuristic (UTF-8/UTF-16 BOM → text; else null byte in the
  first 8 KB → binary; no printable-ratio check)
- String extractors (`extract_ascii_strings`, `extract_utf16_strings`,
  `min_len=6`)
- Decode chain (base64 / hex / gzip / zlib, bounded by `max_depth=3` and
  a 100 MB per-decode-output cap)
- Per-format payload locations (PNG `tEXt` / `iTXt` / `eXIf`, ZIP
  comments, etc.) — non-exhaustive list of where malicious authors
  hide payloads
- The never-skip principle (why `CPV_BINARY_SCAN=0` is not a
  legitimate production toggle)
- TRDD-40f46a83 — the v2.104.0 design spec

### RE2 hybrid matcher (v2.104.0+)

The v2.103.0 hot path applies the full catalog of Python regex patterns
sequentially to every file's text. That is O(N_patterns × N_chars)
per file. v2.104.0 replaces the sequential sweep with an RE2.Set
automaton: every RE2-compatible pattern is compiled into a single
DFA that matches all of them in one linear pass over the input.
Expected effect on the CPV repo's cold run: 5-15× speedup on the
regex phase, bringing the wall-clock regex cost from ~13 s to < 2 s.

Why RE2: **RE2 is an algorithm, not an implementation language**.
We adopt it for two correctness properties Python `re` does not
have:

1. **Linear-time guarantee.** RE2 is a DFA-based matcher with
   provably linear cost in the input length, regardless of pattern
   complexity. Python `re` is a backtracking NFA — adversarial
   inputs can trigger exponential blowup (catastrophic backtracking).
   Although our catalog is curated and ReDoS-safe today, RE2's
   guarantee removes that risk class entirely.
2. **Single-pass automaton.** RE2.Set lets us compile N patterns into
   one DFA that returns the union of all matches in one linear
   pass. Python `re` requires N separate scans. On a catalog of this
   size (486 patterns as of v2.104.0) the asymptotic difference is
   substantial.

The implementation language (C++ underneath the `google-re2` Python
wheel) is incidental. If a pure-Python DFA matcher with the same
guarantees existed, we'd use it instead.

Fallback chain (never skip a pattern):

```text
HybridMatcher.scan(text):
    if google-re2 is importable AND CPV_RE2_DISABLE != 1:
        set_matches = re2_set.match(text)        # RE2-compatible subset
        py_matches  = [p.search(text) for p in py_only_patterns]  # incompatible subset
        return union(set_matches, py_matches)
    else:
        return [p.search(text) for p in all_patterns]   # pure-Python fallback
```

The compatibility partition is decided at runtime, per pattern, inside
`scripts/cpv_re2_matcher.py::HybridMatcher`: each catalog pattern is fed
to `re2.Set.Add`, and any pattern RE2 refuses (lookaround, backrefs,
syntax it deems incompatible) is moved to the Python `re` fallback list
for that matcher instance. The matcher rebuilds the partition every time
it is constructed from the live catalog, so a new pattern is routed
correctly the first time it ships — no runtime lookup of a precomputed
table is involved.

A committed audit snapshot, `scripts/rules/re2_compatibility.json`,
records the expected classification for every pattern alongside a
`_source_sha256` of `skillaudit_patterns.json`. It is documentation /
test-fixture only — the matcher does not read it. The drift guard is the
pytest `tests/test_rules_re2_compat.py`, which fails if `_source_sha256`
no longer matches the live catalog (forcing the snapshot to be
regenerated) and re-asserts that every "compatible" pattern still
compiles under RE2 and every "incompatible" one still does not.

A pattern is NEVER skipped silently. Every pattern either runs
through RE2.Set (compatible subset) OR through Python `re`
(incompatible subset OR all patterns when google-re2 is absent).
Coverage parity is a hard test invariant.

Optional install:

- `pip install -e .[performance]` (extra in `pyproject.toml`)
  ships `google-re2 >= 1.1` explicitly
- `scripts/cpv_install_scanners.py` runs a best-effort
  `pip install google-re2` on first invocation; failure logs a
  WARNING and falls back to pure-Python re (no error raised)
- Default `pip install -e .` (no extra) works with the pure-Python
  fallback — no mandatory dependency

Env-var knob:

| Variable | Default | Effect |
|---|---|---|
| `CPV_RE2_DISABLE` | `0` (RE2 on when `google-re2` is installed; degrades gracefully if not) | Set `1` to force the pure-Python path even when `google-re2` is present (debug / parity testing) |

Full reference: see `binary-scanning.md` (sibling doc) and
TRDD-40f46a83 (the v2.104.0 design spec) for the
`HybridMatcher` API and the `re2_compatibility.json` schema. The
`tests/test_rules_re2_compat.py` suite is the catalog-drift guard (it
checks `_source_sha256` against the live catalog) and the parity check
that compiles every catalog pattern under RE2 to confirm its recorded
compatible/incompatible classification still holds.

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
