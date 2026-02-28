#!/usr/bin/env python3
"""Tests for cpv_validation_common.py.

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

from cpv_validation_common import (  # noqa: E402
    EXIT_CRITICAL,
    EXIT_MAJOR,
    EXIT_MINOR,
    EXIT_NIT,
    EXIT_OK,
    SEVERITY_L1,
    SEVERITY_L2,
    SEVERITY_L3,
    SEVERITY_L5,
    SEVERITY_L8,
    SEVERITY_L10,
    FixableIssue,
    ValidationContext,
    ValidationReport,
    ValidationResult,
    build_private_path_patterns,
    calculate_letter_grade,
    check_utf8_encoding,
    colorize,
    format_result,
    is_path_gitignored,
    is_valid_kebab_case,
    level_to_severity,
    normalize_level,
    parse_gitignore,
    print_report_summary,
    print_results_by_level,
    scan_file_for_absolute_paths,
    scan_file_for_private_info,
    severity_to_level,
    validate_no_absolute_paths,
    validate_no_private_info,
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
        """Severity L1 maps to INFO, L2 to WARNING, L3 to NIT."""
        assert severity_to_level(SEVERITY_L1) == "INFO"
        assert severity_to_level(2) == "WARNING"
        assert severity_to_level(3) == "NIT"

    def test_level_to_severity(self):
        """Level should map to appropriate severity."""
        assert level_to_severity("CRITICAL") == SEVERITY_L10
        assert level_to_severity("MAJOR") == SEVERITY_L8
        assert level_to_severity("MINOR") == SEVERITY_L5
        assert level_to_severity("NIT") == SEVERITY_L3
        assert level_to_severity("WARNING") == SEVERITY_L2
        assert level_to_severity("INFO") == SEVERITY_L1
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


# =============================================================================
# Additional tests targeting uncovered lines
# =============================================================================


class TestFixableIssue:
    """Tests for FixableIssue dataclass and its apply method."""

    def test_apply_calls_fix_func_with_file_and_line(self):
        """FixableIssue.apply should call fix_func with file and line from result."""
        call_log = []

        def mock_fix(file_path: str, line: int | None) -> bool:
            call_log.append((file_path, line))
            return True

        result = ValidationResult(level="MINOR", message="Fixable issue", file="test.py", line=42)
        fixable = FixableIssue(result=result, fix_func=mock_fix, fix_description="Fix the thing")

        success = fixable.apply()
        assert success is True
        assert call_log == [("test.py", 42)]

    def test_apply_returns_false_when_no_file(self):
        """FixableIssue.apply should return False if result has no file."""
        def mock_fix(file_path: str, line: int | None) -> bool:
            return True

        result = ValidationResult(level="MINOR", message="No file issue", file=None)
        fixable = FixableIssue(result=result, fix_func=mock_fix, fix_description="Cannot fix without file")

        success = fixable.apply()
        assert success is False

    def test_apply_returns_false_when_fix_func_fails(self):
        """FixableIssue.apply should return False when fix_func returns False."""
        def failing_fix(file_path: str, line: int | None) -> bool:
            return False

        result = ValidationResult(level="MAJOR", message="Unfixable", file="broken.py", line=10)
        fixable = FixableIssue(result=result, fix_func=failing_fix, fix_description="Cannot fix")

        success = fixable.apply()
        assert success is False


class TestValidationReportFixables:
    """Tests for ValidationReport fixable issue methods."""

    def test_add_fixable_registers_issue_and_result(self):
        """add_fixable should add both a result and a FixableIssue."""
        report = ValidationReport()

        def dummy_fix(file_path: str, line: int | None) -> bool:
            return True

        report.add_fixable(
            level="MINOR",
            message="Can be fixed",
            fix_func=dummy_fix,
            fix_description="Apply auto-fix",
            file="src/module.py",
            line=15,
            phase="structure",
        )

        assert len(report.results) == 1
        assert report.results[0].fixable is True
        assert report.results[0].fix_id == "fix_0"
        assert report.results[0].level == "MINOR"
        assert report.results[0].file == "src/module.py"
        assert len(report.fixable_issues) == 1
        assert report.fixable_issues[0].fix_description == "Apply auto-fix"

    def test_apply_fixes_applies_and_updates_results(self):
        """apply_fixes should apply each fix and mark successful ones as PASSED."""
        report = ValidationReport()

        def good_fix(file_path: str, line: int | None) -> bool:
            return True

        def bad_fix(file_path: str, line: int | None) -> bool:
            return False

        report.add_fixable("MINOR", "Fix me", good_fix, "Auto-fix 1", file="a.py", line=1)
        report.add_fixable("MAJOR", "Also fix", bad_fix, "Auto-fix 2", file="b.py", line=2)

        stats = report.apply_fixes()
        assert stats["applied"] == 1
        assert stats["failed"] == 1
        assert stats["skipped"] == 0

        # The successfully fixed result should be updated
        assert report.results[0].level == "PASSED"
        assert report.results[0].message.startswith("[FIXED]")
        # The failed one should remain unchanged
        assert report.results[1].level == "MAJOR"

    def test_apply_fixes_dry_run_skips_all(self):
        """apply_fixes with dry_run=True should skip all fixes without applying."""
        report = ValidationReport()

        def good_fix(file_path: str, line: int | None) -> bool:
            return True

        report.add_fixable("MINOR", "Fix me", good_fix, "desc", file="a.py")

        stats = report.apply_fixes(dry_run=True)
        assert stats["applied"] == 0
        assert stats["failed"] == 0
        assert stats["skipped"] == 1
        # Result should remain unchanged
        assert report.results[0].level == "MINOR"

    def test_apply_fixes_handles_exception_in_fix_func(self):
        """apply_fixes should count as failed when fix_func raises exception."""
        report = ValidationReport()

        def exploding_fix(file_path: str, line: int | None) -> bool:
            raise RuntimeError("boom")

        report.add_fixable("MINOR", "Explodes", exploding_fix, "Risky fix", file="c.py")

        stats = report.apply_fixes()
        assert stats["applied"] == 0
        assert stats["failed"] == 1


class TestValidationReportPartialValidation:
    """Tests for ValidationReport partial validation support methods."""

    def test_add_and_get_valid_items(self):
        """add_valid_item and get_valid_items should track valid items."""
        report = ValidationReport()
        report.add_valid_item({"name": "plugin-a"})
        report.add_valid_item({"name": "plugin-b"})

        valid = report.get_valid_items()
        assert len(valid) == 2
        assert valid[0]["name"] == "plugin-a"
        assert valid[1]["name"] == "plugin-b"

    def test_add_and_get_failed_items(self):
        """add_failed_item and get_failed_items should track failed items."""
        report = ValidationReport()
        report.add_failed_item({"name": "bad-plugin"})

        failed = report.get_failed_items()
        assert len(failed) == 1
        assert failed[0]["name"] == "bad-plugin"

    def test_to_dict_includes_item_counts(self):
        """to_dict should include valid_items_count and failed_items_count."""
        report = ValidationReport()
        report.add_valid_item("item1")
        report.add_valid_item("item2")
        report.add_failed_item("item3")

        d = report.to_dict()
        assert d["valid_items_count"] == 2
        assert d["failed_items_count"] == 1
        assert d["fixable_count"] == 0


class TestValidationReportNitAndWarning:
    """Tests for NIT and WARNING level features."""

    def test_has_nit_property(self):
        """has_nit should detect NIT-level issues."""
        report = ValidationReport()
        assert not report.has_nit
        report.nit("Nit pick")
        assert report.has_nit

    def test_nit_deducts_one_point(self):
        """Each NIT should deduct 1 point from score."""
        report = ValidationReport()
        report.nit("Nit 1")
        assert report.score == 99
        report.nit("Nit 2")
        assert report.score == 98

    def test_exit_code_strict_returns_nit_exit(self):
        """exit_code_strict should return EXIT_NIT when only NITs present."""
        report = ValidationReport()
        report.nit("A nit issue")
        assert report.exit_code == EXIT_OK  # Normal mode: NITs don't block
        assert report.exit_code_strict() == EXIT_NIT  # Strict mode: NITs block

    def test_exit_code_strict_returns_higher_severity_first(self):
        """exit_code_strict should return MAJOR exit code when MAJOR issues exist alongside NITs."""
        report = ValidationReport()
        report.nit("A nit")
        report.major("A major")
        assert report.exit_code_strict() == EXIT_MAJOR

    def test_warning_method_adds_warning_level(self):
        """warning() convenience method should add WARNING level result."""
        report = ValidationReport()
        report.warning("Security advisory", file="setup.py", line=5)
        assert len(report.results) == 1
        assert report.results[0].level == "WARNING"
        assert report.results[0].file == "setup.py"
        assert report.results[0].line == 5


class TestValidationContext:
    """Tests for ValidationContext error accumulation pattern."""

    def test_set_phase_and_check_passing(self):
        """check() with True condition should add PASSED result with context name prefix."""
        ctx = ValidationContext(name="test-ctx")
        ctx.set_phase("structure")
        result = ctx.check(True, "MAJOR", "File exists", file="plugin.json")
        assert result is True

        report = ctx.finalize()
        assert len(report.results) == 1
        assert report.results[0].level == "PASSED"
        assert "[test-ctx]" in report.results[0].message

    def test_check_failing_adds_error(self):
        """check() with False condition should add error at specified level."""
        ctx = ValidationContext(name="test-ctx")
        ctx.set_phase("semantic")
        result = ctx.check(False, "MAJOR", "Missing description", file="plugin.json")
        assert result is False

        report = ctx.finalize()
        assert len(report.results) == 1
        assert report.results[0].level == "MAJOR"
        assert report.results[0].phase == "semantic"
        assert "[test-ctx]" in report.results[0].message

    def test_require_delegates_to_check_with_critical(self):
        """require() should delegate to check with CRITICAL level."""
        ctx = ValidationContext(name="req-ctx")
        result = ctx.require(False, "Plugin manifest missing")
        assert result is False
        report = ctx.finalize()
        assert report.results[0].level == "CRITICAL"
        assert "[req-ctx]" in report.results[0].message

    def test_validate_item_tracks_valid_and_failed(self):
        """validate_item should track items and add errors for failures."""
        ctx = ValidationContext(name="item-ctx")
        ctx.set_phase("items")

        good_result = ctx.validate_item("good-item", lambda x: True, "good-item")
        bad_result = ctx.validate_item("bad-item", lambda x: False, "bad-item")

        assert good_result is True
        assert bad_result is False

        report = ctx.finalize()
        assert len(report.get_valid_items()) == 1
        assert len(report.get_failed_items()) == 1
        # Failed item should produce a MAJOR error
        errors = report.get_all_errors()
        assert len(errors) == 1
        assert errors[0].level == "MAJOR"

    def test_validate_item_handles_exception(self):
        """validate_item should catch exceptions and add CRITICAL error."""
        ctx = ValidationContext(name="exc-ctx")

        def exploding_validator(item):
            raise ValueError("bad item format")

        result = ctx.validate_item("item", exploding_validator, "exploding-item")
        assert result is False

        report = ctx.finalize()
        assert len(report.get_failed_items()) == 1
        errors = report.get_all_errors()
        assert len(errors) == 1
        assert errors[0].level == "CRITICAL"
        assert "bad item format" in errors[0].message

    def test_add_error_adds_without_condition(self):
        """add_error should add error directly without condition check."""
        ctx = ValidationContext(name="direct-ctx")
        ctx.set_phase("security")
        ctx.add_error("MINOR", "Optional improvement", file="hook.sh", line=10)

        report = ctx.finalize()
        assert len(report.results) == 1
        assert report.results[0].level == "MINOR"
        assert report.results[0].phase == "security"
        assert "[direct-ctx]" in report.results[0].message

    def test_has_errors_and_error_count(self):
        """has_errors and error_count should reflect accumulated errors."""
        ctx = ValidationContext(name="count-ctx")
        assert not ctx.has_errors
        assert ctx.error_count == 0

        ctx.check(False, "MINOR", "Issue 1")
        ctx.check(False, "MAJOR", "Issue 2")
        ctx.check(True, "MINOR", "This passes")

        assert ctx.has_errors
        assert ctx.error_count == 2

    def test_add_fixable_through_context(self):
        """add_fixable should register fixable issue through context."""
        ctx = ValidationContext(name="fix-ctx")
        ctx.set_phase("format")

        def dummy_fix(file_path: str, line: int | None) -> bool:
            return True

        ctx.add_fixable("MINOR", "Trailing whitespace", dummy_fix, "Remove trailing whitespace", file="README.md", line=5)

        report = ctx.finalize()
        assert len(report.fixable_issues) == 1
        assert report.fixable_issues[0].fix_description == "Remove trailing whitespace"
        assert report.results[0].fixable is True
        assert "[fix-ctx]" in report.results[0].message


class TestIsValidKebabCase:
    """Tests for kebab-case name validation."""

    def test_valid_kebab_case_names(self):
        """Valid kebab-case names should return True."""
        assert is_valid_kebab_case("my-plugin") is True
        assert is_valid_kebab_case("a") is True
        assert is_valid_kebab_case("plugin123") is True
        assert is_valid_kebab_case("my-cool-plugin") is True
        assert is_valid_kebab_case("a1-b2-c3") is True

    def test_invalid_kebab_case_names(self):
        """Invalid kebab-case names should return False."""
        assert is_valid_kebab_case("My-Plugin") is False  # uppercase
        assert is_valid_kebab_case("my_plugin") is False  # underscore
        assert is_valid_kebab_case("-leading") is False  # leading hyphen
        assert is_valid_kebab_case("trailing-") is False  # trailing hyphen
        assert is_valid_kebab_case("") is False  # empty
        assert is_valid_kebab_case("123start") is False  # starts with digit


class TestColorizeAndFormat:
    """Tests for colorize and format_result functions."""

    def test_colorize_applies_known_level_color(self):
        """colorize should wrap text with ANSI color for known levels."""
        result = colorize("Error!", "CRITICAL")
        assert "\033[91m" in result  # Red
        assert "Error!" in result
        assert "\033[0m" in result  # Reset

    def test_colorize_unknown_level_no_color(self):
        """colorize should not add color prefix for unknown levels."""
        result = colorize("text", "UNKNOWN")
        assert result == "text\033[0m"

    def test_format_result_with_file_and_line(self):
        """format_result should include file:line location."""
        vr = ValidationResult(level="MAJOR", message="Issue found", file="plugin.json", line=42)
        formatted = format_result(vr)
        assert "MAJOR" in formatted
        assert "Issue found" in formatted
        assert "plugin.json:42" in formatted

    def test_format_result_without_file(self):
        """format_result should not include location when no file given."""
        vr = ValidationResult(level="INFO", message="General info")
        formatted = format_result(vr)
        assert "INFO" in formatted
        assert "General info" in formatted


class TestPrintFunctions:
    """Tests for print_report_summary and print_results_by_level."""

    def test_print_report_summary_outputs_to_stdout(self, capsys):
        """print_report_summary should print score, grade, and counts."""
        report = ValidationReport()
        report.passed("All good")
        report.minor("Small issue")
        print_report_summary(report, title="Test Report")

        captured = capsys.readouterr()
        assert "Test Report" in captured.out
        assert "97/100" in captured.out
        assert "A+" in captured.out
        assert "MINOR:    1" in captured.out

    def test_print_results_by_level_shows_errors(self, capsys):
        """print_results_by_level should display error-level results."""
        report = ValidationReport()
        report.critical("Critical thing")
        report.major("Major thing")
        report.minor("Minor thing")
        report.nit("Nit thing")
        report.warning("Warning thing")
        report.info("Info thing")
        report.passed("Passed thing")

        print_results_by_level(report, verbose=True)

        captured = capsys.readouterr()
        assert "CRITICAL ISSUES" in captured.out
        assert "MAJOR ISSUES" in captured.out
        assert "MINOR ISSUES" in captured.out
        assert "NIT ISSUES" in captured.out
        assert "WARNINGS" in captured.out
        assert "INFO" in captured.out
        assert "PASSED" in captured.out


class TestCheckUtf8Encoding:
    """Tests for UTF-8 encoding validation."""

    def test_valid_utf8_returns_true(self):
        """Valid UTF-8 content should return True."""
        report = ValidationReport()
        content = "Hello, world!".encode("utf-8")
        assert check_utf8_encoding(content, report, "test.txt") is True
        assert len(report.results) == 0

    def test_utf8_bom_returns_false(self):
        """Content with UTF-8 BOM should return False and add MAJOR error."""
        report = ValidationReport()
        content = b"\xef\xbb\xbfHello"
        assert check_utf8_encoding(content, report, "bom.txt") is False
        assert report.has_major
        assert "BOM" in report.results[0].message

    def test_invalid_utf8_returns_false(self):
        """Non-UTF-8 content should return False and add MAJOR error."""
        report = ValidationReport()
        content = b"\xff\xfe\x00\x00"  # Invalid UTF-8 bytes
        assert check_utf8_encoding(content, report, "bad.txt") is False
        assert report.has_major
        assert "not valid UTF-8" in report.results[0].message


class TestNormalizeLevel:
    """Tests for normalize_level function."""

    def test_normalize_known_levels(self):
        """Known levels in any case should normalize to uppercase."""
        assert normalize_level("critical") == "CRITICAL"
        assert normalize_level("Major") == "MAJOR"
        assert normalize_level("minor") == "MINOR"
        assert normalize_level("NIT") == "NIT"
        assert normalize_level("warning") == "WARNING"
        assert normalize_level("info") == "INFO"
        assert normalize_level("passed") == "PASSED"

    def test_normalize_unknown_level_defaults_to_info(self):
        """Unknown level strings should default to INFO."""
        assert normalize_level("SEVERE") == "INFO"
        assert normalize_level("") == "INFO"
        assert normalize_level("debug") == "INFO"


class TestGitignoreParsing:
    """Tests for parse_gitignore and is_path_gitignored."""

    def test_parse_gitignore_reads_patterns(self, tmp_path):
        """parse_gitignore should read non-empty, non-comment lines from .gitignore."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("# comment\n\nnode_modules/\n*.pyc\nbuild/\n")
        patterns = parse_gitignore(gitignore)
        assert patterns == ["node_modules/", "*.pyc", "build/"]

    def test_parse_gitignore_nonexistent_returns_empty(self, tmp_path):
        """parse_gitignore should return empty list for non-existent file."""
        patterns = parse_gitignore(tmp_path / "nonexistent")
        assert patterns == []

    def test_is_path_gitignored_matches_pattern(self):
        """is_path_gitignored should match files against gitignore patterns."""
        patterns = ["*.pyc", "node_modules/", "build/"]
        assert is_path_gitignored("foo.pyc", patterns) is True
        assert is_path_gitignored("src/bar.pyc", patterns) is True
        assert is_path_gitignored("node_modules", patterns) is True
        assert is_path_gitignored("src/main.py", patterns) is False

    def test_is_path_gitignored_anchored_pattern(self):
        """Anchored patterns (starting with /) should match from root only."""
        patterns = ["/dist"]
        assert is_path_gitignored("dist", patterns) is True
        assert is_path_gitignored("sub/dist", patterns) is False

    def test_is_path_gitignored_negation_pattern_skipped(self):
        """Negation patterns (starting with !) should be skipped."""
        patterns = ["*.log", "!important.log"]
        # important.log still matches *.log because negation is not fully implemented
        assert is_path_gitignored("debug.log", patterns) is True

    def test_is_path_gitignored_doublestar_pattern(self):
        """Double-star patterns should match across nested directories."""
        patterns = ["**/test"]
        # After ** simplification, **/test becomes */test which matches sub/test
        assert is_path_gitignored("sub/test", patterns) is True
        # Plain 'test' without directory prefix does not match */test
        assert is_path_gitignored("test", patterns) is False


class TestBuildPrivatePathPatterns:
    """Tests for build_private_path_patterns function."""

    def test_builds_patterns_for_usernames(self):
        """build_private_path_patterns should create regex patterns for each username."""
        patterns = build_private_path_patterns({"alice"})
        # Should have patterns for macOS, Linux, Windows (backslash), Windows (forward slash), and in-path
        assert len(patterns) >= 4
        # Verify macOS pattern matches
        matched = any(p.search("/Users/alice/Documents") for p, _ in patterns)
        assert matched is True
        # Verify Linux pattern matches
        matched = any(p.search("/home/alice/code") for p, _ in patterns)
        assert matched is True

    def test_empty_usernames_returns_empty(self):
        """build_private_path_patterns with empty set should return empty list."""
        patterns = build_private_path_patterns(set())
        assert patterns == []


class TestScanFileForPrivateInfo:
    """Tests for scan_file_for_private_info function."""

    def test_detects_private_username_in_file(self, tmp_path):
        """scan_file_for_private_info should detect known private paths."""
        test_file = tmp_path / "config.json"
        # Use a username that is NOT in EXAMPLE_USERNAMES
        test_file.write_text('{"path": "/Users/secretperson123/Documents/project"}')

        report = ValidationReport()
        count = scan_file_for_private_info(
            test_file,
            report,
            "config.json",
            additional_usernames={"secretperson123"},
        )
        assert count >= 1
        assert report.has_critical or report.has_major

    def test_clean_file_returns_zero(self, tmp_path):
        """scan_file_for_private_info should return 0 for clean files."""
        test_file = tmp_path / "clean.py"
        test_file.write_text('print("hello world")\ndata = {"key": "value"}\n')

        report = ValidationReport()
        count = scan_file_for_private_info(test_file, report, "clean.py")
        assert count == 0

    def test_unreadable_file_returns_zero(self, tmp_path):
        """scan_file_for_private_info should return 0 for non-existent file paths."""
        report = ValidationReport()
        count = scan_file_for_private_info(tmp_path / "nonexistent.py", report, "nonexistent.py")
        assert count == 0


class TestScanFileForAbsolutePaths:
    """Tests for scan_file_for_absolute_paths function."""

    def test_detects_absolute_home_path(self, tmp_path):
        """scan_file_for_absolute_paths should detect absolute home directory paths."""
        test_file = tmp_path / "script.py"
        # Content without regex metacharacters in the matched portion
        test_file.write_text('DATA_DIR = "/Users/realperson999/Documents/data"\n')

        report = ValidationReport()
        count = scan_file_for_absolute_paths(test_file, report, "script.py")
        # The pattern should match as a home directory absolute path
        assert count >= 1

    def test_clean_file_no_issues(self, tmp_path):
        """scan_file_for_absolute_paths should find no issues in a clean file."""
        test_file = tmp_path / "clean.sh"
        test_file.write_text('#!/usr/bin/env bash\necho "hello"\npath="${CLAUDE_PLUGIN_ROOT}/scripts"\n')

        report = ValidationReport()
        count = scan_file_for_absolute_paths(test_file, report, "clean.sh")
        assert count == 0

    def test_skips_env_var_references(self, tmp_path):
        """scan_file_for_absolute_paths should skip paths that use env var references."""
        test_file = tmp_path / "setup.sh"
        test_file.write_text('CONFIG="${HOME}/.config/app"\nROOT="${CLAUDE_PLUGIN_ROOT}/data"\n')

        report = ValidationReport()
        count = scan_file_for_absolute_paths(test_file, report, "setup.sh")
        assert count == 0


class TestValidateNoPrivateInfo:
    """Tests for validate_no_private_info directory scanning."""

    def test_clean_directory_passes(self, tmp_path):
        """validate_no_private_info should pass for a clean directory."""
        (tmp_path / "plugin.json").write_text('{"name": "test"}')
        (tmp_path / "README.md").write_text("# Test Plugin\n")

        report = ValidationReport()
        validate_no_private_info(tmp_path, report)
        # Should have a PASSED result and no errors
        passed_results = [r for r in report.results if r.level == "PASSED"]
        assert len(passed_results) >= 1

    def test_directory_with_private_info_fails(self, tmp_path):
        """validate_no_private_info should detect private info in directory."""
        (tmp_path / "config.json").write_text('{"cache": "/Users/secretagent007/cache"}')

        report = ValidationReport()
        validate_no_private_info(tmp_path, report, additional_usernames={"secretagent007"})
        errors = report.get_all_errors()
        assert len(errors) >= 1


class TestValidateNoAbsolutePaths:
    """Tests for validate_no_absolute_paths directory scanning."""

    def test_clean_directory_passes(self, tmp_path):
        """validate_no_absolute_paths should pass for a directory with no absolute paths."""
        (tmp_path / "plugin.json").write_text('{"name": "clean-plugin"}')
        (tmp_path / "setup.sh").write_text('echo "${CLAUDE_PLUGIN_ROOT}"\n')

        report = ValidationReport()
        validate_no_absolute_paths(tmp_path, report, respect_gitignore=False)

        passed_results = [r for r in report.results if r.level == "PASSED"]
        assert len(passed_results) >= 1

    def test_directory_with_absolute_paths_fails(self, tmp_path):
        """validate_no_absolute_paths should detect absolute paths."""
        (tmp_path / "config.json").write_text('{"bin": "/Users/hackerman99/bin/tool"}')

        report = ValidationReport()
        validate_no_absolute_paths(tmp_path, report, respect_gitignore=False)

        errors = report.get_all_errors()
        assert len(errors) >= 1
