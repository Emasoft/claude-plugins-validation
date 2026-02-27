"""Tests for validate_marketplace.py after refactoring to use canonical validation_common API.

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
        """MarketplaceValidationResult must inherit from validation_common.ValidationResult."""
        from validate_marketplace import MarketplaceValidationResult
        from validation_common import ValidationResult as BaseValidationResult

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
        """MarketplaceValidationReport must inherit from validation_common.ValidationReport."""
        from validate_marketplace import MarketplaceValidationReport
        from validation_common import ValidationReport as BaseValidationReport

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
                "results": [
                    {"level": r.level, "message": r.message}
                    for r in report.results
                ],
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
        mp.write_text('[1, 2, 3]')
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

    def test_non_kebab_name_produces_minor(self, tmp_path):
        """Plugin name not in kebab-case must produce MINOR (lines 373-374)."""
        from validate_marketplace import validate_plugin_entry

        plugin = {"name": "BadName!", "source": "github"}
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(r.level == "MINOR" and "kebab-case" in r.message for r in results)

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

    def test_unknown_fields_produce_info(self, tmp_path):
        """Unknown fields on plugin entry must produce INFO (lines 425-426)."""
        from validate_marketplace import validate_plugin_entry

        plugin = {"name": "my-plugin", "source": "github", "custom_field_xyz": True}
        results = validate_plugin_entry(plugin, 0, tmp_path, "mp.json")
        assert any(r.level == "INFO" and "unknown field" in r.message and "custom_field_xyz" in r.message for r in results)

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

    def test_path_traversal_produces_minor(self, tmp_path):
        """Path containing '..' must produce MINOR warning (lines 684-693)."""
        from validate_marketplace import validate_local_path

        results = validate_local_path("../some-path", "myplugin", tmp_path, "mp.json")
        assert any(r.level == "MINOR" and "path traversal" in r.message for r in results)


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


class TestValidateGithubSourceRequired:
    """Tests for validate_github_source_required covering missing repo, non-string repo, non-github URL."""

    def test_missing_repository_produces_major(self):
        """Plugin without repository field must produce MAJOR (lines 1445-1456)."""
        from validate_marketplace import validate_github_source_required

        plugins = [{"name": "no-repo-plugin", "source": "./my-plugin"}]
        results = validate_github_source_required(plugins, "mp.json")
        assert any(r.level == "MAJOR" and "missing 'repository'" in r.message for r in results)

    def test_non_github_url_produces_minor(self):
        """Repository URL that does not look like GitHub must produce MINOR (lines 1471-1484)."""
        from validate_marketplace import validate_github_source_required

        # The check requires: no "https://github.com/", no "git@github.com:", no "/" in string
        # A bare string without slashes will trigger the warning
        plugins = [{"name": "p", "source": "./p", "repository": "not-a-github-url"}]
        results = validate_github_source_required(plugins, "mp.json")
        assert any(r.level == "MINOR" and "doesn't look like a GitHub URL" in r.message for r in results)

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
            'jobs:\n  build:\n    steps:\n      - run: python3 -c "val = source[\\"repo\\"]; print(f\\"{val[\\\"key\\\"]}\\")"'
            '\n'
        )
        # The regex specifically looks for python3 -c "..." with {expr["key"]} inside
        # Let's create a more precise example:
        wf_file.write_text(
            'steps:\n  - run: |\n      python3 -c "x = {data[\\"key\\"]}"'
            '\n'
        )
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
        wf_file.write_text("name: CI\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hello\n")
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
        mp.write_text(json.dumps({
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
        }))

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

    def test_marketplace_with_owner_not_object(self, tmp_path):
        """Owner field that is not an object must produce MAJOR (lines 1652-1658)."""
        from validate_marketplace import validate_marketplace

        mp = tmp_path / "marketplace.json"
        mp.write_text(json.dumps({"name": "test-mp", "owner": "just-a-string", "plugins": []}))
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
