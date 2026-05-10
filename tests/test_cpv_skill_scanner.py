"""Tests for the Cisco AI Defense skill-scanner wrapper.

Covers: argv assembly (programmatic-only, no API-key flags), JSON parsing
across the schema variations the scanner emits, severity mapping into CPV's
canonical levels, the relativise helper, and the skipped-reason path when
uvx is missing or the scan times out. Subprocess invocation is exercised
via monkeypatch so the suite stays hermetic — no network, no uvx download.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_skill_scanner as css  # noqa: E402

# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cisco_level,expected_cpv",
    [
        ("critical", "critical"),
        ("CRITICAL", "critical"),
        ("high", "major"),
        ("medium", "minor"),
        ("low", "nit"),
        ("info", "info"),
    ],
)
def test_severity_mapping(cisco_level: str, expected_cpv: str) -> None:
    raw = {
        "severity": cisco_level,
        "rule_id": "static.x",
        "message": "m",
        "location": {"file": "a.py", "line": 1},
    }
    finding = css._normalise_finding(raw)
    assert finding.severity == expected_cpv


def test_severity_unknown_falls_back_to_minor() -> None:
    finding = css._normalise_finding({"severity": "weird-level", "rule_id": "x", "message": "m"})
    assert finding.severity == "minor"


def test_severity_missing_defaults_to_info() -> None:
    finding = css._normalise_finding({"rule_id": "x", "message": "m"})
    assert finding.severity == "info"


def test_severity_legacy_field_name() -> None:
    """Older builds use 'severity_level' instead of 'severity'."""
    finding = css._normalise_finding({"severity_level": "high", "rule_id": "x", "message": "m"})
    assert finding.severity == "major"


# ---------------------------------------------------------------------------
# argv assembly — programmatic-only contract
# ---------------------------------------------------------------------------


class TestBuildScanCommand:
    def test_no_api_key_flags_present(self, tmp_path: Path) -> None:
        cmd = css.build_scan_command(tmp_path, json_output_path=tmp_path / "o.json")
        forbidden = {
            "--use-llm",
            "--enable-meta",
            "--use-virustotal",
            "--use-aidefense",
            "--vt-api-key",
            "--aidefense-api-key",
            "--aidefense-api-url",
        }
        assert forbidden.isdisjoint(cmd), f"forbidden API-key flag in argv: {cmd}"

    def test_required_invocation_shape_uvx_fallback(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        """v2.48 — when no persistent ``skill-scanner`` binary is on PATH,
        the launcher falls back to ``uvx --from cisco-ai-skill-scanner``."""
        # Force the uvx fallback path (no persistent install).
        monkeypatch.setattr(css.shutil, "which", lambda name: None)
        out = tmp_path / "out.json"
        cmd = css.build_scan_command(tmp_path, json_output_path=out)
        assert cmd[:3] == ["uvx", "--from", "cisco-ai-skill-scanner"]
        assert "skill-scanner" in cmd
        assert "scan-all" in cmd
        assert str(tmp_path) in cmd
        assert "--recursive" in cmd
        assert "--lenient" in cmd
        assert "--use-behavioral" in cmd
        assert "--use-trigger" in cmd
        assert "--policy" in cmd
        assert "balanced" in cmd
        assert "--format" in cmd
        assert "json" in cmd
        assert "--output-json" in cmd
        assert str(out) in cmd

    def test_required_invocation_shape_persistent_install(
        self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """v2.48 — when the persistent ``skill-scanner`` binary IS on PATH
        (created by ``uv tool install cisco-ai-skill-scanner``), the launcher
        uses it directly and skips the ``uvx --from`` resolve cost (~5-10s
        per invocation)."""
        monkeypatch.setattr(
            css.shutil,
            "which",
            lambda name: "/Users/test/.local/bin/skill-scanner" if name == "skill-scanner" else None,
        )
        out = tmp_path / "out.json"
        cmd = css.build_scan_command(tmp_path, json_output_path=out)
        # Direct binary call — no uvx prefix.
        assert cmd[0] == "skill-scanner"
        assert "uvx" not in cmd
        assert "--from" not in cmd
        # Same downstream args as before.
        assert "scan-all" in cmd
        assert str(tmp_path) in cmd
        assert "--recursive" in cmd
        assert "--lenient" in cmd
        assert "--use-behavioral" in cmd
        assert "--use-trigger" in cmd

    def test_behavioral_flag_can_be_disabled(self, tmp_path: Path) -> None:
        cmd = css.build_scan_command(tmp_path, json_output_path=tmp_path / "o.json", use_behavioral=False)
        assert "--use-behavioral" not in cmd

    def test_trigger_flag_can_be_disabled(self, tmp_path: Path) -> None:
        cmd = css.build_scan_command(tmp_path, json_output_path=tmp_path / "o.json", use_trigger=False)
        assert "--use-trigger" not in cmd

    def test_policy_override(self, tmp_path: Path) -> None:
        cmd = css.build_scan_command(tmp_path, json_output_path=tmp_path / "o.json", policy="strict")
        idx = cmd.index("--policy")
        assert cmd[idx + 1] == "strict"

    def test_package_spec_override(self, tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:
        """``package_spec`` only takes effect when the launcher falls back to
        ``uvx --from <spec>``. The persistent ``skill-scanner`` binary uses
        whichever version was previously installed via ``uv tool install`` and
        ignores ``package_spec`` — so we must force the uvx path for this
        test by mocking the persistent binary as absent."""
        monkeypatch.setattr(css.shutil, "which", lambda name: None)
        cmd = css.build_scan_command(
            tmp_path,
            json_output_path=tmp_path / "o.json",
            package_spec="cisco-ai-skill-scanner==1.2.3",
        )
        assert "cisco-ai-skill-scanner==1.2.3" in cmd


# ---------------------------------------------------------------------------
# JSON parsing — schema variations
# ---------------------------------------------------------------------------


class TestParseFindings:
    def test_results_array_shape(self) -> None:
        blob = {
            "results": [
                {
                    "skill_name": "x",
                    "findings": [
                        {
                            "severity": "critical",
                            "rule_id": "static.injection",
                            "message": "Detected prompt injection",
                            "location": {"file": "agents/x.md", "line": 42},
                        }
                    ],
                }
            ]
        }
        findings = css.parse_findings(blob)
        assert len(findings) == 1
        f = findings[0]
        assert f.severity == "critical"
        assert f.rule_id == "static.injection"
        assert f.file_path == "agents/x.md"
        assert f.line_number == 42

    def test_flat_single_skill_shape(self) -> None:
        blob = {
            "findings": [
                {"severity": "high", "rule_id": "r1", "message": "m1"},
                {"severity": "low", "rule_id": "r2", "message": "m2"},
            ]
        }
        findings = css.parse_findings(blob)
        assert [f.severity for f in findings] == ["major", "nit"]

    def test_string_input(self) -> None:
        blob = json.dumps({"results": [{"findings": [{"severity": "info", "rule_id": "r"}]}]})
        findings = css.parse_findings(blob)
        assert len(findings) == 1

    def test_bytes_input(self) -> None:
        blob = json.dumps({"results": [{"findings": [{"severity": "info", "rule_id": "r"}]}]}).encode("utf-8")
        findings = css.parse_findings(blob)
        assert len(findings) == 1

    def test_empty_results(self) -> None:
        assert css.parse_findings({"results": []}) == ()

    def test_missing_findings_key(self) -> None:
        assert css.parse_findings({"results": [{"skill_name": "x"}]}) == ()

    def test_invalid_top_level_returns_empty(self) -> None:
        assert css.parse_findings([]) == ()  # type: ignore[arg-type]

    def test_line_number_coerces_string(self) -> None:
        blob = {"findings": [{"severity": "high", "rule_id": "r", "location": {"line": "17"}}]}
        assert css.parse_findings(blob)[0].line_number == 17

    def test_line_number_invalid_becomes_none(self) -> None:
        blob = {"findings": [{"severity": "high", "rule_id": "r", "location": {"line": "n/a"}}]}
        assert css.parse_findings(blob)[0].line_number is None

    def test_alternate_id_field_names(self) -> None:
        blob = {"findings": [{"severity": "high", "ruleId": "alt.id", "message": "m"}]}
        assert css.parse_findings(blob)[0].rule_id == "alt.id"
        blob2 = {"findings": [{"severity": "high", "id": "fallback.id"}]}
        assert css.parse_findings(blob2)[0].rule_id == "fallback.id"

    def test_alternate_message_field_names(self) -> None:
        for key in ("description", "title"):
            blob = {"findings": [{"severity": "info", "rule_id": "r", key: "txt"}]}
            assert css.parse_findings(blob)[0].message == "txt"


# ---------------------------------------------------------------------------
# Path relativisation
# ---------------------------------------------------------------------------


class TestRelativise:
    def test_inside_root(self, tmp_path: Path) -> None:
        sub = tmp_path / "agents" / "x.md"
        sub.parent.mkdir()
        sub.write_text("x")
        assert css._relativise(str(sub), tmp_path) == "agents/x.md"

    def test_outside_root_returns_original(self, tmp_path: Path) -> None:
        out = "/some/other/place.md"
        assert css._relativise(out, tmp_path) == out

    def test_empty_path(self, tmp_path: Path) -> None:
        assert css._relativise("", tmp_path) == "<unknown>"


# ---------------------------------------------------------------------------
# run_cisco_scan — subprocess interactions (monkeypatched, hermetic)
# ---------------------------------------------------------------------------


@dataclass
class _FakeCompleted:
    stdout: str
    stderr: str
    returncode: int


class TestRunCiscoScan:
    def test_skipped_when_uvx_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(css, "is_uvx_available", lambda: False)
        result = css.run_cisco_scan(tmp_path)
        assert result.invoked is False
        assert "uvx not on PATH" in result.skipped_reason
        assert result.findings == ()
        assert result.exit_code == -1

    def test_invocation_writes_json_then_parses(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(css, "is_uvx_available", lambda: True)
        json_path = tmp_path / ".cpv-cisco-scan.json"
        sample = {
            "results": [
                {
                    "findings": [
                        {
                            "severity": "high",
                            "rule_id": "static.x",
                            "message": "leaks env",
                            "location": {
                                "file": str(tmp_path / "agents/x.md"),
                                "line": 7,
                            },
                        }
                    ]
                }
            ]
        }

        def fake_run(*_a: Any, **_kw: Any) -> _FakeCompleted:
            json_path.write_text(json.dumps(sample), encoding="utf-8")
            return _FakeCompleted(stdout="", stderr="", returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = css.run_cisco_scan(tmp_path)
        assert result.invoked is True
        assert len(result.findings) == 1
        assert result.findings[0].severity == "major"
        # Sentinel JSON file must be cleaned up afterwards.
        assert not json_path.exists()

    def test_invocation_falls_back_to_stdout_json(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """When the JSON file isn't written, parse stdout if it looks JSON."""
        monkeypatch.setattr(css, "is_uvx_available", lambda: True)
        sample = json.dumps({"results": [{"findings": [{"severity": "info", "rule_id": "r"}]}]})
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _FakeCompleted(stdout=sample, stderr="", returncode=0),
        )
        result = css.run_cisco_scan(tmp_path)
        assert result.invoked is True
        assert len(result.findings) == 1

    def test_timeout_is_handled(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(css, "is_uvx_available", lambda: True)

        def fake_run(*_a: Any, **_kw: Any) -> None:
            raise subprocess.TimeoutExpired(cmd=["uvx"], timeout=1, output=b"", stderr=b"")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = css.run_cisco_scan(tmp_path, timeout_seconds=1)
        assert result.invoked is False
        assert "timed out" in result.skipped_reason
        assert result.exit_code == -2

    def test_filenotfound_returns_skipped(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(css, "is_uvx_available", lambda: True)

        def fake_run(*_a: Any, **_kw: Any) -> None:
            raise FileNotFoundError("uvx vanished mid-call")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = css.run_cisco_scan(tmp_path)
        assert result.invoked is False
        assert "uvx invocation failed" in result.skipped_reason
        assert result.exit_code == -3


# ---------------------------------------------------------------------------
# report_findings — adapter into ValidationReport-shaped duck type
# ---------------------------------------------------------------------------


class _FakeReport:
    """Minimal duck-type matching ValidationReport's per-severity setters.

    Mirrors ValidationReport's real signatures from cpv_validation_common.py:
    info() takes (message, file) — NO line — while critical/major/minor/nit
    accept the full (message, file, line) triple.
    """

    def __init__(self) -> None:
        self.entries: list[tuple[str, str, str, int | None]] = []

    def critical(self, msg: str, f: str, line: int | None) -> None:
        self.entries.append(("critical", msg, f, line))

    def major(self, msg: str, f: str, line: int | None) -> None:
        self.entries.append(("major", msg, f, line))

    def minor(self, msg: str, f: str, line: int | None) -> None:
        self.entries.append(("minor", msg, f, line))

    def nit(self, msg: str, f: str, line: int | None) -> None:
        self.entries.append(("nit", msg, f, line))

    def info(self, msg: str, f: str) -> None:
        # NOTE: real ValidationReport.info() has signature (message, file=None)
        # with NO line parameter. The fake mirrors that exactly so the test
        # catches any drift in cpv_skill_scanner's call sites.
        self.entries.append(("info", msg, f, None))


class TestReportFindings:
    def test_skipped_invocation_emits_info(self, tmp_path: Path) -> None:
        result = css.CiscoScanResult(
            invoked=False,
            findings=(),
            skipped_reason="uvx missing",
            raw_stdout="",
            raw_stderr="",
            exit_code=-1,
        )
        report = _FakeReport()
        appended = css.report_findings(result, tmp_path, report)
        assert appended == 0
        assert len(report.entries) == 1
        assert report.entries[0][0] == "info"
        assert "Cisco skill-scanner skipped" in report.entries[0][1]

    def test_findings_route_to_correct_severity(self, tmp_path: Path) -> None:
        finding_a = css.CiscoFinding(
            severity="critical",
            rule_id="static.x",
            message="bad",
            file_path=str(tmp_path / "a.md"),
            line_number=12,
            raw={},
        )
        finding_b = css.CiscoFinding(
            severity="nit",
            rule_id="static.y",
            message="meh",
            file_path=str(tmp_path / "b.md"),
            line_number=None,
            raw={},
        )
        result = css.CiscoScanResult(
            invoked=True,
            findings=(finding_a, finding_b),
            skipped_reason="",
            raw_stdout="",
            raw_stderr="",
            exit_code=0,
        )
        report = _FakeReport()
        appended = css.report_findings(result, tmp_path, report)
        assert appended == 2
        sev_seen = [e[0] for e in report.entries]
        assert sev_seen == ["critical", "nit"]
        assert "[cisco static.x]" in report.entries[0][1]
        assert report.entries[0][2] == "a.md"
        assert report.entries[0][3] == 12
        assert report.entries[1][2] == "b.md"
        assert report.entries[1][3] is None

    def test_unknown_severity_uses_minor_fallback(self, tmp_path: Path) -> None:
        finding = css.CiscoFinding(
            severity="weird",  # not on _FakeReport
            rule_id="r",
            message="m",
            file_path="x.md",
            line_number=None,
            raw={},
        )
        result = css.CiscoScanResult(
            invoked=True,
            findings=(finding,),
            skipped_reason="",
            raw_stdout="",
            raw_stderr="",
            exit_code=0,
        )
        report = _FakeReport()
        appended = css.report_findings(result, tmp_path, report)
        assert appended == 1
        assert report.entries[0][0] == "minor"
