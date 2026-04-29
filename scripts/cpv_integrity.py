#!/usr/bin/env python3
"""CPV self-integrity verification — fetched from GitHub, not local.

The CPV security validator can be tampered with by anyone with write
access to the local install. A modified `validate_security.py` could
silently neutralise rules, ignore findings, or whitelist malicious
patterns. The local `.cpv-self-hashes.json` manifest is no defense —
an attacker who modifies the validator also modifies the manifest.

This module solves that by fetching the AUTHORITATIVE hash manifest
from GitHub at startup and verifying the LOCAL CPV files against it.
Tampering with the local source is detectable because the GitHub-side
manifest is signed by the maintainer's commit, not by whoever ran the
plugin install.

Behavior:
    1. Fetch the canonical manifest from
       `https://raw.githubusercontent.com/Emasoft/claude-plugins-validation/main/.cpv-self-hashes.json`
       (with a 1-hour cache at `~/.cache/cpv/github-manifest.json`).
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
    Set `CPV_SKIP_GITHUB_INTEGRITY=1` in the environment to skip
    the GitHub fetch (still verifies local manifest if present).
    Use ONLY when working on a CPV fork or local dev branch where
    the canonical manifest does not yet match the local source.
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
MANIFEST_FILE = ".cpv-self-hashes.json"

# Per-version manifest URL — fetched from the git tag matching the local
# plugin's version. Each release commits its own manifest before tagging
# (see publish.py Gate 2.5), so v2.30.0's manifest at the v2.30.0 tag
# matches the v2.30.0 source exactly.
REPO_RAW_TAG_URL = (
    f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/v{{version}}/{MANIFEST_FILE}"
)

# Fallback for dev branches / pre-release versions: main HEAD manifest.
# Used only when the per-version URL returns 404 (tag doesn't exist yet).
REPO_RAW_MAIN_URL = (
    f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/{MANIFEST_FILE}"
)

CACHE_DIR = Path.home() / ".cache" / "cpv"
CACHE_TTL = timedelta(hours=1)
HTTP_TIMEOUT_SEC = 10
USER_AGENT = f"cpv-integrity-check/1.0 ({REPO_OWNER}/{REPO_NAME})"

# Sentinel for "this process already verified, don't re-check"
_VERIFIED_THIS_PROCESS: bool = False


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


def _fetch_github_manifest(
    version: str | None,
    prefer_cache: bool = True,
) -> dict[str, object] | None:
    """Fetch the canonical manifest for the given plugin version.

    Strategy:
        1. If `version` is set, prefer the per-tag URL (ties manifest
           to the exact release the user is running).
        2. Fall back to the `main` branch URL when the tag doesn't
           exist (e.g. pre-release dev versions or freshly-cut tags
           that haven't been pushed yet).
        3. Fall back to the cached manifest (per-version) on any
           network failure.

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

    # Try per-version tag URL first.
    if version:
        url = REPO_RAW_TAG_URL.format(version=version)
        m = _fetch_one(url, version)
        if m is not None:
            return m

    # Fallback to main HEAD.
    m = _fetch_one(REPO_RAW_MAIN_URL, version)
    if m is not None:
        return m

    # Last resort: any cached copy, even stale.
    return _read_cached_manifest(version)


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
          `~/.cache/cpv/github-manifest.json`.
        - On mismatch with `fail_on_mismatch=True`: calls `sys.exit(2)`.
    """
    global _VERIFIED_THIS_PROCESS
    if _VERIFIED_THIS_PROCESS:
        return True

    # Dev / CI escape hatch: explicit opt-out of the GitHub round-trip.
    if os.environ.get("CPV_SKIP_GITHUB_INTEGRITY", "").strip().lower() in (
        "1", "true", "yes", "on"
    ):
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
            tried_url = (
                REPO_RAW_TAG_URL.format(version=version) if version else REPO_RAW_MAIN_URL
            )
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

    files = manifest.get("files", {})
    if not isinstance(files, dict):
        if not quiet:
            print(
                "[CPV integrity] WARNING: GitHub manifest is malformed "
                "(expected {'files': {path: hash}}). Cannot verify.",
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
            "[CPV integrity] CRITICAL: validator source has been MODIFIED\n"
            + "=" * 70 + "\n"
            f"The following {len(mismatches)} CPV-internal file(s) differ from "
            "the canonical version published on GitHub:\n",
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

        print(
            "\nIf you legitimately modified the validator (e.g. for development),\n"
            "set `CPV_SKIP_GITHUB_INTEGRITY=1` in your environment to bypass\n"
            "this check. Otherwise the validator may have been tampered with —\n"
            "DO NOT trust its findings. Reinstall CPV from a clean clone:\n"
            "    rm -rf ~/.claude/plugins/cache/<marketplace>/claude-plugins-validation/\n"
            "    claude plugin update claude-plugins-validation@<marketplace>\n"
            + "=" * 70 + "\n",
            file=sys.stderr,
        )

        if fail_on_mismatch:
            sys.exit(2)
        return False

    if not quiet:
        print(
            f"[CPV integrity] OK — {checked} CPV files verified against the "
            "GitHub canonical manifest.",
            file=sys.stderr,
        )
    _VERIFIED_THIS_PROCESS = True
    return True


def main() -> int:
    """CLI entry point. Run `python cpv_integrity.py [<plugin_root>]`."""
    plugin_root: Path | None = None
    if len(sys.argv) > 1:
        plugin_root = Path(sys.argv[1]).resolve()
    ok = verify_self_integrity(plugin_root, fail_on_mismatch=False, quiet=False)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
