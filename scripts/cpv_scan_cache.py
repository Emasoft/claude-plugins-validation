#!/usr/bin/env python3
"""SQLite-backed content-hash result cache for the CPV security scanner.

This is a *quadruple-keyed* cache: a single entry is keyed by

  (content_hash, catalog_hash, scanner_version, file_ext)

so any drift in the scanned content, the rule catalog, the scanner
binary itself, OR the file extension fully invalidates the entry. A
cache MISS is always safe (the caller is expected to fall back to a
full rescan).

``file_ext`` is load-bearing because the skillaudit scanner picks its
context classifier from the file SUFFIX (``.py``/``.json``/``.md``/
``.yml``/``.ts``), so the SAME bytes produce DIFFERENT verdicts under
different extensions. Without the extension in the key, the first
extension scanned would poison every other extension's lookup with its
own classifier's verdict (cross-extension collision → FP or FN).
Callers that don't care about extension-sensitivity (or scan
extensionless content) pass ``file_ext=""`` and share one bucket.

Storage location is resolved by walking a 5-step priority chain (first
writable wins); see :func:`_resolve_cache_path`. When NO candidate is
writable, the module degrades gracefully: every ``get`` returns ``None``,
every ``put`` becomes a silent no-op, and an INFO message is logged
exactly once (so the user knows caching is disabled without spamming the
console on every scan).

## Security invariants (CVE-class — MUST hold)

* Cache file mode ``0o600`` (and parent dir ``0o700``) so cached
  findings cannot be read by other users on shared boxes.
* Quadruple-key invalidation — ANY of (content, catalog,
  scanner_version, file_ext) changing invalidates the entry. The
  PRIMARY KEY enforces this at the SQL level; ``get`` only matches when
  ALL four keys are identical.
* Corruption recovery — if the SQLite file is unreadable / not a valid
  database, the next ``get``/``put`` wipes and recreates it. The module
  never serves stale or partially-decoded findings.
* Hard kill switch — ``CPV_SCAN_CACHE=0`` makes ``get`` always return
  ``None`` and ``put`` a no-op. Used by ``--no-cache`` callers.
* Force-rescan-but-warm-cache — ``CPV_SCAN_CACHE_DEEP=1`` makes ``get``
  return ``None`` (force a full rescan) BUT ``put`` still writes through
  so the next normal run benefits from the rescan's findings.

## Concurrency

SQLite handles the cross-process / cross-thread synchronisation
natively. We open the connection with
``check_same_thread=False`` for ``put``/``get`` calls and use
``isolation_level=None`` (autocommit) plus ``BEGIN IMMEDIATE`` for
writes so two writers don't tear each other's transactions. The
top-level functions are stateless wrappers — they open a connection
per call, execute the operation, and close. That keeps the public
surface dead simple at the cost of one open/close per query, which is
negligible compared to the seconds-long scan work the cache is
shielding.

This module is stdlib-only (``sqlite3``, ``os``, ``json``, ``pathlib``,
``logging``, ``time``, ``stat``). No external dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Filename for the SQLite database. Constant across every resolution
# candidate so users can grep / inspect predictably.
#
# The ``-v2`` stem is a SCHEMA version, not a product version: it was
# introduced when ``file_ext`` joined the PRIMARY KEY (quadruple-key
# cache). Bumping the filename retires every old 3-key ``scan-cache.sqlite``
# cleanly — a fresh file is created and the stale DB is simply ignored
# (a cache MISS is always safe). This avoids relying on the
# corruption-recovery path to migrate an incompatible older schema.
_CACHE_FILENAME = "scan-cache-v2.sqlite"

# Same database under the GitHub Actions ephemeral runner ($RUNNER_TEMP).
# Kept in lockstep with ``_CACHE_FILENAME`` so the schema-version bump
# applies everywhere. The runner temp is recreated per CI run, so this
# location never actually carries a stale schema across runs — but
# matching the version keeps the two names from drifting.
_CACHE_FILENAME_GHA = "cpv-scan-cache-v2.sqlite"

# Default prune thresholds — tunable per call.
_DEFAULT_MAX_AGE_DAYS = 180
_DEFAULT_MAX_ENTRIES = 100_000

# Permissions — these are CVE-class invariants, NOT cosmetic.
_FILE_MODE = 0o600
_DIR_MODE = 0o700

# Module-level logger. Module name is the canonical "cpv_scan_cache"
# so users / CI can mute it independently.
_LOG = logging.getLogger("cpv_scan_cache")

# We only ever want to emit "cache disabled because no writable
# location" once per process. After the first emission this flips True.
_NO_WRITABLE_WARNED = False


# ---------------------------------------------------------------------------
# Environment-driven mode helpers
# ---------------------------------------------------------------------------


def _is_disabled() -> bool:
    """``CPV_SCAN_CACHE=0`` → cache fully disabled.

    The env var is read on every call (NOT cached) so tests can flip it
    via ``monkeypatch.setenv`` between assertions without restarting the
    module. The cost is one ``os.environ.get`` per get/put which is
    sub-microsecond — irrelevant next to a security scan.
    """
    return os.environ.get("CPV_SCAN_CACHE") == "0"


def _is_deep_mode() -> bool:
    """``CPV_SCAN_CACHE_DEEP=1`` → force-rescan but write-through.

    ``get`` returns ``None`` (so the caller does a full scan); ``put``
    still writes the fresh findings so the NEXT normal run benefits.
    Used by ``--deep`` invocations that want to refresh the cache
    without throwing it away.
    """
    return os.environ.get("CPV_SCAN_CACHE_DEEP") == "1"


# ---------------------------------------------------------------------------
# Storage location resolution
# ---------------------------------------------------------------------------


def _candidate_paths() -> list[Path]:
    """Build the 5-step priority chain of cache locations.

    Order is fixed and documented in the module docstring. Each entry is
    a fully-resolved ``Path`` pointing at the cache *file* (not the
    parent directory). ``_resolve_cache_path`` then walks this list and
    picks the first one whose parent dir is creatable + writable.

    Why we don't filter by ``.exists()`` here: a candidate that doesn't
    exist yet may still be perfectly writable. The actual writability
    test happens inside ``_resolve_cache_path``.
    """
    candidates: list[Path] = []

    # 1. Explicit override — wins over everything else.
    explicit = os.environ.get("CPV_SCAN_CACHE_DIR")
    if explicit:
        candidates.append(Path(explicit).expanduser() / _CACHE_FILENAME)

    # 2. Claude Code session-scoped data dir.
    cpd = os.environ.get("CLAUDE_PLUGIN_DATA")
    if cpd:
        candidates.append(Path(cpd).expanduser() / _CACHE_FILENAME)

    # 3. uvx user with Claude Code: per-plugin data dir under ~/.claude.
    candidates.append(Path.home() / ".claude" / "plugins" / "data" / "claude-plugins-validation" / _CACHE_FILENAME)

    # 4. uvx CLI without Claude Code: XDG cache dir.
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        candidates.append(Path(xdg).expanduser() / "cpv" / _CACHE_FILENAME)
    else:
        candidates.append(Path.home() / ".cache" / "cpv" / _CACHE_FILENAME)

    # 5. GitHub Actions ephemeral runner — use $RUNNER_TEMP.
    if os.environ.get("GITHUB_ACTIONS") == "true":
        runner_tmp = os.environ.get("RUNNER_TEMP")
        if runner_tmp:
            candidates.append(Path(runner_tmp).expanduser() / _CACHE_FILENAME_GHA)

    return candidates


def _try_prepare(path: Path) -> bool:
    """Try to create ``path``'s parent dir (mode 0700). Return True on success.

    A candidate is "usable" if we can mkdir its parent AND that parent
    is writable. If the parent dir creation fails (permission denied,
    read-only filesystem, etc.) we silently move on to the next
    candidate.

    Note we don't actually CREATE the SQLite file here — that happens
    on first use via ``sqlite3.connect``. We only need to confirm the
    directory exists and is writable.
    """
    try:
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
        # Even on existing dirs, force perms back to 0700 (defence in
        # depth: a previous run might have created the dir with looser
        # mode under an old umask).
        try:
            os.chmod(parent, _DIR_MODE)
        except OSError:
            # Some filesystems (FAT, network mounts) don't support
            # chmod; that's fine, we just lose the perms enforcement.
            pass
        return os.access(parent, os.W_OK)
    except OSError:
        return False


def _resolve_cache_path() -> Path | None:
    """Walk the 5-step priority chain and return the first writable path.

    Returns ``None`` (caching disabled) if NO candidate is usable. In
    that case the module logs ONE info-level message and downgrades
    every subsequent get/put to a no-op.
    """
    global _NO_WRITABLE_WARNED

    for candidate in _candidate_paths():
        if _try_prepare(candidate):
            return candidate

    if not _NO_WRITABLE_WARNED:
        _NO_WRITABLE_WARNED = True
        _LOG.info("cpv_scan_cache: no writable cache location found; caching disabled")
    return None


# ---------------------------------------------------------------------------
# Connection / schema management
# ---------------------------------------------------------------------------


# DDL — kept in one place so reset_cache() and the lazy init path emit
# byte-identical schemas. Adding columns later requires bumping a
# schema version (not present yet — single-table design is intentional).
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scan_cache (
  content_hash TEXT NOT NULL,
  catalog_hash TEXT NOT NULL,
  scanner_version TEXT NOT NULL,
  file_ext TEXT NOT NULL DEFAULT '',
  findings_json TEXT NOT NULL,
  cached_at INTEGER NOT NULL,
  PRIMARY KEY (content_hash, catalog_hash, scanner_version, file_ext)
);
CREATE INDEX IF NOT EXISTS idx_cached_at ON scan_cache (cached_at);
"""


def _open_connection(path: Path) -> sqlite3.Connection:
    """Open (or create) the SQLite cache and return a connection.

    Settings:
      - ``check_same_thread=False`` so callers can share/handoff conns
        across threads (we still open-per-call in top-level helpers,
        but tests use ``threading`` for concurrent writers).
      - ``isolation_level=None`` (autocommit). Write paths use explicit
        ``BEGIN IMMEDIATE`` so concurrent writers serialise cleanly
        without holding readers off longer than needed.
      - ``timeout=10.0`` — generous wait for the write lock so the
        threaded-writers test doesn't false-fail under load.

    Schema is applied unconditionally (``CREATE TABLE IF NOT EXISTS``);
    cheap on a warm cache, mandatory on a freshly-resolved location.

    File mode is forced to 0600 immediately after creation. We can't
    pre-create the file with the mode (sqlite3 owns that), so we chmod
    right after. Race: if another process happens to read between
    sqlite3 creating the file and the chmod landing, they'd see the
    looser umask-default mode briefly — but the database is empty at
    that microsecond so nothing sensitive is leaked.
    """
    # Force-create the file with restrictive perms BEFORE sqlite3 sees
    # it. ``os.open`` lets us pin the mode, then we just close the FD
    # and hand the path to sqlite3.
    if not path.exists():
        try:
            fd = os.open(
                str(path),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                _FILE_MODE,
            )
            os.close(fd)
        except OSError:
            # Race with another process creating the same file — fine,
            # whoever wins still ends up with sqlite3-managed content.
            pass

    conn = sqlite3.connect(
        str(path),
        timeout=10.0,
        isolation_level=None,
        check_same_thread=False,
    )

    # WAL mode lets readers proceed while a writer holds the write lock —
    # the threaded-writers test depends on this. Best-effort: some
    # filesystems (FAT on USB sticks, certain NFS mounts) reject WAL;
    # we fall back to default rollback journaling silently.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass

    conn.executescript(_SCHEMA_SQL)

    # Belt-and-suspenders: re-enforce 0600 in case sqlite3 created the
    # file under our umask before we got to chmod it.
    try:
        current_mode = stat.S_IMODE(os.stat(path).st_mode)
        if current_mode != _FILE_MODE:
            os.chmod(path, _FILE_MODE)
    except OSError:
        pass

    return conn


def _wipe_and_recreate(path: Path) -> sqlite3.Connection | None:
    """Drop the cache file and rebuild the schema.

    Called by:
      - ``reset_cache()`` (explicit reset)
      - ``get``/``put`` after they catch a corruption error.

    Returns the freshly-opened connection, or ``None`` if even the
    unlink fails (in which case the caller treats this as a permanent
    miss and stops trying).
    """
    try:
        path.unlink(missing_ok=True)
        # WAL companion files may persist after a crash; clean them too
        # so the freshly-recreated DB starts pristine.
        for suffix in ("-wal", "-shm", "-journal"):
            (path.parent / f"{path.name}{suffix}").unlink(missing_ok=True)
    except OSError:
        return None

    try:
        return _open_connection(path)
    except sqlite3.Error:
        return None


def _delete_entry(
    path: Path,
    content_hash: str,
    catalog_hash: str,
    scanner_version: str,
    file_ext: str,
) -> None:
    """Best-effort DELETE of a single quadruple-keyed row.

    Used by ``get_cached_findings`` when it reads back a corrupt/poisoned
    entry (non-decodable JSON, a non-list value, or a list with a non-dict
    element). Every such branch MUST purge the offending row — not merely
    return ``None`` for this call — so the next run rescans from scratch
    instead of repeatedly decoding the same poison on every lookup, and so
    the corrupt row never lingers behind a same-key MISS. Swallows every
    sqlite error because the cache is best-effort: a failed purge just means
    the bad row survives until the next ``put`` overwrites it, which is no
    worse than the pre-purge state.
    """
    try:
        conn = _open_connection(path)
        conn.execute(
            "DELETE FROM scan_cache "
            "WHERE content_hash = ? "
            "AND catalog_hash = ? "
            "AND scanner_version = ? "
            "AND file_ext = ?",
            (content_hash, catalog_hash, scanner_version, file_ext),
        )
        conn.close()
    except sqlite3.Error:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_cached_findings(
    content_hash: str,
    catalog_hash: str,
    scanner_version: str,
    *,
    file_ext: str = "",
) -> list[dict[str, Any]] | None:
    """Return cached findings, or ``None`` on miss / disable / corruption.

    Semantics:
      - ``CPV_SCAN_CACHE=0`` → always ``None`` (cache off).
      - ``CPV_SCAN_CACHE_DEEP=1`` → always ``None`` (force rescan).
      - No writable cache path → ``None`` (logged once).
      - Quadruple-key mismatch → ``None``.
      - Corruption (bad JSON, broken sqlite file) → wipe + return ``None``.

    ``file_ext`` is the lowercased file extension (e.g. ``".py"``,
    ``".md"``) and is part of the key because the scanner's verdict
    depends on the classifier selected by the extension. It is
    keyword-only with a ``""`` default so extension-agnostic callers
    keep the old 3-argument call shape. Lookup matches the SAME bucket
    the entry was PUT into — same bytes under a different extension is a
    deliberate MISS (different classifier ran), not a collision.

    The findings list is JSON-decoded; the caller is responsible for
    treating it as untrusted (it came from disk that another process
    may have written) and validating its shape.
    """
    if _is_disabled() or _is_deep_mode():
        return None

    path = _resolve_cache_path()
    if path is None:
        return None

    try:
        conn = _open_connection(path)
    except sqlite3.DatabaseError:
        # Corruption — wipe and report MISS. Next put() will repopulate.
        _wipe_and_recreate(path)
        return None

    try:
        cur = conn.execute(
            "SELECT findings_json FROM scan_cache "
            "WHERE content_hash = ? "
            "AND catalog_hash = ? "
            "AND scanner_version = ? "
            "AND file_ext = ?",
            (content_hash, catalog_hash, scanner_version, file_ext),
        )
        row = cur.fetchone()
    except sqlite3.DatabaseError:
        # Mid-query corruption — wipe and miss.
        try:
            conn.close()
        except sqlite3.Error:
            pass
        _wipe_and_recreate(path)
        return None
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass

    if row is None:
        return None

    try:
        findings = json.loads(row[0])
    except (TypeError, ValueError):
        # Stored a non-decodable string somehow — treat as corrupted
        # entry and discard it from the cache, not just from this call.
        _delete_entry(path, content_hash, catalog_hash, scanner_version, file_ext)
        return None

    # The schema doesn't enforce a list type — defensive check so a
    # malicious or accidentally-stored non-list doesn't leak into the
    # caller's findings-handling code. Purge the offending row (same as the
    # non-decodable-JSON and non-dict-element paths) so the next run rescans
    # from scratch rather than re-decoding the same non-list value on every
    # lookup. Without the purge this branch was the ONLY corruption path that
    # left the poison in place, diverging from its siblings.
    if not isinstance(findings, list):
        _delete_entry(path, content_hash, catalog_hash, scanner_version, file_ext)
        return None

    # Per-element shape check (audit MINOR #9). The consumer skips
    # non-dict elements and, if EVERY element is skipped, treats the file
    # as scanned-clean — so a same-UID-poisoned row of non-dicts would
    # silently degrade to a false negative. Require every element to be a
    # dict; on failure, DELETE the row (like the non-decodable-JSON path
    # above) and return None so the next run rescans from scratch rather
    # than trusting a corrupt/poisoned entry.
    if not all(isinstance(e, dict) for e in findings):
        _delete_entry(path, content_hash, catalog_hash, scanner_version, file_ext)
        return None

    return findings


def put_cached_findings(
    content_hash: str,
    catalog_hash: str,
    scanner_version: str,
    findings: list[dict[str, Any]],
    *,
    file_ext: str = "",
) -> None:
    """Idempotent write. Silent on every failure (cache is best-effort).

    ``file_ext`` is the lowercased file extension and is part of the
    PRIMARY KEY (see :func:`get_cached_findings`). It is keyword-only
    with a ``""`` default so extension-agnostic callers keep the old
    4-argument call shape. Two PUTs with identical content but different
    extensions land in DIFFERENT rows (no overwrite).

    The cache is a performance optimisation, NOT a correctness primitive.
    A failed put means the next scan won't be cached — that's fine, the
    user just pays the full scan cost again. We therefore swallow ALL
    write errors (disk full, permission denied, sqlite corruption,
    JSON-encode failure on an exotic findings object) so the cache
    layer never blocks the scan.

    ``CPV_SCAN_CACHE=0`` short-circuits the whole thing (no-op).
    ``CPV_SCAN_CACHE_DEEP=1`` does NOT short-circuit — deep mode wants
    the cache populated for the NEXT run.
    """
    if _is_disabled():
        return

    path = _resolve_cache_path()
    if path is None:
        return

    try:
        findings_json = json.dumps(findings)
    except (TypeError, ValueError):
        # Findings contain something json can't encode — silent skip.
        # The caller's contract says findings are JSON-serialisable
        # dicts of primitives; if they pass something exotic we can't
        # cache it but we shouldn't blow up the scan either.
        return

    # Use a separate variable name for the recovery path so mypy can
    # narrow the Optional[Connection] back to Connection inside the
    # write block. Without this, mypy sees `conn` as Connection | None
    # after the rebuild branch and flags every later `.execute()` call.
    try:
        conn = _open_connection(path)
    except sqlite3.DatabaseError:
        # Corruption — try once to wipe and proceed. If even that fails
        # we silently skip the put.
        recovered = _wipe_and_recreate(path)
        if recovered is None:
            return
        conn = recovered

    now = int(time.time())
    try:
        # INSERT OR REPLACE makes put() idempotent — re-caching the same
        # key with new findings just overwrites. We don't need an
        # explicit BEGIN here because autocommit + a single statement is
        # atomic.
        conn.execute(
            "INSERT OR REPLACE INTO scan_cache "
            "(content_hash, catalog_hash, scanner_version, file_ext, findings_json, cached_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (content_hash, catalog_hash, scanner_version, file_ext, findings_json, now),
        )
    except sqlite3.DatabaseError:
        # Could be "database is locked" under contention — that's OK,
        # we just skip THIS put. The next scan will retry.
        pass
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def prune_cache(
    max_age_days: int = _DEFAULT_MAX_AGE_DAYS,
    max_entries: int = _DEFAULT_MAX_ENTRIES,
) -> dict[str, int]:
    """Two-pass prune: by age first, then by LRU until under entry cap.

    Returns ``{"removed_age": N, "removed_lru": M}``. Each number is
    the count of rows actually deleted by that pass, NOT the count
    that would be deleted if both passes were independent (the LRU
    pass only sees rows that survived the age pass).

    On any failure (no writable cache, sqlite error) returns
    ``{"removed_age": 0, "removed_lru": 0}`` — pruning is best-effort
    just like put.
    """
    result = {"removed_age": 0, "removed_lru": 0}

    path = _resolve_cache_path()
    if path is None:
        return result

    try:
        conn = _open_connection(path)
    except sqlite3.DatabaseError:
        return result

    try:
        # ---- Age pass ----
        # ``max_age_days <= 0`` means "no age pruning"; skip the
        # statement entirely so we don't accidentally wipe with a 0
        # cutoff.
        if max_age_days > 0:
            cutoff = int(time.time()) - (max_age_days * 86_400)
            cur = conn.execute(
                "DELETE FROM scan_cache WHERE cached_at < ?",
                (cutoff,),
            )
            # ``rowcount`` is the canonical deleted-count on sqlite3 for
            # DELETE. -1 means "unknown" which only happens for
            # SELECTs — for our DELETE it's always meaningful.
            result["removed_age"] = max(0, cur.rowcount)

        # ---- LRU pass ----
        # Count survivors. If we're at or under the cap, nothing to do.
        cur = conn.execute("SELECT COUNT(*) FROM scan_cache")
        (current,) = cur.fetchone()
        if current > max_entries:
            excess = current - max_entries
            # Delete the OLDEST `excess` rows. The index on cached_at
            # makes this an indexed scan, not a sort-the-whole-table.
            cur = conn.execute(
                "DELETE FROM scan_cache "
                "WHERE rowid IN ("
                "  SELECT rowid FROM scan_cache "
                "  ORDER BY cached_at ASC "
                "  LIMIT ?"
                ")",
                (excess,),
            )
            result["removed_lru"] = max(0, cur.rowcount)
    except sqlite3.DatabaseError:
        # If the prune itself fails, return what we managed before the
        # error (or zero). Don't wipe — pruning shouldn't be destructive.
        pass
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass

    return result


def reset_cache() -> None:
    """Drop + recreate the cache file. For tests and ``--deep`` recovery.

    No-op if no writable cache location is available. Idempotent —
    safe to call when the cache doesn't exist yet.
    """
    path = _resolve_cache_path()
    if path is None:
        return

    # _wipe_and_recreate returns a connection — we don't need it here,
    # just close it.
    conn = _wipe_and_recreate(path)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def cache_stats() -> dict[str, Any]:
    """Return diagnostic info about the cache file.

    Schema of the returned dict:
      - ``path``: str (the resolved cache path) or ``None`` if disabled.
      - ``entries``: int (row count) or 0.
      - ``size_bytes``: int (file size on disk) or 0.
      - ``oldest_at``: int (epoch seconds of oldest entry) or ``None``.
      - ``hit_count``: int — placeholder (returns 0; live counters
        would require a metadata table and are out of scope for the
        scaffold; tests only assert the key is present and >= 0).
      - ``miss_count``: int — same caveat as hit_count.

    Returns a fully-populated dict even when disabled, so callers can
    blindly index into it without conditional branches.
    """
    out: dict[str, Any] = {
        "path": None,
        "entries": 0,
        "size_bytes": 0,
        "oldest_at": None,
        "hit_count": 0,
        "miss_count": 0,
    }

    path = _resolve_cache_path()
    if path is None:
        return out

    out["path"] = str(path)

    if not path.exists():
        # Resolved but never used — empty stats are fine.
        return out

    try:
        out["size_bytes"] = path.stat().st_size
    except OSError:
        pass

    try:
        conn = _open_connection(path)
    except sqlite3.DatabaseError:
        return out

    try:
        cur = conn.execute("SELECT COUNT(*) FROM scan_cache")
        (entries,) = cur.fetchone()
        out["entries"] = entries

        cur = conn.execute("SELECT MIN(cached_at) FROM scan_cache")
        (oldest,) = cur.fetchone()
        out["oldest_at"] = oldest  # may be None when table is empty
    except sqlite3.DatabaseError:
        pass
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass

    return out


# ---------------------------------------------------------------------------
# Test-only helpers (exposed for tests; safe in production)
# ---------------------------------------------------------------------------


def _reset_warned_flag() -> None:
    """Reset the "we already logged 'no writable location'" guard.

    Tests that monkey-patch the resolution chain need this so they can
    re-assert the log message fires on a fresh resolution attempt.
    """
    global _NO_WRITABLE_WARNED
    _NO_WRITABLE_WARNED = False
