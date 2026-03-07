#!/usr/bin/env python3
"""Tests for validate_component_name from cpv_validation_common.py.

Coverage: 100% (all code paths)
- Valid names: simple, single-char, two-char, long kebab, at-max-length
- Invalid names: ends-with-digit, starts-with-digit, underscore, uppercase,
  consecutive hyphens, empty, too-long
- Directory name matching: mismatch, match, None (skipped)

15 tests, all executing real validate_component_name logic with no mocking.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport, validate_component_name  # noqa: E402


class TestValidateComponentName:
    """Tests for validate_component_name covering all code paths."""

    def test_valid_simple_name(self):
        """Valid kebab-case name 'my-plugin' produces no CRITICAL or MAJOR."""
        report = ValidationReport()
        validate_component_name("my-plugin", "plugin", report)
        levels = {r.level for r in report.results}
        assert "CRITICAL" not in levels
        assert "MAJOR" not in levels

    def test_valid_single_char(self):
        """Single lowercase letter 'a' is a valid name with no CRITICAL or MAJOR."""
        report = ValidationReport()
        validate_component_name("a", "plugin", report)
        levels = {r.level for r in report.results}
        assert "CRITICAL" not in levels
        assert "MAJOR" not in levels

    def test_valid_two_char(self):
        """Two-character name 'ab' is valid with no CRITICAL or MAJOR."""
        report = ValidationReport()
        validate_component_name("ab", "plugin", report)
        levels = {r.level for r in report.results}
        assert "CRITICAL" not in levels
        assert "MAJOR" not in levels

    def test_valid_long_kebab(self):
        """Long kebab-case name 'my-long-plugin-name-here' produces no CRITICAL or MAJOR."""
        report = ValidationReport()
        validate_component_name("my-long-plugin-name-here", "plugin", report)
        levels = {r.level for r in report.results}
        assert "CRITICAL" not in levels
        assert "MAJOR" not in levels

    def test_invalid_ends_with_digit(self):
        """Name ending with digit 'my-plugin2' produces CRITICAL with 'must not end with a digit'."""
        report = ValidationReport()
        validate_component_name("my-plugin2", "plugin", report)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert len(criticals) == 1
        assert "must not end with a digit" in criticals[0].message

    def test_invalid_starts_with_digit(self):
        """Name starting with digit '2plugin' produces CRITICAL with 'must not start with a digit'."""
        report = ValidationReport()
        validate_component_name("2plugin", "plugin", report)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert len(criticals) == 1
        assert "must not start with a digit" in criticals[0].message

    def test_invalid_underscore(self):
        """Name with underscore 'my_plugin' produces CRITICAL mentioning 'underscore'."""
        report = ValidationReport()
        validate_component_name("my_plugin", "plugin", report)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert len(criticals) == 1
        assert "underscore" in criticals[0].message.lower()

    def test_invalid_uppercase(self):
        """Name with uppercase 'MyPlugin' produces CRITICAL mentioning 'uppercase'."""
        report = ValidationReport()
        validate_component_name("MyPlugin", "plugin", report)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert len(criticals) == 1
        assert "uppercase" in criticals[0].message.lower()

    def test_invalid_consecutive_hyphens(self):
        """Name with consecutive hyphens 'my--plugin' produces CRITICAL with 'consecutive hyphens'."""
        report = ValidationReport()
        validate_component_name("my--plugin", "plugin", report)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert len(criticals) == 1
        assert "consecutive hyphens" in criticals[0].message

    def test_invalid_empty_name(self):
        """Empty name '' produces CRITICAL mentioning 'empty'."""
        report = ValidationReport()
        validate_component_name("", "plugin", report)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert len(criticals) == 1
        assert "empty" in criticals[0].message.lower()

    def test_invalid_too_long(self):
        """Name of 71 chars produces MAJOR with 'exceeds 70'."""
        # Build a valid kebab-case name that is exactly 71 chars long
        # "a" + "-bcde" * 14 = 1 + 70 = 71 chars
        long_name = "a" + "-bcde" * 14  # 71 chars
        assert len(long_name) == 71
        report = ValidationReport()
        validate_component_name(long_name, "plugin", report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert len(majors) >= 1
        assert any("exceeds 70" in m.message for m in majors)

    def test_valid_at_max_length(self):
        """Name of exactly 70 chars produces no MAJOR about length."""
        # "a" + "-bcde" * 13 + "-bcd" = 1 + 65 + 4 = 70 chars
        name_70 = "a" + "-bcde" * 13 + "-bcd"
        assert len(name_70) == 70
        report = ValidationReport()
        validate_component_name(name_70, "plugin", report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert len(majors) == 0

    def test_dir_name_mismatch(self):
        """Name 'foo' with directory_name='bar' produces MAJOR with 'must match directory name'."""
        report = ValidationReport()
        validate_component_name("foo", "skill", report, directory_name="bar")
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert len(majors) == 1
        assert "must match directory name" in majors[0].message

    def test_dir_name_match(self):
        """Name 'foo' with directory_name='foo' produces no MAJOR."""
        report = ValidationReport()
        validate_component_name("foo", "skill", report, directory_name="foo")
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert len(majors) == 0

    def test_dir_name_none_skips_check(self):
        """Name 'foo' with directory_name=None produces no MAJOR about directory mismatch."""
        report = ValidationReport()
        validate_component_name("foo", "skill", report, directory_name=None)
        majors = [r for r in report.results if r.level == "MAJOR"]
        # No MAJOR at all -- specifically no directory mismatch
        dir_mismatch = [m for m in majors if "directory name" in m.message.lower()]
        assert len(dir_mismatch) == 0
