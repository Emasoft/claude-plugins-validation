#!/usr/bin/env python3
"""Tests for validate_component_name from cpv_validation_common.py.

Coverage: 100% (all code paths)
- Valid names: simple, single-char, two-char, long kebab, ends-with-digit,
  starts-with-digit (every component type, TRDD-ZO54IL46), at-max-length
- Invalid names: underscore, uppercase, consecutive hyphens, empty, too-long,
  and a digit-first name that still breaks one of those rules
- Directory name matching: mismatch, match, None (skipped)

All tests execute real validate_component_name logic with no mocking.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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

    def test_valid_ends_with_digit(self):
        """Name ending with digit 'my-plugin2' is valid per AgentSkills.io spec."""
        report = ValidationReport()
        validate_component_name("my-plugin2", "plugin", report)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert len(criticals) == 0

    # TRDD-ZO54IL46: a leading digit is ACCEPTED. Claude Code itself accepts it
    # (`claude plugin validate` passes on plugin `3-pillars-tasks-management` with
    # command `3p-uninstall-rules`), and the marketplace manifest reference says a
    # plugin entry name starts "with a letter or digit". No Claude Code reference
    # chapter forbids a leading digit for any type this function validates.
    @pytest.mark.parametrize("component_type", ["plugin", "skill", "agent", "command", "mcp-server"])
    @pytest.mark.parametrize("name", ["3-pillars-tasks-management", "3p-uninstall-rules", "2plugin", "7"])
    def test_valid_starts_with_digit(self, component_type, name):
        """A digit-first kebab-case name produces no CRITICAL or MAJOR for any component type."""
        report = ValidationReport()
        validate_component_name(name, component_type, report)
        levels = {r.level for r in report.results}
        assert "CRITICAL" not in levels, [r.message for r in report.results]
        assert "MAJOR" not in levels, [r.message for r in report.results]

    def test_digit_first_with_uppercase_still_critical(self):
        """A digit-first name with an uppercase letter still produces the uppercase CRITICAL."""
        report = ValidationReport()
        validate_component_name("3Pillars", "plugin", report)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert len(criticals) == 1
        assert "uppercase" in criticals[0].message.lower()

    def test_digit_first_with_underscore_still_critical(self):
        """A digit-first name with an underscore still produces the underscore CRITICAL."""
        report = ValidationReport()
        validate_component_name("3p_rules", "command", report)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        assert len(criticals) == 1
        assert "underscore" in criticals[0].message.lower()

    @pytest.mark.parametrize("name", ["-3p", "3p-", "3p--rules"])
    def test_digit_first_hyphen_shape_still_critical(self, name):
        """A leading hyphen, trailing hyphen, or doubled hyphen is still refused after a digit start."""
        report = ValidationReport()
        validate_component_name(name, "agent", report)
        assert any(r.level == "CRITICAL" for r in report.results), name

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
        """Name of 65 chars produces MAJOR with 'exceeds 64'."""
        # Build a valid kebab-case name that is exactly 65 chars long
        # "a" + "-bcde" * 12 + "-bcd" = 1 + 60 + 4 = 65 chars
        long_name = "a" + "-bcde" * 12 + "-bcd"
        assert len(long_name) == 65
        report = ValidationReport()
        validate_component_name(long_name, "plugin", report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert len(majors) >= 1
        assert any("exceeds 64" in m.message for m in majors)

    def test_valid_at_max_length(self):
        """Name of exactly 64 chars produces no MAJOR about length."""
        # "a" + "-bcde" * 12 + "-bc" = 1 + 60 + 3 = 64 chars
        name_64 = "a" + "-bcde" * 12 + "-bc"
        assert len(name_64) == 64
        report = ValidationReport()
        validate_component_name(name_64, "plugin", report)
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
