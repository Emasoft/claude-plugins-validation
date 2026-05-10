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

Every CPV validator entry point (`validate_security.py`,
`validate_plugin.py`, `validate_skill.py`, `validate_marketplace.py`,
etc.) MUST call `verify_self_integrity()` as the first action of its
`main()`. The check is fast (one HTTP GET + SHA256 of ~100 files,
typically < 200ms) and idempotent (cached after the first call per
process).

Bypass for development:
    Set `PLUGIN_SKIP_GITHUB_INTEGRITY=1` in the environment to skip
    the GitHub fetch (still verifies local manifest if present).
    The legacy `CPV_SKIP_GITHUB_INTEGRITY=1` is honoured for one
    release with a DeprecationWarning printed once per process.
"""

from __future__ import annotations

import hashlib
import json
import os
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

    Per TRDD-bbff5bc5 §6.1, prefer the new name `PLUGIN_SKIP_GITHUB_INTEGRITY`
    and fall back to the legacy `CPV_SKIP_GITHUB_INTEGRITY` with a one-line
    DeprecationWarning per process. Both honoured for one release; legacy
    removed in v2.53.0.
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
                "Old name removed in v2.53.0.",
                file=sys.stderr,
            )
        return True
    return False


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
        when `fail_on_mismatch=False`.

    Side effects:
        - Writes / refreshes the cached manifest at
          `~/.cache/cpv/github-manifest-vN.json`.
        - On mismatch with `fail_on_mismatch=True`: calls `sys.exit(2)`.
    """
    global _VERIFIED_THIS_PROCESS
    if _VERIFIED_THIS_PROCESS:
        return True

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

    if plugin_root is None:
        plugin_root = Path(__file__).resolve().parent.parent

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

    if mismatches:
        print(
            "\n" + "=" * 70 + "\n"
            "[CPV integrity] CRITICAL: integrity manifest mismatch\n" + "=" * 70 + "\n"
            f"The following {len(mismatches)} CPV-internal file(s) differ from "
            "the canonical manifest published on GitHub:\n",
            file=sys.stderr,
        )
        for rel_path, expected_hex, actual in mismatches[:50]:
            if actual == "<missing>":
                print(f"  - {rel_path}  (deleted locally)", file=sys.stderr)
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
        try:
            cache_root = plugin_root.parent
            siblings = sorted(
                (p for p in cache_root.iterdir() if p.is_dir() and p.name != plugin_root.name),
                reverse=True,
            )
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
        except OSError:
            pass

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
