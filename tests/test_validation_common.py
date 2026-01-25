#!/usr/bin/env python3
"""Tests for validation_common.py.

Tests the core validation infrastructure:
- ValidationResult dataclass
- ValidationReport class
- Severity levels and exit codes
- Scoring and grading functions
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validation_common import (  # noqa: E402
    EXIT_CRITICAL,
    EXIT_MAJOR,
    EXIT_MINOR,
    EXIT_OK,
    SEVERITY_L1,
    SEVERITY_L10,
    SEVERITY_L5,
    SEVERITY_L8,
    ValidationReport,
    ValidationResult,
    calculate_letter_grade,
    level_to_severity,
    severity_to_level,
)


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_create_result_with_required_fields(self):
        """ValidationResult should be created with level and message."""
        result = ValidationResult(level="CRITICAL", message="Test error")
        assert result.level == "CRITICAL"
        assert result.message == "Test error"
        assert result.file is None
        assert result.line is None

    def test_create_result_with_all_fields(self):
        """ValidationResult should accept all optional fields."""
        result = ValidationResult(
            level="MAJOR",
            message="Test issue",
            file="/path/to/file.py",
            line=42,
            phase="semantic",
            fixable=True,
            fix_id="fix_issue_1",
        )
        assert result.level == "MAJOR"
        assert result.message == "Test issue"
        assert result.file == "/path/to/file.py"
        assert result.line == 42
        assert result.phase == "semantic"
        assert result.fixable is True
        assert result.fix_id == "fix_issue_1"

    def test_to_dict_minimal(self):
        """to_dict should include only level and message for minimal result."""
        result = ValidationResult(level="INFO", message="Just info")
        d = result.to_dict()
        assert d == {"level": "INFO", "message": "Just info"}

    def test_to_dict_full(self):
        """to_dict should include all fields when present."""
        result = ValidationResult(
            level="MINOR",
            message="Small issue",
            file="test.py",
            line=10,
            phase="structure",
            fixable=True,
            fix_id="fix_1",
        )
        d = result.to_dict()
        assert d["level"] == "MINOR"
        assert d["message"] == "Small issue"
        assert d["file"] == "test.py"
        assert d["line"] == 10
        assert d["phase"] == "structure"
        assert d["fixable"] is True
        assert d["fix_id"] == "fix_1"


class TestValidationReport:
    """Tests for ValidationReport class."""

    def test_create_empty_report(self):
        """Empty report should have no results."""
        report = ValidationReport()
        assert len(report.results) == 0
        assert report.score == 100
        assert report.exit_code == EXIT_OK

    def test_add_result(self):
        """add() should add a ValidationResult to the report."""
        report = ValidationReport()
        report.add("CRITICAL", "Critical error", file="test.py", line=5)
        assert len(report.results) == 1
        assert report.results[0].level == "CRITICAL"
        assert report.results[0].message == "Critical error"
        assert report.results[0].file == "test.py"
        assert report.results[0].line == 5

    def test_convenience_methods(self):
        """Convenience methods (passed, info, minor, major, critical) should work."""
        report = ValidationReport()
        report.passed("All good")
        report.info("FYI")
        report.minor("Small issue")
        report.major("Big issue")
        report.critical("Blocking issue")

        assert len(report.results) == 5
        levels = [r.level for r in report.results]
        assert levels == ["PASSED", "INFO", "MINOR", "MAJOR", "CRITICAL"]

    def test_has_critical(self):
        """has_critical should detect CRITICAL issues."""
        report = ValidationReport()
        assert not report.has_critical

        report.minor("Minor issue")
        assert not report.has_critical

        report.critical("Critical issue")
        assert report.has_critical

    def test_has_major(self):
        """has_major should detect MAJOR issues."""
        report = ValidationReport()
        assert not report.has_major

        report.minor("Minor issue")
        assert not report.has_major

        report.major("Major issue")
        assert report.has_major

    def test_has_minor(self):
        """has_minor should detect MINOR issues."""
        report = ValidationReport()
        assert not report.has_minor

        report.info("Just info")
        assert not report.has_minor

        report.minor("Minor issue")
        assert report.has_minor


class TestExitCodes:
    """Tests for exit code mapping."""

    def test_exit_ok_when_no_issues(self):
        """Exit code should be OK (0) when no issues."""
        report = ValidationReport()
        report.passed("Test passed")
        report.info("Some info")
        assert report.exit_code == EXIT_OK

    def test_exit_minor_when_only_minor(self):
        """Exit code should be MINOR (3) when only minor issues."""
        report = ValidationReport()
        report.minor("Minor issue 1")
        report.minor("Minor issue 2")
        assert report.exit_code == EXIT_MINOR

    def test_exit_major_when_major_present(self):
        """Exit code should be MAJOR (2) when major issues present."""
        report = ValidationReport()
        report.minor("Minor issue")
        report.major("Major issue")
        assert report.exit_code == EXIT_MAJOR

    def test_exit_critical_when_critical_present(self):
        """Exit code should be CRITICAL (1) when critical issues present."""
        report = ValidationReport()
        report.minor("Minor issue")
        report.major("Major issue")
        report.critical("Critical issue")
        assert report.exit_code == EXIT_CRITICAL

    def test_exit_code_constants(self):
        """Exit code constants should have expected values."""
        assert EXIT_OK == 0
        assert EXIT_CRITICAL == 1
        assert EXIT_MAJOR == 2
        assert EXIT_MINOR == 3


class TestScoring:
    """Tests for health score calculation."""

    def test_perfect_score_when_no_issues(self):
        """Score should be 100 when no issues."""
        report = ValidationReport()
        report.passed("All passed")
        assert report.score == 100

    def test_critical_deducts_25(self):
        """Each CRITICAL should deduct 25 points."""
        report = ValidationReport()
        report.critical("Critical 1")
        assert report.score == 75

        report.critical("Critical 2")
        assert report.score == 50

    def test_major_deducts_10(self):
        """Each MAJOR should deduct 10 points."""
        report = ValidationReport()
        report.major("Major 1")
        assert report.score == 90

        report.major("Major 2")
        assert report.score == 80

    def test_minor_deducts_3(self):
        """Each MINOR should deduct 3 points."""
        report = ValidationReport()
        report.minor("Minor 1")
        assert report.score == 97

        report.minor("Minor 2")
        assert report.score == 94

    def test_info_and_passed_dont_affect_score(self):
        """INFO and PASSED should not affect score."""
        report = ValidationReport()
        for _ in range(10):
            report.info("Some info")
            report.passed("Some pass")
        assert report.score == 100

    def test_score_minimum_is_zero(self):
        """Score should never go below 0."""
        report = ValidationReport()
        for _ in range(10):
            report.critical("Critical issue")
        assert report.score == 0

    def test_combined_score_calculation(self):
        """Combined issues should calculate correctly."""
        report = ValidationReport()
        report.critical("Critical")  # -25 -> 75
        report.major("Major")  # -10 -> 65
        report.minor("Minor")  # -3 -> 62
        assert report.score == 62


class TestLetterGrade:
    """Tests for letter grade calculation.

    Grade scale:
    - A+ : 97-100
    - A  : 93-96
    - A- : 90-92
    - B+ : 87-89
    - B  : 83-86
    - B- : 80-82
    - C+ : 77-79
    - C  : 73-76
    - C- : 70-72
    - D  : 60-69
    - F  : 0-59
    """

    def test_grade_a_plus(self):
        """Score 97-100 should give grade A+."""
        assert calculate_letter_grade(100) == "A+"
        assert calculate_letter_grade(97) == "A+"

    def test_grade_a(self):
        """Score 93-96 should give grade A."""
        assert calculate_letter_grade(96) == "A"
        assert calculate_letter_grade(93) == "A"

    def test_grade_a_minus(self):
        """Score 90-92 should give grade A-."""
        assert calculate_letter_grade(92) == "A-"
        assert calculate_letter_grade(90) == "A-"

    def test_grade_b_plus(self):
        """Score 87-89 should give grade B+."""
        assert calculate_letter_grade(89) == "B+"
        assert calculate_letter_grade(87) == "B+"

    def test_grade_b(self):
        """Score 83-86 should give grade B."""
        assert calculate_letter_grade(86) == "B"
        assert calculate_letter_grade(83) == "B"

    def test_grade_b_minus(self):
        """Score 80-82 should give grade B-."""
        assert calculate_letter_grade(82) == "B-"
        assert calculate_letter_grade(80) == "B-"

    def test_grade_c_plus(self):
        """Score 77-79 should give grade C+."""
        assert calculate_letter_grade(79) == "C+"
        assert calculate_letter_grade(77) == "C+"

    def test_grade_c(self):
        """Score 73-76 should give grade C."""
        assert calculate_letter_grade(76) == "C"
        assert calculate_letter_grade(73) == "C"

    def test_grade_c_minus(self):
        """Score 70-72 should give grade C-."""
        assert calculate_letter_grade(72) == "C-"
        assert calculate_letter_grade(70) == "C-"

    def test_grade_d(self):
        """Score 60-69 should give grade D."""
        assert calculate_letter_grade(69) == "D"
        assert calculate_letter_grade(65) == "D"
        assert calculate_letter_grade(60) == "D"

    def test_grade_f(self):
        """Score < 60 should give grade F."""
        assert calculate_letter_grade(59) == "F"
        assert calculate_letter_grade(30) == "F"
        assert calculate_letter_grade(0) == "F"


class TestSeverityConversion:
    """Tests for severity level conversion functions."""

    def test_severity_to_level_critical(self):
        """Severity L10 should map to CRITICAL."""
        assert severity_to_level(SEVERITY_L10) == "CRITICAL"
        assert severity_to_level(10) == "CRITICAL"

    def test_severity_to_level_major(self):
        """Severity L7-L9 should map to MAJOR."""
        assert severity_to_level(7) == "MAJOR"
        assert severity_to_level(SEVERITY_L8) == "MAJOR"
        assert severity_to_level(9) == "MAJOR"

    def test_severity_to_level_minor(self):
        """Severity L4-L6 should map to MINOR."""
        assert severity_to_level(4) == "MINOR"
        assert severity_to_level(SEVERITY_L5) == "MINOR"
        assert severity_to_level(6) == "MINOR"

    def test_severity_to_level_info(self):
        """Severity L1-L3 should map to INFO."""
        assert severity_to_level(SEVERITY_L1) == "INFO"
        assert severity_to_level(2) == "INFO"
        assert severity_to_level(3) == "INFO"

    def test_level_to_severity(self):
        """Level should map to appropriate severity."""
        assert level_to_severity("CRITICAL") == SEVERITY_L10
        assert level_to_severity("MAJOR") == SEVERITY_L8
        assert level_to_severity("MINOR") == SEVERITY_L5
        assert level_to_severity("INFO") == 2
        assert level_to_severity("PASSED") == SEVERITY_L1


class TestReportSerialization:
    """Tests for report serialization."""

    def test_to_dict(self):
        """to_dict should produce valid dictionary."""
        report = ValidationReport()
        report.passed("Test passed")
        report.critical("Test failed")

        d = report.to_dict()
        assert "score" in d
        assert "grade" in d
        assert "exit_code" in d
        assert "counts" in d
        assert "results" in d
        assert d["exit_code"] == EXIT_CRITICAL

    def test_to_json(self):
        """to_json should produce valid JSON string."""
        report = ValidationReport()
        report.minor("Test issue")

        json_str = report.to_json()
        parsed = json.loads(json_str)
        assert parsed["score"] == 97
        assert len(parsed["results"]) == 1

    def test_count_by_level(self):
        """count_by_level should count results correctly."""
        report = ValidationReport()
        report.passed("Pass 1")
        report.passed("Pass 2")
        report.minor("Minor 1")
        report.major("Major 1")
        report.major("Major 2")
        report.critical("Critical 1")

        counts = report.count_by_level()
        assert counts["PASSED"] == 2
        assert counts["INFO"] == 0
        assert counts["MINOR"] == 1
        assert counts["MAJOR"] == 2
        assert counts["CRITICAL"] == 1


class TestReportMerge:
    """Tests for report merging."""

    def test_merge_reports(self):
        """Merging should combine results from both reports."""
        report1 = ValidationReport()
        report1.passed("Pass 1")
        report1.minor("Minor 1")

        report2 = ValidationReport()
        report2.major("Major 1")
        report2.critical("Critical 1")

        report1.merge(report2)

        assert len(report1.results) == 4
        assert report1.has_critical
        assert report1.has_major
        assert report1.has_minor


class TestErrorAccumulation:
    """Tests for error accumulation pattern methods."""

    def test_get_all_errors(self):
        """get_all_errors should return only error-level results."""
        report = ValidationReport()
        report.passed("Pass")
        report.info("Info")
        report.minor("Minor")
        report.major("Major")
        report.critical("Critical")

        errors = report.get_all_errors()
        assert len(errors) == 3
        levels = {e.level for e in errors}
        assert levels == {"MINOR", "MAJOR", "CRITICAL"}

    def test_get_errors_by_level(self):
        """get_errors_by_level should filter by specific level."""
        report = ValidationReport()
        report.minor("Minor 1")
        report.minor("Minor 2")
        report.major("Major 1")

        minor_errors = report.get_errors_by_level("MINOR")
        assert len(minor_errors) == 2
        assert all(e.level == "MINOR" for e in minor_errors)

    def test_get_errors_by_phase(self):
        """get_errors_by_phase should filter by phase."""
        report = ValidationReport()
        report.add("MINOR", "Issue 1", phase="structure")
        report.add("MAJOR", "Issue 2", phase="semantic")
        report.add("MINOR", "Issue 3", phase="structure")
        report.add("INFO", "Info", phase="structure")  # Should not be included

        structure_errors = report.get_errors_by_phase("structure")
        assert len(structure_errors) == 2
        assert all(e.phase == "structure" for e in structure_errors)
