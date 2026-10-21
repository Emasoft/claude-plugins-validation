#!/usr/bin/env python3
"""Plugin self-integrity verification — fetched from GitHub, not local.

The CPV security validator can be tampered with by anyone with write
access to the local install. A modified `validate_security.py` could
silently neutralise rules, ignore findings, or whitelist malicious
patterns. The local `.plugin-self-hashes.json` manifest is no defense —
an attacker who modifies the validator also modifies the manifest.

This module solves that by fetching the AUTHORITATIVE hash manifest
from GitHub at startup and verifying the LOCAL CPV files against it.
Tampering with the local source is detectable because the GitHub-side
manifest is signed by the maintainer's commit, not by whoever ran the
plugin install.

This is the **canonical** module per TRDD-bbff5bc5 (publish.py auth
standard). For one release (v2.51.0) the legacy `cpv_integrity` module
still exists as a thin shim that re-exports from here. The legacy shim
is removed in v2.53.0.

Behavior:
    1. Fetch the canonical manifest from
       `https://raw.githubusercontent.com/Emasoft/claude-plugins-validation/main/.plugin-self-hashes.json`
       (with a 1-hour cache at `~/.cache/cpv/github-manifest-vN.json`).
       Falls back to the legacy filename `.cpv-self-hashes.json` for
       one release while v2.50.x cached clients are still in the wild.
    2. For each file in the manifest that exists locally, compute its
       SHA256 and compare to the canonical hash.
    3. On mismatch, print a CRITICAL warning naming every modified
       file and (by default) exit with code 2 — refusing to trust the
       validator's output.
    4. On network failure, fall back to the cached manifest. If no
       cache is available, emit a warning but allow execution to
       continue (user may be intentionally offline).
       This fail-open applies to PLUGIN mode only. A self-located WHEEL
       install (issue #243: uvx / pip) is checked against the manifest of
       its exact release tag, only for the files the wheel ships, and
       REFUSES (exit 2) when that manifest is unavailable or anything
       differs; so does a root that is neither a wheel nor a plugin.

Every CPV validator entry point (`validate_security.py`,
`validate_plugin.py`, `validate_skill.py`, `validate_marketplace.py`,
etc.) MUST call `verify_self_integrity()` as the first action of its
`main()`. The check is fast (one HTTP GET + SHA256 of ~100 files,
typically < 200ms) and idempotent (cached after the first call per
process).

Bypass for development:
    Set `PLUGIN_SKIP_GITHUB_INTEGRITY=1` in the environment to skip
    the GitHub fetch (still verifies local manifest if present).
    The legacy `CPV_SKIP_GITHUB_INTEGRITY=1` is still honoured for
    backward compatibility (a DeprecationWarning is printed once per
    process). The legacy alias will be removed in a future major release.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

REPO_OWNER = "Emasoft"
REPO_NAME = "claude-plugins-validation"

MANIFEST_FILE = ".plugin-self-hashes.json"
MANIFEST_FILE_LEGACY = ".cpv-self-hashes.json"  # removed in v2.53.0

# Per-version manifest URLs — fetched from the git tag matching the local
# plugin's version. Each release commits its own manifest before tagging
# (see publish.py Gate 9), so v2.51.0's manifest at the v2.51.0 tag matches
# the v2.51.0 source exactly.
REPO_RAW_TAG_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/v{{version}}/{MANIFEST_FILE}"
REPO_RAW_TAG_URL_LEGACY = (
    f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/v{{version}}/{MANIFEST_FILE_LEGACY}"
)

# Fallback for dev branches / pre-release versions: main HEAD manifest.
# Used only when the per-version URL returns 404 (tag doesn't exist yet).
REPO_RAW_MAIN_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/{MANIFEST_FILE}"
REPO_RAW_MAIN_URL_LEGACY = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/{MANIFEST_FILE_LEGACY}"

CACHE_DIR = Path.home() / ".cache" / "cpv"
CACHE_TTL = timedelta(hours=1)
HTTP_TIMEOUT_SEC = 10
USER_AGENT = f"cpv-integrity-check/2.0 ({REPO_OWNER}/{REPO_NAME})"

# Sentinels for "this process already verified / warned, don't repeat"
_VERIFIED_THIS_PROCESS: bool = False
_LEGACY_ENV_WARNED: bool = False
_LEGACY_FILENAME_WARNED: bool = False


def _sha256_of_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError):
        return None


def _read_local_plugin_version(plugin_root: Path) -> str | None:
    """Read the plugin's own version from `.claude-plugin/plugin.json`."""
    pj = plugin_root / ".claude-plugin" / "plugin.json"
    if not pj.is_file():
        return None
    try:
        data = json.loads(pj.read_text(encoding="utf-8"))
        v = data.get("version")
        return str(v) if isinstance(v, str) else None
    except (OSError, json.JSONDecodeError):
        return None


# A plugin-cache install lives at <cache>/<marketplace>/<plugin>/<version>/, so a
# sibling of plugin_root is another cached version ONLY in that layout. In a dev
# checkout plugin_root.parent is an arbitrary WORKSPACE, and without the two
# gates below its unrelated sibling dirs (reports/, docs_dev/, thoughts/, ...)
# were printed as runnable `python3 .../remote_validation.py` recovery commands
# for launcher files that do not exist — in directories agents routinely write
# to, so the hint was both wrong and a place a planted script could be endorsed.
_VERSION_DIR_RE = re.compile(r"\d+(?:\.\d+)+")


def _discover_sibling_cached_versions(plugin_root: Path) -> list[Path]:
    """Sibling dirs that are REAL cached versions of this plugin, newest first.

    A sibling qualifies only when its NAME is a dotted numeric version AND it
    actually ships scripts/remote_validation.py — the hint must never suggest
    running a launcher whose existence was not verified.
    """
    out: list[Path] = []
    try:
        for p in plugin_root.parent.iterdir():
            if (
                p.is_dir()
                and p.name != plugin_root.name
                and _VERSION_DIR_RE.fullmatch(p.name)
                and (p / "scripts" / "remote_validation.py").is_file()
            ):
                out.append(p)
    except OSError:
        return []
    out.sort(key=lambda p: tuple(int(x) for x in p.name.split(".")), reverse=True)
    return out


def _cache_path_for_version(version: str | None) -> Path:
    """Cache file is per-version so different installations don't collide."""
    safe = (version or "main").replace("/", "_").replace(":", "_")
    return CACHE_DIR / f"github-manifest-v{safe}.json"


def _read_cached_manifest(version: str | None) -> dict[str, object] | None:
    cache_path = _cache_path_for_version(version)
    if not cache_path.is_file():
        return None
    try:
        parsed = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _fetch_one(url: str, version: str | None) -> dict[str, object] | None:
    """Fetch a single URL and cache it for the given version key."""
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:  # noqa: S310 - hardcoded HTTPS
            data = resp.read().decode("utf-8")
        parsed = json.loads(data)
    except (URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None
    if not isinstance(parsed, dict):
        return None
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path_for_version(version).write_text(data, encoding="utf-8")
    except OSError:
        pass  # Cache write failures are non-fatal.
    return parsed


def _warn_legacy_filename_once() -> None:
    """Emit a single per-process WARNING when the legacy filename is hit."""
    global _LEGACY_FILENAME_WARNED
    if _LEGACY_FILENAME_WARNED:
        return
    _LEGACY_FILENAME_WARNED = True
    print(
        f"[CPV integrity] NOTE: Fetched legacy '{MANIFEST_FILE_LEGACY}' manifest "
        f"(new name '{MANIFEST_FILE}' returned 404 at this tag). This compat "
        "fallback is removed in v2.53.0.",
        file=sys.stderr,
    )


def _fetch_github_manifest(
    version: str | None,
    prefer_cache: bool = True,
) -> dict[str, object] | None:
    """Fetch the canonical manifest for the given plugin version.

    Strategy (per TRDD-bbff5bc5 §2.2):
        1. If `version` is set, prefer the per-tag URL with NEW filename.
        2. Same per-tag URL with LEGACY filename (one-release compat).
        3. Fall back to the `main` branch URL with NEW filename.
        4. Same `main` branch URL with LEGACY filename.
        5. Per-version cache (any age) on total network failure.

    `prefer_cache=True` short-circuits to the cache if the cached copy
    is younger than `CACHE_TTL`. Set False to force a fresh fetch.
    """
    if prefer_cache:
        cache_path = _cache_path_for_version(version)
        if cache_path.is_file():
            try:
                mtime = datetime.fromtimestamp(cache_path.stat().st_mtime)
                if datetime.now() - mtime < CACHE_TTL:
                    cached = _read_cached_manifest(version)
                    if cached is not None:
                        return cached
            except OSError:
                pass

    # 1. Try per-version tag URL with NEW filename first.
    if version:
        url = REPO_RAW_TAG_URL.format(version=version)
        m = _fetch_one(url, version)
        if m is not None:
            return m
        # 2. Per-tag URL with LEGACY filename.
        url_legacy = REPO_RAW_TAG_URL_LEGACY.format(version=version)
        m = _fetch_one(url_legacy, version)
        if m is not None:
            _warn_legacy_filename_once()
            return m

    # 3. Main HEAD with NEW filename.
    m = _fetch_one(REPO_RAW_MAIN_URL, version)
    if m is not None:
        return m

    # 4. Main HEAD with LEGACY filename.
    m = _fetch_one(REPO_RAW_MAIN_URL_LEGACY, version)
    if m is not None:
        _warn_legacy_filename_once()
        return m

    # 5. Last resort: any cached copy, even stale.
    return _read_cached_manifest(version)


def _read_skip_env_var() -> bool:
    """Return True if either skip env var is truthy.

    Per TRDD-bbff5bc5 §6.1, the canonical name is
    `PLUGIN_SKIP_GITHUB_INTEGRITY`. The legacy `CPV_SKIP_GITHUB_INTEGRITY`
    is still honoured with a one-line DeprecationWarning per process for
    backward compatibility. The original removal target (v2.53.0) was not
    enforced; removal is now deferred until a future major bump.
    """
    truthy = ("1", "true", "yes", "on")
    new_val = os.environ.get("PLUGIN_SKIP_GITHUB_INTEGRITY", "").strip().lower()
    if new_val in truthy:
        return True
    legacy_val = os.environ.get("CPV_SKIP_GITHUB_INTEGRITY", "").strip().lower()
    if legacy_val in truthy:
        global _LEGACY_ENV_WARNED
        if not _LEGACY_ENV_WARNED:
            _LEGACY_ENV_WARNED = True
            print(
                "[CPV integrity] DEPRECATED: env var 'CPV_SKIP_GITHUB_INTEGRITY' "
                "is renamed to 'PLUGIN_SKIP_GITHUB_INTEGRITY' (TRDD-bbff5bc5). "
                "The legacy name is still honoured but will be removed in a "
                "future major release — please update.",
                file=sys.stderr,
            )
        return True
    return False


def _detect_added_files(plugin_root: Path, manifest_files: dict[str, object]) -> list[str]:
    """Shipped files present on disk but ABSENT from the canonical manifest.

    TRDD-b8c6d04f change 3 — integrity verification must be BIDIRECTIONAL.
    Besides catching manifest entries that were modified or deleted locally,
    it must catch an ADDED (inoculated) shipped file that is not in the
    canonical manifest at all — otherwise an attacker who drops a new
    `scripts/evil.py` (or a malicious skill / command / agent) into the
    install evades detection, because the manifest→local loop only iterates
    entries that are ALREADY in the manifest. "Even one file skipped is
    enough to poison the plugin" — so an extra file must be caught too.

    Enumeration reuses `enumerate_shipped_files` (the same git-tracked / walk
    logic the manifest builder uses), so runtime cruft, caches, and
    gitignored files are excluded symmetrically and never false-flag. The two
    manifest files are excluded by the enumerator. Returns a sorted list of
    relative paths; empty when enumeration is unavailable (graceful — an
    enumeration failure must not become a spurious integrity CRITICAL).
    """
    try:
        scripts_dir = Path(__file__).resolve().parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from _plugin_compute_hashes import enumerate_shipped_files
    except ImportError:
        return []
    try:
        on_disk = enumerate_shipped_files(plugin_root)
    except (OSError, ValueError):
        return []
    manifest_set = {k for k in manifest_files if isinstance(k, str)}
    return sorted(on_disk - manifest_set)


# ---------------------------------------------------------------------------
# Wheel (package-install) mode — issue #243.
#
# Under `uvx --from git+...@vX.Y.Z cpv-remote-validate`, CPV runs from a wheel
# installed into a site-packages dir. There, `Path(__file__).parent.parent` is
# site-packages itself: it has no `.claude-plugin/plugin.json`, so the plugin
# path read the version as None, fell back to the `main` manifest (now 404, so
# a stale cache was used) and reported every repository-only file the wheel
# never ships as "deleted locally" — exit 2 on every package install.
#
# Wheel mode is detected POSITIVELY (never from a missing plugin.json alone):
# this module sits in `<site-packages>/scripts/`, and exactly one
# `claude_plugins_validation-*.dist-info` beside it lists this very file in its
# RECORD. The version comes from THAT dist-info (not a sys.path lookup that
# could resolve a different install), only the manifest of the exact tag
# `v<version>` is trusted (no `main` fallback, cache keyed by the exact ref),
# and only the files the wheel ships are compared, each at its INSTALLED path.
# Anything that cannot be proven refuses: the owner's policy is "refuse to run
# if not validated by hash".
# ---------------------------------------------------------------------------

# Distribution name of the wheel. Pinned against pyproject [project].name by
# tests/test_issue_243_wheel_integrity.py so a rename updates both.
DIST_NAME = "claude-plugins-validation"

# Manifest key (repository path) -> installed path inside site-packages, for
# the files pyproject's [tool.hatch.build.targets.wheel.force-include] adds to
# the wheel. Pinned against pyproject by the same test. Everything else the
# wheel ships comes from `packages = ["scripts"]` and keeps its path.
_WHEEL_FORCE_INCLUDE: dict[str, str] = {
    ".markdownlint.json": "scripts/.markdownlint.json",
    ".markdownlintignore": "scripts/.markdownlintignore",
}

# Top-level dirs under site-packages that the wheel writes; the added-file walk
# covers exactly these.
_WHEEL_TOP_DIRS = ("scripts", "templates")

# OS-written cruft that may appear in any directory; never importable.
_WHEEL_OS_CRUFT_BASENAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})

# Skip switches that plugin mode honours but a package install ignores (#243).
_WHEEL_IGNORED_SKIP_VARS = ("PLUGIN_SKIP_GITHUB_INTEGRITY", "CPV_SKIP_GITHUB_INTEGRITY", "PYTEST_CURRENT_TEST")

# Backoff before the single retry of the tag-manifest fetch (seconds).
_WHEEL_FETCH_RETRY_SLEEP_SEC = 2.0

# The module file used for wheel detection. A module attribute (not a bare
# __file__ read) so tests can point detection at a fake site-packages tree.
_MODULE_FILE = Path(__file__)

_SITE_DIR_NAMES = frozenset({"site-packages", "dist-packages"})
_SELF_RECORD_PATH = "scripts/_plugin_verify_hashes.py"
# A version string is interpolated into a URL and a cache file name.
_SAFE_VERSION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]*")

REFUSAL_HEADLINE = (
    "CPV refused to run: its own integrity could not be verified. This is not a verdict on the plugin being validated."
)


class _WheelInstall:
    """A positively-proven wheel install of CPV."""

    def __init__(self, site_packages: Path, dist_info: Path) -> None:
        self.site_packages = site_packages
        self.dist_info = dist_info


class _WheelDetectionError(Exception):
    """Wheel-shaped install whose identity cannot be proven (fail closed)."""


def _refuse(reasons: list[str], *, fail_on_mismatch: bool) -> bool:
    """Refuse to run: print why (ALWAYS, even when quiet) and exit 2 / return False.

    Never memoizes success: `_VERIFIED_THIS_PROCESS` is left untouched so a
    later strict gate re-checks instead of short-circuiting (finding 141).
    """
    print("\n" + "=" * 70 + "\n[CPV integrity] CRITICAL: " + REFUSAL_HEADLINE, file=sys.stderr)
    for reason in reasons[:50]:
        print(f"  - {reason}", file=sys.stderr)
    if len(reasons) > 50:
        print(f"  …and {len(reasons) - 50} more", file=sys.stderr)
    print(
        "Reinstall CPV from a released tag; if you are developing CPV itself, run it "
        "from a source checkout instead.\n" + "=" * 70,
        file=sys.stderr,
    )
    if fail_on_mismatch:
        sys.exit(2)
    return False


def _note_ignored_skip_vars(context: str) -> None:
    """Tell the user (always, on stderr) which skip switches are being ignored, and why."""
    ignored = [name for name in _WHEEL_IGNORED_SKIP_VARS if os.environ.get(name, "").strip()]
    if ignored:
        print(
            f"[CPV integrity] NOTE: {', '.join(ignored)} is ignored for {context}; the integrity check runs anyway.",
            file=sys.stderr,
        )


def _record_lists_self(record: Path) -> bool:
    import csv

    try:
        with open(record, encoding="utf-8", newline="") as fh:
            return any(row and row[0] == _SELF_RECORD_PATH for row in csv.reader(fh))
    except (OSError, csv.Error, UnicodeDecodeError):
        return False


def _normalized_dist_name(name: str) -> str:
    """PEP 503/427 normalisation used in dist-info dir names: lowercase, runs of -_. -> _."""
    return re.sub(r"[-_.]+", "_", name).lower()


def _wheel_dist_info_prefix() -> str:
    """`<normalized DIST_NAME>-` — the dist-info dir is `<prefix><version>.dist-info`."""
    return _normalized_dist_name(DIST_NAME) + "-"


def _detect_wheel_install(module_file: Path) -> _WheelInstall | None:
    """Return the wheel install this module belongs to, or None if it is not one.

    Wheel mode requires ALL of: the module's dir is named `scripts`, its parent
    is a `site-packages`/`dist-packages` dir, and a `<dist>-*.dist-info` beside
    it lists this module in its RECORD. The dist-info pattern is derived from
    `DIST_NAME` (never hardcoded), so a package rename needs one edit. A dev /
    editable install (module not under site-packages) is therefore never wheel
    mode. More than one matching dist-info is ambiguous and raises
    `_WheelDetectionError`.
    """
    module_file = module_file.resolve()
    scripts_dir = module_file.parent
    site_packages = scripts_dir.parent
    if scripts_dir.name != "scripts" or site_packages.name not in _SITE_DIR_NAMES:
        return None
    prefix = _wheel_dist_info_prefix()
    try:
        candidates = sorted(
            p
            for p in site_packages.iterdir()
            if p.is_dir() and p.name.lower().startswith(prefix) and p.name.endswith(".dist-info")
        )
    except OSError:
        return None
    matches = [p for p in candidates if _record_lists_self(p / "RECORD")]
    if not matches:
        return None
    if len(matches) > 1:
        raise _WheelDetectionError("more than one dist-info claims this module: " + ", ".join(p.name for p in matches))
    return _WheelInstall(site_packages, matches[0])


def _wheel_version(install: _WheelInstall) -> str | None:
    """Version from the proven dist-info itself (its Name must normalise to DIST_NAME).

    Read via `importlib.metadata.Distribution.at(<the dist-info whose RECORD
    lists this module>)` rather than `importlib.metadata.version(DIST_NAME)`:
    the latter resolves through sys.path and could return a DIFFERENT install
    of the same distribution than the files being verified.
    """
    from importlib.metadata import Distribution

    try:
        dist = Distribution.at(install.dist_info)
        name = dist.metadata["Name"]
        version = dist.version
    except Exception:  # noqa: BLE001 - any unreadable metadata means "unknown" -> refuse
        return None
    if not isinstance(name, str) or _normalized_dist_name(name) != _normalized_dist_name(DIST_NAME):
        return None
    if not isinstance(version, str) or not _SAFE_VERSION_RE.fullmatch(version):
        return None
    return version


def _wheel_cache_path(version: str) -> Path:
    """Cache keyed by the EXACT tag ref, in its own namespace.

    Distinct from `_cache_path_for_version`, whose file for a version may hold a
    `main`-branch manifest written by the plugin-mode fallback.
    """
    return CACHE_DIR / f"github-manifest-tag-v{version}.json"


def _fetch_wheel_manifest(version: str) -> dict[str, object] | None:
    """Manifest at tag `v<version>` only — never `main`. Cache of that exact tag as fallback."""
    import time

    cache_path = _wheel_cache_path(version)
    url = REPO_RAW_TAG_URL.format(version=version)
    parsed: object = None
    data = ""
    # One retry with a short backoff: a single attempt is fragile on CI runners.
    for attempt in range(2):
        if attempt:
            time.sleep(_WHEEL_FETCH_RETRY_SLEEP_SEC)
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:  # noqa: S310 - hardcoded HTTPS
                data = resp.read().decode("utf-8")
            parsed = json.loads(data)
        except (URLError, OSError, json.JSONDecodeError, TimeoutError, UnicodeDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            break
    if isinstance(parsed, dict):
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(data, encoding="utf-8")
        except OSError:
            pass  # Cache write failures are non-fatal.
        return parsed
    # Network/parse failure: a cached copy of the SAME immutable tag only.
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return cached if isinstance(cached, dict) else None
    return None


def _wheel_expected_paths(files: dict[str, object]) -> dict[str, str]:
    """Installed path -> expected hash, for the manifest entries the wheel ships.

    Repository-only entries (workflows, tests, memory pages, plugin.json, ...)
    are never expected in a package install, so they are not reported deleted.
    """
    out: dict[str, str] = {}
    for key, expected in files.items():
        if not isinstance(key, str) or not isinstance(expected, str):
            continue
        if key in _WHEEL_FORCE_INCLUDE:
            out[_WHEEL_FORCE_INCLUDE[key]] = expected
        elif key.startswith("scripts/"):
            out[key] = expected
    return out


def _detect_added_wheel_files(site_packages: Path, expected: set[str]) -> list[str]:
    """Files under the wheel's top dirs that the manifest does not account for.

    The skip rule is applied HERE, without importing `_plugin_compute_hashes`
    (a file that is itself being verified — importing it first would let a
    tampered copy decide what the walk ignores). Skipped: `*.pyc` directly
    inside a `__pycache__` dir (bytecode caches Python writes) and the OS
    cruft basenames below. Everything else is reported, including a sourceless
    `*.pyc` outside `__pycache__` (importable; no legitimate wheel ships one)
    and any non-.pyc file inside `__pycache__` (a namespace-package path).
    The plugin-mode `_SHIPPED_WALK_SKIP_DIRS` is deliberately NOT reused: it
    skips `build`, `dist`, `reports`, ..., which would let
    `scripts/build/evil.py` hide.
    """
    added: list[str] = []
    for top in _WHEEL_TOP_DIRS:
        base = site_packages / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(site_packages)
            rel_path = rel.as_posix()
            if rel.parent.name == "__pycache__" and rel.suffix == ".pyc":
                continue
            if rel.name in _WHEEL_OS_CRUFT_BASENAMES:
                continue
            if rel_path not in expected:
                added.append(rel_path)
    return sorted(added)


def _verify_wheel_install(install: _WheelInstall, *, fail_on_mismatch: bool, quiet: bool) -> bool:
    """Verify a wheel install against the manifest of its exact release tag."""
    global _VERIFIED_THIS_PROCESS
    version = _wheel_version(install)
    if version is None:
        return _refuse(
            [f"the installed version of {DIST_NAME} could not be read from {install.dist_info}"],
            fail_on_mismatch=fail_on_mismatch,
        )
    manifest = _fetch_wheel_manifest(version)
    if manifest is None:
        return _refuse(
            [
                f"no hash manifest for tag v{version} could be fetched "
                f"({REPO_RAW_TAG_URL.format(version=version)}) and no cached copy of that tag "
                f"exists at {_wheel_cache_path(version)}"
            ],
            fail_on_mismatch=fail_on_mismatch,
        )
    files = manifest.get("hashed_files") or manifest.get("files")
    if not isinstance(files, dict):
        return _refuse([f"the v{version} hash manifest is malformed or empty"], fail_on_mismatch=fail_on_mismatch)
    expected = _wheel_expected_paths(files)
    if not expected:
        return _refuse(
            [f"the v{version} hash manifest lists none of the files this package ships"],
            fail_on_mismatch=fail_on_mismatch,
        )
    problems: list[str] = []
    for rel_path, expected_hash in sorted(expected.items()):
        local = install.site_packages / rel_path
        if not local.is_file():
            problems.append(f"{rel_path}  (shipped by v{version} but missing from this install)")
            continue
        actual = _sha256_of_file(local)
        expected_hex = expected_hash.split(":", 1)[-1] if expected_hash.startswith("sha256:") else expected_hash
        if actual != expected_hex:
            problems.append(
                f"{rel_path}  (changed: expected sha256:{expected_hex[:16]}…, actual sha256:{(actual or 'unreadable')[:16]}…)"
            )
    for added_rel in _detect_added_wheel_files(install.site_packages, set(expected)):
        problems.append(f"{added_rel}  (present in this install but NOT in the v{version} manifest — added file)")
    if problems:
        return _refuse(
            [f"{len(problems)} file(s) of this {DIST_NAME} v{version} install differ from its tag manifest:"]
            + problems,
            fail_on_mismatch=fail_on_mismatch,
        )
    if not quiet:
        print(
            f"[CPV integrity] OK — {len(expected)} files of the {DIST_NAME} v{version} package "
            "verified against the GitHub tag manifest.",
            file=sys.stderr,
        )
    _VERIFIED_THIS_PROCESS = True
    return True


def verify_self_integrity(
    plugin_root: Path | None = None,
    *,
    fail_on_mismatch: bool = True,
    quiet: bool = False,
) -> bool:
    """Verify local CPV files against the GitHub canonical manifest.

    Args:
        plugin_root: CPV plugin root. Defaults to the parent of this
            module's file (i.e., the validator deployment in use).
        fail_on_mismatch: When True (default), exit with code 2 if any
            CPV file differs from the canonical hash. When False, just
            return False so the caller can decide.
        quiet: When True, suppress the per-file "OK" log lines. Errors
            are still printed.

    Returns:
        True if every locally-present file matches the GitHub hash, OR
        if the network is unreachable AND no cached manifest exists
        (graceful degradation — user may be offline). False on mismatch
        when `fail_on_mismatch=False`. That offline fail-open applies to
        PLUGIN mode only (a root with `.claude-plugin/plugin.json`). A
        self-located WHEEL install (issue #243) and a root that is neither
        a wheel nor a plugin REFUSE instead: exit 2 (or False) whenever the
        exact-tag manifest is unavailable or anything differs.

    Side effects:
        - Writes / refreshes the cached manifest at
          `~/.cache/cpv/github-manifest-vN.json`.
        - On mismatch with `fail_on_mismatch=True`: calls `sys.exit(2)`.
    """
    global _VERIFIED_THIS_PROCESS
    if _VERIFIED_THIS_PROCESS:
        return True

    # Issue #243: a self-located run from a wheel (uvx / pip install) is
    # verified against the files THAT PACKAGE ships, at their installed
    # paths, using only the manifest of its exact release tag. Detection runs
    # BEFORE the skip checks below: owner policy is "refuse to run if not
    # validated by hash", so no env var and no pytest auto-skip can turn the
    # check off for a package install. (Plugin mode keeps its skips.)
    if plugin_root is None:
        try:
            wheel = _detect_wheel_install(_MODULE_FILE)
        except _WheelDetectionError as exc:
            return _refuse([f"wheel install could not be identified: {exc}"], fail_on_mismatch=fail_on_mismatch)
        if wheel is not None:
            _note_ignored_skip_vars("package installs")
            return _verify_wheel_install(wheel, fail_on_mismatch=fail_on_mismatch, quiet=quiet)
        plugin_root = _MODULE_FILE.resolve().parent.parent

    # Neither a proven wheel install nor a plugin root: there is no version to
    # anchor a manifest to, and the old fallback (version None -> `main`
    # manifest) compared CPV against a different tree. Refuse instead.
    # Runs BEFORE the plugin-mode skips: this case has no version to anchor, so
    # no skip switch may let it through (owner policy, #243).
    if not (plugin_root / ".claude-plugin" / "plugin.json").is_file():
        _note_ignored_skip_vars("a root that is neither a package install nor a plugin")
        return _refuse(
            [
                f"{plugin_root} is neither a CPV plugin root (no .claude-plugin/plugin.json) "
                f"nor a proven {DIST_NAME} wheel install (no matching dist-info RECORD)"
                ". A non-standard install layout with no site-packages/dist-packages dir "
                "(e.g. some Nix or conda layouts) is not recognised as a package install."
            ],
            fail_on_mismatch=fail_on_mismatch,
        )

    # Dev / CI escape hatch: explicit opt-out of the GitHub round-trip.
    if _read_skip_env_var():
        _VERIFIED_THIS_PROCESS = True
        return True

    # Auto-bypass when running under pytest. The variable PYTEST_CURRENT_TEST
    # is set by pytest for the duration of every test and is the canonical
    # way to detect "we are inside the test suite". Test runs by definition
    # use the working-tree validator source — so the GitHub gate would fire
    # on every commit-in-progress and break the dev loop. Setting the env
    # var explicitly in every subprocess.run() call would be repetitive and
    # easy to forget; making the gate self-disable here is the durable fix.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        _VERIFIED_THIS_PROCESS = True
        return True

    version = _read_local_plugin_version(plugin_root)
    manifest = _fetch_github_manifest(version)
    if manifest is None:
        # No GitHub, no cache. Cannot verify — warn loudly but allow
        # execution to continue. User may be offline; refusing to run
        # would be worse UX than running unverified with a warning.
        if not quiet:
            tried_url = REPO_RAW_TAG_URL.format(version=version) if version else REPO_RAW_MAIN_URL
            cache_path = _cache_path_for_version(version)
            print(
                "[CPV integrity] WARNING: Could not fetch the canonical "
                f"hash manifest from GitHub ({tried_url}) and no cached "
                f"copy is available at {cache_path}. Cannot verify "
                "validator integrity. If your network is restricted, this "
                "is expected; if not, the validator may be tampered with.",
                file=sys.stderr,
            )
        _VERIFIED_THIS_PROCESS = True
        return True

    # Forward-compat with v2 schema: v1 uses `files`, v2 uses `hashed_files`.
    # The v2.51.0 reader handles BOTH so a v2.53.0 schema-v2 manifest is
    # readable by this v2.51.0 module without error.
    files = manifest.get("hashed_files") or manifest.get("files") or {}
    if not isinstance(files, dict):
        if not quiet:
            print(
                "[CPV integrity] WARNING: GitHub manifest is malformed "
                "(expected {'files'|'hashed_files': {path: hash}}). Cannot verify.",
                file=sys.stderr,
            )
        _VERIFIED_THIS_PROCESS = True
        return True

    mismatches: list[tuple[str, str, str]] = []
    checked = 0
    for rel_path, expected in files.items():
        if not isinstance(rel_path, str) or not isinstance(expected, str):
            continue
        local = plugin_root / rel_path
        if not local.is_file():
            # File deleted locally but present in canonical manifest.
            # Could be a stale manifest (file removed in newer commit)
            # or a deletion attack. We treat it as missing and report.
            mismatches.append((rel_path, expected, "<missing>"))
            continue
        actual = _sha256_of_file(local)
        if actual is None:
            continue
        expected_hex = expected.split(":", 1)[-1] if expected.startswith("sha256:") else expected
        checked += 1
        if actual != expected_hex:
            mismatches.append((rel_path, expected_hex, actual))

    # Bidirectional check (TRDD-b8c6d04f change 3): an ADDED shipped file —
    # present locally but absent from the canonical manifest — is an
    # inoculation vector the manifest→local loop above can never see.
    for added_rel in _detect_added_files(plugin_root, files):
        mismatches.append((added_rel, "<not-in-manifest>", "<added>"))

    if mismatches:
        print(
            "\n" + "=" * 70 + "\n"
            "[CPV integrity] CRITICAL: integrity manifest mismatch\n" + "=" * 70 + "\n"
            f"The following {len(mismatches)} CPV-internal file(s) differ from, "
            "are missing from, or were added beyond the canonical manifest "
            "published on GitHub:\n",
            file=sys.stderr,
        )
        for rel_path, expected_hex, actual in mismatches[:50]:
            if actual == "<missing>":
                print(f"  - {rel_path}  (deleted locally)", file=sys.stderr)
            elif actual == "<added>":
                print(
                    f"  - {rel_path}  (present locally but NOT in the canonical manifest — added/inoculated file)",
                    file=sys.stderr,
                )
            else:
                print(
                    f"  - {rel_path}\n"
                    f"      expected: sha256:{expected_hex[:16]}…\n"
                    f"      actual:   sha256:{actual[:16]}…",
                    file=sys.stderr,
                )
        if len(mismatches) > 50:
            print(f"  …and {len(mismatches) - 50} more", file=sys.stderr)

        # Issue #18: distinguish three drift scenarios + offer the
        # known-clean-version recovery path explicitly.
        print(
            "\nThree scenarios produce this error — distinguish them before acting:\n"
            "\n"
            "  1. RELEASE-SHIPPED DRIFT (most common on a fresh install).\n"
            "     The CPV release pipeline forgot to refresh the manifest before\n"
            "     tagging this version. Nothing on your end is wrong. Workaround:\n"
            "     run an OLDER cached version that does match its manifest:\n",
            file=sys.stderr,
        )
        # Auto-discover sibling cached versions and print exact commands.
        # Only verified real installs qualify (see _discover_sibling_cached_versions).
        siblings = _discover_sibling_cached_versions(plugin_root)
        current_version = _read_local_plugin_version(plugin_root) or "?"
        if siblings:
            print(
                f"     You have these other cached versions next to v{current_version}:",
                file=sys.stderr,
            )
            for sib in siblings[:5]:
                sib_launcher = sib / "scripts" / "remote_validation.py"
                print(
                    f'       python3 "{sib_launcher}" <subcommand> <args>',
                    file=sys.stderr,
                )
        else:
            print(
                "     (no sibling cached versions found — you only have this one)",
                file=sys.stderr,
            )

        print(
            "\n  2. LEGITIMATE LOCAL MODIFICATIONS (CPV development / fork).\n"
            "     If YOU edited any of the listed files (working on a fork, debug\n"
            "     instrumentation, etc.), set the bypass env var:\n"
            "       export PLUGIN_SKIP_GITHUB_INTEGRITY=1\n"
            "\n"
            "  3. TAMPERING (rare but possible).\n"
            "     If you didn't modify anything AND the listed files include the\n"
            "     security validator (validate_security.py, validate_plugin.py,\n"
            "     _plugin_verify_hashes.py itself), do NOT trust this install. Reinstall:\n"
            "       rm -rf ~/.claude/plugins/cache/<marketplace>/claude-plugins-validation/\n"
            "       claude plugin update claude-plugins-validation@<marketplace>\n"
            "\n"
            "Tracking: https://github.com/Emasoft/claude-plugins-validation/issues/18\n" + "=" * 70 + "\n",
            file=sys.stderr,
        )

        if fail_on_mismatch:
            sys.exit(2)
        return False

    if not quiet:
        print(
            f"[CPV integrity] OK — {checked} CPV files verified against the GitHub canonical manifest.",
            file=sys.stderr,
        )
    _VERIFIED_THIS_PROCESS = True
    return True


def fetch_canonical_manifest(version: str | None) -> dict[str, object] | None:
    """Public-facing fetcher used by the self-scan skip logic.

    Returns the canonical hash manifest for the given version (per-tag
    GitHub URL), falling back to main HEAD then to any cached copy.
    Falls back through the legacy filename for one release. Returns None
    on total failure (no network, no cache, malformed response).

    Used by validate_security.py when the target plugin claims to be CPV
    but is NOT the running CPV. The local manifest of the target cannot
    be trusted (could be spoofed); only the GitHub-tag-anchored manifest
    can confirm whether a file is the canonical CPV file.
    """
    return _fetch_github_manifest(version)


def main() -> int:
    """CLI entry point. Run `python _plugin_verify_hashes.py [<plugin_root>]`."""
    plugin_root: Path | None = None
    if len(sys.argv) > 1:
        plugin_root = Path(sys.argv[1]).resolve()
    ok = verify_self_integrity(plugin_root, fail_on_mismatch=False, quiet=False)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
