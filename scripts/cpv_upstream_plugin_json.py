#!/usr/bin/env python3
"""Upstream plugin.json fetcher + diff helpers for marketplace cross-validation.

TRDD-c0ee9543 Phase B. Closes GAP-1, GAP-2, GAP-7..10, GAP-12, GAP-13:
the marketplace entry's `name` / `version` / `description` / `author` /
`keywords` / `homepage` fields are cross-checked against the upstream
`plugin.json` referenced by the entry's `source`.

Public API
==========

- ``fetch_upstream_plugin_json(entry, *, marketplace_dir, cache_dir, ttl_seconds)``
  Returns the upstream `plugin.json` dict, or None on unreachable.
- ``diff_marketplace_vs_upstream(entry, upstream)`` -> list[FieldDrift]
  Compares the marketplace entry against the fetched upstream and
  produces severity-tagged drift records keyed off stable RC-MKPL-* codes.
- ``cross_validate_enabled(marketplace_dir)`` -> bool
  Honours the three opt-out mechanisms (env var, per-marketplace sentinel,
  per-entry underscore flag is checked by the caller).
- ``CPV_PLUGIN_JSON_FETCHER_VERSION`` — bumped on a breaking fetcher change.
  Mixed into the cache key so old entries are silently invalidated.

Supported source types
======================
- ``github`` → fetches ``raw.githubusercontent.com/<repo>/<ref|main>/.claude-plugin/plugin.json``
- ``url`` (with the URL pointing to a raw plugin.json or a git repo) →
  best-effort raw fetch; on HTML the fetcher returns None and the caller
  emits ``RC-MKPL-UPSTREAM-UNREACHABLE``.
- ``git-subdir`` → ``raw.githubusercontent.com/<repo>/<ref|main>/<subdir>/.claude-plugin/plugin.json``
  when the URL is github.com; otherwise None (no shallow clone).
- ``relative-path`` (the ``./path`` shorthand or ``directory`` source type)
  → reads ``.claude-plugin/plugin.json`` directly from disk.
- ``npm`` / ``git`` (non-github) → None (out of scope for this wave; we
  return None and the caller falls back to WARNING).

Caching
=======
- ``~/.cache/cpv/plugin-json/<sha256>.json`` plus a sidecar ``.meta`` file
  carrying the timestamp + fetcher version + source hash.
- Default TTL is 3600 s; override via ``CPV_PLUGIN_JSON_TTL_SECONDS=<N>``
  in the env or by passing ``ttl_seconds=N`` to the call.
- Cache key = sha256(json.dumps({source: <source>, fetcher: <ver>})).
- An ``OSError`` writing to the cache is non-fatal — the fetch still
  returns the parsed data.

Parallelism
===========
For bulk marketplaces (Layout B with 30+ plugins) the validator uses a
ThreadPoolExecutor (max_workers=8) — see ``validate_marketplace.py``'s
``validate_plugins_array`` call site. This module is thread-safe because
its only mutable state is the on-disk cache, which uses atomic
write+rename.

Bypass / opt-out
================
1. ``CPV_SKIP_UPSTREAM_CROSS_CHECK=1`` env var skips ALL Phase B cross-checks.
2. ``<marketplace>/.claude-plugin/.cpv-no-upstream-check`` (zero-byte file)
   skips Phase B for the whole marketplace.
3. ``"_cpv_skip_upstream_check": true`` on a marketplace entry skips it for
   that entry only. (Handled by the caller in validate_marketplace.py.)

Gate 0 of ``publish.py`` REJECTS ``CPV_SKIP_UPSTREAM_CROSS_CHECK`` so a
release can never ship without the cross-check.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

CPV_PLUGIN_JSON_FETCHER_VERSION: str = "1.0.0"
"""Bump on a breaking fetcher change (new headers, new source type, etc.).
Mixed into the cache key so old entries are auto-invalidated on upgrade."""

DEFAULT_CACHE_DIR: Path = Path.home() / ".cache" / "cpv" / "plugin-json"
DEFAULT_TTL_SECONDS: int = 3600
HTTP_TIMEOUT_SEC: int = 10
USER_AGENT: str = (
    f"cpv-upstream-plugin-json/{CPV_PLUGIN_JSON_FETCHER_VERSION} "
    "(claude-plugins-validation; Emasoft/claude-plugins-validation)"
)


# ────────────────────────────────────────────────────────────────────────────
# Data shapes
# ────────────────────────────────────────────────────────────────────────────


Severity = Literal["MAJOR", "MINOR", "NIT", "WARNING"]


@dataclass(frozen=True)
class FieldDrift:
    """One drifted field between a marketplace entry and its upstream plugin.json.

    Attributes
    ----------
    code: stable error code (RC-MKPL-NAME-MISMATCH, etc.) the fixer skill
        keys off of.
    severity: MAJOR / MINOR / NIT / WARNING.
    field: name of the drifted field (e.g. ``"name"``, ``"description"``).
    entry_value: the value the marketplace entry carries.
    upstream_value: the value the upstream plugin.json carries.
    message: human-readable diagnostic — preformatted with code prefix.
    suggestion: optional fix recipe pointer.
    """

    code: str
    severity: Severity
    field: str
    entry_value: Any
    upstream_value: Any
    message: str
    suggestion: str | None = None


# ────────────────────────────────────────────────────────────────────────────
# Opt-out / bypass
# ────────────────────────────────────────────────────────────────────────────


def _truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def cross_validate_enabled(marketplace_dir: Path | None) -> bool:
    """Return False when ANY opt-out mechanism is in effect.

    Three mechanisms checked here:
      1. CPV_SKIP_UPSTREAM_CROSS_CHECK=1 (whole-CI bypass)
      2. <marketplace>/.claude-plugin/.cpv-no-upstream-check (per-marketplace)
    The per-entry opt-out (`_cpv_skip_upstream_check`) is checked by the
    caller because it's keyed off the entry dict, not the marketplace path.
    """
    if _truthy_env(os.environ.get("CPV_SKIP_UPSTREAM_CROSS_CHECK")):
        return False
    if marketplace_dir is not None:
        sentinel = marketplace_dir / ".claude-plugin" / ".cpv-no-upstream-check"
        if sentinel.exists():
            return False
    return True


def entry_skips_cross_check(entry: dict) -> bool:
    """Per-entry opt-out via `_cpv_skip_upstream_check: true`."""
    val = entry.get("_cpv_skip_upstream_check")
    return val is True


# ────────────────────────────────────────────────────────────────────────────
# Cache layer (sha256-keyed, atomic write+rename, sidecar .meta)
# ────────────────────────────────────────────────────────────────────────────


def _cache_key(source: Any, marketplace_dir: Path | None = None) -> str:
    """Stable sha256 over (source, fetcher_version, [marketplace_dir]).

    For relative-path sources we mix in the absolute marketplace directory
    because two different marketplaces can both have a `./plugin` entry
    that points at totally different on-disk content. For remote sources
    (github/url/git-subdir) the source dict alone is unique because the
    URL is global.
    """
    # Relative-path sources are local — different marketplaces resolve
    # them to different plugin.json files, so the cache key MUST include
    # the marketplace's absolute path. Remote sources are global.
    is_local = (isinstance(source, str) and source.startswith("./")) or (
        isinstance(source, dict) and source.get("source") in ("directory", "relative-path")
    )
    payload_dict: dict[str, Any] = {
        "source": source,
        "fetcher": CPV_PLUGIN_JSON_FETCHER_VERSION,
    }
    if is_local and marketplace_dir is not None:
        payload_dict["marketplace_dir"] = str(marketplace_dir.resolve())
    payload = json.dumps(payload_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_paths(cache_dir: Path, key: str) -> tuple[Path, Path]:
    data_path = cache_dir / f"{key}.json"
    meta_path = cache_dir / f"{key}.meta"
    return data_path, meta_path


def _read_cached(cache_dir: Path, key: str, ttl_seconds: int) -> dict[str, Any] | None:
    """Return the cached dict if fresh, else None."""
    data_path, meta_path = _cache_paths(cache_dir, key)
    if not data_path.is_file() or not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    ts_raw = meta.get("timestamp")
    if not isinstance(ts_raw, str):
        return None
    try:
        ts = datetime.fromisoformat(ts_raw)
    except ValueError:
        return None
    if ttl_seconds <= 0:
        # TTL=0 means "always treat as stale" — force a refresh.
        return None
    if datetime.now() - ts > timedelta(seconds=ttl_seconds):
        return None
    if meta.get("fetcher") != CPV_PLUGIN_JSON_FETCHER_VERSION:
        # Fetcher version bumped — cache entry is no longer trustworthy.
        return None
    try:
        parsed = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _write_cached(
    cache_dir: Path,
    key: str,
    data: dict[str, Any],
    source: Any,
    marketplace_dir: Path | None = None,
) -> None:
    """Write data + meta atomically (tmp+rename). Failures are non-fatal."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        data_path, meta_path = _cache_paths(cache_dir, key)
        data_tmp = data_path.with_suffix(data_path.suffix + ".tmp")
        meta_tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
        data_tmp.write_text(json.dumps(data), encoding="utf-8")
        data_tmp.replace(data_path)
        meta = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "fetcher": CPV_PLUGIN_JSON_FETCHER_VERSION,
            "source_hash": _cache_key(source, marketplace_dir),
        }
        meta_tmp.write_text(json.dumps(meta), encoding="utf-8")
        meta_tmp.replace(meta_path)
    except OSError:
        # Cache failures are non-fatal — caller still gets `data`.
        pass


# ────────────────────────────────────────────────────────────────────────────
# Source-type fetchers
# ────────────────────────────────────────────────────────────────────────────


def _fetch_via_url(url: str) -> dict[str, Any] | None:
    """HTTP GET → parse JSON. Returns None on any failure."""
    if _truthy_env(os.environ.get("CPV_TEST_FORCE_UPSTREAM_UNREACHABLE")):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:  # noqa: S310
            data = resp.read().decode("utf-8")
        parsed = json.loads(data)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _github_raw_url(repo: str, ref: str | None, subdir: str | None = None) -> str:
    """Construct the raw.githubusercontent.com URL for a plugin.json fetch."""
    ref = ref or "main"
    if subdir:
        subdir_clean = subdir.strip("/")
        return f"https://raw.githubusercontent.com/{repo}/{ref}/{subdir_clean}/.claude-plugin/plugin.json"
    return f"https://raw.githubusercontent.com/{repo}/{ref}/.claude-plugin/plugin.json"


def _fetch_github(repo: str, ref: str | None) -> dict[str, Any] | None:
    """Fetch raw plugin.json from a github source.

    Falls back through ``main`` → ``master`` when no ``ref`` is specified.
    """
    if ref:
        return _fetch_via_url(_github_raw_url(repo, ref))
    # Try main, then master.
    for candidate_ref in ("main", "master"):
        result = _fetch_via_url(_github_raw_url(repo, candidate_ref))
        if result is not None:
            return result
    return None


def _fetch_git_subdir(url: str, subdir: str, ref: str | None) -> dict[str, Any] | None:
    """Fetch from a git-subdir source.

    For GitHub URLs we synthesise the raw.githubusercontent.com path.
    Non-github git hosts (gitlab/bitbucket/self-hosted) return None for now —
    they would need a shallow clone. The caller emits
    RC-MKPL-UPSTREAM-UNREACHABLE for those.
    """
    # Normalise github URLs: https://github.com/owner/repo[.git] → owner/repo
    norm = url.rstrip("/")
    if norm.endswith(".git"):
        norm = norm[:-4]
    if norm.startswith("https://github.com/"):
        repo = norm[len("https://github.com/") :]
        return _fetch_via_url(_github_raw_url(repo, ref, subdir))
    if norm.startswith("git@github.com:"):
        repo = norm[len("git@github.com:") :]
        return _fetch_via_url(_github_raw_url(repo, ref, subdir))
    return None


def _fetch_relative_path(source: str | dict, marketplace_dir: Path) -> dict[str, Any] | None:
    """Read plugin.json from disk for the `./path` shorthand or `directory` type.

    Returns None when the path resolves outside ``marketplace_dir`` (security
    guard against `..` traversal in untrusted marketplace content).
    """
    rel: str
    if isinstance(source, str):
        rel = source
    elif isinstance(source, dict):
        path_val = source.get("path")
        if not isinstance(path_val, str) or not path_val:
            return None
        rel = path_val
    else:
        return None
    # Reject absolute paths and `..` traversal (defence-in-depth).
    if rel.startswith("/") or ".." in Path(rel).parts:
        return None
    plugin_root = (marketplace_dir / rel).resolve()
    try:
        # Containment check — plugin_root must live under marketplace_dir.
        plugin_root.relative_to(marketplace_dir.resolve())
    except ValueError:
        return None
    candidates = (
        plugin_root / ".claude-plugin" / "plugin.json",
        plugin_root / "plugin.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            try:
                parsed = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


# ────────────────────────────────────────────────────────────────────────────
# Public fetch entry point
# ────────────────────────────────────────────────────────────────────────────


def _ttl_from_env(default: int) -> int:
    """Return TTL from env override, defaulting to `default`."""
    raw = os.environ.get("CPV_PLUGIN_JSON_TTL_SECONDS")
    if raw is None:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def fetch_upstream_plugin_json(
    entry: dict[str, Any],
    *,
    marketplace_dir: Path | None = None,
    cache_dir: Path | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any] | None:
    """Fetch the upstream plugin.json for a marketplace entry.

    Args
    ----
    entry: the marketplace entry dict (from ``marketplace.json.plugins[i]``).
    marketplace_dir: marketplace root, required for relative-path sources.
    cache_dir: cache directory; defaults to ``~/.cache/cpv/plugin-json``.
    ttl_seconds: cache TTL; defaults to env or ``DEFAULT_TTL_SECONDS``.

    Returns
    -------
    The parsed upstream `plugin.json` dict, or None when the source is
    unreachable (caller emits WARNING, not MAJOR).
    """
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR
    if ttl_seconds is None:
        ttl_seconds = _ttl_from_env(DEFAULT_TTL_SECONDS)

    source = entry.get("source")
    key = _cache_key(source, marketplace_dir)

    # Cache fast-path. Note: relative-path / on-disk sources still go
    # through cache so a stale plugin.json doesn't cause flapping
    # results in CI, but a TTL=0 caller always re-reads.
    cached = _read_cached(cache_dir, key, ttl_seconds)
    if cached is not None:
        return cached

    data: dict[str, Any] | None = None

    if isinstance(source, str):
        # Bare relative-path shorthand: "./plugin"
        if marketplace_dir is not None and source.startswith("./"):
            data = _fetch_relative_path(source, marketplace_dir)
    elif isinstance(source, dict):
        src_type = source.get("source")
        if src_type == "github":
            repo = source.get("repo")
            if isinstance(repo, str):
                data = _fetch_github(repo, source.get("ref") if isinstance(source.get("ref"), str) else None)
        elif src_type == "url":
            url = source.get("url")
            if isinstance(url, str):
                data = _fetch_via_url(url)
        elif src_type == "git-subdir":
            url = source.get("url")
            subdir = source.get("subdir") or source.get("path")
            ref = source.get("ref") if isinstance(source.get("ref"), str) else None
            if isinstance(url, str) and isinstance(subdir, str):
                data = _fetch_git_subdir(url, subdir, ref)
        elif src_type == "directory":
            if marketplace_dir is not None:
                data = _fetch_relative_path(source, marketplace_dir)
        # npm / git (non-github) → unreachable for this wave.

    if data is not None:
        _write_cached(cache_dir, key, data, source, marketplace_dir)
    return data


# ────────────────────────────────────────────────────────────────────────────
# Diff
# ────────────────────────────────────────────────────────────────────────────


def _make_name_drift(entry_value: Any, upstream_value: Any, entry_label: str) -> FieldDrift:
    return FieldDrift(
        code="RC-MKPL-NAME-MISMATCH",
        severity="MAJOR",
        field="name",
        entry_value=entry_value,
        upstream_value=upstream_value,
        message=(
            f"[RC-MKPL-NAME-MISMATCH] entry '{entry_label}' has "
            f"name={entry_value!r} but upstream plugin.json declares "
            f"name={upstream_value!r}. `claude plugin install "
            f"<plugin.json-name>@<marketplace>` would resolve against "
            f"the marketplace entry name — this mismatch causes "
            f'"not found" at install time. See '
            "marketplace-upstream-drift.md §1 for the fix."
        ),
        suggestion=(
            f"Align the marketplace entry name with upstream: change "
            f"name={entry_value!r} → name={upstream_value!r}, OR add "
            f'"_cpv_skip_upstream_check": true on this entry if the '
            "name divergence is intentional (brand-vs-canonical alias)."
        ),
    )


def _make_version_drift(entry_value: Any, upstream_value: Any, entry_label: str) -> FieldDrift:
    return FieldDrift(
        code="RC-MKPL-VERSION-DRIFT",
        severity="MINOR",
        field="version",
        entry_value=entry_value,
        upstream_value=upstream_value,
        message=(
            f"[RC-MKPL-VERSION-DRIFT] entry '{entry_label}' pins "
            f"version={entry_value!r} but upstream plugin.json is at "
            f"version={upstream_value!r}. The plugin manifest always "
            f"wins silently (plugin-marketplaces.md:696-698), so the "
            f"marketplace-side version drifts invisibly. See "
            "marketplace-upstream-drift.md §2."
        ),
        suggestion=(
            "Prefer: drop the marketplace-side version field (single "
            "source of truth = plugin.json). Alternate: bump it to "
            f"{upstream_value!r} to match upstream."
        ),
    )


def _make_metadata_drift(
    field: str,
    entry_value: Any,
    upstream_value: Any,
    entry_label: str,
) -> FieldDrift:
    return FieldDrift(
        code="RC-MKPL-METADATA-DRIFT",
        severity="NIT",
        field=field,
        entry_value=entry_value,
        upstream_value=upstream_value,
        message=(
            f"[RC-MKPL-METADATA-DRIFT] entry '{entry_label}' field "
            f"{field!r}: marketplace value differs from upstream "
            f"plugin.json. Marketplace UI may show a different "
            f"{field} than the installed plugin. See "
            "marketplace-upstream-drift.md §6."
        ),
        suggestion=(
            f"Drop the marketplace-side {field!r} field and let "
            "upstream win, OR re-align the marketplace value with "
            "upstream."
        ),
    )


def diff_marketplace_vs_upstream(
    entry: dict[str, Any],
    upstream: dict[str, Any],
) -> list[FieldDrift]:
    """Compare a marketplace entry to its upstream plugin.json.

    Returns drifts ordered by severity (MAJOR > MINOR > NIT) so the caller
    can emit them in that order. Returns empty list when there's no drift.

    Compared fields:
      - name       → MAJOR (RC-MKPL-NAME-MISMATCH)
      - version    → MINOR (RC-MKPL-VERSION-DRIFT)
      - description, author, keywords, homepage → NIT (RC-MKPL-METADATA-DRIFT)

    The author field is normalised: a string equality is enough; an object
    is compared by its `name` field (the canonical schema) to avoid spurious
    NITs over `{name, email}` vs `name` dict shape.
    """
    drifts: list[FieldDrift] = []
    raw_label = entry.get("name")
    entry_label: str = raw_label if isinstance(raw_label, str) else "<unnamed entry>"

    # MAJOR: name
    entry_name = entry.get("name")
    upstream_name = upstream.get("name")
    if isinstance(entry_name, str) and isinstance(upstream_name, str) and entry_name != upstream_name:
        drifts.append(_make_name_drift(entry_name, upstream_name, entry_label))

    # MINOR: version
    entry_version = entry.get("version")
    upstream_version = upstream.get("version")
    if isinstance(entry_version, str) and isinstance(upstream_version, str) and entry_version != upstream_version:
        drifts.append(_make_version_drift(entry_version, upstream_version, entry_label))

    # NIT: description
    entry_desc = entry.get("description")
    upstream_desc = upstream.get("description")
    if isinstance(entry_desc, str) and isinstance(upstream_desc, str) and entry_desc != upstream_desc:
        drifts.append(_make_metadata_drift("description", entry_desc, upstream_desc, entry_label))

    # NIT: keywords
    entry_kw = entry.get("keywords")
    upstream_kw = upstream.get("keywords")
    if isinstance(entry_kw, list) and isinstance(upstream_kw, list) and entry_kw != upstream_kw:
        drifts.append(_make_metadata_drift("keywords", entry_kw, upstream_kw, entry_label))

    # NIT: author (string or object form — compare name-equivalent)
    entry_author = entry.get("author")
    upstream_author = upstream.get("author")
    entry_author_name = _author_name(entry_author)
    upstream_author_name = _author_name(upstream_author)
    if entry_author_name is not None and upstream_author_name is not None and entry_author_name != upstream_author_name:
        drifts.append(_make_metadata_drift("author", entry_author_name, upstream_author_name, entry_label))

    # NIT: homepage
    entry_home = entry.get("homepage")
    upstream_home = upstream.get("homepage")
    if isinstance(entry_home, str) and isinstance(upstream_home, str) and entry_home != upstream_home:
        drifts.append(_make_metadata_drift("homepage", entry_home, upstream_home, entry_label))

    return drifts


def _author_name(value: Any) -> str | None:
    """Normalise an author field (string OR {name, email} dict) to a name."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        name = value.get("name")
        return name if isinstance(name, str) else None
    return None


# ────────────────────────────────────────────────────────────────────────────
# CLI for manual diagnosis (e.g. cpv-doctor invocation)
# ────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python cpv_upstream_plugin_json.py <marketplace.json>``.

    For each entry, prints "<entry-name>: <hit | unreachable | drift-codes>".
    Exit code 0 on every fetch resolved (or skipped); 1 on errors.
    """
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("Usage: cpv_upstream_plugin_json.py <marketplace.json>", file=sys.stderr)
        return 1
    mkpl_path = Path(args[0]).resolve()
    if not mkpl_path.is_file():
        print(f"Error: {mkpl_path} is not a file", file=sys.stderr)
        return 1
    try:
        mkpl = json.loads(mkpl_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error: cannot read marketplace.json: {e}", file=sys.stderr)
        return 1

    marketplace_dir = mkpl_path.parent if mkpl_path.parent.name == ".claude-plugin" else mkpl_path.parent
    if mkpl_path.parent.name == ".claude-plugin":
        marketplace_dir = mkpl_path.parent.parent

    if not cross_validate_enabled(marketplace_dir):
        print("Cross-check disabled (opt-out flag or env var).", file=sys.stderr)
        return 0

    plugins = mkpl.get("plugins") if isinstance(mkpl, dict) else None
    if not isinstance(plugins, list):
        print("Error: marketplace.json `plugins` is not a list", file=sys.stderr)
        return 1

    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        if entry_skips_cross_check(entry):
            print(f"{entry.get('name', '<?>')}: skipped (_cpv_skip_upstream_check)")
            continue
        upstream = fetch_upstream_plugin_json(entry, marketplace_dir=marketplace_dir)
        if upstream is None:
            print(f"{entry.get('name', '<?>')}: unreachable")
            continue
        drifts = diff_marketplace_vs_upstream(entry, upstream)
        if not drifts:
            print(f"{entry.get('name', '<?>')}: clean")
            continue
        codes = ", ".join(sorted({d.code for d in drifts}))
        print(f"{entry.get('name', '<?>')}: drift — {codes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
