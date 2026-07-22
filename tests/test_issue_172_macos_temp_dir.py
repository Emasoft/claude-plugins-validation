#!/usr/bin/env python3
"""Two-sided tests for issue #172 — the absolute-path portability check must be
even-handed about each platform's temp directory.

Issue #172 (cross-platform temp folders): a plugin documenting macOS's per-user
temp dir (``$TMPDIR`` → ``/var/folders/<xx>/<hash>/T/…``) got a publish-blocking
portability finding in a doc, because that path matches the ``/var`` system-path
pattern and was NOT in ``ALLOWED_DOC_PATH_PREFIXES`` — while the Linux equivalents
were silent (``/tmp`` is never matched by ``ABSOLUTE_PATH_PATTERNS`` at all, and
``/var/tmp`` is already allowlisted). Windows temp forms (``C:\\Windows\\Temp``,
``%TEMP%``) and the ``/private/var/folders`` symlink form are not matched by the
pattern set either, so they need no entry; the ONLY real cross-platform asymmetry
is the macOS ``/var/folders/`` form.

Fix: allowlist ``/var/folders/`` in doc files too, so any platform's temp dir can
be named even-handedly. Every test below is two-sided — the macOS-temp FP clears
AND a genuine leak / non-temp system path / hardcoded-in-code path still fires.

NB on path shapes: this validator has a pre-existing guard (in
``scan_file_for_absolute_paths``) that skips any match containing a regex-special
char — ``.`` included — so every test path here is deliberately DOT-FREE to
actually exercise the pattern, unrelated to the #172 fix.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (  # noqa: E402
    ALLOWED_DOC_PATH_PREFIXES,
    ValidationReport,
    scan_file_for_absolute_paths,
)


def _scan(tmp_path: Path, name: str, content: str) -> ValidationReport:
    """Write ``content`` to ``tmp_path/name`` and run the absolute-path scan."""
    f = tmp_path / name
    f.write_text(content)
    report = ValidationReport()
    scan_file_for_absolute_paths(f, report, name)
    return report


def _blocking_levels(report: ValidationReport) -> list[str]:
    """MAJOR/MINOR/CRITICAL levels present (the ones a portability finding uses)."""
    return [r.level for r in report.results if r.level in {"CRITICAL", "MAJOR", "MINOR"}]


class TestVarFoldersAllowlisted:
    """The macOS temp-dir prefix is registered in the doc allowlist."""

    def test_var_folders_prefix_is_in_the_allowlist(self) -> None:
        """``/var/folders/`` is an allowed doc path prefix (the #172 fix)."""
        assert "/var/folders/" in ALLOWED_DOC_PATH_PREFIXES


class TestMacosTempDirFPClears:
    """A macOS ``/var/folders/…`` temp path in a doc no longer blocks."""

    def test_var_folders_temp_in_markdown_doc_clears(self, tmp_path: Path) -> None:
        """macOS $TMPDIR base in a .md → no MAJOR/MINOR finding."""
        report = _scan(
            tmp_path,
            "README.md",
            "scratch dir: /var/folders/zz/abc123qq/T/plugintmp for state\n",
        )
        assert _blocking_levels(report) == []

    def test_var_folders_temp_in_txt_doc_clears(self, tmp_path: Path) -> None:
        """The same path in a .txt doc also clears."""
        report = _scan(
            tmp_path,
            "NOTES.txt",
            "temp lives at /var/folders/9m/hh00gg11/T/buildcache here\n",
        )
        assert _blocking_levels(report) == []


class TestVarFoldersFNSafety:
    """FN-safety: the allowlist entry did NOT over-broaden ``/var`` or the doc scope."""

    def test_non_temp_var_path_still_fires(self, tmp_path: Path) -> None:
        """A non-temp /var system path (/var/spool) is NOT cleared → still fires."""
        report = _scan(
            tmp_path,
            "README.md",
            "queue at /var/spool/mail/daemonuser here\n",
        )
        assert _blocking_levels(report), "a non-temp /var system path must still fire"

    def test_users_home_leak_still_fires(self, tmp_path: Path) -> None:
        """A /Users/<name>/… dev/home path is a real leak → still fires."""
        report = _scan(
            tmp_path,
            "README.md",
            "built at /Users/zzrealdev/secretproj/buildout dir\n",
        )
        assert _blocking_levels(report), "a /Users home leak must still fire"

    def test_var_folders_in_shell_script_still_fires(self, tmp_path: Path) -> None:
        """The allowlist is DOC-only: hardcoding /var/folders in code is a real
        portability bug (that path is per-boot/per-session on macOS) → still fires."""
        report = _scan(
            tmp_path,
            "setup.sh",
            "SCRATCH=/var/folders/zz/abc123qq/T/plugintmp\n",
        )
        assert _blocking_levels(report), "hardcoding /var/folders in code must still fire"
