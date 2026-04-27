"""Tests for Phase 6 (RC-105) SARIF 2.1.0 output writer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_sarif_writer import (  # noqa: E402
    SARIF_SCHEMA_URI,
    SARIF_VERSION,
    _extract_rule_id,
    results_to_sarif,
    write_sarif,
)
from cpv_validation_common import ValidationReport  # noqa: E402


def _make_plugin(tmp_path: Path) -> Path:
    plugin = tmp_path / "demo"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo", "version": "0.0.1", "description": "test"}\n'
    )
    return plugin


# -----------------------------------------------------------------------------
# Rule-ID extraction
# -----------------------------------------------------------------------------


class TestRuleIdExtraction:
    @pytest.mark.parametrize("msg,expected", [
        ("RC-69 dangerous eval pattern", "RC-69"),
        ("CPV-P2-C1 agent color must be named", "CPV-P2-C1"),
        ("GAP-1 file source rejected", "GAP-1"),
        ("Hardcoded user path /Users/foo detected", "CPV-GENERIC"),
        ("", "CPV-GENERIC"),
        ("RC-105 SARIF something", "RC-105"),
    ])
    def test_extracts_or_falls_back(self, msg: str, expected: str) -> None:
        assert _extract_rule_id(msg) == expected


# -----------------------------------------------------------------------------
# Severity → SARIF level mapping
# -----------------------------------------------------------------------------


class TestSeverityMapping:
    @pytest.mark.parametrize("level,sarif_level", [
        ("CRITICAL", "error"),
        ("MAJOR", "error"),
        ("MINOR", "warning"),
        ("WARNING", "warning"),
        ("NIT", "note"),
        ("INFO", "note"),
    ])
    def test_each_level_maps(self, tmp_path: Path, level: str, sarif_level: str) -> None:
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        report.add(level, "RC-50 sample message", file=str(plugin / "src/x.py"), line=10)
        sarif = results_to_sarif(report.results, plugin)
        results = sarif["runs"][0]["results"]
        assert len(results) == 1
        assert results[0]["level"] == sarif_level
        assert results[0]["properties"]["cpv_severity"] == level

    def test_passed_skipped(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        report.passed("test passed")
        report.add("CRITICAL", "RC-1 real bug")
        sarif = results_to_sarif(report.results, plugin)
        # Only the CRITICAL appears, PASSED is dropped
        assert len(sarif["runs"][0]["results"]) == 1


# -----------------------------------------------------------------------------
# SARIF schema shape
# -----------------------------------------------------------------------------


class TestSarifShape:
    def test_top_level_keys(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        sarif = results_to_sarif([], plugin)
        assert sarif["$schema"] == SARIF_SCHEMA_URI
        assert sarif["version"] == SARIF_VERSION
        assert isinstance(sarif["runs"], list)
        assert len(sarif["runs"]) == 1

    def test_tool_driver_present(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        sarif = results_to_sarif([], plugin, tool_version="2.27.0")
        driver = sarif["runs"][0]["tool"]["driver"]
        assert driver["name"] == "claude-plugins-validation"
        assert driver["version"] == "2.27.0"
        assert "informationUri" in driver
        assert isinstance(driver["rules"], list)

    def test_originalUriBaseIds_set(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        sarif = results_to_sarif([], plugin)
        bases = sarif["runs"][0]["originalUriBaseIds"]
        assert "%SRCROOT%" in bases
        assert bases["%SRCROOT%"]["uri"].startswith("file://")
        assert bases["%SRCROOT%"]["uri"].endswith("/")


# -----------------------------------------------------------------------------
# Per-result location
# -----------------------------------------------------------------------------


class TestLocation:
    def test_relative_uri(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        report.add("CRITICAL", "RC-1 x", file=str(plugin / "src/foo.py"), line=42)
        sarif = results_to_sarif(report.results, plugin)
        loc = sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        assert loc["artifactLocation"]["uri"] == "src/foo.py"
        assert loc["artifactLocation"]["uriBaseId"] == "%SRCROOT%"
        assert loc["region"]["startLine"] == 42

    def test_no_line_omits_region(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        report.add("MAJOR", "RC-2 y", file=str(plugin / "src/bar.py"))
        sarif = results_to_sarif(report.results, plugin)
        loc = sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]
        assert "region" not in loc

    def test_no_file_omits_locations(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        report.add("MAJOR", "RC-3 z")
        sarif = results_to_sarif(report.results, plugin)
        assert "locations" not in sarif["runs"][0]["results"][0]

    def test_outside_root_falls_back_to_str(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        # File path outside the plugin root
        report = ValidationReport()
        report.add("MINOR", "RC-4 outside", file="/etc/passwd", line=1)
        sarif = results_to_sarif(report.results, plugin)
        uri = sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        assert uri == "/etc/passwd"


# -----------------------------------------------------------------------------
# Rule descriptors aggregation
# -----------------------------------------------------------------------------


class TestRuleDescriptors:
    def test_dedupes_rule_ids(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        for _ in range(3):
            report.add("CRITICAL", "RC-69 same rule", file=str(plugin / "src/x.py"), line=1)
        sarif = results_to_sarif(report.results, plugin)
        rules = sarif["runs"][0]["tool"]["driver"]["rules"]
        # 3 occurrences, but only 1 rule descriptor
        assert len(rules) == 1
        assert rules[0]["id"] == "RC-69"
        # 3 results emitted
        assert len(sarif["runs"][0]["results"]) == 3

    def test_multiple_rule_ids(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        report.add("CRITICAL", "RC-1 a")
        report.add("MAJOR", "RC-2 b")
        report.add("MINOR", "RC-3 c")
        sarif = results_to_sarif(report.results, plugin)
        ids = {r["id"] for r in sarif["runs"][0]["tool"]["driver"]["rules"]}
        assert ids == {"RC-1", "RC-2", "RC-3"}


# -----------------------------------------------------------------------------
# Round-trip via write_sarif
# -----------------------------------------------------------------------------


class TestWriteSarif:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        report.add("CRITICAL", "RC-1 x", file=str(plugin / "a.py"), line=5)
        report.add("MAJOR", "RC-2 y", file=str(plugin / "b.py"), line=10)
        out = tmp_path / "out.sarif"
        result_path = write_sarif(report.results, out, plugin, tool_version="9.9.9")
        assert result_path == out.resolve()
        assert out.exists()
        loaded = json.loads(out.read_text())
        assert loaded["version"] == "2.1.0"
        assert loaded["runs"][0]["tool"]["driver"]["version"] == "9.9.9"
        assert len(loaded["runs"][0]["results"]) == 2

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        report.add("CRITICAL", "RC-1 x")
        nested = tmp_path / "deep" / "nest" / "out.sarif"
        write_sarif(report.results, nested, plugin)
        assert nested.exists()


# -----------------------------------------------------------------------------
# Properties (category, phase, suggestion)
# -----------------------------------------------------------------------------


class TestProperties:
    def test_category_propagates(self, tmp_path: Path) -> None:
        from cpv_validation_common import ValidationResult  # noqa: WPS433
        plugin = _make_plugin(tmp_path)
        rs = [ValidationResult(
            "CRITICAL", "RC-1 x", str(plugin / "a.py"), 1, "security", False, None, "secrets", None
        )]
        sarif = results_to_sarif(rs, plugin)
        props = sarif["runs"][0]["results"][0]["properties"]
        assert props["category"] == "secrets"
        assert props["phase"] == "security"

    def test_suggestion_becomes_fixes(self, tmp_path: Path) -> None:
        from cpv_validation_common import ValidationResult  # noqa: WPS433
        plugin = _make_plugin(tmp_path)
        rs = [ValidationResult("MAJOR", "RC-2 y", suggestion="Use os.environ.get instead")]
        sarif = results_to_sarif(rs, plugin)
        fixes = sarif["runs"][0]["results"][0]["fixes"]
        assert fixes[0]["description"]["text"] == "Use os.environ.get instead"


# -----------------------------------------------------------------------------
# Accepts both objects and dicts
# -----------------------------------------------------------------------------


class TestInputShapes:
    def test_accepts_validation_result_objects(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        report.add("CRITICAL", "RC-1 a")
        sarif = results_to_sarif(report.results, plugin)
        assert len(sarif["runs"][0]["results"]) == 1

    def test_accepts_plain_dicts(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        rs = [{"level": "CRITICAL", "message": "RC-1 z", "file": str(plugin / "a.py"), "line": 1}]
        sarif = results_to_sarif(rs, plugin)
        assert len(sarif["runs"][0]["results"]) == 1
        assert sarif["runs"][0]["results"][0]["level"] == "error"
