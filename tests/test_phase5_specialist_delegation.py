"""Tests for Phase 5 specialist-tool delegation (RC-102 — trufflehog/semgrep).

Each external scanner check follows the same pattern as cc-audit and tirith:
* shutil.which() probe
* subprocess.run with --json output
* parse + map severity to CPV levels
* WARNING when binary missing
* CLI --no-X flag to skip

These tests use monkeypatched subprocess.run so they don't require any
of the binaries actually installed.

v2.48 — gitleaks tests removed: gitleaks integration was dropped because
trufflehog (~700 detectors, --concurrency parallelism) provides superset
coverage with reliable parallel scans (gitleaks crashed under parallelism).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import validate_security  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402
from validate_security import (  # noqa: E402
    check_semgrep,
    check_trufflehog,
)
from validate_security import (
    validate_security as run_validate_security,
)


def _make_plugin(tmp_path: Path) -> Path:
    plugin = tmp_path / "demo"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo", "version": "0.0.1", "description": "test"}\n'
    )
    return plugin


def _msgs(report: ValidationReport, prefix: str) -> list[str]:
    return [r.message for r in report.results if r.message.startswith(prefix)]


# -----------------------------------------------------------------------------
# Binary-missing path — emits a single WARNING
# -----------------------------------------------------------------------------


class TestBinaryMissing:
    def test_trufflehog_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(validate_security.shutil, "which", lambda name: None)
        report = ValidationReport()
        plugin = _make_plugin(tmp_path)
        check_trufflehog(plugin, report)
        warnings = [r.message for r in report.results if r.level == "WARNING"]
        assert any("trufflehog" in w and "not found" in w for w in warnings)

    def test_semgrep_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(validate_security.shutil, "which", lambda name: None)
        report = ValidationReport()
        plugin = _make_plugin(tmp_path)
        check_semgrep(plugin, report)
        warnings = [r.message for r in report.results if r.level == "WARNING"]
        assert any("semgrep" in w and "not found" in w for w in warnings)


# -----------------------------------------------------------------------------
# Mocked subprocess responses — verify parsing
# -----------------------------------------------------------------------------


class _FakeCompletedProcess:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class TestTrufflehogParsing:
    def test_verified_secret_critical(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(validate_security.shutil, "which", lambda name: f"/usr/local/bin/{name}")
        sample = json.dumps(
            {
                "DetectorName": "AWS",
                "Verified": True,
                "SourceMetadata": {"Data": {"Filesystem": {"file": "src/leak.py", "line": 10}}},
            }
        )

        def fake_run(*a: Any, **kw: Any) -> _FakeCompletedProcess:
            return _FakeCompletedProcess(stdout=sample, returncode=1)

        monkeypatch.setattr(validate_security.subprocess, "run", fake_run)
        report = ValidationReport()
        check_trufflehog(_make_plugin(tmp_path), report)
        msgs = [r for r in report.results if "trufflehog" in r.message and "VERIFIED" in r.message]
        assert msgs and msgs[0].level == "CRITICAL"

    def test_unverified_secret_major(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(validate_security.shutil, "which", lambda name: f"/usr/local/bin/{name}")
        sample = json.dumps(
            {
                "DetectorName": "GenericPassword",
                "Verified": False,
                "SourceMetadata": {"Data": {"Filesystem": {"file": "src/x.py", "line": 5}}},
            }
        )

        def fake_run(*a: Any, **kw: Any) -> _FakeCompletedProcess:
            return _FakeCompletedProcess(stdout=sample, returncode=1)

        monkeypatch.setattr(validate_security.subprocess, "run", fake_run)
        report = ValidationReport()
        check_trufflehog(_make_plugin(tmp_path), report)
        msgs = [r for r in report.results if "trufflehog" in r.message and "UNVERIFIED" in r.message]
        assert msgs and msgs[0].level == "MAJOR"

    def test_no_findings_passed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(validate_security.shutil, "which", lambda name: f"/usr/local/bin/{name}")
        monkeypatch.setattr(
            validate_security.subprocess, "run", lambda *a, **kw: _FakeCompletedProcess(stdout="", returncode=0)
        )
        report = ValidationReport()
        check_trufflehog(_make_plugin(tmp_path), report)
        passed = [r for r in report.results if r.level == "PASSED" and "trufflehog" in r.message]
        assert passed

    def test_validator_script_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Findings in CPV's own validator-source files are skipped — but ONLY
        when self-scan is active AND the file's hash matches the canonical
        manifest. The fake plugin under test isn't CPV (no self-scan
        activation), so a same-named file at `scripts/validate_security.py`
        must STILL be reported. This is the security improvement of v2.37.0:
        name-only spoofing no longer evades scanning.
        """
        monkeypatch.setattr(validate_security.shutil, "which", lambda name: f"/usr/local/bin/{name}")
        sample = json.dumps(
            {
                "DetectorName": "AWS",
                "Verified": True,
                "SourceMetadata": {"Data": {"Filesystem": {"file": "scripts/validate_security.py", "line": 100}}},
            }
        )
        monkeypatch.setattr(
            validate_security.subprocess, "run", lambda *a, **kw: _FakeCompletedProcess(stdout=sample, returncode=1)
        )
        report = ValidationReport()
        plugin = _make_plugin(tmp_path)
        # Self-scan must NOT be active for this fake plugin.
        validate_security._set_cpv_self_scan(False, plugin_root=plugin, notice_report=None)
        check_trufflehog(plugin, report)
        critical = [r for r in report.results if r.level == "CRITICAL"]
        assert critical, (
            "name-only suppression must NOT apply when self-scan is inactive; "
            "the spoofable path-based skip was removed in v2.37.0."
        )


# v2.48 — TestGitleaksParsing class removed: gitleaks integration was
# dropped in favor of trufflehog (superset coverage + parallel-safe via
# --concurrency). The check_gitleaks function and its placeholder-secret
# helpers no longer exist.


class TestSemgrepParsing:
    def test_error_severity_major(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(validate_security.shutil, "which", lambda name: f"/usr/local/bin/{name}")
        plugin = _make_plugin(tmp_path)
        # File path in finding must be relative to plugin_path
        sample = json.dumps(
            {
                "results": [
                    {
                        "check_id": "python.security.audit.dangerous-system-call",
                        "extra": {"message": "eval() detected", "severity": "ERROR"},
                        "path": str(plugin / "src/x.py"),
                        "start": {"line": 1},
                    }
                ]
            }
        )
        monkeypatch.setattr(
            validate_security.subprocess, "run", lambda *a, **kw: _FakeCompletedProcess(stdout=sample, returncode=0)
        )
        report = ValidationReport()
        check_semgrep(plugin, report)
        msgs = [r for r in report.results if "semgrep" in r.message and r.level == "MAJOR"]
        assert msgs


# -----------------------------------------------------------------------------
# CLI flag integration — --no-trufflehog / --no-semgrep
# -----------------------------------------------------------------------------


class TestCliFlags:
    def test_no_trufflehog_skips_the_check(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Skip ALL external + Phase 1+ checks for isolation; only verify
        # that enable_trufflehog=False suppresses the trufflehog WARNING.
        # v2.48 — `enable_gitleaks` removed (gitleaks integration deleted).
        monkeypatch.setattr(validate_security.shutil, "which", lambda name: None)
        plugin = _make_plugin(tmp_path)
        report = run_validate_security(
            plugin,
            enable_tirith=False,
            enable_trufflehog=False,
            enable_semgrep=False,
        )
        warnings = [r.message for r in report.results if r.level == "WARNING"]
        assert not any("trufflehog" in w for w in warnings)
        assert not any("semgrep" in w for w in warnings)
