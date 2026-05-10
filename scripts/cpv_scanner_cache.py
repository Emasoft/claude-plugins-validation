#!/usr/bin/env python3
"""Content-hash scanner-result cache for CPV (Phase D, v2.78.0).

Caches the JSON-serialisable result of any per-file linter or whole-tree
security scanner under a content-addressed key, so re-running
`validate_plugin --strict` on a tree with no source changes hits the
cache instead of re-invoking ruff / mypy / shellcheck / trufflehog /
semgrep / cc-audit / tirith from scratch.

The cache key combines:

  - target_id        — for per-file linters: absolute file path (kept for
                       human-readable filenames). For tree-level scanners
                       (trufflehog, semgrep, …) this is the merkle hash
                       of every input file (so a single drift anywhere
                       invalidates the cache entry).
  - content_sha256   — sha256 of the file's bytes (per-file mode) OR the
                       merkle hash of the whole tree (tree mode).
  - scanner_name     — short identifier (e.g. "ruff", "trufflehog"); used
                       both as cache-filename prefix and as part of the
                       digest so two scanners running on the same content
                       never collide.
  - scanner_version  — `<scanner> --version` output, normalised. Bumping
                       a scanner invalidates everything cached against
                       its previous version.
  - args_hash        — sha256 of the stable-sorted argv (excluding the
                       target path itself); a flag change invalidates
                       just the entries it would have produced.

Cache layout (`~/.cache/cpv/scanner-results/`):
    <scanner_name>__<digest>.json

Each entry is a dict with three keys:
    {"key":   {...CacheKey fields...},
     "result": <JSON-serialisable result dict>,
     "ts":    <epoch seconds at write time>}

Atomic write: write to a per-write tmp file inside the cache dir,
fsync, then `os.replace` onto the canonical name. `os.replace` is
atomic on every POSIX filesystem and on NTFS, so concurrent writers
inside Phase B's ThreadPoolExecutor never observe a half-written
file. Reads are pure `open + json.load` — if the file is malformed
or unreadable, `get()` treats it as a miss instead of crashing.

Stdlib-only by design: no `filelock`, no `cachetools`. The atomic-
rename pattern + content-addressed filenames make external locking
unnecessary.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# Cache root. Uses XDG-ish layout (~/.cache/cpv/scanner-results/) so it
# stays out of the project tree and survives `rm -rf .cpv-cache/` style
# cleanups inside CI.
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "cpv" / "scanner-results"

# Stale entries are pruned lazily after this many days. 30 d gives us
# ~one release cycle worth of warm hits before the cache rolls itself
# over; long enough to be useful, short enough that an obsolete scanner
# version cannot rot the cache forever.
DEFAULT_TTL_DAYS = 30


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheKey:
    """Stable identity for a scanner invocation against one or more files.

    For per-file linters: ``target_id`` is the absolute file path and
    ``content_sha256`` is the file's sha256 — one cache entry per file.

    For tree-level scanners (trufflehog, semgrep, cc-audit, tirith):
    ``target_id`` and ``content_sha256`` are both the same merkle hash
    of every scanned file, so a drift in any input invalidates the
    single tree-level entry.

    ``scanner_name`` and ``scanner_version`` partition the cache by
    tool identity — a ruff bump invalidates only ruff entries.

    ``args_hash`` partitions by argv (excluding the target path itself);
    changing a `--select=` flag invalidates the entries that flag would
    have produced without disturbing entries from other invocations.
    """

    target_id: str
    content_sha256: str
    scanner_name: str
    scanner_version: str
    args_hash: str

    def to_cache_filename(self) -> str:
        """Return a deterministic filename for this key.

        Format: ``<scanner_name>__<digest>.json`` where ``<digest>`` is
        the first 16 hex chars of the sha256 of all key fields. 16 chars
        = 64 bits, collision-resistant well past any plausible cache
        size (a million-entry cache has < 1e-7 birthday-collision odds).
        """
        # Combine ALL discriminating fields — scanner_name is in the
        # filename prefix already but ALSO baked into the digest so a
        # rename of scanner_name (without version bump) doesn't
        # accidentally hit a stale entry that drops out the door.
        body = "\x00".join(
            (
                self.target_id,
                self.content_sha256,
                self.scanner_name,
                self.scanner_version,
                self.args_hash,
            )
        )
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        # Sanitise scanner_name for filesystem safety — only [a-z0-9_-]
        # survives, anything else collapses to "_". This protects
        # against injection-style scanner names that contain "/" or
        # NUL bytes (defensive — every caller in CPV passes a static
        # literal but the dataclass is public).
        safe_name = "".join(c if (c.isalnum() or c in "_-") else "_" for c in self.scanner_name)[:32]
        if not safe_name:
            safe_name = "scanner"
        return f"{safe_name}__{digest}.json"


# ---------------------------------------------------------------------------
# Cache implementation
# ---------------------------------------------------------------------------


class ScannerCache:
    """Thread-safe content-hash cache for scanner results.

    Cache layout under ``cache_dir``:
        <scanner_name>__<digest>.json — one file per (key) entry

    Each file: ``{"key": dict(key), "result": ..., "ts": <epoch>}``.

    Atomic write: tempfile.mkstemp inside cache_dir + os.replace.
    Reads are immutable (entries are never modified in place — a
    re-put rewrites a fresh tmp + replace), so the only failure mode
    a concurrent reader can see is "the file disappeared mid-stat",
    which we treat as a miss.
    """

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        ttl_days: int = DEFAULT_TTL_DAYS,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_days * 86_400

    # ---- core API --------------------------------------------------------

    def get(self, key: CacheKey) -> dict | None:
        """Return cached result dict OR None on miss/stale/corrupt.

        A "miss" is any of:
          - file does not exist
          - file is older than ``ttl_seconds`` (stale)
          - file is unreadable (permission, IO error)
          - file's JSON is malformed
          - JSON's stored ``key`` does not match the requested key
            (defensive — protects against digest collisions)
        """
        path = self.cache_dir / key.to_cache_filename()
        try:
            st = path.stat()
        except OSError:
            return None

        # Stale gate — entries past TTL are treated as a miss. A
        # subsequent put() will overwrite the stale file in place.
        if self.ttl_seconds > 0:
            age = time.time() - st.st_mtime
            if age > self.ttl_seconds:
                return None

        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            # Corrupt / partially-written / disappeared between stat
            # and open: treat as miss. Don't propagate — a cache miss
            # is a no-op for the caller, a crash is not.
            return None

        # Defensive cross-check: the stored key MUST match the
        # requested key. If the on-disk filename's digest happened to
        # collide (astronomically unlikely with a 64-bit prefix, but
        # the check is cheap), bail out as a miss.
        stored_key = payload.get("key")
        expected_key = asdict(key)
        if stored_key != expected_key:
            return None

        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        return result

    def put(self, key: CacheKey, result: dict) -> None:
        """Store a result dict under the cache key (atomic write).

        Uses ``tempfile.mkstemp`` inside ``cache_dir`` + ``os.replace``
        so concurrent threads never observe a partial file. ``os.replace``
        is atomic on POSIX and NTFS — see Python docs for the guarantee.
        """
        if not isinstance(result, dict):
            raise TypeError(f"ScannerCache.put: result must be a dict, got {type(result).__name__}")

        payload = {
            "key": asdict(key),
            "result": result,
            "ts": time.time(),
        }

        # Serialise FIRST — if json.dumps raises, we never touched the
        # cache directory. ``default=str`` keeps non-serialisable objects
        # (Path, datetime) from blowing up the write; they get rendered
        # as strings, which is correct for cached output where the
        # caller only needs textual stability.
        try:
            data = json.dumps(payload, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            # Non-serialisable result — don't cache. The caller will
            # see a miss next time and re-run the scanner. Failing to
            # cache is a soft failure, never an error.
            return

        final_path = self.cache_dir / key.to_cache_filename()

        # mkstemp returns an OS-level file descriptor; using fdopen
        # lets us write + flush + fsync without touching Python's
        # buffered IO layer twice.
        fd, tmp_name = tempfile.mkstemp(
            prefix=".cpv-tmp-",
            suffix=".json",
            dir=str(self.cache_dir),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, final_path)
        except OSError:
            # Best-effort cleanup on write failure — never propagate.
            # A failed put() degrades to "no cache for this entry";
            # the caller will recompute next time.
            try:
                tmp_path.unlink()
            except OSError:
                pass
            return

    def invalidate_older_than(self, days: int = 30) -> int:
        """Delete cache entries older than ``days``. Returns count removed.

        Called lazily by callers that want to reclaim space; not on the
        hot path. Iterates the cache dir once and ``unlink``s any file
        whose mtime is older than the cutoff. IO failures during the
        sweep are silently skipped — partial sweeps are fine.
        """
        cutoff = time.time() - (days * 86_400)
        removed = 0
        try:
            entries = list(self.cache_dir.iterdir())
        except OSError:
            return 0

        for entry in entries:
            # Only sweep our own cache files. Anything else (a stray
            # tmp file from a crashed put, a user-dropped marker file)
            # is left alone unless it has the .json suffix AND
            # matches our naming pattern.
            if not entry.is_file():
                continue
            if not entry.name.endswith(".json"):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                continue
            try:
                entry.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def clear(self) -> int:
        """Drop every entry in the cache (test/debug helper).

        Returns count removed. Treats IO failures as best-effort.
        """
        removed = 0
        try:
            entries = list(self.cache_dir.iterdir())
        except OSError:
            return 0
        for entry in entries:
            if not entry.is_file():
                continue
            if not entry.name.endswith(".json"):
                continue
            try:
                entry.unlink()
                removed += 1
            except OSError:
                continue
        return removed


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def sha256_of_file(path: Path) -> str:
    """Stream sha256 of a file's bytes.

    65 KiB chunk size matches the helper in
    ``scripts/_plugin_compute_hashes.py`` (the only other CPV file
    that streams bytes through sha256). Picked once, kept consistent.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_of_args(args: list[str]) -> str:
    """sha256 of a CLI argv list.

    Args are joined with NUL ('\\x00') separators so that a list like
    ``["a b", "c"]`` cannot collide with ``["a", "b c"]`` (which it
    would under naive whitespace joining). Args are NOT sorted — the
    ORDER of CLI flags can be semantically meaningful (e.g. multiple
    ``--config`` flags to semgrep are applied in order). Callers that
    want order-insensitivity must sort BEFORE calling.
    """
    body = "\x00".join(args)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def tree_merkle(file_paths: list[Path], *, base: Path | None = None) -> str:
    """sha256 of sorted ``(relative_path, content_sha256)`` for all files.

    Used as the ``content_sha256`` for tree-level scanners (trufflehog,
    semgrep, cc-audit, tirith). A drift in any single byte of any file
    in the tree changes the merkle, invalidating the cached result.

    Inputs:
      file_paths — every file the scanner will read (caller's
                   responsibility to enumerate the same set of files
                   on cache hit and cache miss; otherwise the cache
                   key drifts).
      base       — when supplied, paths are recorded relative to this
                   root, so the merkle is portable across machines
                   with different absolute-path prefixes. When None,
                   the absolute path is used (good for a single-host
                   cache; NOT shareable across hosts).

    Files that cannot be read (permission denied, vanished mid-scan)
    are silently skipped — the cache will simply miss for the next
    invocation, which is correct.
    """
    h = hashlib.sha256()
    rows: list[tuple[str, str]] = []
    for p in file_paths:
        try:
            digest = sha256_of_file(p)
        except OSError:
            continue
        if base is not None:
            try:
                rel = str(p.relative_to(base))
            except ValueError:
                rel = str(p)
        else:
            rel = str(p)
        rows.append((rel, digest))
    # Sort AFTER hashing so the merkle is order-independent. Two
    # callers passing the same files in different orders will get the
    # same merkle.
    rows.sort()
    for rel, digest in rows:
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(digest.encode("ascii"))
        h.update(b"\x00")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Scanner version probe (one-shot per process, cached)
# ---------------------------------------------------------------------------


# Process-local cache so we don't run `<scanner> --version` more than
# once per CPV invocation. Threading lock guards the dict because
# Phase B linters run inside a ThreadPoolExecutor.
_VERSION_CACHE: dict[str, str] = {}
_VERSION_LOCK = threading.Lock()


def get_scanner_version(scanner_name: str) -> str:
    """Run ``<scanner_name> --version`` and return a normalised string.

    Cached per process (subsequent calls for the same scanner are
    O(1)). Returns ``"unknown"`` if the scanner is not on PATH or its
    ``--version`` invocation fails — the cache key still partitions
    correctly, just with less granularity.

    Special-cased scanners that don't accept a flat ``--version``:
      - go's ``go version``
      - none others currently
    """
    with _VERSION_LOCK:
        if scanner_name in _VERSION_CACHE:
            return _VERSION_CACHE[scanner_name]

    if not shutil.which(scanner_name):
        with _VERSION_LOCK:
            _VERSION_CACHE[scanner_name] = "unknown"
        return "unknown"

    # Most CPV scanners accept --version; ``go`` uses ``go version``
    # without a flag prefix. The fall-through after a non-zero exit
    # also catches edge cases like cargo (which emits its version on
    # the FIRST line of stderr only when stdout is a TTY).
    if scanner_name == "go":
        argv = ["go", "version"]
    else:
        argv = [scanner_name, "--version"]

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        version = "unknown"
    else:
        # Prefer stdout; fall back to stderr (some tools — notably
        # gofmt — print --version to stderr instead).
        raw = (result.stdout or result.stderr or "").strip()
        # Take only the first non-empty line; trim whitespace.
        first = ""
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped:
                first = stripped
                break
        version = first or "unknown"

    with _VERSION_LOCK:
        _VERSION_CACHE[scanner_name] = version
    return version


def reset_version_cache() -> None:
    """Clear the per-process scanner-version cache (test helper).

    Production callers never need this; tests use it to simulate a
    scanner upgrade between cache writes.
    """
    with _VERSION_LOCK:
        _VERSION_CACHE.clear()
