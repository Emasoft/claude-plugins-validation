# Scan cache in the canonical pipeline (v2.104.0+)

## Table of contents

- [Overview](#overview)
- [Storage path resolution chain](#storage-path-resolution-chain)
- [SQLite schema](#sqlite-schema)
- [Security invariants](#security-invariants)
- [Env-vars](#env-vars)
- [GitHub Actions integration](#github-actions-integration)
- [Pruning (age + LRU)](#pruning-age--lru)
- [Introspection helpers](#introspection-helpers)
- [When to invalidate manually](#when-to-invalidate-manually)
- [What the cache does NOT cache](#what-the-cache-does-not-cache)
- [Performance characteristics](#performance-characteristics)
- [See also](#see-also)

## Overview

The scan cache is a content-hash-keyed SQLite result cache for per-file
skillaudit findings. On a clean repo the first run computes the sha256
of every scanned file and stores
`(content_hash, catalog_hash, scanner_version, file_ext) → findings_json`
rows. Every subsequent invocation recomputes the file hash and, on a
cache hit, skips the LLM-pattern scan entirely and replays the persisted
findings. This is what gets repeat `validate_plugin .` invocations
against the CPV repo from ≈ 17 s (cold) down to < 1 s (warm, ≥ 90 % hit
rate) — roughly a **50× speedup on repeat runs**.

The cache is purely additive: a MISS always falls through to the real
scanner and writes the result; a HIT only happens when all four keys
(content + catalog + scanner version + file extension) match exactly.
There is no version that produces stale findings — bumping
`scripts/rules/skillaudit_patterns.json` or
`cpv_skillaudit_native.__version__` invalidates every prior entry by
construction. The file extension is part of the key because the
skillaudit scanner picks its context classifier from the file suffix, so
the same bytes can produce different verdicts under `.py` vs `.md` vs
`.json` — without `file_ext` in the key the first-scanned extension would
poison every other extension's lookup.

## Storage path resolution chain

The cache picks the first writable path from this priority order, falling
back silently to disabled-mode if none are writable:

1. `$CPV_SCAN_CACHE_DIR/scan-cache-v2.sqlite` — explicit user override (highest priority)
2. `$CLAUDE_PLUGIN_DATA/scan-cache-v2.sqlite` — Claude-Code-managed per-plugin data dir; survives plugin reinstalls / updates
3. `~/.claude/plugins/data/claude-plugins-validation/scan-cache-v2.sqlite` — hard-coded fallback for the CC default plugin data location
4. `$XDG_CACHE_HOME/cpv/scan-cache-v2.sqlite` (or `~/.cache/cpv/scan-cache-v2.sqlite` if `XDG_CACHE_HOME` unset) — XDG-compliant user cache dir
5. `$RUNNER_TEMP/cpv-scan-cache-v2.sqlite` when `GITHUB_ACTIONS=true` — ephemeral CI runner location (paired with `actions/cache` — see [GitHub Actions integration](#github-actions-integration))

The `-v2` stem is a *schema* version (not the product version): it was
bumped when `file_ext` joined the PRIMARY KEY, so any stale 3-key
`scan-cache.sqlite` left by an older CPV is simply ignored (a cache MISS
is always safe) rather than migrated.

If every candidate is unwritable (permissions error, read-only FS,
out-of-space), the cache is disabled for that process. Validation
continues normally; the user sees no error and no perf regression vs.
pre-v2.104.0 behaviour.

## SQLite schema

A single table with a composite quadruple-key for invalidation safety
(mirrors `_SCHEMA_SQL` in `scripts/cpv_scan_cache.py`):

```sql
CREATE TABLE IF NOT EXISTS scan_cache (
    content_hash    TEXT NOT NULL,   -- sha256 of file bytes (lowercase hex)
    catalog_hash    TEXT NOT NULL,   -- sha256 of scripts/rules/skillaudit_patterns.json
    scanner_version TEXT NOT NULL,   -- cpv_skillaudit_native.__version__
    file_ext        TEXT NOT NULL DEFAULT '', -- lowercased suffix, e.g. '.py' (selects the classifier)
    findings_json   TEXT NOT NULL,   -- JSON-encoded list of findings
    cached_at       INTEGER NOT NULL,-- epoch seconds; prune key (age + LRU)
    PRIMARY KEY (content_hash, catalog_hash, scanner_version, file_ext)
);
CREATE INDEX IF NOT EXISTS idx_cached_at ON scan_cache (cached_at);
```

The quadruple-key guarantees that a single file's findings entry is
immediately stale on **any** of:

- File contents changed (`content_hash` differs)
- Pattern catalog changed (`catalog_hash` differs, e.g. after rule
  catalog regen)
- Scanner module bumped (`scanner_version` differs, e.g. after an
  algorithm fix that would change findings for unchanged input)
- File extension changed (`file_ext` differs — the same bytes scanned as
  a different file type select a different context classifier, so they
  must not share a cache bucket)

## Security invariants

- The SQLite file is created with mode `0o600` (owner read/write only)
  and its parent directory with mode `0o700`. Other users on the same
  host cannot read the cached findings.
- Database corruption (truncation, page-checksum mismatch,
  unparseable header) triggers an automatic wipe-and-recreate. The
  next run starts from a cold cache. No findings are lost — every
  recreated row is recomputed by the live scanner.
- A cache MISS is always safe — the real scanner runs and emits the
  authoritative findings. There is no code path that emits cached
  findings without all four keys matching.
- The quadruple-key invalidation prevents stale findings on catalog
  upgrade (new rule lands, every prior entry is invalidated) and on
  scanner upgrade (algorithm changes, every prior entry is
  invalidated). Users cannot accidentally suppress new rules by
  retaining old cache entries. The `file_ext` key additionally prevents
  a cross-extension verdict collision (a CVE-class invariant: the same
  bytes scanned as a different type get a different classifier).

## Env-vars

| Variable | Default | Effect |
|---|---|---|
| `CPV_SCAN_CACHE` | enabled | Set to `0` to disable the cache entirely for this process (forces every file through the real scanner; restores pre-v2.104.0 wall time) |
| `CPV_SCAN_CACHE_DEEP` | off | Set to `1` to ignore cache hits and write through — every file is scanned fresh AND its entry is refreshed. Used by the publish-time integrity gate to confirm cached findings still match live findings |
| `CPV_SCAN_CACHE_DIR` | (chain) | Override the storage directory. The cache file lands at `<dir>/scan-cache-v2.sqlite`. Highest-priority path in the resolution chain |

None of these env vars need to be set in normal use — the default
behaviour is correct everywhere. They exist for debugging, CI-runner
hardening, and the deep-write integrity gate.

## GitHub Actions integration

Scaffolded plugins already include this block in their `ci.yml` (see
`gen_ci_yml` template) so new plugins inherit warm-cache behaviour on
every push without any extra work:

```yaml
- uses: actions/cache@v4
  with:
    path: ~/.cache/cpv
    key: cpv-scan-cache-${{ runner.os }}-${{ hashFiles('**/.cpv-self-hashes.json') }}
    restore-keys: |
      cpv-scan-cache-${{ runner.os }}-
```

How the key composes:

- `runner.os` (e.g. `Linux`, `macOS`) — keep caches per-OS so a Linux
  sha256 result is never replayed on macOS where the path-canonicalization
  differs.
- `hashFiles('**/.cpv-self-hashes.json')` — the integrity manifest
  every scaffolded plugin ships. When the manifest changes (which it
  does on every CPV-template update), the cache key changes and the
  next CI run starts cold. This piggybacks on an existing per-plugin
  rotation key, so no new state needs to be tracked.
- `restore-keys` falls back to the most recent same-OS cache when the
  exact key misses (e.g. on the first push after a manifest bump).
  Partial hits still skip 80-95 % of file scans.

Legacy plugins that pre-date v2.104.0 can paste the same block into
their `ci.yml` and immediately benefit. The `cache` step works without
the CPV side knowing it exists — CPV just sees a populated `~/.cache/cpv`
on cache-restore and a written `~/.cache/cpv` after the validate step.

## Pruning (age + LRU)

`prune_cache(max_age_days=180, max_entries=100_000)` runs two passes, in
order:

- **Age pass** — `DELETE FROM scan_cache WHERE cached_at < (now - 180 d)`.
  Skipped entirely when `max_age_days <= 0` (so a 0 cutoff can never wipe
  the table by accident).
- **LRU pass** — if the surviving row count still exceeds
  `max_entries` (**100 000**, ≈ 30-50 MB on disk for typical findings
  payloads), delete the oldest `cached_at` rows until the count is back
  at the cap. The `idx_cached_at` index makes this an indexed scan, not a
  full-table sort.

Both keys are `cached_at` (the write timestamp) — the cache does not
track a separate last-hit time, so "LRU" here means least-recently-
*written*. The function is best-effort: any sqlite error returns the
counts pruned so far without wiping anything.

The ai-maestro-janitor (separate plugin) also exposes a callable for
disk-pressure pruning — when the janitor sees < 5 GB free on `$HOME`'s
filesystem, it can shrink the CPV cache below the normal cap. This is
opt-in via the janitor's config; CPV does not require janitor to be
installed.

## Introspection helpers

`scripts/cpv_scan_cache.py` exposes three top-level functions for
introspection and maintenance (it is a library module, not a standalone
CLI — there is no `cpv-scan-cache` console command):

| Function | Effect |
|---|---|
| `cache_stats()` | Returns a diagnostic dict (see below) without mutating the cache |
| `reset_cache()` | Drops and recreates the SQLite file; the next validator run rebuilds from a cold cache |
| `prune_cache(max_age_days=180, max_entries=100_000)` | Runs the two-pass age+LRU prune (see [Pruning](#pruning-age--lru)) and returns `{"removed_age": N, "removed_lru": M}` |

`cache_stats()` returns a fully-populated dict even when caching is
disabled, so callers can index into it unconditionally:

```python
{
    "path": "/home/user/.cache/cpv/scan-cache-v2.sqlite",  # or None if disabled
    "entries": 18432,        # row count
    "size_bytes": 13002752,  # file size on disk
    "oldest_at": 1719500000, # epoch seconds of the oldest entry, or None
    "hit_count": 0,          # placeholder — live counters are out of scope
    "miss_count": 0,         # placeholder — same caveat
}
```

`hit_count` / `miss_count` are deliberate placeholders (always `0`):
tracking a live hit rate would need a metadata table and is out of scope
for the current single-table design.

## When to invalidate manually

You should rarely need to. The quadruple-key in
[SQLite schema](#sqlite-schema) handles every automatic invalidation
case:

- **Catalog bump** (`scripts/rules/skillaudit_patterns.json` changed) — automatic
- **Scanner version bump** (`cpv_skillaudit_native.__version__` changed)
  — automatic
- **File content changed** — automatic (content hash differs)
- **File extension changed** — automatic (`file_ext` differs)

Manual invalidation is only justified for:

- **Debugging a "this finding shouldn't be cached" suspicion** — call
  `reset_cache()` (or just delete the SQLite file) and re-run; if the
  finding still appears, it was not a cache bug
- **Migrating between machines** — the cache is per-host by design;
  copying the file is supported but not necessary, the cache just
  rebuilds on the new host
- **Disk pressure** — `prune_cache()` or a full `reset_cache()`

There is **no** correctness reason to invalidate manually. Bumping CPV
through the normal release pipeline cascades a scanner-version bump,
which auto-invalidates every entry; users on a stable CPV version with
an unchanged catalog and unchanged file get the cached result and
should.

## What the cache does NOT cache

Scope is deliberately narrow: only the per-file skillaudit findings get
cached. Everything else runs fresh on every invocation, including:

- **Cross-file checks** — `validate_xref`, `validate_canonical_pipeline_drift`,
  `validate_marketplace`, dependency-graph walks. These are
  whole-repo-shape questions; their answer depends on files the
  per-file cache key cannot see.
- **Manifest validation** — `plugin.json`, `marketplace.json`,
  `.mcp.json` parsing and schema checks. Cheap enough that caching
  would add complexity for no measurable win.
- **Lint engine** (`cpv_lint_engine`) — already parallelized to ≈ 6 s
  via `ThreadPoolExecutor` over 15 languages (see
  [parallelism.md](parallelism.md)). The wall-time it would save is
  smaller than the cache-bookkeeping overhead.
- **Hook validation** (`validate_hook`) — already parallelized; per-hook
  workers complete in milliseconds; no caching needed.
- **GitHub integrity gate** — must observe live HEAD by definition;
  never cached.
- **CLI invocation, plugin discovery, manifest globbing** — outside
  the scan loop entirely.

In short: the cache short-circuits the single dominant cost
(`cpv_skillaudit_native.scan_content`, which was 76 % of pre-v2.103.0
wall time and ≈ 13 s of the v2.103.x ≈ 17 s baseline). Everything
else continues to run on every invocation.

## Performance characteristics

| Scenario | Wall time | Notes |
|---|---|---|
| Cold cache (first run, or after `reset`) | ≈ baseline (~17 s for CPV repo) | Identical to v2.103.x; cache write overhead is negligible relative to scan time |
| Warm cache, ≥ 90 % hit rate (typical repeat run, no file changes) | < 1 s | ~50× speedup vs. cold |
| Warm cache, partial hit (after editing N files) | ≈ baseline × (changed_files / total_files) | Linear in the changed-files fraction; editing 30 % of files runs in ≈ 30 % of cold time |
| `CPV_SCAN_CACHE=0` (disabled) | ≈ baseline (~17 s) | Restores pre-v2.104.0 behaviour for debugging |
| `CPV_SCAN_CACHE_DEEP=1` (deep-write) | ≈ baseline × 1.05 | Same as cold plus a small SQLite write overhead per file; used by integrity gate |

The cache pays for itself on the second invocation of the same scan
against the same repo. For CI runs against a constantly-changing repo
the typical hit rate plateaus at 85-95 % (only the touched files
miss), so wall time tracks the diff size rather than the repo size.

## See also

- [Parallel scanning (v2.103.0+)](parallelism.md) — the parallelism
  rewrite that the cache layers on top of; cache hits skip the
  ProcessPool fan-out entirely, so the two optimisations compose
  multiplicatively, not additively.
- `scripts/cpv_skillaudit_native.py` — the scanner whose findings the
  cache stores (`scan_content`, `__version__`).
- `scripts/rules/skillaudit_patterns.json` — the catalog whose sha256 is
  part of the cache key.
- `gen_ci_yml` in `scripts/generate_plugin_repo.py` — emits the
  `actions/cache@v4` block for every scaffolded plugin.
