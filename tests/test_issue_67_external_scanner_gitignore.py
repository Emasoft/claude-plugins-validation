#!/usr/bin/env python3
"""Two-sided regression tests for GitHub issue #67 — EXTERNAL scanners
(trufflehog / cc-audit / tirith / semgrep / Cisco) must honor the plugin's
own ``.gitignore``, not just a HARDCODED dev-scratch dir list.

Reported case: a gitignored ``INPUT_DEV/**/*.zip`` research corpus produced
~97 trufflehog hits, because the external scanners run as subprocesses over the
WHOLE tree and post-filter findings against ``_DEV_SCRATCH_DIR_PARTS`` (only
``docs_dev/`` / ``scripts_dev/`` / ``reports/`` / …) — which doesn't cover an
arbitrary gitignored sub-tree.

Fix: a shared ``_external_finding_is_gitignored(file_ref, gi)`` predicate wired
into every external scanner's skip chain, generalising the hardcoded list to
the plugin's ACTUAL ``.gitignore``. This aligns the external scanners with the
in-process gitignore handling (the secret scanner walks via ``gi.walk``; the
skillaudit native scanner skips only gitignored-AND-untracked paths via
``gitignored_unshipped_paths``) — so it opens NO new false-negative surface.

Each test is TWO-SIDED: a gitignored finding is dropped AND a tracked/shipped
finding still fires — proving the fix is a precise not-shipped discrimination,
not a blanket removal of detection.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_validation_common as cvc  # noqa: E402
import validate_security as vs  # noqa: E402


def _mkplugin(tmp: Path) -> Path:
    """Minimal plugin tree with a .gitignore excluding INPUT_DEV/ + *.log, plus a
    real gitignored file and a real tracked file."""
    (tmp / ".claude-plugin").mkdir()
    (tmp / ".claude-plugin" / "plugin.json").write_text('{"name":"p","version":"1.0.0"}')
    (tmp / ".gitignore").write_text("INPUT_DEV/\n*.log\n")
    (tmp / "INPUT_DEV" / "vendor").mkdir(parents=True)
    (tmp / "INPUT_DEV" / "vendor" / "leak.txt").write_text("AKIA0000EXAMPLE")
    (tmp / "scripts").mkdir()
    (tmp / "scripts" / "app.py").write_text("x = 1\n")
    return tmp


class TestGitignorePredicate:
    """Unit-level two-sided coverage of the shared predicate."""

    def test_file_under_dir_only_pattern_is_gitignored(self) -> None:
        """A FILE under a dir-only pattern (INPUT_DEV/) is recognised as ignored —
        the exact reported corpus case."""
        with tempfile.TemporaryDirectory() as d:
            root = _mkplugin(Path(d))
            gi = cvc.get_gitignore_filter(root)
            assert vs._external_finding_is_gitignored("INPUT_DEV/vendor/leak.txt", gi) is True

    def test_absolute_path_form_is_gitignored(self) -> None:
        """External scanners hand back ABSOLUTE paths (trufflehog/cc-audit/Cisco);
        the predicate normalises them to plugin-root-relative first."""
        with tempfile.TemporaryDirectory() as d:
            root = _mkplugin(Path(d))
            gi = cvc.get_gitignore_filter(root)
            abs_ref = str(root / "INPUT_DEV" / "vendor" / "leak.txt")
            assert vs._external_finding_is_gitignored(abs_ref, gi) is True

    def test_glob_pattern_is_gitignored(self) -> None:
        """A *.log glob match is ignored."""
        with tempfile.TemporaryDirectory() as d:
            root = _mkplugin(Path(d))
            (root / "debug.log").write_text("noise")
            gi = cvc.get_gitignore_filter(root)
            assert vs._external_finding_is_gitignored("debug.log", gi) is True

    def test_tracked_file_is_not_gitignored(self) -> None:
        """A shipped (tracked) file must NOT be treated as gitignored — the finding
        still fires (both rel and abs forms)."""
        with tempfile.TemporaryDirectory() as d:
            root = _mkplugin(Path(d))
            gi = cvc.get_gitignore_filter(root)
            assert vs._external_finding_is_gitignored("scripts/app.py", gi) is False
            assert vs._external_finding_is_gitignored(str(root / "scripts" / "app.py"), gi) is False

    def test_path_outside_root_fails_safe(self) -> None:
        """A path outside plugin_root returns False (fail-safe → scanned)."""
        with tempfile.TemporaryDirectory() as d:
            root = _mkplugin(Path(d))
            gi = cvc.get_gitignore_filter(root)
            assert vs._external_finding_is_gitignored("/etc/passwd", gi) is False

    def test_empty_file_ref_fails_safe(self) -> None:
        """An empty file_ref returns False (never silently suppress)."""
        with tempfile.TemporaryDirectory() as d:
            root = _mkplugin(Path(d))
            gi = cvc.get_gitignore_filter(root)
            assert vs._external_finding_is_gitignored("", gi) is False

    def test_no_gitignore_means_nothing_ignored(self) -> None:
        """A plugin with NO .gitignore ignores nothing — every finding still fires."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".claude-plugin").mkdir()
            (root / ".claude-plugin" / "plugin.json").write_text('{"name":"p","version":"1.0.0"}')
            (root / "INPUT_DEV").mkdir()
            (root / "INPUT_DEV" / "x.txt").write_text("AKIA0000EXAMPLE")
            gi = cvc.get_gitignore_filter(root)
            assert vs._external_finding_is_gitignored("INPUT_DEV/x.txt", gi) is False


def _trufflehog_finding_line(abs_file: str) -> str:
    """One trufflehog --json line for an UNVERIFIED secret at abs_file:1."""
    return json.dumps(
        {
            "DetectorName": "AWS",
            "Verified": False,
            "SourceMetadata": {"Data": {"Filesystem": {"file": abs_file, "line": 1}}},
        }
    )


def _run_trufflehog_with_finding(root: Path, target_rel: str, monkeypatch: pytest.MonkeyPatch) -> cvc.ValidationReport:
    """Drive check_trufflehog with a faked binary + a single synthetic finding
    pointing at ``target_rel`` (relative to root). No real trufflehog needed —
    only the binary probe and the subprocess are faked; the skip logic is real."""
    abs_file = str(root / target_rel)
    fake = subprocess.CompletedProcess(
        args=["trufflehog"], returncode=0, stdout=_trufflehog_finding_line(abs_file) + "\n", stderr=""
    )
    monkeypatch.setattr(vs.shutil, "which", lambda name: "/usr/bin/trufflehog" if name == "trufflehog" else None)
    monkeypatch.setattr(vs.subprocess, "run", lambda *a, **k: fake)
    report = cvc.ValidationReport()
    vs.check_trufflehog(root, report)
    return report


def _truffle_findings(report: cvc.ValidationReport) -> list:
    return [
        r
        for r in report.results
        if r.level in ("CRITICAL", "MAJOR", "MINOR", "NIT") and "trufflehog" in (r.message or "")
    ]


class TestTrufflehogEndToEnd:
    """End-to-end two-sided: the gitignore skip threads through trufflehog's real
    finding loop (binary + subprocess faked; the skip decision is real code)."""

    def test_gitignored_finding_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A trufflehog hit inside the gitignored INPUT_DEV/ corpus is dropped."""
        with tempfile.TemporaryDirectory() as d:
            root = _mkplugin(Path(d))
            report = _run_trufflehog_with_finding(root, "INPUT_DEV/vendor/leak.txt", monkeypatch)
            assert _truffle_findings(report) == []

    def test_tracked_finding_still_fires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A trufflehog hit in a shipped (tracked) source file still fires."""
        with tempfile.TemporaryDirectory() as d:
            root = _mkplugin(Path(d))
            (root / "scripts" / "leak.py").write_text("KEY = 'AKIA0000EXAMPLE'\n")
            report = _run_trufflehog_with_finding(root, "scripts/leak.py", monkeypatch)
            assert len(_truffle_findings(report)) >= 1


class TestAllExternalScannersWired:
    """Structural guard — every external scanner that runs over the whole tree
    must wire the gitignore predicate, so a future edit can't silently drop the
    skip from one of them (each is the SAME 4-line pattern)."""

    def test_external_scanners_call_gitignore_predicate(self) -> None:
        for fn in (vs.check_trufflehog, vs.check_cc_audit, vs.check_tirith_scanner, vs.check_semgrep):
            src = inspect.getsource(fn)
            assert "_external_finding_is_gitignored" in src, f"{fn.__name__} missing gitignore skip"

    def test_cisco_skip_calls_gitignore_predicate(self) -> None:
        """Cisco's _cisco_should_skip is a closure inside validate_security; assert
        the predicate is referenced (with its dedicated cisco_gi filter)."""
        src = inspect.getsource(vs.validate_security)
        assert "_external_finding_is_gitignored(file_path, cisco_gi)" in src
