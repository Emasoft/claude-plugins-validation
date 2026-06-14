#!/usr/bin/env python3
"""Issue #117 — the "users will need to compile" WARNING must not fire when the
plugin ships prebuilt binaries as RELEASE ASSETS plus a checksum-verified
installer.

A compiled-source plugin (e.g. a Rust crate) that ships its binaries out of
tree — as per-platform release tarballs downloaded + sha256-verified by an
``install*.sh`` — is NOT a "must compile" case: the default install path is a
checksum-verified prebuilt-binary download; compiling is the last-resort
fallback. Committing multi-MB per-platform binaries into ``bin/`` is the
anti-pattern, so this legitimate distribution shape must not draw the advisory
compile WARNING.

The fix detects a release-asset installer — an ``install*.sh`` / ``*install*.sh``
that (a) downloads a release asset (``gh release download`` OR a curl/wget of a
``.tar.gz``/``.tgz``/``.zip``) AND (b) verifies it (``sha256``/``shasum``/
``sha256sum``) — and demotes the compile WARNING to INFO when present.

Two-sided coverage:
  * FP side — a Rust crate + an installer that downloads + sha256-verifies a
    release tarball → no compile WARNING (an INFO is emitted instead).
  * Genuine side — a Rust crate with no ``bin/`` binaries and no release-asset
    installer (or only a build-from-source ``install.sh``) → STILL WARNs.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


# ── Helpers ────────────────────────────────────────────────────────────────────


def _rust_crate(plugin_root: Path, crate_subdir: str = "scripts/memgrep") -> Path:
    """Create a minimal Rust crate (source + Cargo.toml) under the plugin tree."""
    crate = plugin_root / crate_subdir
    crate.mkdir(parents=True)
    (crate / "main.rs").write_text("fn main() {}\n")
    (crate / "Cargo.toml").write_text('[package]\nname = "memgrep"\nversion = "0.1.0"\n')
    return crate


def _run_validator(plugin_root: Path):
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_cross_platform

    report = ValidationReport()
    validate_cross_platform(plugin_root, report)
    return report


def _compile_warnings(report) -> list:
    return [r for r in report.results if r.level == "WARNING" and "will need to compile" in r.message]


def _release_infos(report) -> list:
    return [r for r in report.results if r.level == "INFO" and "release assets" in r.message]


# ── FP side: release-asset installer suppresses the compile WARNING ─────────────


def test_rust_crate_with_release_installer_no_compile_warning(tmp_path: Path) -> None:
    """A Rust crate + an installer that downloads + sha256-verifies a release
    tarball → no compile WARNING; an INFO is emitted instead."""
    plugin = tmp_path / "rust-release"
    _rust_crate(plugin)
    (plugin / "scripts" / "install-memgrep.sh").write_text(
        "#!/usr/bin/env bash\n"
        "curl -fsSL https://github.com/x/y/releases/download/v1/memgrep-linux-x64.tar.gz -o m.tar.gz\n"
        "sha256sum -c m.tar.gz.sha256\n"
        "tar xzf m.tar.gz\n"
    )
    report = _run_validator(plugin)
    warns = _compile_warnings(report)
    assert not warns, f"Release-asset installer must suppress compile WARNING, got: {[w.message for w in warns]}"
    assert _release_infos(report), "Expected a release-asset INFO when the WARNING is suppressed"


def test_release_installer_gh_release_download_form(tmp_path: Path) -> None:
    """The ``gh release download`` + ``shasum`` form is recognised too."""
    plugin = tmp_path / "rust-gh"
    _rust_crate(plugin)
    (plugin / "install.sh").write_text(
        "#!/usr/bin/env bash\n"
        "gh release download v1 --pattern 'memgrep-*-x64.tar.gz'\n"
        "shasum -a 256 -c memgrep-linux-x64.tar.gz.sha256\n"
    )
    report = _run_validator(plugin)
    assert not _compile_warnings(report), "gh-release-download + shasum installer must suppress the WARNING"


# ── Genuine side: must STILL warn ───────────────────────────────────────────────


def test_rust_crate_no_installer_still_warns(tmp_path: Path) -> None:
    """A Rust crate with a build system but no ``bin/`` binaries and no release
    installer STILL WARNs — users genuinely must compile."""
    plugin = tmp_path / "rust-plain"
    _rust_crate(plugin)
    report = _run_validator(plugin)
    warns = _compile_warnings(report)
    assert len(warns) == 1, f"Expected the compile WARNING, got: {[r.message for r in report.results]}"


def test_rust_crate_build_only_installer_still_warns(tmp_path: Path) -> None:
    """A build-from-source ``install.sh`` (``cargo build``, no download + verify)
    does NOT qualify as a release-asset installer → STILL WARNs."""
    plugin = tmp_path / "rust-buildonly"
    _rust_crate(plugin)
    (plugin / "install.sh").write_text("#!/usr/bin/env bash\ncargo build --release\n")
    report = _run_validator(plugin)
    warns = _compile_warnings(report)
    assert len(warns) == 1, f"A build-only installer must NOT suppress the WARNING, got: {[w.message for w in warns]}"


def test_release_download_without_checksum_still_warns(tmp_path: Path) -> None:
    """FN-safety: an installer that downloads a release tarball but does NOT
    verify a checksum does NOT qualify (both signals are required) → STILL WARNs."""
    plugin = tmp_path / "rust-nochecksum"
    _rust_crate(plugin)
    (plugin / "install.sh").write_text(
        "#!/usr/bin/env bash\n"
        "curl -fsSL https://github.com/x/y/releases/download/v1/memgrep-linux-x64.tar.gz -o m.tar.gz\n"
        "tar xzf m.tar.gz\n"
    )
    report = _run_validator(plugin)
    warns = _compile_warnings(report)
    assert len(warns) == 1, f"Download-without-checksum must NOT suppress the WARNING, got: {[w.message for w in warns]}"
