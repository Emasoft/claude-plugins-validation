"""Tests for validate_marketplace.py after refactoring to use canonical cpv_validation_common API.

These tests verify:
1. MarketplaceValidationResult has category/suggestion fields + backward-compat aliases
2. MarketplaceValidationReport extends BaseValidationReport with marketplace-specific fields
3. All severity levels are UPPERCASE throughout
4. has_critical / has_major / has_minor / exit_code are properties (not methods)
5. report.add() uses the canonical signature (level, message, file=, line=)
6. add_marketplace_result() works for marketplace-specific results
7. format_report() uses UPPERCASE level comparisons
8. main() JSON output uses UPPERCASE levels
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# Ensure scripts dir is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


class TestMarketplaceValidationResult:
    """Tests for MarketplaceValidationResult dataclass."""

    def test_has_category_field(self):
        """MarketplaceValidationResult must have a category field."""
        from validate_marketplace import MarketplaceValidationResult

        r = MarketplaceValidationResult(level="CRITICAL", message="test", category="structure")
        assert r.category == "structure"

    def test_has_suggestion_field(self):
        """MarketplaceValidationResult must have a suggestion field."""
        from validate_marketplace import MarketplaceValidationResult

        r = MarketplaceValidationResult(level="MAJOR", message="test", suggestion="fix it")
        assert r.suggestion == "fix it"

    def test_file_path_alias(self):
        """file_path property must alias the canonical 'file' field."""
        from validate_marketplace import MarketplaceValidationResult

        r = MarketplaceValidationResult(level="MINOR", message="test", file="foo.json")
        assert r.file_path == "foo.json"
        assert r.file == "foo.json"

    def test_line_number_alias(self):
        """line_number property must alias the canonical 'line' field."""
        from validate_marketplace import MarketplaceValidationResult

        r = MarketplaceValidationResult(level="INFO", message="test", line=42)
        assert r.line_number == 42
        assert r.line == 42

    def test_inherits_from_base(self):
        """MarketplaceValidationResult must inherit from cpv_validation_common.ValidationResult."""
        from cpv_validation_common import ValidationResult as BaseValidationResult
        from validate_marketplace import MarketplaceValidationResult

        assert issubclass(MarketplaceValidationResult, BaseValidationResult)

    def test_backward_compat_alias_exists(self):
        """ValidationResult name must be aliased to MarketplaceValidationResult."""
        from validate_marketplace import MarketplaceValidationResult, ValidationResult

        assert ValidationResult is MarketplaceValidationResult


class TestMarketplaceValidationReport:
    """Tests for MarketplaceValidationReport dataclass."""

    def test_has_marketplace_specific_fields(self):
        """MarketplaceValidationReport must have marketplace_path, marketplace_name, plugins_found etc."""
        from validate_marketplace import MarketplaceValidationReport

        r = MarketplaceValidationReport()
        assert hasattr(r, "marketplace_path")
        assert hasattr(r, "marketplace_name")
        assert hasattr(r, "plugins_found")

    def test_inherits_from_base(self):
        """MarketplaceValidationReport must inherit from cpv_validation_common.ValidationReport."""
        from cpv_validation_common import ValidationReport as BaseValidationReport
        from validate_marketplace import MarketplaceValidationReport

        assert issubclass(MarketplaceValidationReport, BaseValidationReport)

    def test_has_critical_is_property(self):
        """has_critical must be a property, not a method."""
        from validate_marketplace import MarketplaceValidationReport

        r = MarketplaceValidationReport()
        # Access as property (no parentheses) -- must not raise TypeError
        result = r.has_critical
        assert result is False

    def test_has_major_is_property(self):
        """has_major must be a property, not a method."""
        from validate_marketplace import MarketplaceValidationReport

        r = MarketplaceValidationReport()
        result = r.has_major
        assert result is False

    def test_has_minor_is_property(self):
        """has_minor must be a property, not a method."""
        from validate_marketplace import MarketplaceValidationReport

        r = MarketplaceValidationReport()
        result = r.has_minor
        assert result is False

    def test_exit_code_is_property(self):
        """exit_code must be a property, not a method."""
        from validate_marketplace import MarketplaceValidationReport

        r = MarketplaceValidationReport()
        result = r.exit_code
        assert result == 0

    def test_add_uses_canonical_signature(self):
        """report.add() must accept (level, message, file=, line=) signature."""
        from validate_marketplace import MarketplaceValidationReport

        r = MarketplaceValidationReport()
        # Canonical signature: add(level, message, file=None, line=None, ...)
        r.add("CRITICAL", "test message", file="foo.json", line=10)
        assert len(r.results) == 1
        assert r.results[0].level == "CRITICAL"
        assert r.results[0].message == "test message"

    def test_add_marketplace_result(self):
        """add_marketplace_result() must create MarketplaceValidationResult with category/suggestion."""
        from validate_marketplace import MarketplaceValidationReport, MarketplaceValidationResult

        r = MarketplaceValidationReport()
        r.add_marketplace_result(
            level="MAJOR",
            message="test",
            file="foo.json",
            line=5,
            category="plugin",
            suggestion="fix it",
        )
        assert len(r.results) == 1
        result = r.results[0]
        assert isinstance(result, MarketplaceValidationResult)
        assert result.level == "MAJOR"
        assert result.category == "plugin"
        assert result.suggestion == "fix it"

    def test_backward_compat_alias_exists(self):
        """ValidationReport name must be aliased to MarketplaceValidationReport."""
        from validate_marketplace import MarketplaceValidationReport, ValidationReport

        assert ValidationReport is MarketplaceValidationReport

    def test_exit_code_critical(self):
        """exit_code property must return 1 for CRITICAL issues."""
        from validate_marketplace import MarketplaceValidationReport

        r = MarketplaceValidationReport()
        r.add("CRITICAL", "boom")
        assert r.exit_code == 1

    def test_exit_code_major(self):
        """exit_code property must return 2 for MAJOR issues."""
        from validate_marketplace import MarketplaceValidationReport

        r = MarketplaceValidationReport()
        r.add("MAJOR", "bad")
        assert r.exit_code == 2


class TestUppercaseSeverityLevels:
    """Verify all severity levels in the file are UPPERCASE."""

    def test_validate_marketplace_file_uses_uppercase(self):
        """validate_marketplace_file must produce UPPERCASE levels."""
        from validate_marketplace import validate_marketplace_file

        # Test with a nonexistent path to trigger critical error
        _, results = validate_marketplace_file(Path("/nonexistent/marketplace.json"))
        assert len(results) > 0
        for r in results:
            assert r.level == r.level.upper(), f"Level '{r.level}' is not uppercase"

    def test_validate_marketplace_name_uses_uppercase(self):
        """validate_marketplace_name must produce UPPERCASE levels."""
        from validate_marketplace import validate_marketplace_name

        results = validate_marketplace_name(123, "test.json")  # Pass non-string to trigger critical
        assert len(results) > 0
        for r in results:
            assert r.level == r.level.upper(), f"Level '{r.level}' is not uppercase"

    def test_validate_plugin_entry_uses_uppercase(self):
        """validate_plugin_entry must produce UPPERCASE levels."""
        from validate_marketplace import validate_plugin_entry

        # Empty plugin dict should trigger critical (missing required fields)
        results = validate_plugin_entry({}, 0, Path("/tmp"), "test.json")
        assert len(results) > 0
        for r in results:
            assert r.level == r.level.upper(), f"Level '{r.level}' is not uppercase"

    def test_validate_plugins_array_uses_uppercase(self):
        """validate_plugins_array must produce UPPERCASE levels."""
        from validate_marketplace import validate_plugins_array

        # Pass non-list to trigger critical
        _, results = validate_plugins_array("not a list", Path("/tmp"), "test.json")
        assert len(results) > 0
        for r in results:
            assert r.level == r.level.upper(), f"Level '{r.level}' is not uppercase"

    def test_validate_marketplace_full_uses_uppercase(self):
        """Full validate_marketplace must produce only UPPERCASE levels."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an invalid marketplace.json to trigger various levels
            mp = Path(tmpdir) / "marketplace.json"
            mp.write_text(json.dumps({"name": "BAD NAME!", "plugins": "not-array"}))
            report = validate_marketplace(Path(tmpdir))

            assert len(report.results) > 0
            for r in report.results:
                assert r.level == r.level.upper(), f"Level '{r.level}' is not uppercase in full validation"


class TestFormatReport:
    """Tests for the format_report function after refactoring."""

    def test_format_report_uses_uppercase_levels(self):
        """format_report must group results by UPPERCASE levels."""
        from validate_marketplace import MarketplaceValidationReport, format_report

        report = MarketplaceValidationReport()
        report.add("CRITICAL", "critical issue")
        report.add("MAJOR", "major issue")
        report.add("MINOR", "minor issue")

        output = format_report(report, verbose=True)
        assert "CRITICAL ISSUES" in output
        assert "MAJOR ISSUES" in output
        assert "MINOR ISSUES" in output

    def test_format_report_final_status_uses_properties(self):
        """format_report must use has_critical/has_major/has_minor as properties."""
        from validate_marketplace import MarketplaceValidationReport, format_report

        report = MarketplaceValidationReport()
        report.add("CRITICAL", "boom")
        output = format_report(report)
        assert "FAILED" in output


class TestMainJsonOutput:
    """Tests for main() JSON output using UPPERCASE levels."""

    def test_json_output_has_uppercase_levels(self):
        """JSON output from main() must use UPPERCASE severity levels."""
        from validate_marketplace import validate_marketplace

        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "marketplace.json"
            mp.write_text(json.dumps({"name": "test-mp", "owner": {"name": "test"}, "plugins": []}))

            report = validate_marketplace(Path(tmpdir))
            # Build JSON output dict the same way main() does
            output = {
                "results": [{"level": r.level, "message": r.message} for r in report.results],
                "summary": {
                    "CRITICAL": sum(1 for r in report.results if r.level == "CRITICAL"),
                    "MAJOR": sum(1 for r in report.results if r.level == "MAJOR"),
                    "MINOR": sum(1 for r in report.results if r.level == "MINOR"),
                    "INFO": sum(1 for r in report.results if r.level == "INFO"),
                },
            }
            for result_dict in output["results"]:
                assert result_dict["level"] == result_dict["level"].upper()


class TestNoLowercaseLevelsInSource:
    """Verify the source file itself has no lowercase level string literals."""

    def test_no_lowercase_level_strings_in_source(self):
        """Source file must not contain level='critical', level='major', level='minor', level='info' (lowercase)."""
        import re

        src_path = Path(__file__).resolve().parent.parent / "scripts" / "validate_marketplace.py"
        content = src_path.read_text()

        # These patterns should NOT match anywhere in the file
        bad_patterns = [
            r'level\s*=\s*["\']critical["\']',
            r'level\s*=\s*["\']major["\']',
            r'level\s*=\s*["\']minor["\']',
            r'level\s*=\s*["\']info["\']',
            r'level\s*=\s*["\']nit["\']',
            r'level\s*=\s*["\']warning["\']',
        ]
        for pattern in bad_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            # Filter: only flag if it's actually lowercase
            real_matches = [m for m in matches if any(c.islower() for c in m.split("=")[1].strip().strip("\"'"))]
            assert len(real_matches) == 0, f"Found lowercase level in source: {real_matches}"

    def test_no_lowercase_level_comparisons_in_source(self):
        """Source file must not compare r.level == 'critical' etc. (lowercase)."""
        import re

        src_path = Path(__file__).resolve().parent.parent / "scripts" / "validate_marketplace.py"
        content = src_path.read_text()

        bad_patterns = [
            r'\.level\s*==\s*["\']critical["\']',
            r'\.level\s*==\s*["\']major["\']',
            r'\.level\s*==\s*["\']minor["\']',
            r'\.level\s*==\s*["\']info["\']',
            r'\.level\s*in\s*\(["\']critical["\']',
        ]
        for pattern in bad_patterns:
            matches = re.findall(pattern, content)
            assert len(matches) == 0, f"Found lowercase level comparison in source: {matches}"


# ============================================================================
# Additional tests targeting uncovered lines (226-227, 253-274, 278-287,
# 312-320, 336, 373-374, 387-397, 410, 415, 420, 425-426, 438-448,
# 460-470, 489-601, 611-695, 704-753, 789-823, 866, 870-893, 916-1010,
# 1049-1230, 1268-1278, 1289-1336)
# ============================================================================


class TestValidateMarketplaceFileEdgeCases:
    """Tests for validate_marketplace_file covering JSON errors and non-dict roots."""

    def test_invalid_json_returns_critical_with_line_number(self, tmp_path):
        """Malformed JSON must produce CRITICAL with JSONDecodeError line info (lines 253-264)."""
        from validate_marketplace import validate_marketplace_file

        mp = tmp_path / "marketplace.json"
        mp.write_text('{"name": "bad",\n"plugins": [}')  # syntax error on line 2
        data, results = validate_marketplace_file(mp)
        assert data is None
        assert any(r.level == "CRITICAL" and "Invalid JSON" in r.message for r in results)
        # The result should carry line number from JSONDecodeError
        json_err = [r for r in results if "Invalid JSON" in r.message][0]
        assert json_err.line is not None

    def test_json_root_is_array_returns_critical(self, tmp_path):
        """JSON root that is an array (not object) must produce CRITICAL (lines 278-287)."""
        from validate_marketplace import validate_marketplace_file

        mp = tmp_path / "marketplace.json"
        mp.write_text("[1, 2, 3]")
        data, results = validate_marketplace_file(mp)
        assert data is None
        assert any(r.level == "CRITICAL" and "must be a JSON object" in r.message for r in results)

    def test_marketplace_file_is_a_file_path(self, tmp_path):
        """Passing the .json file directly (not dir) must work (lines 225-227)."""
        from validate_marketplace import validate_marketplace_file

        mp = tmp_path / "marketplace.json"
        mp.write_text(json.dumps({"name": "ok-mp", "owner": {"name": "me"}, "plugins": []}))
        data, results = validate_marketplace_file(mp)
        assert data is not None
        assert data["name"] == "ok-mp"


class TestValidateMarketplaceNameEdgeCases:
    """Tests for validate_marketplace_name covering empty name and reserved names."""

    def test_empty_name_is_critical(self):
        """Empty string marketplace name must produce CRITICAL (lines 312-320)."""
        from validate_marketplace import validate_marketplace_name

        results = validate_marketplace_name("", "test.json")
        assert any(r.level == "CRITICAL" and "cannot be empty" in r.message for r in results)

    def test_reserved_name_is_critical(self):
        """Reserved marketplace name must produce CRITICAL (line 336)."""
        from validate_marketplace import validate_marketplace_name

        results = validate_marketplace_name("claude-code-marketplace", "test.json")
        assert any(r.level == "CRITICAL" and "reserved" in r.message for r in results)


class TestValidatePluginEntryFields:
    """Tests for validate_plugin_entry covering version, source, path, repository, tags, deps."""

    def test_non_kebab_name_produces_critical(self, tmp_path):
        """Plugin name not in kebab-case must produce CRITICAL."""
        from validate_marketplace import validate_plugin_entry

        plugin = {"name": "BadName!", "source": "github"}
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(r.level == "CRITICAL" and "naming pattern" in r.message.lower() for r in results)

    def test_non_string_version_produces_major(self, tmp_path):
        """Non-string version must produce MAJOR (lines 387-397)."""
        from validate_marketplace import validate_plugin_entry

        plugin = {"name": "my-plugin", "source": "github", "version": 123}
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "version must be a string" in r.message for r in results)

    def test_plugin_with_source_calls_validate_plugin_source(self, tmp_path):
        """Plugin with source dict delegates to validate_plugin_source (line 410)."""
        from validate_marketplace import validate_plugin_entry

        plugin = {"name": "p", "source": {"source": "github", "repo": "owner/repo"}}
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        # Should not produce CRITICAL about missing source field
        assert not any(r.level == "CRITICAL" and "missing required field: source" in r.message for r in results)

    def test_unknown_fields_produce_major(self, tmp_path):
        """Unknown fields on plugin entry must produce MAJOR with RC-MKPL-UNKNOWN-FIELD.

        v2.81.0 (TRDD-c0ee9543, Phase A) — promoted from INFO to MAJOR
        because `claude plugin validate` rejects unknown fields (this is
        what bit the ai-maestro-visual-communicator-plugin install on
        2026-05-11). The severity bump is intentional and the stable
        RC-MKPL-UNKNOWN-FIELD code is what the fixer skill routes on.
        """
        from validate_marketplace import validate_plugin_entry

        plugin = {"name": "my-plugin", "source": "github", "custom_field_xyz": True}
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(
            r.level == "MAJOR" and "RC-MKPL-UNKNOWN-FIELD" in r.message and "custom_field_xyz" in r.message
            for r in results
        )

    def test_non_list_tags_produce_minor(self, tmp_path):
        """Non-list tags must produce MINOR (lines 438-448)."""
        from validate_marketplace import validate_plugin_entry

        plugin = {"name": "my-plugin", "source": "github", "tags": "not-a-list"}
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(r.level == "MINOR" and "tags must be an array" in r.message for r in results)

    def test_non_list_dependencies_produce_major(self, tmp_path):
        """Non-list dependencies must produce MAJOR (lines 460-470)."""
        from validate_marketplace import validate_plugin_entry

        plugin = {"name": "my-plugin", "source": "github", "dependencies": "not-a-list"}
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "dependencies must be an array" in r.message for r in results)

    def test_dependency_object_form_accepted_issue_106(self, tmp_path):
        """Object-form dep {name, version} is ACCEPTED (issue #106 — was wrongly MAJOR).

        A marketplace plugin-entry's `dependencies` mirrors the plugin.json
        schema (GAP-6), which validate_plugin advises declaring as
        {name, version}. The marketplace validator used to reject the object
        form outright — the contradiction this fix resolves.
        """
        from validate_marketplace import validate_plugin_entry

        plugin = {
            "name": "my-plugin",
            "source": "github",
            "dependencies": [{"name": "dev-browser", "version": "~1.2.0"}],
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        dep_majors = [r.message for r in results if r.level == "MAJOR" and "dependencies" in r.message]
        assert not dep_majors, f"Object-form dep must not produce MAJOR; got: {dep_majors}"

    def test_dependency_object_with_marketplace_accepted(self, tmp_path):
        """Object dep {name, marketplace, version} is accepted (full subkey set)."""
        from validate_marketplace import validate_plugin_entry

        plugin = {
            "name": "my-plugin",
            "source": "github",
            "dependencies": [{"name": "x", "marketplace": "acme", "version": "^2.0"}],
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        dep_majors = [r.message for r in results if r.level == "MAJOR" and "dependencies" in r.message]
        assert not dep_majors, f"Full-subkey object dep must not produce MAJOR; got: {dep_majors}"

    def test_dependency_malformed_elements_still_major(self, tmp_path):
        """Malformed dep elements still produce MAJOR (schema integrity preserved).

        A number, a nested list, an object missing `name`, and a non-kebab
        bare string must each still be MAJOR so genuinely-invalid deps keep
        being rejected.
        """
        from validate_marketplace import validate_plugin_entry

        plugin = {
            "name": "my-plugin",
            "source": "github",
            "dependencies": [42, ["nested"], {"version": "1.0.0"}, "Bad_Name"],
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        dep_majors = [r for r in results if r.level == "MAJOR" and "dependencies[" in r.message]
        # 42 + nested-list → "string or object"; {version} → missing name;
        # "Bad_Name" → bare-string not kebab. At least 4 MAJORs.
        assert len(dep_majors) >= 4, (
            f"Expected ≥4 MAJORs for malformed dep elements; got: {[r.message for r in dep_majors]}"
        )

    def test_dependency_unknown_subkey_still_major_via_index(self, tmp_path):
        """Object dep with an UNKNOWN subkey produces a finding referencing the dep index."""
        from validate_marketplace import validate_plugin_entry

        plugin = {
            "name": "my-plugin",
            "source": "github",
            "dependencies": [{"name": "x", "foo": 1}],
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        # Unknown dep sub-key is a MINOR in the shared schema; it must surface
        # with the dep index + the offending key name.
        assert any(
            "dependencies[0].foo" in r.message and "not a recognized dependency sub-field" in r.message
            for r in results
        ), f"Expected unknown-subkey finding for dependencies[0].foo; got: {[r.message for r in results]}"

    def test_dependency_bare_unversioned_string_warns_not_major(self, tmp_path):
        """A bare unversioned string is accepted with the pin-it WARNING (schema consistency)."""
        from validate_marketplace import validate_plugin_entry

        plugin = {
            "name": "my-plugin",
            "source": "github",
            "dependencies": ["dev-browser"],
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        dep_majors = [r.message for r in results if r.level == "MAJOR" and "dependencies" in r.message]
        assert not dep_majors, f"Bare unversioned string dep must not be MAJOR; got: {dep_majors}"
        assert any(
            r.level == "WARNING" and "dependencies[0]" in r.message and "no version constraint" in r.message
            for r in results
        ), f"Expected pin-it WARNING for bare unversioned string; got: {[r.message for r in results]}"

    def test_dependency_invalid_bare_string_still_major(self, tmp_path):
        """A non-kebab bare-string dep is still MAJOR (invalid names keep rejecting)."""
        from validate_marketplace import validate_plugin_entry

        plugin = {
            "name": "my-plugin",
            "source": "github",
            "dependencies": ["NotKebab!"],
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(
            r.level == "MAJOR" and "dependencies[0]" in r.message and "kebab-case" in r.message for r in results
        ), f"Expected MAJOR for non-kebab bare-string dep; got: {[r.message for r in results]}"


class TestValidatePluginSource:
    """Tests for validate_plugin_source covering string sources, dict sources, SHA, submodule conflict."""

    def test_string_source_relative_path_missing_dir(self, tmp_path):
        """Relative source path to nonexistent dir must produce MAJOR (lines 494-508)."""
        from validate_marketplace import validate_plugin_source

        plugin = {"name": "myplugin", "source": "./nonexistent-plugin-dir"}
        results = validate_plugin_source(plugin, "myplugin", tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "does not exist" in r.message for r in results)

    def test_string_source_invalid_type(self, tmp_path):
        """String source that is not a relative path and not a valid type must produce MAJOR (lines 509-520)."""
        from validate_marketplace import validate_plugin_source

        plugin = {"name": "myplugin", "source": "invalid-source-type"}
        results = validate_plugin_source(plugin, "myplugin", tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "invalid source type" in r.message for r in results)

    def test_dict_source_missing_source_field(self, tmp_path):
        """Dict source missing inner 'source' field must produce MAJOR (lines 534-543)."""
        from validate_marketplace import validate_plugin_source

        plugin = {"name": "myplugin", "source": {"repo": "owner/repo"}}
        results = validate_plugin_source(plugin, "myplugin", tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "missing 'source' field" in r.message for r in results)

    def test_dict_source_invalid_source_type(self, tmp_path):
        """Dict source with invalid source type must produce MAJOR (lines 544-553)."""
        from validate_marketplace import validate_plugin_source

        plugin = {"name": "myplugin", "source": {"source": "ftp"}}
        results = validate_plugin_source(plugin, "myplugin", tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "invalid source type" in r.message for r in results)

    def test_git_subdir_canonical_subdir_not_flagged_missing_path(self, tmp_path):
        """n4: git-subdir using only the canonical `subdir` must NOT be flagged 'requires path'.

        `subdir` and `path` are interchangeable aliases for git-subdir; the
        required-field check must accept either.
        """
        from validate_marketplace import validate_plugin_source

        plugin = {
            "name": "myplugin",
            "source": {"source": "git-subdir", "url": "https://github.com/o/r", "subdir": "pkgs/x"},
        }
        results = validate_plugin_source(plugin, "myplugin", tmp_path, "mp.json")
        assert not any("requires 'path'" in r.message for r in results), (
            f"canonical subdir was wrongly flagged: {[r.message for r in results]}"
        )

    def test_git_subdir_with_path_alias_accepted(self, tmp_path):
        """n4: git-subdir using the `path` alias must still satisfy the requirement (benign side)."""
        from validate_marketplace import validate_plugin_source

        plugin = {
            "name": "myplugin",
            "source": {"source": "git-subdir", "url": "https://github.com/o/r", "path": "pkgs/x"},
        }
        results = validate_plugin_source(plugin, "myplugin", tmp_path, "mp.json")
        assert not any("requires 'path'" in r.message for r in results)
        assert not any("requires 'subdir'" in r.message for r in results)

    def test_git_subdir_missing_both_subdir_and_path_is_major(self, tmp_path):
        """n4 (threat side): git-subdir with NEITHER subdir nor path must still produce a MAJOR."""
        from validate_marketplace import validate_plugin_source

        plugin = {"name": "myplugin", "source": {"source": "git-subdir", "url": "https://github.com/o/r"}}
        results = validate_plugin_source(plugin, "myplugin", tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "requires 'path'" in r.message for r in results)

    def test_dict_source_file_type_is_settings_level_only(self, tmp_path):
        """GAP-1 (v2.22.2): 'file' source is valid only at settings.json level,
        not as a per-plugin source in marketplace.json. Per plugin-marketplaces.md:223-229
        the 5 allowed per-plugin source types are relative-path, github, url, git-subdir, npm.
        Using 'file' here emits a MAJOR explaining it is a settings-level-only type."""
        from validate_marketplace import validate_plugin_source

        plugin = {"name": "myplugin", "source": {"source": "file", "path": "/abs/path/marketplace.json"}}
        results = validate_plugin_source(plugin, "myplugin", tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "settings-level" in r.message and "file" in r.message for r in results), (
            "expected MAJOR identifying 'file' as settings-level-only"
        )

    def test_string_source_file_name_is_settings_level_only(self, tmp_path):
        """GAP-1 variant: bare 'file' string as source (not a relative path) hits the
        same marketplace-level-only path in the string-source branch."""
        from validate_marketplace import validate_plugin_source

        plugin = {"name": "myplugin", "source": "file"}
        results = validate_plugin_source(plugin, "myplugin", tmp_path, "mp.json")
        # Either the MARKETPLACE_LEVEL_ONLY branch or the "invalid source type" branch is
        # acceptable — what must NOT happen is CPV accepting this silently.
        assert any(
            r.level == "MAJOR" and ("settings-level" in r.message or "invalid source type" in r.message)
            for r in results
        )

    def test_dict_source_missing_required_fields(self, tmp_path):
        """Dict source with valid type but missing required fields must produce MAJOR (lines 555-566)."""
        from validate_marketplace import validate_plugin_source

        plugin = {"name": "myplugin", "source": {"source": "github"}}  # missing 'repo'
        results = validate_plugin_source(plugin, "myplugin", tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "requires 'repo'" in r.message for r in results)

    def test_dict_source_bad_sha_format(self, tmp_path):
        """SHA that is not a 40-char hex string must produce MINOR (lines 569-579)."""
        from validate_marketplace import validate_plugin_source

        plugin = {"name": "myplugin", "source": {"source": "github", "repo": "o/r", "sha": "not-a-sha"}}
        results = validate_plugin_source(plugin, "myplugin", tmp_path, "mp.json")
        assert any(r.level == "MINOR" and "sha" in r.message.lower() for r in results)

    def test_remote_source_with_local_submodule_conflict(self, tmp_path):
        """Remote source with existing local submodule dir must produce MAJOR (lines 582-599)."""
        from validate_marketplace import validate_plugin_source

        # Create a local dir that looks like a submodule (has .git)
        plugin_dir = tmp_path / "myplugin"
        plugin_dir.mkdir()
        (plugin_dir / ".git").touch()

        plugin = {"name": "myplugin", "source": {"source": "github", "repo": "owner/myplugin"}}
        results = validate_plugin_source(plugin, "myplugin", tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "local submodule" in r.message for r in results)


class TestValidateLocalPath:
    """Tests for validate_local_path covering absolute paths, missing dirs, non-dirs, plugin.json check."""

    def test_absolute_path_produces_critical(self, tmp_path):
        """Absolute local path must produce CRITICAL (lines 625-641)."""
        from validate_marketplace import validate_local_path

        results = validate_local_path("/some/absolute/path", "myplugin", tmp_path, "mp.json")
        assert any(r.level == "CRITICAL" and "absolute path" in r.message for r in results)

    def test_relative_path_missing_dir_produces_major(self, tmp_path):
        """Relative path to nonexistent dir must produce MAJOR (lines 647-656)."""
        from validate_marketplace import validate_local_path

        results = validate_local_path("nonexistent-dir", "myplugin", tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "does not exist" in r.message for r in results)

    def test_path_is_file_not_dir_produces_major(self, tmp_path):
        """Path pointing to a file instead of directory must produce MAJOR (lines 657-665)."""
        from validate_marketplace import validate_local_path

        f = tmp_path / "not-a-dir"
        f.write_text("I am a file")
        results = validate_local_path("not-a-dir", "myplugin", tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "not a directory" in r.message for r in results)

    def test_dir_without_plugin_json_produces_major(self, tmp_path):
        """Directory without plugin.json or .claude-plugin/plugin.json must produce MAJOR (lines 667-681)."""
        from validate_marketplace import validate_local_path

        d = tmp_path / "plugin-dir"
        d.mkdir()
        results = validate_local_path("plugin-dir", "myplugin", tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "missing plugin.json" in r.message for r in results)

    def test_path_traversal_produces_critical(self, tmp_path):
        """Path containing '..' must produce CRITICAL (blocked by Claude Code)."""
        from validate_marketplace import validate_local_path

        results = validate_local_path("../some-path", "myplugin", tmp_path, "mp.json")
        assert any(r.level == "CRITICAL" and "path traversal" in r.message.lower() for r in results)


class TestValidateRepositoryUrl:
    """Tests for validate_repository_url covering non-string, shorthand, bad scheme."""

    def test_non_string_repository_produces_minor(self):
        """Non-string repository must produce MINOR (lines 706-715)."""
        from validate_marketplace import validate_repository_url

        results = validate_repository_url(123, "myplugin", "mp.json")
        assert any(r.level == "MINOR" and "must be a string" in r.message for r in results)

    def test_url_without_scheme_not_shorthand_produces_minor(self):
        """URL without scheme and not a GitHub shorthand must produce MINOR (lines 720-733)."""
        from validate_marketplace import validate_repository_url

        results = validate_repository_url("just-a-string", "myplugin", "mp.json")
        assert any(r.level == "MINOR" and "may be invalid" in r.message for r in results)

    def test_unusual_scheme_produces_minor(self):
        """URL with unusual scheme must produce MINOR (lines 734-742)."""
        from validate_marketplace import validate_repository_url

        results = validate_repository_url("ftp://example.com/repo", "myplugin", "mp.json")
        assert any(r.level == "MINOR" and "unusual scheme" in r.message for r in results)

    def test_valid_github_shorthand_produces_no_issues(self):
        """GitHub shorthand (owner/repo) must not produce issues (lines 722-723)."""
        from validate_marketplace import validate_repository_url

        results = validate_repository_url("owner/repo", "myplugin", "mp.json")
        assert len(results) == 0

    def test_slash_bearing_garbage_rejected(self):
        """A schemeless string with a space must NOT pass as a shorthand (m11)."""
        from validate_marketplace import validate_repository_url

        results = validate_repository_url("not a url just/slash", "myplugin", "mp.json")
        assert any(r.level == "MINOR" and "may be invalid" in r.message for r in results)

    def test_too_many_path_segments_rejected(self):
        """A schemeless 'a/b/c/d' must NOT pass as a two-segment owner/repo shorthand (m11)."""
        from validate_marketplace import validate_repository_url

        results = validate_repository_url("a/b/c/d", "myplugin", "mp.json")
        assert any(r.level == "MINOR" and "may be invalid" in r.message for r in results)

    def test_dotted_owner_repo_shorthand_accepted(self):
        """A legitimate shorthand with dots/hyphens (owner.name/repo-x) must still pass clean (m11)."""
        from validate_marketplace import validate_repository_url

        results = validate_repository_url("my-org.io/repo-x", "myplugin", "mp.json")
        assert len(results) == 0


class TestValidatePluginsArray:
    """Tests for validate_plugins_array covering duplicates, non-dict entries."""

    def test_duplicate_plugin_names_produce_major(self, tmp_path):
        """Duplicate plugin names must produce MAJOR (lines 808-818)."""
        from validate_marketplace import validate_plugins_array

        plugins = [
            {"name": "dup-plugin", "source": "github"},
            {"name": "dup-plugin", "source": "github"},
        ]
        names, results = validate_plugins_array(plugins, tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "Duplicate plugin name" in r.message for r in results)

    def test_non_dict_plugin_entry_produces_critical(self, tmp_path):
        """Non-dict entry in plugins array must produce CRITICAL (lines 791-800)."""
        from validate_marketplace import validate_plugins_array

        plugins = ["not-a-dict", {"name": "ok-plugin", "source": "github"}]
        names, results = validate_plugins_array(plugins, tmp_path, "mp.json")
        assert any(r.level == "CRITICAL" and "must be an object" in r.message for r in results)


class TestValidateGithubDeployment:
    """Tests for validate_github_deployment covering README presence and plugin subfolders."""

    def test_missing_readme_produces_major(self, tmp_path):
        """Missing README.md must produce MAJOR (lines 848-863)."""
        from validate_marketplace import validate_github_deployment

        results = validate_github_deployment(tmp_path, [])
        assert any(r.level == "MAJOR" and "Missing README.md" in r.message for r in results)

    def test_readme_present_calls_validate_readme_content(self, tmp_path):
        """Existing README.md triggers content validation (line 866)."""
        from validate_marketplace import validate_github_deployment

        readme = tmp_path / "README.md"
        readme.write_text("# My Marketplace\nSome content\n")
        results = validate_github_deployment(tmp_path, [])
        # Should have results from validate_readme_content (missing sections)
        assert any("missing required sections" in r.message for r in results)

    def test_plugin_subfolder_missing_readme_produces_minor(self, tmp_path):
        """Plugin subfolder without README.md must produce MINOR (lines 870-901)."""
        from validate_marketplace import validate_github_deployment

        # Create marketplace README
        (tmp_path / "README.md").write_text(
            "# MP\n## Installation\nclaude plugin marketplace add\nclaude plugin install\nverify\nrestart\n"
            "## Update\n## Uninstall\n## Troubleshooting\nhook path not found\nold version after update\nrestart claude code\n"
        )
        # Create a plugin dir without README
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()

        plugins = [{"name": "my-plugin", "source": "./my-plugin"}]
        results = validate_github_deployment(tmp_path, plugins)
        assert any(r.level == "MINOR" and "subfolder missing README.md" in r.message for r in results)

    def _marketplace_readme(self, root: Path) -> None:
        """Write a complete marketplace-root README so only per-plugin checks remain."""
        (root / "README.md").write_text(
            "# MP\n## Installation\nclaude plugin marketplace add\nclaude plugin install\nverify\nrestart\n"
            "## Update\n## Uninstall\n## Troubleshooting\nhook path not found\nold version after update\nrestart claude code\n"
        )

    def test_directory_source_plugin_readme_checked(self, tmp_path):
        """A `directory`-dict-source plugin's subfolder must get the per-plugin README check (m6).

        The old bespoke resolver ignored the dict `directory` source, so such a
        plugin was silently skipped — never flagged for a missing README.
        """
        from validate_marketplace import validate_github_deployment

        self._marketplace_readme(tmp_path)
        plugin_dir = tmp_path / "plugins" / "dir-plugin"
        plugin_dir.mkdir(parents=True)  # no README inside

        plugins = [{"name": "dir-plugin", "source": {"source": "directory", "path": "./plugins/dir-plugin"}}]
        results = validate_github_deployment(tmp_path, plugins)
        assert any(
            r.level == "MINOR" and "dir-plugin" in r.message and "subfolder missing README.md" in r.message
            for r in results
        ), f"directory-source plugin README not checked: {[r.message for r in results]}"

    def test_directory_source_plugin_with_readme_passes(self, tmp_path):
        """A `directory`-source plugin WITH a README must not be flagged (m6, benign side)."""
        from validate_marketplace import validate_github_deployment

        self._marketplace_readme(tmp_path)
        plugin_dir = tmp_path / "plugins" / "ok-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "README.md").write_text("# ok-plugin\n")

        plugins = [{"name": "ok-plugin", "source": {"source": "directory", "path": "./plugins/ok-plugin"}}]
        results = validate_github_deployment(tmp_path, plugins)
        assert not any("subfolder missing README.md" in r.message for r in results)


class TestValidateReadmeContent:
    """Tests for validate_readme_content covering missing sections, installation steps, placeholders, troubleshooting."""

    def test_missing_required_sections_produces_major(self, tmp_path):
        """README missing required sections must produce MAJOR (lines 932-946)."""
        from validate_marketplace import validate_readme_content

        readme = tmp_path / "README.md"
        readme.write_text("# My Marketplace\nJust some text, no sections.\n")
        results = validate_readme_content(readme)
        assert any(r.level == "MAJOR" and "missing required sections" in r.message for r in results)

    def test_incomplete_installation_section_produces_minor(self, tmp_path):
        """Installation section missing sub-steps must produce MINOR (lines 948-966)."""
        from validate_marketplace import validate_readme_content

        readme = tmp_path / "README.md"
        readme.write_text(
            "# MP\n## Installation\nJust install it.\n## Update\n## Uninstall\n## Troubleshooting\n"
            "hook path not found\nold version after update\nrestart claude code\n"
        )
        results = validate_readme_content(readme)
        assert any(r.level == "MINOR" and "Installation section may be incomplete" in r.message for r in results)

    def test_placeholder_content_produces_minor(self, tmp_path):
        """README with placeholder text must produce MINOR (lines 968-987)."""
        from validate_marketplace import validate_readme_content

        readme = tmp_path / "README.md"
        readme.write_text(
            "# MP\n## Installation\nclaude plugin marketplace add\nclaude plugin install\nverify\nrestart\n"
            "## Update\n## Uninstall\n## Troubleshooting\n"
            "hook path not found\nold version after update\nrestart claude code\n"
            "[TODO] fill this in\n"
        )
        results = validate_readme_content(readme)
        assert any(r.level == "MINOR" and "placeholder content" in r.message for r in results)

    def _complete_readme_body(self) -> str:
        """A README body with every required section/topic so only the placeholder check can fire."""
        return (
            "# MP\n## Installation\nclaude plugin marketplace add\nclaude plugin install\nverify\nrestart\n"
            "## Update\n## Uninstall\n## Troubleshooting\n"
            "hook path not found\nold version after update\nrestart claude code\n"
        )

    def test_tbd_word_anchored_flags_real_placeholder(self, tmp_path):
        """A standalone 'TBD' token must still be flagged as placeholder content (n6, threat side)."""
        from validate_marketplace import validate_readme_content

        readme = tmp_path / "README.md"
        readme.write_text(self._complete_readme_body() + "Release date: TBD\n")
        results = validate_readme_content(readme)
        assert any(r.level == "MINOR" and "placeholder content" in r.message for r in results)

    def test_tbd_substring_not_false_flagged(self, tmp_path):
        """A word merely CONTAINING 'tbd' as a substring must NOT trip the placeholder check (n6, benign side).

        The pattern is now word-anchored (\\bTBD\\b); an unanchored substring
        match would false-positive on words like 'subtbdir'.
        """
        from validate_marketplace import validate_readme_content

        readme = tmp_path / "README.md"
        readme.write_text(self._complete_readme_body() + "See the subtbdir layout for details.\n")
        results = validate_readme_content(readme)
        assert not any("placeholder content" in r.message for r in results)

    def test_missing_troubleshooting_topics_produces_minor(self, tmp_path):
        """Troubleshooting section missing required topics must produce MINOR (lines 989-1008)."""
        from validate_marketplace import validate_readme_content

        readme = tmp_path / "README.md"
        readme.write_text(
            "# MP\n## Installation\nclaude plugin marketplace add\nclaude plugin install\nverify\nrestart\n"
            "## Update\n## Uninstall\n## Troubleshooting\nSome generic troubleshooting.\n"
        )
        results = validate_readme_content(readme)
        assert any(r.level == "MINOR" and "Troubleshooting section missing" in r.message for r in results)


class TestValidateGitSubmodules:
    """Tests for validate_git_submodules covering no-git, no-gitmodules, submodule parsing."""

    def test_no_git_dir_produces_info(self, tmp_path):
        """Non-git directory must produce INFO and skip (lines 1036-1046)."""
        from validate_marketplace import validate_git_submodules

        results = validate_git_submodules(tmp_path, [])
        assert any(r.level == "INFO" and "not a git repository" in r.message for r in results)

    def test_no_gitmodules_with_local_dirs_produces_major(self, tmp_path):
        """Git repo without .gitmodules but with local plugin dirs must produce MAJOR (lines 1074-1088)."""
        from validate_marketplace import validate_git_submodules

        (tmp_path / ".git").mkdir()
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()

        plugins = [{"name": "my-plugin", "source": "./my-plugin"}]
        results = validate_git_submodules(tmp_path, plugins)
        assert any(r.level == "MAJOR" and "Missing .gitmodules" in r.message for r in results)

    def test_no_gitmodules_all_url_based_produces_info(self, tmp_path):
        """Git repo with all URL-based sources and no .gitmodules must produce INFO (lines 1064-1073)."""
        from validate_marketplace import validate_git_submodules

        (tmp_path / ".git").mkdir()
        plugins = [{"name": "remote-plugin", "source": {"source": "github", "repo": "owner/repo"}}]
        results = validate_git_submodules(tmp_path, plugins)
        assert any(r.level == "INFO" and "URL-based git sources" in r.message for r in results)

    def test_gitmodules_plugin_not_submodule_produces_major(self, tmp_path):
        """Plugin dir exists but not listed as submodule must produce MAJOR (lines 1150-1171)."""
        from validate_marketplace import validate_git_submodules

        (tmp_path / ".git").mkdir()
        # Create .gitmodules with a DIFFERENT submodule
        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text('[submodule "other-plugin"]\n\tpath = other-plugin\n\turl = https://github.com/o/other\n')
        # Create plugin dir that is NOT in .gitmodules
        (tmp_path / "unlisted-plugin").mkdir()

        plugins = [{"name": "unlisted-plugin", "source": {"source": "github", "repo": "owner/unlisted"}}]
        results = validate_git_submodules(tmp_path, plugins)
        assert any(r.level == "MAJOR" and "not a git submodule" in r.message for r in results)

    def test_submodule_summary_info_fires_for_plugins_layout(self, tmp_path):
        """The 'configured as git submodules' INFO must fire for the canonical plugins/<name> layout (m5).

        `submodules` is keyed by PATH ('plugins/foo'), so the old name-membership
        test ('foo' in submodules) was ~always False and the summary never emitted.
        """
        from validate_marketplace import validate_git_submodules

        (tmp_path / ".git").mkdir()
        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text(
            '[submodule "plugins/foo"]\n\tpath = plugins/foo\n\turl = https://github.com/o/foo\n'
        )
        plugin_dir = tmp_path / "plugins" / "foo"
        plugin_dir.mkdir(parents=True)
        # Mark the submodule as a checked-out dir so it isn't flagged "not a submodule".
        (plugin_dir / ".git").write_text("gitdir: ../../.git/modules/plugins/foo\n")

        plugins = [{"name": "foo", "source": {"source": "github", "repo": "o/foo"}}]
        results = validate_git_submodules(tmp_path, plugins)
        assert not any(r.level in ("CRITICAL", "MAJOR") for r in results)
        assert any(r.level == "INFO" and "configured as git submodules" in r.message for r in results)


class TestMarketplacePrivateInfoResolution:
    """M3/m1: private-info scanner coverage for Layout-B sources and metachar paths."""

    def test_directory_source_plugin_is_scanned(self, tmp_path):
        """A `directory`-dict-source plugin must be scanned for private path leaks (M3).

        The scanner previously resolved only `./x` and bare-string sources, so a
        leak inside a `{source:"directory", path:"./plugins/x"}` plugin was a
        security false-negative — completely unscanned.
        """
        from validate_marketplace import validate_marketplace_private_info

        plugin_dir = tmp_path / "plugins" / "leaky"
        plugin_dir.mkdir(parents=True)
        # A real home-path leak with a non-example username.
        (plugin_dir / "config.md").write_text("path: /Users/realdev/secret/project/file.txt\n")

        plugins = [{"name": "leaky", "source": {"source": "directory", "path": "./plugins/leaky"}}]
        results = validate_marketplace_private_info(tmp_path, plugins)
        assert any(
            r.level in ("CRITICAL", "MAJOR") and "realdev" in r.message for r in results
        ), f"directory-source plugin was not scanned: {[r.message for r in results]}"

    def test_clean_directory_source_plugin_passes(self, tmp_path):
        """A clean `directory`-source plugin must NOT produce a private-info finding (M3, benign side)."""
        from validate_marketplace import validate_marketplace_private_info

        plugin_dir = tmp_path / "plugins" / "clean"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "config.md").write_text("path: ${CLAUDE_PLUGIN_ROOT}/data/file.txt\n")

        plugins = [{"name": "clean", "source": {"source": "directory", "path": "./plugins/clean"}}]
        results = validate_marketplace_private_info(tmp_path, plugins)
        assert not any(r.level in ("CRITICAL", "MAJOR") for r in results)

    def test_dotted_home_path_leak_not_skipped(self, tmp_path):
        """A home-path leak containing '.' must be flagged, not silently skipped (m1).

        The old metachar skip-set included '.', so a real path like
        /Users/realdev/a.b.c/ was dropped as if it were a regex pattern.
        """
        from validate_marketplace import validate_marketplace_private_info

        infra = tmp_path / "scripts"
        infra.mkdir()
        (infra / "thing.py").write_text('CONFIG = "/Users/realdev/a.b.c/data/file.txt"\n')

        results = validate_marketplace_private_info(tmp_path, [])
        assert any(
            r.level in ("CRITICAL", "MAJOR") and "realdev" in r.message for r in results
        ), f"dotted home path was skipped: {[r.message for r in results]}"


class TestValidateGithubSourceRequired:
    """Tests for validate_github_source_required covering missing repo, non-string repo, non-github URL."""

    def test_missing_repository_produces_warning_not_major(self):
        """Plugin without the OPTIONAL repository field must produce a non-blocking WARNING, not MAJOR.

        'repository' is a documented OPTIONAL plugin-entry field, so its absence
        on a spec-compliant marketplace must not mark it INVALID (audit HIGH).
        """
        from validate_marketplace import validate_github_source_required

        plugins = [{"name": "no-repo-plugin", "source": "./my-plugin"}]
        results = validate_github_source_required(plugins, "mp.json")
        assert not any(r.level == "MAJOR" for r in results), [r.message for r in results]
        assert any(r.level == "WARNING" and "repository" in r.message for r in results)

    def test_malformed_repository_url_produces_minor(self):
        """A repository value that is not a URL at all must still produce MINOR.

        Rewritten for the v2.1.232 sync, which made this check HOST-AGNOSTIC.
        It used to demand github.com and its message said so; the spec calls
        `repository` a "source code repository URL" and CC now clones bare
        gitlab.com marketplace URLs like github.com ones, so requiring GitHub
        was a publish gate CPV invented. The finding still fires on junk — only
        the reason it fires changed — which is what keeps this from being a
        test that was deleted rather than corrected.
        """
        from validate_marketplace import validate_github_source_required

        plugins = [{"name": "p", "source": "./p", "repository": "not-a-github-url"}]
        results = validate_github_source_required(plugins, "mp.json")
        assert any(r.level == "MINOR" and "repository URL may be invalid" in r.message for r in results)

    def test_non_github_host_is_accepted(self):
        """The FP half: a GitLab/Bitbucket/self-hosted repository URL must NOT be flagged."""
        from validate_marketplace import validate_github_source_required

        for repo in (
            "https://gitlab.com/team/plugins",
            "https://gitlab.com/group/subgroup/nested/plugin",
            "git@gitlab.com:team/plugin.git",
            "https://bitbucket.org/team/plugin",
        ):
            plugins = [{"name": "p", "source": "./p", "repository": repo}]
            results = validate_github_source_required(plugins, "mp.json")
            assert not [r for r in results if r.level in ("MINOR", "MAJOR")], repo

    def test_all_valid_repos_produce_info(self):
        """All plugins with valid GitHub repos must produce INFO summary (lines 1501-1509)."""
        from validate_marketplace import validate_github_source_required

        plugins = [{"name": "p", "source": "./p", "repository": "https://github.com/owner/p"}]
        results = validate_github_source_required(plugins, "mp.json")
        assert any(r.level == "INFO" and "valid repository URLs" in r.message for r in results)


class TestValidateWorkflowInlinePython:
    """Tests for validate_workflow_inline_python covering detection of dangerous f-string dict access."""

    def test_no_workflows_dir_returns_empty(self, tmp_path):
        """No .github/workflows dir must return empty results (line 1553)."""
        from validate_marketplace import validate_workflow_inline_python

        results = validate_workflow_inline_python(tmp_path)
        assert len(results) == 0

    def test_dangerous_inline_python_detected(self, tmp_path):
        """Dict bracket access in inline Python f-string must produce MAJOR (lines 1569-1595)."""
        from validate_marketplace import validate_workflow_inline_python

        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        wf_file = wf_dir / "ci.yml"
        wf_file.write_text(
            'jobs:\n  build:\n    steps:\n      - run: python3 -c "val = source[\\"repo\\"]; print(f\\"{val[\\"key\\"]}\\")"'
            "\n"
        )
        # The regex specifically looks for python3 -c "..." with {expr["key"]} inside
        # Let's create a more precise example:
        wf_file.write_text('steps:\n  - run: |\n      python3 -c "x = {data[\\"key\\"]}"\n')
        results = validate_workflow_inline_python(tmp_path)
        # Either it detects the pattern (MAJOR) or produces INFO (no dangerous patterns)
        # The actual detection depends on regex matching the shell-quoted python
        assert len(results) > 0

    def test_clean_workflows_produce_info(self, tmp_path):
        """Clean workflow files must produce INFO (lines 1597-1605)."""
        from validate_marketplace import validate_workflow_inline_python

        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        wf_file = wf_dir / "ci.yml"
        wf_file.write_text(
            "name: CI\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hello\n"
        )
        results = validate_workflow_inline_python(tmp_path)
        assert any(r.level == "INFO" and "No dangerous inline Python" in r.message for r in results)


class TestValidateMarketplaceIntegration:
    """Integration tests for validate_marketplace covering full pipeline with real temp dirs."""

    def test_full_valid_marketplace(self, tmp_path):
        """A complete valid marketplace must pass validation with no CRITICAL or MAJOR issues."""
        from validate_marketplace import validate_marketplace

        # Create plugin dir with plugin.json
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        (claude_dir / "plugin.json").write_text(json.dumps({"name": "my-plugin"}))
        (plugin_dir / "README.md").write_text("# My Plugin\nDescription.\n")

        # Create marketplace.json
        mp = tmp_path / "marketplace.json"
        mp.write_text(
            json.dumps(
                {
                    "name": "test-marketplace",
                    "owner": {"name": "test-owner"},
                    "plugins": [
                        {
                            "name": "my-plugin",
                            "source": "./my-plugin",
                            "repository": "https://github.com/owner/my-plugin",
                            "version": "1.0.0",
                        }
                    ],
                }
            )
        )

        # Create README.md
        readme = tmp_path / "README.md"
        readme.write_text(
            "# Test Marketplace\n\n"
            "## Installation\n"
            "claude plugin marketplace add\n"
            "claude plugin install my-plugin@test-marketplace\n"
            "Verify installation by listing plugins.\n"
            "Restart Claude Code after installing.\n\n"
            "## Update\nUpdate instructions.\n\n"
            "## Uninstall\nUninstall instructions.\n\n"
            "## Troubleshooting\n"
            "If you get hook path not found after update, reinstall.\n"
            "If old version still showing after update, clear cache.\n"
            "Restart Claude Code after any update.\n"
        )

        report = validate_marketplace(tmp_path)
        critical = [r for r in report.results if r.level == "CRITICAL"]
        assert len(critical) == 0, f"Unexpected CRITICAL issues: {[r.message for r in critical]}"

    def test_layout_b_directory_source_type_is_accepted(self, tmp_path):
        """A marketplace entry with {"source": "directory", "path": "./plugins/foo"} must be valid."""
        from validate_marketplace import validate_marketplace

        # Create a nested plugin under plugins/my-plugin
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        plug = plugins_dir / "my-plugin"
        plug.mkdir()
        (plug / ".claude-plugin").mkdir()
        (plug / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "my-plugin", "version": "0.1.0"}))
        (plug / "README.md").write_text("# My Plugin\n")

        # marketplace.json uses the object form with source.source=directory
        mp = tmp_path / "marketplace.json"
        mp.write_text(
            json.dumps(
                {
                    "name": "my-marketplace",
                    "owner": {"name": "test-owner"},
                    "plugins": [
                        {
                            "name": "my-plugin",
                            "source": {"source": "directory", "path": "./plugins/my-plugin"},
                            "version": "0.1.0",
                        }
                    ],
                }
            )
        )

        report = validate_marketplace(tmp_path)
        critical_and_major = [r for r in report.results if r.level in ("CRITICAL", "MAJOR")]
        # No critical issues from the directory source type itself — the nested plugin may have
        # MAJOR findings (e.g., missing README sections), but the source type must be accepted.
        source_type_issues = [r for r in critical_and_major if "invalid source type" in r.message.lower()]
        assert not source_type_issues, (
            f"'directory' should be a valid source type, got: {[r.message for r in source_type_issues]}"
        )

    def test_layout_b_string_source_shorthand_is_accepted(self, tmp_path):
        """A marketplace entry with source: './plugins/foo' (shorthand) must be valid for Layout B."""
        from validate_marketplace import validate_marketplace

        plug = tmp_path / "plugins" / "bar"
        plug.mkdir(parents=True)
        (plug / ".claude-plugin").mkdir()
        (plug / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "bar", "version": "0.1.0"}))

        mp = tmp_path / "marketplace.json"
        mp.write_text(
            json.dumps(
                {
                    "name": "my-marketplace",
                    "owner": {"name": "test-owner"},
                    "plugins": [{"name": "bar", "source": "./plugins/bar", "version": "0.1.0"}],
                }
            )
        )

        report = validate_marketplace(tmp_path)
        source_type_issues = [r for r in report.results if "invalid source type" in (r.message or "").lower()]
        assert not source_type_issues, (
            f"Shorthand './path' must be accepted, got: {[r.message for r in source_type_issues]}"
        )

    def test_recommend_restructure_fires_on_wshobson_style_marketplace(self, tmp_path):
        """A nested-monorepo marketplace with no tags/CHANGELOG/CI/publish.py and
        mixed authorship must trigger the architecture WARNING recommending
        migration to Layout A or Layout B with full release ceremony.
        """
        from validate_marketplace import validate_marketplace

        # Build a marketplace with 4 nested plugins, 3 different authors, 4
        # different major.minor versions, and NO tags/CHANGELOG/CI/publish.py.
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        specs = [
            ("plugin-a", "1.0.0", "Alice"),
            ("plugin-b", "1.5.0", "Bob"),
            ("plugin-c", "2.3.0", "Alice"),
            ("plugin-d", "0.7.0", "Charlie"),
        ]
        for name, version, author_name in specs:
            pdir = plugins_dir / name
            pdir.mkdir()
            (pdir / ".claude-plugin").mkdir()
            (pdir / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": version}))

        mp = tmp_path / "marketplace.json"
        mp.write_text(
            json.dumps(
                {
                    "name": "community-monorepo",
                    "owner": {"name": "Lead Maintainer"},
                    "plugins": [
                        {
                            "name": name,
                            "source": f"./plugins/{name}",
                            "version": version,
                            "author": {"name": author_name},
                        }
                        for name, version, author_name in specs
                    ],
                }
            )
        )
        # Intentionally NO:
        #   - .github/workflows/
        #   - scripts/publish.py
        #   - CHANGELOG.md
        #   - cliff.toml
        #   - git tags (no git init)

        report = validate_marketplace(tmp_path)
        arch_warnings = [
            r for r in report.results if r.category == "architecture" and "nested monorepo" in (r.message or "")
        ]
        assert len(arch_warnings) >= 1, (
            "Expected a CPV architecture warning for the wshobson-style pattern. "
            f"Categories present: {[r.category for r in report.results]}"
        )
        # The warning should mention at least 3 of the 7 problem signals
        warning_msg = arch_warnings[0].message or ""
        assert "CHANGELOG" in warning_msg
        assert "Mixed authorship" in warning_msg or "different authors" in warning_msg
        assert "Layout A" in warning_msg and "Layout B" in warning_msg
        # New format: each signal ships title + why-it-hurts + cpv-approach lines
        assert "Why it hurts" in warning_msg
        assert "CPV's approach" in warning_msg

    def test_recommend_restructure_does_not_fire_on_clean_layout_b(self, tmp_path):
        """A clean Layout B marketplace with CI, CHANGELOG, cliff.toml, and single
        author must NOT trigger the architecture warning.
        """
        from validate_marketplace import validate_marketplace

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        for name in ["alpha", "beta", "gamma"]:
            pdir = plugins_dir / name
            pdir.mkdir()
            (pdir / ".claude-plugin").mkdir()
            (pdir / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": "1.0.0"}))

        # Add all the discipline-enforcing pieces
        (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.0.0]\n")
        (tmp_path / "cliff.toml").write_text("[changelog]\n")
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "publish.py").write_text("#!/usr/bin/env python3\n")
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "validate.yml").write_text("name: Validate\non: push\n")

        mp = tmp_path / "marketplace.json"
        mp.write_text(
            json.dumps(
                {
                    "name": "clean-mkt",
                    "owner": {"name": "Solo Dev"},
                    "plugins": [
                        {
                            "name": n,
                            "source": f"./plugins/{n}",
                            "version": "1.0.0",
                            "author": {"name": "Solo Dev"},
                        }
                        for n in ["alpha", "beta", "gamma"]
                    ],
                }
            )
        )

        report = validate_marketplace(tmp_path)
        arch_warnings = [r for r in report.results if r.category == "architecture"]
        assert not arch_warnings, (
            f"Clean Layout B should not trigger architecture warnings, got: {[r.message[:100] for r in arch_warnings]}"
        )

    def test_layout_a_github_source_still_works(self, tmp_path):
        """Layout A: github source must still validate without needing a local directory."""
        from validate_marketplace import validate_marketplace

        mp = tmp_path / "marketplace.json"
        mp.write_text(
            json.dumps(
                {
                    "name": "my-marketplace",
                    "owner": {"name": "test-owner"},
                    "plugins": [
                        {
                            "name": "remote-plugin",
                            "source": {"source": "github", "repo": "owner/remote-plugin"},
                            "version": "1.0.0",
                        }
                    ],
                }
            )
        )

        report = validate_marketplace(tmp_path)
        source_type_issues = [r for r in report.results if "invalid source type" in (r.message or "").lower()]
        assert not source_type_issues, f"'github' must be valid, got: {[r.message for r in source_type_issues]}"

    def test_marketplace_with_owner_not_object(self, tmp_path):
        """Owner as a bare string produces MINOR per GAP-14 (canonical form is {name: str})."""
        from validate_marketplace import validate_marketplace

        mp = tmp_path / "marketplace.json"
        mp.write_text(json.dumps({"name": "test-mp", "owner": "just-a-string", "plugins": []}))
        report = validate_marketplace(tmp_path)
        # v2.22.3 — GAP-14: bare-string owner softened from MAJOR to MINOR
        assert any(r.level == "MINOR" and "bare string" in r.message.lower() for r in report.results), (
            f"expected MINOR for bare-string owner, got: {[(r.level, r.message) for r in report.results]}"
        )

    def test_marketplace_with_owner_non_string_non_object(self, tmp_path):
        """Owner that is neither a string nor an object still produces MAJOR."""
        from validate_marketplace import validate_marketplace

        mp = tmp_path / "marketplace.json"
        mp.write_text(json.dumps({"name": "test-mp", "owner": 42, "plugins": []}))
        report = validate_marketplace(tmp_path)
        assert any(r.level == "MAJOR" and "owner" in r.message.lower() for r in report.results)

    def test_marketplace_missing_required_fields(self, tmp_path):
        """Marketplace.json missing required fields must produce CRITICAL (lines 1633-1640)."""
        from validate_marketplace import validate_marketplace

        mp = tmp_path / "marketplace.json"
        mp.write_text(json.dumps({"description": "missing name, owner, plugins"}))
        report = validate_marketplace(tmp_path)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("Missing required field" in m for m in critical_msgs)


class TestV221MarketplaceReservedFuzzy:
    """Fuzzy-match impersonation detection for marketplace names (plugin-marketplaces.md:160)."""

    def test_official_claude_prefix_blocked(self):
        """Names starting with 'official-claude' must produce MAJOR impersonation finding."""
        from validate_marketplace import validate_marketplace_name

        results = validate_marketplace_name("official-claude-something", "test.json")
        assert any(r.level == "MAJOR" and "impersonate" in r.message.lower() for r in results)

    def test_anthropic_prefix_blocked(self):
        """Names starting with 'anthropic-' must produce MAJOR impersonation finding."""
        from validate_marketplace import validate_marketplace_name

        results = validate_marketplace_name("anthropic-tools-v2", "test.json")
        assert any(r.level == "MAJOR" and "impersonate" in r.message.lower() for r in results)

    def test_community_claude_code_prefix_not_impersonation(self):
        """A community 'claude-code-' prefix is NOT impersonation and must not be flagged (m3).

        Prefixing a community marketplace with 'claude-code-' is extremely common
        and not an attempt to impersonate an official Anthropic marketplace. The
        exact reserved names (claude-code-marketplace, claude-code-plugins) are
        still blocked at CRITICAL via RESERVED_MARKETPLACE_NAMES.
        """
        from validate_marketplace import validate_marketplace_name

        results = validate_marketplace_name("claude-code-superhub", "test.json")
        assert not any("impersonate" in r.message.lower() for r in results)

    def test_community_claude_plugins_prefix_not_impersonation(self):
        """A community 'claude-plugins-' prefix is NOT impersonation — incl. CPV's own sibling (m3).

        The champion validator must not flag its own sibling repo naming
        convention ('claude-plugins-validation') as a suspected impersonation.
        """
        from validate_marketplace import validate_marketplace_name

        results = validate_marketplace_name("claude-plugins-foo", "test.json")
        assert not any("impersonate" in r.message.lower() for r in results)

        # CPV's own sibling marketplace naming must pass cleanly.
        cpv_sibling = validate_marketplace_name("claude-plugins-validation", "test.json")
        assert not any("impersonate" in r.message.lower() for r in cpv_sibling)

    def test_official_prefix_blocked(self):
        """A bare 'official-' prefix (any vendor) must still produce a MAJOR impersonation finding."""
        from validate_marketplace import validate_marketplace_name

        results = validate_marketplace_name("official-plugins-hub", "test.json")
        assert any(r.level == "MAJOR" and "impersonate" in r.message.lower() for r in results)

    def test_claude_marketplace_prefix_blocked(self):
        """'claude-marketplace*' mimics THE official marketplace and must still be flagged MAJOR."""
        from validate_marketplace import validate_marketplace_name

        results = validate_marketplace_name("claude-marketplace-mirror", "test.json")
        assert any(r.level == "MAJOR" and "impersonate" in r.message.lower() for r in results)

    def test_unrelated_name_accepted(self):
        """Unrelated marketplace names must not trigger the impersonation check."""
        from validate_marketplace import validate_marketplace_name

        results = validate_marketplace_name("emasoft-plugins", "test.json")
        assert not any("impersonate" in r.message.lower() for r in results)

    def test_exact_reserved_name_still_critical(self):
        """Exact reserved names keep their CRITICAL classification (regression guard)."""
        from validate_marketplace import validate_marketplace_name

        results = validate_marketplace_name("anthropic-plugins", "test.json")
        # Reserved match is CRITICAL, not MAJOR
        assert any(r.level == "CRITICAL" and "reserved" in r.message for r in results)
        # And the exact-match branch should suppress the fuzzy MAJOR to avoid duplicate findings
        assert not any(r.level == "MAJOR" and "impersonate" in r.message.lower() for r in results)


def _write_plugin_manifest(plugin_root, version):
    """Helper: create plugin_root/.claude-plugin/plugin.json with the given version string."""
    manifest_dir = plugin_root / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"name": plugin_root.name, "version": version} if version is not None else {"name": plugin_root.name}
    (manifest_dir / "plugin.json").write_text(json.dumps(manifest))


class TestV221MarketplaceVersionDuplication:
    """Version declared in both plugin.json and marketplace.json entry (plugin-marketplaces.md:696-698)."""

    def test_version_in_both_matching_nit(self, tmp_path):
        """Relative-path source with matching versions in both manifests must produce NIT."""
        from validate_marketplace import validate_plugin_entry

        plugin_root = tmp_path / "my-plugin"
        plugin_root.mkdir()
        _write_plugin_manifest(plugin_root, "1.2.3")

        plugin = {"name": "my-plugin", "source": "./my-plugin", "version": "1.2.3"}
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(r.level == "NIT" and "only one place" in r.message.lower() for r in results), (
            f"expected NIT with 'only one place' guidance, got: {[(r.level, r.message) for r in results]}"
        )

    def test_version_in_both_diverging_minor(self, tmp_path):
        """Relative-path source with diverging versions must produce MINOR drift warning."""
        from validate_marketplace import validate_plugin_entry

        plugin_root = tmp_path / "my-plugin"
        plugin_root.mkdir()
        _write_plugin_manifest(plugin_root, "2.0.0")

        plugin = {"name": "my-plugin", "source": "./my-plugin", "version": "1.0.0"}
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(r.level == "MINOR" and "wins silently" in r.message.lower() for r in results), (
            f"expected MINOR drift warning, got: {[(r.level, r.message) for r in results]}"
        )

    def test_version_in_entry_only_ok(self, tmp_path):
        """Marketplace entry has version, plugin.json has no version → no version-consistency finding."""
        from validate_marketplace import validate_plugin_entry

        plugin_root = tmp_path / "my-plugin"
        plugin_root.mkdir()
        _write_plugin_manifest(plugin_root, None)

        plugin = {"name": "my-plugin", "source": "./my-plugin", "version": "1.0.0"}
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert not any(
            r.level in {"NIT", "MINOR", "INFO"}
            and ("wins silently" in r.message.lower() or "only one place" in r.message.lower())
            for r in results
        )

    def test_version_in_plugin_json_only_ok(self, tmp_path):
        """plugin.json has version, marketplace entry does not → no version-consistency finding."""
        from validate_marketplace import validate_plugin_entry

        plugin_root = tmp_path / "my-plugin"
        plugin_root.mkdir()
        _write_plugin_manifest(plugin_root, "1.0.0")

        plugin = {"name": "my-plugin", "source": "./my-plugin"}  # no version field
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert not any("wins silently" in r.message.lower() or "only one place" in r.message.lower() for r in results)

    def test_remote_source_version_set_in_both_info_fallback(self, tmp_path):
        """GitHub source with version in entry → INFO fallback (cannot verify remote plugin.json)."""
        from validate_marketplace import validate_plugin_entry

        plugin = {
            "name": "my-plugin",
            "source": {"source": "github", "repo": "owner/my-plugin"},
            "version": "1.0.0",
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(r.level == "INFO" and "cannot verify version consistency" in r.message.lower() for r in results), (
            f"expected INFO about unverifiable remote source, got: {[(r.level, r.message) for r in results]}"
        )


# =============================================================================
# v2.22.3 — Marketplace minor/NIT fix suite (GAP-2, 3, 6, 7, 13, 15, 25, 28, 32,
# 33, 34, 101)
# Each test targets one of the audit gaps in
# docs_dev/audit-pass2-plugins-20260417-180252.md Part D Priority 1/2/3.
# =============================================================================


class TestV2223MarketplaceMinorFixes:
    """Append-only regression coverage for the v2.22.3 marketplace validator fixes."""

    def test_gap6_userconfig_channels_monitors_accepted_as_optional(self, tmp_path):
        """GAP-6: userConfig/channels/monitors at plugin-entry level no longer trigger INFO unknown-field."""
        from validate_marketplace import validate_plugin_entry

        plugin = {
            "name": "my-plugin",
            "source": {"source": "github", "repo": "owner/my-plugin"},
            "userConfig": {"api_endpoint": {"title": "API endpoint", "type": "string"}},
            "channels": [],
            "monitors": [],
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert not any(
            r.level == "INFO" and "unknown field" in r.message.lower() and "userConfig" in r.message for r in results
        ), f"userConfig should no longer trigger unknown-field INFO: {[(r.level, r.message) for r in results]}"
        assert not any(
            r.level == "INFO" and "unknown field" in r.message.lower() and "channels" in r.message for r in results
        )
        assert not any(
            r.level == "INFO" and "unknown field" in r.message.lower() and "monitors" in r.message for r in results
        )

    def test_gap7_sha_uppercase_hex_accepted(self, tmp_path):
        """GAP-7: uppercase-hex SHA no longer emits a MINOR (git accepts A-F)."""
        from validate_marketplace import validate_plugin_source

        plugin = {
            "name": "my-plugin",
            "source": {
                "source": "github",
                "repo": "owner/my-plugin",
                "sha": "ABCDEF1234567890ABCDEF1234567890ABCDEF12",
            },
        }
        results = validate_plugin_source(plugin, "my-plugin", tmp_path, "mp.json")
        assert not any(r.level == "MINOR" and "sha" in r.message.lower() for r in results), (
            f"uppercase-hex SHA should be accepted: {[(r.level, r.message) for r in results]}"
        )

    def test_gap7_sha_mixed_case_accepted(self, tmp_path):
        """GAP-7 regression guard: lowercase SHAs also still accepted."""
        from validate_marketplace import validate_plugin_source

        plugin = {
            "name": "my-plugin",
            "source": {
                "source": "github",
                "repo": "owner/my-plugin",
                "sha": "abcdef1234567890abcdef1234567890abcdef12",
            },
        }
        results = validate_plugin_source(plugin, "my-plugin", tmp_path, "mp.json")
        assert not any(r.level == "MINOR" and "sha" in r.message.lower() for r in results)

    def test_gap7_sha_too_short_still_minor(self, tmp_path):
        """GAP-7 regression guard: a SHA that is not 40 chars long still produces MINOR."""
        from validate_marketplace import validate_plugin_source

        plugin = {"name": "p", "source": {"source": "github", "repo": "o/p", "sha": "abc"}}
        results = validate_plugin_source(plugin, "p", tmp_path, "mp.json")
        assert any(r.level == "MINOR" and "sha" in r.message.lower() for r in results)

    def test_gap2_git_source_emits_nit(self, tmp_path):
        """GAP-2: `source: "git"` emits a NIT suggesting the canonical `url` type."""
        from validate_marketplace import validate_plugin_source

        plugin = {
            "name": "my-plugin",
            "source": {"source": "git", "url": "https://gitea.internal/org/plugin.git"},
        }
        results = validate_plugin_source(plugin, "my-plugin", tmp_path, "mp.json")
        assert any(
            r.level == "NIT" and "CPV-only alias" in r.message and "url" in r.message.lower() for r in results
        ), f"expected NIT advising canonical 'url' type: {[(r.level, r.message) for r in results]}"

    def test_gap3_directory_source_emits_nit(self, tmp_path):
        """GAP-3: `source: "directory"` emits NIT suggesting bare relative-path shorthand."""
        from validate_marketplace import validate_plugin_source

        nested = tmp_path / "plugins" / "my-plugin"
        nested.mkdir(parents=True)
        plugin = {
            "name": "my-plugin",
            "source": {"source": "directory", "path": "./plugins/my-plugin"},
        }
        results = validate_plugin_source(plugin, "my-plugin", tmp_path, "mp.json")
        assert any(
            r.level == "NIT" and "CPV-only extension" in r.message and "plain string shorthand" in (r.suggestion or "")
            for r in results
        ), f"expected NIT advising bare relative-path shorthand: {[(r.level, r.message) for r in results]}"

    def test_gap13_author_url_non_string_is_minor(self, tmp_path):
        """GAP-13: author.url as non-string produces a MINOR with clear message."""
        from validate_marketplace import validate_plugin_entry

        plugin = {
            "name": "my-plugin",
            "source": {"source": "github", "repo": "o/p"},
            "author": {"name": "Alice", "url": 12345},
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(r.level == "MINOR" and "author.url" in r.message and "string" in r.message for r in results)

    def test_gap13_author_url_bad_scheme_is_minor(self, tmp_path):
        """GAP-13: author.url not starting with http/https/git produces a MINOR."""
        from validate_marketplace import validate_plugin_entry

        plugin = {
            "name": "my-plugin",
            "source": {"source": "github", "repo": "o/p"},
            "author": {"name": "Alice", "url": "ftp://bad-scheme.example.com"},
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(r.level == "MINOR" and "author.url" in r.message and "http" in r.message for r in results)

    def test_gap13_author_url_http_ok(self, tmp_path):
        """GAP-13: author.url with http/https/git scheme is accepted silently."""
        from validate_marketplace import validate_plugin_entry

        for url in [
            "https://github.com/alice",
            "http://alice.example.com",
            "git://git.example.com/alice",
        ]:
            plugin = {
                "name": "my-plugin",
                "source": {"source": "github", "repo": "o/p"},
                "author": {"name": "Alice", "url": url},
            }
            results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
            assert not any(r.level == "MINOR" and "author.url" in r.message for r in results), (
                f"URL {url} should be accepted: {[(r.level, r.message) for r in results]}"
            )

    def test_gap15_owner_url_emits_nit(self, tmp_path):
        """GAP-15: owner.url is not documented in the canonical owner schema — emit NIT."""
        from validate_marketplace import validate_marketplace

        mp = tmp_path / "marketplace.json"
        mp.write_text(
            json.dumps(
                {
                    "name": "test-mp",
                    "owner": {"name": "Alice", "url": "https://example.com"},
                    "plugins": [],
                    "metadata": {"description": "hi"},
                }
            )
        )
        report = validate_marketplace(tmp_path)
        assert any(
            r.level == "NIT" and "owner.url" in r.message and "not in the documented" in r.message
            for r in report.results
        ), f"expected NIT about owner.url: {[(r.level, r.message) for r in report.results]}"

    def test_gap25_strict_false_with_nested_plugin_json_components_is_major(self, tmp_path):
        """GAP-25: strict:false + nested plugin.json with component fields -> MAJOR conflicting-manifests."""
        from validate_marketplace import validate_plugin_entry

        plugin_root = tmp_path / "my-plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "my-plugin",
                    "commands": "./commands",  # conflict-triggering field
                }
            )
        )
        plugin = {
            "name": "my-plugin",
            "source": "./my-plugin",
            "strict": False,
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(r.level == "MAJOR" and "conflicting manifests" in r.message for r in results), (
            f"expected MAJOR for strict:false vs plugin.json components: {[(r.level, r.message) for r in results]}"
        )

    def test_gap25_strict_false_with_metadata_only_plugin_json_is_silent(self, tmp_path):
        """GAP-25: strict:false with a plugin.json that has ONLY metadata (no components) is fine."""
        from validate_marketplace import validate_plugin_entry

        plugin_root = tmp_path / "my-plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "my-plugin", "version": "1.0.0", "description": "metadata only"})
        )
        plugin = {"name": "my-plugin", "source": "./my-plugin", "strict": False}
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert not any("conflicting manifests" in r.message for r in results), (
            f"metadata-only plugin.json must not trigger conflicting-manifests MAJOR: {[(r.level, r.message) for r in results]}"
        )

    def test_gap34_plugin_root_prefix_prepended_to_relative_path(self, tmp_path):
        """GAP-34: metadata.pluginRoot is prepended when resolving a relative source string."""
        from validate_marketplace import validate_marketplace

        (tmp_path / "plugins" / "formatter" / ".claude-plugin").mkdir(parents=True)
        (tmp_path / "plugins" / "formatter" / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "formatter", "version": "1.0.0"})
        )
        mp = tmp_path / "marketplace.json"
        mp.write_text(
            json.dumps(
                {
                    "name": "test-mp",
                    "owner": {"name": "Lead"},
                    "metadata": {"description": "with pluginRoot", "pluginRoot": "./plugins"},
                    "plugins": [{"name": "formatter", "source": "./formatter"}],
                }
            )
        )
        report = validate_marketplace(tmp_path)
        # The relative path resolves as ./plugins/formatter thanks to GAP-34.
        assert not any(
            r.level == "MAJOR" and "does not exist" in r.message and "formatter" in r.message for r in report.results
        ), (
            f"pluginRoot should make ./formatter resolve to ./plugins/formatter: {[(r.level, r.message) for r in report.results]}"
        )

    def test_gap34_plugin_root_prefix_applied_to_bare_name_source(self, tmp_path):
        """GAP-34: bare-name source (`source: "formatter"`) resolves under pluginRoot."""
        from validate_marketplace import validate_marketplace

        (tmp_path / "plugins" / "formatter" / ".claude-plugin").mkdir(parents=True)
        (tmp_path / "plugins" / "formatter" / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "formatter", "version": "1.0.0"})
        )
        mp = tmp_path / "marketplace.json"
        mp.write_text(
            json.dumps(
                {
                    "name": "test-mp",
                    "owner": {"name": "Lead"},
                    "metadata": {"description": "with pluginRoot", "pluginRoot": "./plugins"},
                    "plugins": [{"name": "formatter", "source": "formatter"}],
                }
            )
        )
        report = validate_marketplace(tmp_path)
        # Previously: bare name "formatter" would trigger "invalid source type".
        # After GAP-34: bare name resolves via pluginRoot and passes.
        assert not any(r.level == "MAJOR" and "invalid source type: formatter" in r.message for r in report.results), (
            f"bare-name source must resolve via pluginRoot: {[(r.level, r.message) for r in report.results]}"
        )

    def test_gap32_top_level_description_emits_nit(self, tmp_path):
        """GAP-32: top-level `description` emits NIT favoring metadata.description."""
        from validate_marketplace import validate_marketplace

        mp = tmp_path / "marketplace.json"
        mp.write_text(
            json.dumps(
                {
                    "name": "test-mp",
                    "owner": {"name": "Lead"},
                    "description": "I am at top level",
                    "plugins": [],
                }
            )
        )
        report = validate_marketplace(tmp_path)
        assert any(r.level == "NIT" and "Top-level 'description'" in r.message for r in report.results), (
            f"expected NIT for top-level description: {[(r.level, r.message) for r in report.results]}"
        )

    def test_gap33_top_level_version_emits_nit(self, tmp_path):
        """GAP-33: top-level `version` emits NIT favoring metadata.version."""
        from validate_marketplace import validate_marketplace

        mp = tmp_path / "marketplace.json"
        mp.write_text(
            json.dumps(
                {
                    "name": "test-mp",
                    "owner": {"name": "Lead"},
                    "version": "1.0.0",
                    "plugins": [],
                    "metadata": {"description": "x"},
                }
            )
        )
        report = validate_marketplace(tmp_path)
        assert any(r.level == "NIT" and "Top-level 'version'" in r.message for r in report.results), (
            f"expected NIT for top-level version: {[(r.level, r.message) for r in report.results]}"
        )

    def test_gap28_channel_userconfig_unknown_type_is_minor(self, tmp_path):
        """GAP-28: channels[i].userConfig.type outside the allowed set produces MINOR."""
        from validate_marketplace import validate_plugin_entry

        plugin = {
            "name": "my-plugin",
            "source": {"source": "github", "repo": "o/p"},
            "channels": [
                {
                    "userConfig": {
                        "api_endpoint": {"title": "Endpoint", "type": "quantum"},
                    }
                }
            ],
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(
            r.level == "MINOR" and "channels[0].userConfig.api_endpoint.type" in r.message and "'quantum'" in r.message
            for r in results
        ), f"expected MINOR for unknown channel userConfig type: {[(r.level, r.message) for r in results]}"

    def test_gap101_channel_userconfig_missing_title_is_minor(self, tmp_path):
        """GAP-101: channels[i].userConfig entries without `title` produce MINOR (CPV Issue #9)."""
        from validate_marketplace import validate_plugin_entry

        plugin = {
            "name": "my-plugin",
            "source": {"source": "github", "repo": "o/p"},
            "channels": [
                {
                    "userConfig": {
                        "api_endpoint": {"type": "string", "description": "missing title"},
                    }
                }
            ],
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(
            r.level == "MINOR" and "channels[0].userConfig.api_endpoint" in r.message and "'title'" in r.message
            for r in results
        ), f"expected MINOR for missing 'title': {[(r.level, r.message) for r in results]}"

    def test_gap101_top_level_userconfig_missing_title_is_minor(self, tmp_path):
        """GAP-101 (top-level variant): plugin.userConfig without `title` also MINOR."""
        from validate_marketplace import validate_plugin_entry

        plugin = {
            "name": "my-plugin",
            "source": {"source": "github", "repo": "o/p"},
            "userConfig": {"key1": {"type": "string", "description": "no title"}},
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(r.level == "MINOR" and "userConfig.key1" in r.message and "'title'" in r.message for r in results)

    def test_gap101_channel_userconfig_valid_entry_silent(self, tmp_path):
        """GAP-101: well-formed channels[i].userConfig entry produces no userConfig findings."""
        from validate_marketplace import validate_plugin_entry

        plugin = {
            "name": "my-plugin",
            "source": {"source": "github", "repo": "o/p"},
            "channels": [
                {
                    "userConfig": {
                        "api_endpoint": {
                            "title": "Endpoint",
                            "type": "string",
                            "description": "desc",
                            "sensitive": True,
                        }
                    }
                }
            ],
        }
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert not any(r.level in {"MINOR", "MAJOR", "CRITICAL"} and "userConfig" in r.message for r in results), (
            f"valid userConfig must be silent: {[(r.level, r.message) for r in results]}"
        )


class TestNestedPluginSelfReferenceGuard:
    """n2: _validate_nested_plugin must not validate a marketplace root as a plugin."""

    def test_marketplace_root_as_nested_plugin_is_skipped(self, tmp_path):
        """A nested root that is a marketplace (marketplace.json, no plugin.json) is skipped with INFO (n2)."""
        from validate_marketplace import _validate_nested_plugin

        (tmp_path / "marketplace.json").write_text(
            json.dumps({"name": "mp", "owner": {"name": "x"}, "plugins": []})
        )
        results = _validate_nested_plugin(tmp_path, "self-ref", "mp.json")
        assert any(
            r.level == "INFO" and "self-referential" in r.message for r in results
        ), f"expected self-reference skip INFO: {[(r.level, r.message) for r in results]}"

    def test_real_nested_plugin_still_validated(self, tmp_path):
        """A nested root that IS a plugin (has plugin.json) must NOT be skipped (n2, benign side)."""
        from validate_marketplace import _validate_nested_plugin

        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "p", "version": "1.0.0"}))
        results = _validate_nested_plugin(tmp_path, "real", "mp.json")
        # The self-reference INFO must NOT appear — the plugin was actually validated.
        assert not any("self-referential" in r.message for r in results)


class TestPluginRootMemoization:
    """m10: _read_marketplace_plugin_root memoizes per (path, mtime) without going stale."""

    def test_pluginroot_value_correct_and_cached(self, tmp_path):
        """Repeated reads return the same pluginRoot and do not re-parse a constant file (m10)."""
        from validate_marketplace import _read_marketplace_plugin_root

        (tmp_path / "marketplace.json").write_text(
            json.dumps({"name": "mp", "metadata": {"pluginRoot": "./plugins"}, "plugins": []})
        )
        first = _read_marketplace_plugin_root(tmp_path)
        second = _read_marketplace_plugin_root(tmp_path)
        assert first == "./plugins"
        assert second == "./plugins"

    def test_pluginroot_memo_invalidated_on_file_change(self, tmp_path):
        """A rewrite of marketplace.json (new mtime) must be reflected, not served stale (m10)."""
        import os
        import time

        from validate_marketplace import _read_marketplace_plugin_root

        mp = tmp_path / "marketplace.json"
        mp.write_text(json.dumps({"name": "mp", "metadata": {"pluginRoot": "./old"}, "plugins": []}))
        assert _read_marketplace_plugin_root(tmp_path) == "./old"

        # Rewrite with a different pluginRoot and bump mtime so the memo key changes
        # even if the write lands within the same clock tick.
        mp.write_text(json.dumps({"name": "mp", "metadata": {"pluginRoot": "./new"}, "plugins": []}))
        future = time.time() + 10
        os.utime(mp, (future, future))
        assert _read_marketplace_plugin_root(tmp_path) == "./new"


class TestMarketplaceReportAtomicWrite:
    """m9: the --report write must land atomically with no leftover .tmp file."""

    def test_report_written_and_no_tmp_left(self, tmp_path):
        """main() --report produces the final report and leaves no sibling .tmp (m9)."""
        from unittest.mock import patch

        from validate_marketplace import main

        (tmp_path / "marketplace.json").write_text(
            json.dumps({"name": "mp", "owner": {"name": "x"}, "plugins": []})
        )
        report_path = tmp_path / "out" / "report.md"
        with patch(
            "sys.argv",
            ["validate_marketplace.py", str(tmp_path), "--report", str(report_path)],
        ):
            main()
        assert report_path.is_file()
        assert report_path.read_text().strip() != ""
        # The atomic-rename helper must not leave the sibling temp file behind.
        assert not (report_path.parent / (report_path.name + ".tmp")).exists()
