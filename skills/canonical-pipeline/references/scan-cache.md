# Scan cache in the canonical pipeline (v2.104.0+)

## Table of contents

- [Overview](#overview)
- [Storage path resolution chain](#storage-path-resolution-chain)
- [SQLite schema](#sqlite-schema)
- [Security invariants](#security-invariants)
- [Env-vars](#env-vars)
- [GitHub Actions integration](#github-actions-integration)
- [LRU pruning](#lru-pruning)
- [Stats CLI](#stats-cli)
- [When to invalidate manually](#when-to-invalidate-manually)
- [What the cache does NOT cache](#what-the-cache-does-not-cache)
- [Performance characteristics](#performance-characteristics)
- [See also](#see-also)

## Overview

The scan cache is a content-hash-keyed SQLite result cache for per-file
skillaudit findings. On a clean repo the first run computes the sha256
of every scanned file and stores `(content_hash, catalog_hash, scanner_version)
→ findings_json` rows. Every subsequent invocation recomputes the file
hash and, on a cache hit, skips the LLM-pattern scan entirely and replays
the persisted findings. This is what gets repeat `validate_plugin .`
invocations against the CPV repo from ≈ 17 s (cold) down to < 1 s (warm,
≥ 90 % hit rate) — roughly a **50× speedup on repeat runs**.

The cache is purely additive: a MISS always falls through to the real
scanner and writes the result; a HIT only happens when all three keys
(content + catalog + scanner version) match exactly. There is no version
that produces stale findings — bumping `rules/skillaudit_patterns.json`
or `cpv_skillaudit_native.__version__` invalidates every prior entry by
construction.

## Storage path resolution chain

The cache picks the first writable path from this priority order, falling
back silently to disabled-mode if none are writable:

1. `$CPV_SCAN_CACHE_DIR/scan-cache.sqlite` — explicit user override (highest priority)
2. `$CLAUDE_PLUGIN_DATA/scan-cache.sqlite` — Claude-Code-managed per-plugin data dir; survives plugin reinstalls / updates
3. `~/.claude/plugins/data/claude-plugins-validation/scan-cache.sqlite` — hard-coded fallback for the CC default plugin data location
4. `$XDG_CACHE_HOME/cpv/scan-cache.sqlite` (or `~/.cache/cpv/scan-cache.sqlite` if `XDG_CACHE_HOME` unset) — XDG-compliant user cache dir
5. `$RUNNER_TEMP/cpv-scan-cache.sqlite` when `GITHUB_ACTIONS=true` — ephemeral CI runner location (paired with `actions/cache` — see [GitHub Actions integration](#github-actions-integration))

If every candidate is unwritable (permissions error, read-only FS,
out-of-space), the cache is disabled for that process. Validation
continues normally; the user sees no error and no perf regression vs.
pre-v2.104.0 behaviour.

## SQLite schema

A single table with a composite triple-key for invalidation safety:

```sql
CREATE TABLE IF NOT EXISTS scan_cache (
    content_hash    TEXT NOT NULL,   -- sha256 of file bytes (lowercase hex)
    catalog_hash    TEXT NOT NULL,   -- sha256 of rules/skillaudit_patterns.json
    scanner_version TEXT NOT NULL,   -- cpv_skillaudit_native.__version__
    findings_json   TEXT NOT NULL,   -- JSON-encoded list of findings
    created_at      INTEGER NOT NULL,-- epoch seconds; LRU pruning key
    last_hit_at     INTEGER NOT NULL,-- epoch seconds; LRU pruning key
    PRIMARY KEY (content_hash, catalog_hash, scanner_version)
);
CREATE INDEX IF NOT EXISTS scan_cache_last_hit_idx
    ON scan_cache(last_hit_at);
```

The triple-key guarantees that a single file's findings entry is
immediately stale on **any** of:

- File contents changed (`content_hash` differs)
- Pattern catalog changed (`catalog_hash` differs, e.g. after rule
  catalog regen)
- Scanner module bumped (`scanner_version` differs, e.g. after an
  algorithm fix that would change findings for unchanged input)

## Security invariants

- The SQLite file is created with mode `0o700` (owner read/write/execute
  only). Other users on the same host cannot read it.
- Database corruption (truncation, page-checksum mismatch,
  unparseable header) triggers an automatic wipe-and-recreate. The
  next run starts from a cold cache. No findings are lost — every
  recreated row is recomputed by the live scanner.
- A cache MISS is always safe — the real scanner runs and emits the
  authoritative findings. There is no code path that emits cached
  findings without all three keys matching.
- The triple-key invalidation prevents stale findings on catalog
  upgrade (new rule lands, every prior entry is invalidated) and on
  scanner upgrade (algorithm changes, every prior entry is
  invalidated). Users cannot accidentally suppress new rules by
  retaining old cache entries.

## Env-vars

| Variable | Default | Effect |
|---|---|---|
| `CPV_SCAN_CACHE` | enabled | Set to `0` to disable the cache entirely for this process (forces every file through the real scanner; restores pre-v2.104.0 wall time) |
| `CPV_SCAN_CACHE_DEEP` | off | Set to `1` to ignore cache hits and write through — every file is scanned fresh AND its entry is refreshed. Used by the publish-time integrity gate to confirm cached findings still match live findings |
| `CPV_SCAN_CACHE_DIR` | (chain) | Override the storage directory. The cache file lands at `<dir>/scan-cache.sqlite`. Highest-priority path in the resolution chain |

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

## LRU pruning

The cache caps itself at the smaller of:

- **100 000 entries** (≈ 30-50 MB on disk for typical findings payloads), OR
- **180 days** of age (`now - last_hit_at > 180 d`)

Whichever ceiling is hit first triggers pruning. Pruning is opportunistic
— it runs at the end of the validator invocation when the row count is
above 95 % of the cap (so the steady-state cache holds ≈ 95 000 entries
before the next prune). The prune deletes the oldest `last_hit_at` rows
until row count is ≈ 90 % of cap.

The ai-maestro-janitor (separate plugin) also exposes a callable for
disk-pressure pruning — when the janitor sees < 5 GB free on `$HOME`'s
filesystem, it can shrink the CPV cache below the normal cap. This is
opt-in via the janitor's config; CPV does not require janitor to be
installed.

## Stats CLI

The cache exposes a small CLI subcommand for introspection:

```bash
cpv-scan-cache stats
```

Output:

```
scan-cache: /home/user/.cache/cpv/scan-cache.sqlite
entries:       18 432
disk size:     12.4 MB
hit rate:      94.3 % (last 7 days)
oldest entry:  82 days ago
newest entry:  4 minutes ago
catalog hash:  3f9c1a2b… (rules/skillaudit_patterns.json)
scanner ver:   1.4.2
```

Other subcommands:

| Subcommand | Effect |
|---|---|
| `cpv-scan-cache stats` | Prints the table above |
| `cpv-scan-cache reset` | Deletes the SQLite file; next validator run rebuilds from scratch |
| `cpv-scan-cache prune` | Force-runs the LRU prune even when below the cap |
| `cpv-scan-cache verify` | Re-runs the scanner against a 1 % random sample of cached entries; compares output; reports drift |

## When to invalidate manually

You should rarely need to. The triple-key in [SQLite schema](#sqlite-schema)
handles every automatic invalidation case:

- **Catalog bump** (`rules/skillaudit_patterns.json` changed) — automatic
- **Scanner version bump** (`cpv_skillaudit_native.__version__` changed)
  — automatic
- **File content changed** — automatic (content hash differs)

Manual invalidation is only justified for:

- **Debugging a "this finding shouldn't be cached" suspicion** — run
  `cpv-scan-cache reset` (or just delete the SQLite file) and re-run;
  if the finding still appears, it was not a cache bug
- **Migrating between machines** — the cache is per-host by design;
  copying the file is supported but not necessary, the LRU just
  rebuilds on the new host
- **Disk pressure** — `cpv-scan-cache prune` or full reset

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
- `rules/skillaudit_patterns.json` — the catalog whose sha256 is part
  of the cache key.
- `gen_ci_yml` template in `scripts/standardize_plugin.py` — emits the
  `actions/cache@v4` block for every scaffolded plugin.
