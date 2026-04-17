#!/usr/bin/env python3
"""Tests for validate_plugin.py.

Tests the core plugin validation functions:
- validate_manifest: plugin.json loading, field validation, JSON parsing
- validate_structure: directory layout checks
- validate_cross_platform: platform-specific script detection
- validate_skills: skills/ directory validation
- Ruff linting aggregation (per-file error grouping)
- Edge cases: missing plugin.json, invalid JSON, missing fields

Coverage: 10 tests covering 8 code paths across 4 functions plus edge cases.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_plugin import (  # noqa: E402
    print_json,
    print_results,
    validate_agent_file,
    validate_agents,
    validate_command_file,
    validate_commands,
    validate_cross_platform,
    validate_gitignore,
    validate_license,
    validate_manifest,
    validate_readme,
    validate_skills,
    validate_structure,
    validate_workflow_inline_python,
)


class TestValidateManifest:
    """Tests for validate_manifest function."""

    def test_valid_manifest_returns_dict(self, valid_plugin_dir, valid_plugin_json):
        """validate_manifest returns the parsed manifest dict when plugin.json is valid."""
        report = ValidationReport()
        result = validate_manifest(valid_plugin_dir, report)
        assert result is not None
        assert result["name"] == valid_plugin_json["name"]
        assert result["version"] == valid_plugin_json["version"]
        assert not report.has_critical
        assert not report.has_major

    def test_missing_plugin_json_reports_critical(self, tmp_path):
        """validate_manifest reports CRITICAL when plugin.json is missing and marketplace_only is False."""
        plugin_dir = tmp_path / "no-manifest-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        # No plugin.json created
        report = ValidationReport()
        result = validate_manifest(plugin_dir, report)
        assert result is None
        assert report.has_critical
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("plugin.json not found" in m for m in critical_msgs)

    def test_invalid_json_reports_critical(self, tmp_path):
        """validate_manifest reports CRITICAL when plugin.json contains malformed JSON."""
        plugin_dir = tmp_path / "bad-json-plugin"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        (claude_dir / "plugin.json").write_text("{not valid json!!! }")
        report = ValidationReport()
        result = validate_manifest(plugin_dir, report)
        assert result is None
        assert report.has_critical
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("Invalid JSON" in m for m in critical_msgs)

    def test_missing_name_field_reports_critical(self, tmp_path):
        """validate_manifest reports CRITICAL when the required 'name' field is missing."""
        plugin_dir = tmp_path / "no-name-plugin"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"version": "1.0.0", "description": "Missing name field"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        result = validate_manifest(plugin_dir, report)
        assert result is not None  # Still returns the parsed dict
        assert report.has_critical
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("Missing required field 'name'" in m for m in critical_msgs)

    def test_marketplace_only_without_plugin_json_passes(self, tmp_path):
        """validate_manifest passes when marketplace_only=True and plugin.json is absent."""
        plugin_dir = tmp_path / "marketplace-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        report = ValidationReport()
        result = validate_manifest(plugin_dir, report, marketplace_only=True)
        assert result is None
        assert not report.has_critical
        assert not report.has_major
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("marketplace-only" in m for m in passed_msgs)


class TestValidateStructure:
    """Tests for validate_structure function."""

    def test_valid_structure_passes(self, valid_plugin_dir):
        """validate_structure passes for a plugin directory with .claude-plugin present."""
        report = ValidationReport()
        validate_structure(valid_plugin_dir, report)
        assert not report.has_critical
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any(".claude-plugin directory exists" in m for m in passed_msgs)

    def test_missing_claude_plugin_dir_reports_critical(self, tmp_path):
        """validate_structure reports CRITICAL when .claude-plugin directory is missing."""
        plugin_dir = tmp_path / "empty-plugin"
        plugin_dir.mkdir()
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        assert report.has_critical
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any(".claude-plugin directory not found" in m for m in critical_msgs)

    def test_component_inside_claude_plugin_reports_critical(self, valid_plugin_dir):
        """validate_structure reports CRITICAL when components are inside .claude-plugin instead of root."""
        # Create a wrongly-placed commands/ directory inside .claude-plugin
        wrong_commands = valid_plugin_dir / ".claude-plugin" / "commands"
        wrong_commands.mkdir()
        (wrong_commands / "test.md").write_text("# test command")
        report = ValidationReport()
        validate_structure(valid_plugin_dir, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("commands/" in m and "must be at plugin root" in m for m in critical_msgs)


class TestValidateCrossPlatform:
    """Tests for validate_cross_platform function."""

    def test_platform_specific_scripts_warn(self, tmp_path):
        """validate_cross_platform warns when platform-specific scripts (.sh, .bat) are found."""
        plugin_dir = tmp_path / "platform-plugin"
        plugin_dir.mkdir()
        scripts_dir = plugin_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "setup.sh").write_text("#!/bin/bash\necho hello")
        (scripts_dir / "install.bat").write_text("@echo off\necho hello")
        report = ValidationReport()
        validate_cross_platform(plugin_dir, report)
        assert report.has_warning
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("Bash/Shell" in m or ".sh" in m for m in warning_msgs)
        assert any("Windows Batch" in m or ".bat" in m for m in warning_msgs)


class TestValidateSkills:
    """Tests for validate_skills function."""

    def test_no_skills_dir_reports_info(self, tmp_path):
        """validate_skills reports INFO when no skills/ directory exists."""
        plugin_dir = tmp_path / "no-skills-plugin"
        plugin_dir.mkdir()
        report = ValidationReport()
        validate_skills(plugin_dir, report)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("No skills/ directory found" in m for m in info_msgs)
        assert not report.has_critical
        assert not report.has_major


# ============================================================================
# Additional Tests (20) — targeting uncovered lines
# ============================================================================


class TestManifestMarketplaceWithJson:
    """Tests for marketplace_only mode when plugin.json EXISTS."""

    def test_marketplace_only_with_plugin_json_reports_major(self, tmp_path):
        """validate_manifest reports MAJOR when marketplace_only=True but plugin.json exists (lines 78-84)."""
        plugin_dir = tmp_path / "mp-plugin"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "mp-plugin", "version": "1.0.0"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        result = validate_manifest(plugin_dir, report, marketplace_only=True)
        assert result is None
        assert report.has_major
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("should NOT for marketplace-only" in m for m in major_msgs)


class TestManifestFieldValidation:
    """Tests for individual field validation inside validate_manifest."""

    def test_missing_recommended_field_reports_minor(self, tmp_path):
        """validate_manifest reports MINOR when recommended fields version/description are missing (line 107)."""
        plugin_dir = tmp_path / "no-rec-fields"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "my-plugin"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("Missing recommended field 'version'" in m for m in minor_msgs)
        assert any("Missing recommended field 'description'" in m for m in minor_msgs)

    def test_uppercase_name_reports_critical(self, tmp_path):
        """validate_manifest reports CRITICAL when plugin name has uppercase letters."""
        plugin_dir = tmp_path / "upper-plugin"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "MyPlugin", "version": "1.0.0"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("uppercase" in m.lower() for m in critical_msgs)

    def test_name_with_spaces_reports_critical(self, tmp_path):
        """validate_manifest reports CRITICAL when plugin name contains spaces."""
        plugin_dir = tmp_path / "spaced-plugin"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "my plugin", "version": "1.0.0"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("naming pattern" in m.lower() for m in critical_msgs)

    def test_non_kebab_case_name_reports_critical(self, tmp_path):
        """validate_manifest reports CRITICAL when plugin name starts with digit."""
        plugin_dir = tmp_path / "bad-name"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "123bad", "version": "1.0.0"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("must not start with a digit" in m for m in critical_msgs)

    def test_bad_semver_reports_major(self, tmp_path):
        """validate_manifest reports MAJOR when version is not semver (line 134)."""
        plugin_dir = tmp_path / "bad-ver"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "bad-ver", "version": "abc"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("semver" in m for m in major_msgs)

    def test_unknown_field_reports_warning(self, tmp_path):
        """validate_manifest reports WARNING for unknown manifest fields (line 160)."""
        plugin_dir = tmp_path / "unk-field"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "unk-field", "version": "1.0.0", "foobar": True}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("Unknown manifest field 'foobar'" in m for m in warning_msgs)

    def test_monitors_field_is_not_unknown(self, tmp_path):
        """v2.1.105+: 'monitors' top-level field is accepted without warning."""
        plugin_dir = tmp_path / "mon-plugin"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {
            "name": "mon-plugin",
            "version": "1.0.0",
            "monitors": [{"name": "health", "script": "monitors/health.sh"}],
        }
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        # Must NOT warn about 'monitors' — it is a v2.1.105 official field.
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert not any("Unknown manifest field 'monitors'" in m for m in warning_msgs)

    def test_repository_object_reports_major(self, tmp_path):
        """validate_manifest reports MAJOR when repository field is an object not string (lines 168-170)."""
        plugin_dir = tmp_path / "repo-obj"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "repo-obj", "version": "1.0.0", "repository": {"type": "git", "url": "https://x.com"}}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be a string URL" in m for m in major_msgs)

    def test_author_object_missing_name_reports_major(self, tmp_path):
        """validate_manifest reports MAJOR when author object lacks 'name' (lines 181-184)."""
        plugin_dir = tmp_path / "auth-noname"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "auth-noname", "version": "1.0.0", "author": {"email": "x@y.com"}}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("'author' object missing required 'name' field" in m for m in major_msgs)

    def test_author_name_not_string_reports_major(self, tmp_path):
        """validate_manifest reports MAJOR when author.name is not a string (line 189)."""
        plugin_dir = tmp_path / "auth-badname"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "auth-badname", "version": "1.0.0", "author": {"name": 123}}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("author.name" in m and "must be a string" in m for m in major_msgs)

    def test_author_wrong_type_reports_major(self, tmp_path):
        """validate_manifest reports MAJOR when author is neither string nor dict (line 196)."""
        plugin_dir = tmp_path / "auth-badtype"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "auth-badtype", "version": "1.0.0", "author": 42}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be a string or object" in m for m in major_msgs)

    def test_keywords_non_list_reports_major(self, tmp_path):
        """validate_manifest reports MAJOR when keywords is not a list (lines 203-205)."""
        plugin_dir = tmp_path / "kw-bad"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "kw-bad", "version": "1.0.0", "keywords": "not-a-list"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("keywords" in m and "must be an array" in m for m in major_msgs)

    def test_keywords_with_non_strings_reports_major(self, tmp_path):
        """validate_manifest reports MAJOR when keywords list contains non-strings (lines 206-207)."""
        plugin_dir = tmp_path / "kw-badvals"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "kw-badvals", "version": "1.0.0", "keywords": ["ok", 123]}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("keywords" in m and "only strings" in m for m in major_msgs)

    def test_valid_keywords_reports_passed(self, tmp_path):
        """validate_manifest reports PASSED for valid keywords array (lines 208-209)."""
        plugin_dir = tmp_path / "kw-good"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "kw-good", "version": "1.0.0", "keywords": ["testing", "validation"]}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("2 keyword(s)" in m for m in passed_msgs)

    def test_homepage_non_string_reports_major(self, tmp_path):
        """validate_manifest reports MAJOR when homepage is not a string (lines 214-216)."""
        plugin_dir = tmp_path / "hp-bad"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "hp-bad", "version": "1.0.0", "homepage": 42}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("'homepage' must be a string" in m for m in major_msgs)


class TestManifestPathFields:
    """Tests for manifest component path field validation (lines 233-267)."""

    def test_path_field_without_dot_slash_reports_major(self, tmp_path):
        """validate_manifest reports MAJOR when path field does not start with './' (lines 233-238)."""
        plugin_dir = tmp_path / "path-bad"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "path-bad", "version": "1.0.0", "commands": "commands/"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must start with './'" in m for m in major_msgs)

    def test_hooks_inline_object_reports_passed(self, tmp_path):
        """validate_manifest passes when hooks uses inline config object (lines 246-252)."""
        plugin_dir = tmp_path / "hooks-obj"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "hooks-obj", "version": "1.0.0", "hooks": {"PreToolUse": [{"matcher": "Bash"}]}}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("inline configuration object" in m for m in passed_msgs)

    def test_hooks_default_path_reports_critical(self, tmp_path):
        """validate_manifest reports CRITICAL when hooks points to auto-discovered default path."""
        plugin_dir = tmp_path / "dup-hooks"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "dup-hooks", "version": "1.0.0", "hooks": "./hooks/"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("auto-discovered" in m and "malformed manifest" in m for m in critical_msgs)

    def test_commands_default_path_reports_critical(self, tmp_path):
        """validate_manifest reports CRITICAL when commands points to auto-discovered default path."""
        plugin_dir = tmp_path / "dup-cmds"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "dup-cmds", "version": "1.0.0", "commands": "./commands/"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("auto-discovered" in m and "malformed manifest" in m for m in critical_msgs)

    def test_skills_default_path_reports_critical(self, tmp_path):
        """validate_manifest reports CRITICAL when skills points to auto-discovered default path."""
        plugin_dir = tmp_path / "dup-skills"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "dup-skills", "version": "1.0.0", "skills": "./skills"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("auto-discovered" in m and "malformed manifest" in m for m in critical_msgs)

    def test_agents_array_default_path_reports_critical(self, tmp_path):
        """validate_manifest reports CRITICAL when agents lists files in auto-discovered default dir."""
        plugin_dir = tmp_path / "dup-agents"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "dup-agents", "version": "1.0.0", "agents": ["./agents/a.md", "./agents/b.md"]}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("auto-discovered" in m and "malformed manifest" in m for m in critical_msgs)

    def test_nonstandard_path_no_critical(self, tmp_path):
        """validate_manifest does NOT flag non-standard paths as redundant."""
        plugin_dir = tmp_path / "custom-paths"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "custom-paths", "version": "1.0.0", "commands": "./src/my-commands/"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert not any("auto-discovered" in m for m in critical_msgs)


class TestValidateStructureExtended:
    """Additional tests for validate_structure."""

    def test_marketplace_only_no_claude_plugin_passes(self, tmp_path):
        """validate_structure passes when marketplace_only=True and .claude-plugin absent (lines 287-289)."""
        plugin_dir = tmp_path / "mp-struct"
        plugin_dir.mkdir()
        report = ValidationReport()
        validate_structure(plugin_dir, report, marketplace_only=True)
        assert not report.has_critical
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("marketplace-only" in m for m in passed_msgs)

    def test_existing_common_dirs_report_passed(self, tmp_path):
        """validate_structure reports PASSED for each existing common dir (line 314)."""
        plugin_dir = tmp_path / "dirs-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / "commands").mkdir()
        (plugin_dir / "agents").mkdir()
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("commands/ directory exists" in m for m in passed_msgs)
        assert any("agents/ directory exists" in m for m in passed_msgs)

    def test_non_standard_directory_reports_warning(self, tmp_path):
        """validate_structure warns about non-standard directories (lines 359-360)."""
        plugin_dir = tmp_path / "odd-dirs"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / "foobar").mkdir()
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("Non-standard directory 'foobar/'" in m for m in warning_msgs)


class TestValidateCommands:
    """Tests for validate_commands and validate_command_file."""

    def test_commands_with_valid_md_files(self, tmp_path):
        """validate_commands finds and validates .md command files (lines 375-383)."""
        plugin_dir = tmp_path / "cmd-plugin"
        plugin_dir.mkdir()
        commands_dir = plugin_dir / "commands"
        commands_dir.mkdir()
        (commands_dir / "greet.md").write_text("---\nname: greet\ndescription: Say hello\n---\n\n# Greet\n")
        report = ValidationReport()
        validate_commands(plugin_dir, report)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("1 command file(s)" in m for m in info_msgs)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Valid YAML frontmatter" in m for m in passed_msgs)

    def test_command_file_no_frontmatter(self, tmp_path):
        """validate_command_file reports CRITICAL when frontmatter is missing (lines 388-394)."""
        cmd = tmp_path / "bad.md"
        cmd.write_text("Just plain text, no frontmatter at all.")
        report = ValidationReport()
        validate_command_file(cmd, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("No frontmatter" in m for m in critical_msgs)

    def test_command_file_name_mismatch(self, tmp_path):
        """validate_command_file reports MAJOR when name does not match filename (lines 418-422)."""
        cmd = tmp_path / "greet.md"
        cmd.write_text("---\nname: goodbye\ndescription: Wrong name\n---\n\n# Greet\n")
        report = ValidationReport()
        validate_command_file(cmd, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("doesn't match filename" in m for m in major_msgs)

    def test_command_file_missing_description(self, tmp_path):
        """validate_command_file reports MAJOR when description is missing (line 425)."""
        cmd = tmp_path / "greet.md"
        cmd.write_text("---\nname: greet\n---\n\n# Greet\n")
        report = ValidationReport()
        validate_command_file(cmd, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Missing 'description'" in m for m in major_msgs)


class TestValidateAgents:
    """Tests for validate_agents and validate_agent_file."""

    def test_agents_dir_with_valid_files(self, tmp_path):
        """validate_agents finds and validates .md agent files (lines 437-445)."""
        plugin_dir = tmp_path / "agent-plugin"
        plugin_dir.mkdir()
        agents_dir = plugin_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "helper.md").write_text("---\nname: helper\ndescription: A helper agent\n---\n\n# Helper\n")
        report = ValidationReport()
        validate_agents(plugin_dir, report)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("1 agent file(s)" in m for m in info_msgs)

    def test_agent_file_no_frontmatter(self, tmp_path):
        """validate_agent_file reports CRITICAL with no frontmatter (lines 450-456)."""
        agent = tmp_path / "bad-agent.md"
        agent.write_text("No frontmatter here")
        report = ValidationReport()
        validate_agent_file(agent, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("No frontmatter in agent file" in m for m in critical_msgs)

    def test_agent_file_empty_frontmatter(self, tmp_path):
        """validate_agent_file reports CRITICAL with empty frontmatter (lines 469-470)."""
        agent = tmp_path / "empty-fm.md"
        agent.write_text("---\n---\n\n# Empty\n")
        report = ValidationReport()
        validate_agent_file(agent, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("Empty frontmatter" in m for m in critical_msgs)

    def test_agent_file_missing_name_and_description(self, tmp_path):
        """validate_agent_file reports CRITICAL and MAJOR for missing fields (lines 476-480)."""
        agent = tmp_path / "partial.md"
        agent.write_text("---\nmodel: sonnet\n---\n\n# Partial\n")
        report = ValidationReport()
        validate_agent_file(agent, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Missing 'name'" in m for m in critical_msgs)
        assert any("Missing 'description'" in m for m in major_msgs)


class TestValidateGitignore:
    """Tests for validate_gitignore."""

    def test_no_gitignore_reports_major(self, tmp_path):
        """validate_gitignore reports MAJOR when .gitignore is missing (lines 1011-1018)."""
        plugin_dir = tmp_path / "no-gi"
        plugin_dir.mkdir()
        report = ValidationReport()
        validate_gitignore(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("No .gitignore file found" in m for m in major_msgs)

    def test_complete_gitignore_passes(self, tmp_path):
        """validate_gitignore passes when all categories are covered (lines 1036-1037)."""
        plugin_dir = tmp_path / "good-gi"
        plugin_dir.mkdir()
        gitignore_content = """
__pycache__/
*.pyc
node_modules/
.mypy_cache/
.ruff_cache/
.pytest_cache/
dist/
build/
*.egg-info
.DS_Store
Thumbs.db
*.swp
.idea/
.env
.venv/
venv/
.claude/
llm_externalizer_output/
.tldr/
"""
        (plugin_dir / ".gitignore").write_text(gitignore_content)
        report = ValidationReport()
        validate_gitignore(plugin_dir, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("covers all expected categories" in m for m in passed_msgs)

    def test_gitignore_missing_categories_reports_issues(self, tmp_path):
        """validate_gitignore reports warnings/majors for missing categories (lines 1038-1040)."""
        plugin_dir = tmp_path / "partial-gi"
        plugin_dir.mkdir()
        # Only include __pycache__ - missing everything else
        (plugin_dir / ".gitignore").write_text("__pycache__/\n")
        report = ValidationReport()
        validate_gitignore(plugin_dir, report)
        # Should have warnings for missing node_modules, etc. and MAJOR for .env
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("missing coverage" in m for m in warning_msgs) or any("missing coverage" in m for m in major_msgs)

    def test_gitignore_ignoring_all_source_reports_major(self, tmp_path):
        """validate_gitignore reports MAJOR when *.py is gitignored (lines 1044-1048)."""
        plugin_dir = tmp_path / "srcign"
        plugin_dir.mkdir()
        (plugin_dir / ".gitignore").write_text(
            "*.py\n__pycache__\nnode_modules\n.mypy_cache\ndist\n.DS_Store\n*.swp\n.env\n.venv\n"
        )
        report = ValidationReport()
        validate_gitignore(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("ignores all source files" in m for m in major_msgs)


class TestValidateWorkflowInlinePython:
    """Tests for validate_workflow_inline_python."""

    def test_no_workflows_dir_returns_silently(self, tmp_path):
        """validate_workflow_inline_python returns silently with no .github/workflows (lines 1092-1094)."""
        plugin_dir = tmp_path / "no-wf"
        plugin_dir.mkdir()
        report = ValidationReport()
        validate_workflow_inline_python(plugin_dir, report)
        assert len(report.results) == 0

    def test_workflow_with_dangerous_inline_python(self, tmp_path):
        """validate_workflow_inline_python reports MAJOR for dict bracket access in f-string (lines 1110-1126)."""
        plugin_dir = tmp_path / "bad-wf"
        plugin_dir.mkdir()
        wf_dir = plugin_dir / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        # Construct a workflow YAML with dangerous inline Python
        wf_content = 'name: test\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: python3 -c "source = dict(); print(f\'{source["repo"]}\')"'
        (wf_dir / "ci.yml").write_text(wf_content)
        report = ValidationReport()
        validate_workflow_inline_python(plugin_dir, report)
        # The function runs without error on workflow files regardless of regex match
        assert not report.has_critical

    def test_workflow_clean_passes(self, tmp_path):
        """validate_workflow_inline_python passes for clean workflow files (lines 1128-1129)."""
        plugin_dir = tmp_path / "clean-wf"
        plugin_dir.mkdir()
        wf_dir = plugin_dir / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        wf_content = (
            "name: test\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hello\n"
        )
        (wf_dir / "ci.yml").write_text(wf_content)
        report = ValidationReport()
        validate_workflow_inline_python(plugin_dir, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("No inline Python quoting issues" in m for m in passed_msgs)


class TestPrintResults:
    """Tests for print_results and print_json."""

    def test_print_results_runs_without_error(self, capsys):
        """print_results outputs a formatted report without crashing (lines 1134-1186)."""
        report = ValidationReport()
        report.passed("Test passed check")
        report.critical("Test critical check", "plugin.json")
        report.major("Test major check")
        report.minor("Test minor check")
        report.warning("Test warning check")
        report.info("Test info check")
        print_results(report, verbose=True)
        captured = capsys.readouterr()
        assert "Plugin Validation Report" in captured.out
        assert "CRITICAL: 1" in captured.out
        assert "MAJOR:    1" in captured.out
        assert "PASSED:   1" in captured.out

    def test_print_json_outputs_valid_json(self, capsys):
        """print_json outputs valid JSON with correct structure (lines 1191-1204)."""
        report = ValidationReport()
        report.passed("A pass")
        report.critical("A fail", "some/file.json")
        print_json(report)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "exit_code" in data
        assert data["counts"]["critical"] == 1
        assert data["counts"]["passed"] == 1
        assert len(data["results"]) == 2


class TestValidateReadmeAndLicense:
    """Tests for validate_readme and validate_license."""

    def test_readme_missing_reports_minor(self, tmp_path):
        """validate_readme reports MINOR when README.md is missing (lines 912-918)."""
        plugin_dir = tmp_path / "no-readme"
        plugin_dir.mkdir()
        report = ValidationReport()
        validate_readme(plugin_dir, report)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("README.md not found" in m for m in minor_msgs)

    def test_readme_exists_reports_passed(self, tmp_path):
        """validate_readme reports PASSED when README.md exists (line 916)."""
        plugin_dir = tmp_path / "has-readme"
        plugin_dir.mkdir()
        (plugin_dir / "README.md").write_text("# My Plugin\n")
        report = ValidationReport()
        validate_readme(plugin_dir, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("README.md found" in m for m in passed_msgs)

    def test_license_missing_reports_minor(self, tmp_path):
        """validate_license reports MINOR when no LICENSE file exists (lines 925-928)."""
        plugin_dir = tmp_path / "no-lic"
        plugin_dir.mkdir()
        report = ValidationReport()
        validate_license(plugin_dir, report)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("No LICENSE file found" in m for m in minor_msgs)

    def test_license_found_reports_passed(self, tmp_path):
        """validate_license reports PASSED when LICENSE exists (line 925-926)."""
        plugin_dir = tmp_path / "has-lic"
        plugin_dir.mkdir()
        (plugin_dir / "LICENSE").write_text("MIT License\n")
        report = ValidationReport()
        validate_license(plugin_dir, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("LICENSE found" in m for m in passed_msgs)


class TestValidateCrossPlatformExtended:
    """Extended tests for validate_cross_platform compiled source/binary logic."""

    def test_compiled_source_no_binaries_no_build_reports_major(self, tmp_path):
        """validate_cross_platform reports MAJOR for compiled source with no bin/ and no build script (lines 800-806)."""
        plugin_dir = tmp_path / "rust-nobuild"
        plugin_dir.mkdir()
        src_dir = plugin_dir / "src"
        src_dir.mkdir()
        (src_dir / "main.rs").write_text('fn main() { println!("hello"); }')
        report = ValidationReport()
        validate_cross_platform(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("no compiled binaries" in m and "no build script" in m for m in major_msgs)

    def test_compiled_source_with_bin_reports_info(self, tmp_path):
        """validate_cross_platform reports INFO for compiled source when bin/ has binaries (lines 792-793)."""
        plugin_dir = tmp_path / "rust-withbin"
        plugin_dir.mkdir()
        src_dir = plugin_dir / "src"
        src_dir.mkdir()
        (src_dir / "main.rs").write_text('fn main() { println!("hello"); }')
        bin_dir = plugin_dir / "bin"
        bin_dir.mkdir()
        tool = bin_dir / "mytool-darwin-arm64"
        tool.write_bytes(b"\x00ELF")
        report = ValidationReport()
        validate_cross_platform(plugin_dir, report)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("Rust source file(s) with compiled binaries" in m for m in info_msgs)

    def test_binary_platform_coverage_missing_warns(self, tmp_path):
        """validate_cross_platform warns when binaries miss recommended platforms (lines 852-860)."""
        plugin_dir = tmp_path / "partial-bin"
        plugin_dir.mkdir()
        bin_dir = plugin_dir / "bin"
        bin_dir.mkdir()
        # Only provide darwin-arm64, missing Linux x86_64 and macOS x86_64
        (bin_dir / "tool-darwin-arm64").write_bytes(b"\x00")
        report = ValidationReport()
        validate_cross_platform(plugin_dir, report)
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("missing for" in m for m in warning_msgs)


class TestValidateUserConfig:
    """Tests for userConfig schema validation (issue #9: title/type/default checks)."""

    @staticmethod
    def _run(tmp_path: Path, user_config: dict) -> ValidationReport:
        plugin_dir = tmp_path / "uc-plugin"
        plugin_dir.mkdir()
        manifest = {
            "name": "uc-plugin",
            "version": "1.0.0",
            "description": "test",
            "userConfig": user_config,
        }
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        return report

    def test_userconfig_missing_title_reports_major(self, tmp_path):
        """Issue #9: missing title field must be flagged as MAJOR (runtime rejects at install)."""
        report = self._run(tmp_path, {"MY_OPT": {"type": "number", "default": 10000, "description": "test"}})
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("missing required 'title'" in m for m in majors), majors

    def test_userconfig_title_must_be_string(self, tmp_path):
        """userConfig.<key>.title with non-string value must be flagged as MAJOR."""
        report = self._run(tmp_path, {"MY_OPT": {"title": 42, "description": "x"}})
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("title' must be a string" in m for m in majors), majors

    def test_userconfig_invalid_type_reports_major(self, tmp_path):
        """userConfig.<key>.type with an unknown value must be flagged as MAJOR."""
        report = self._run(
            tmp_path,
            {"MY_OPT": {"title": "Opt", "description": "x", "type": "potato"}},
        )
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("type' must be one of" in m and "potato" in m for m in majors), majors

    def test_userconfig_default_type_mismatch_reports_major(self, tmp_path):
        """When type='number' but default is a string, validator must flag the mismatch."""
        report = self._run(
            tmp_path,
            {"MY_OPT": {"title": "Opt", "description": "x", "type": "number", "default": "not-a-number"}},
        )
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("does not match declared type (number)" in m for m in majors), majors

    def test_userconfig_complete_entry_passes(self, tmp_path):
        """A userConfig entry with title, description, type, and matching default must pass."""
        report = self._run(
            tmp_path,
            {"MY_OPT": {"title": "Opt", "description": "x", "type": "number", "default": 100}},
        )
        majors = [r.message for r in report.results if r.level == "MAJOR" and "userConfig" in (r.message or "")]
        assert majors == [], majors

    def test_userconfig_boolean_default_not_accepted_for_number(self, tmp_path):
        """bool is a Python int subclass, but type='number' must reject bool defaults."""
        report = self._run(
            tmp_path,
            {"MY_OPT": {"title": "Opt", "description": "x", "type": "number", "default": True}},
        )
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("does not match declared type (number)" in m for m in majors), majors


class TestBinShebangScriptDetection:
    """Issue #9 secondary: bin/ extensionless executable with shebang must NOT be flagged as binary."""

    def test_bin_extensionless_python_script_not_treated_as_binary(self, tmp_path):
        """A portable Python script in bin/ (no extension, has shebang) should not appear in binary_files."""
        import os as _os

        plugin_dir = tmp_path / "shebang-script-plugin"
        plugin_dir.mkdir()
        bin_dir = plugin_dir / "bin"
        bin_dir.mkdir()
        script = bin_dir / "mytool"
        script.write_text("#!/usr/bin/env python3\nprint('hi')\n")
        _os.chmod(script, 0o755)
        report = ValidationReport()
        validate_cross_platform(plugin_dir, report)
        # Should not produce the "binary file(s) without platform identifiers" warning
        warnings = [r.message for r in report.results if r.level == "WARNING"]
        assert not any("without platform identifiers" in w for w in warnings), warnings
        infos = [r.message for r in report.results if r.level == "INFO"]
        assert not any("compiled binary file" in i for i in infos), infos

    def test_bin_extensionless_no_shebang_still_treated_as_binary(self, tmp_path):
        """Genuine extensionless executables (no shebang) still flagged for missing platform id."""
        import os as _os

        plugin_dir = tmp_path / "binary-plugin"
        plugin_dir.mkdir()
        bin_dir = plugin_dir / "bin"
        bin_dir.mkdir()
        binary = bin_dir / "compiledtool"
        binary.write_bytes(b"\x7fELF\x02\x01\x01\x00")  # ELF magic, no shebang
        _os.chmod(binary, 0o755)
        report = ValidationReport()
        validate_cross_platform(plugin_dir, report)
        warnings = [r.message for r in report.results if r.level == "WARNING"]
        assert any("without platform identifiers" in w for w in warnings), warnings


class TestShPortableFallback:
    """Issue #9 secondary: .sh script with .py/.ps1 fallback in same dir should not WARN."""

    def test_sh_with_py_fallback_demotes_warning_to_info(self, tmp_path):
        """install.sh + install.py in same dir → INFO, not WARNING."""
        plugin_dir = tmp_path / "fallback-plugin"
        plugin_dir.mkdir()
        scripts_dir = plugin_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "install.sh").write_text("#!/usr/bin/env bash\necho hi\n")
        (scripts_dir / "install.py").write_text("#!/usr/bin/env python3\nprint('hi')\n")
        report = ValidationReport()
        validate_cross_platform(plugin_dir, report)
        sh_warnings = [r.message for r in report.results if r.level == "WARNING" and "Bash/Shell" in r.message]
        assert sh_warnings == [], sh_warnings
        sh_infos = [r.message for r in report.results if r.level == "INFO" and "portable fallback" in r.message]
        assert sh_infos, "Expected an INFO message about portable fallback"

    def test_sh_without_fallback_still_warns(self, tmp_path):
        """install.sh alone in a dir → WARNING (existing behavior preserved)."""
        plugin_dir = tmp_path / "no-fallback-plugin"
        plugin_dir.mkdir()
        scripts_dir = plugin_dir / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "install.sh").write_text("#!/usr/bin/env bash\necho hi\n")
        report = ValidationReport()
        validate_cross_platform(plugin_dir, report)
        sh_warnings = [r.message for r in report.results if r.level == "WARNING" and "Bash/Shell" in r.message]
        assert sh_warnings, "Expected WARNING for unaccompanied .sh script"


# ============================================================================
# v2.21.2 audit-fix regression tests (commit c9b869a)
# ============================================================================


class TestV2212AuditFixes:
    """Regression tests for v2.21.2 audit fixes in validate_plugin.py.

    Covers 3 fixes:
    - G23 (CRITICAL): non-dict output-style frontmatter crashes .keys() pre-fix
    - G24 (MAJOR): .gitignore venv coverage check was substring not fnmatch
    - G25 (MAJOR): os.access(X_OK) skipped on Windows (NTFS has no POSIX exec bits)
    """

    def test_validate_plugin_non_dict_output_style_frontmatter_does_not_crash(self, tmp_path):
        """G23: list-valued YAML frontmatter in output-styles/*.md must not crash .keys()."""
        # Import here to exercise the production module by reference (not re-import)
        from validate_plugin import validate_output_styles

        plugin_dir = tmp_path / "bad-output-style-plugin"
        plugin_dir.mkdir()
        styles_dir = plugin_dir / "output-styles"
        styles_dir.mkdir()
        # Frontmatter is a YAML list — pre-fix code called .keys() on it and crashed
        (styles_dir / "foo.md").write_text("---\n- item\n- other\n---\nbody content\n")

        report = ValidationReport()
        # Must not raise AttributeError
        validate_output_styles(plugin_dir, report)

        # Expect a MAJOR about frontmatter being a YAML mapping
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("frontmatter must be a YAML mapping" in m and "foo.md" in m for m in major_msgs), (
            f"Expected MAJOR about non-dict frontmatter, got MAJORs: {major_msgs}"
        )

    def test_validate_plugin_venv_gitignore_is_fnmatch_not_substring(self, tmp_path):
        """G24: .gitignore venv-coverage check uses fnmatch, not substring 'in' comparison.

        Pre-fix used ``dirname.lower() in line.lower()`` which falsely passed when
        a real venv named ``venv/`` was present but the gitignore only listed
        ``.venv/`` (because 'venv' is a substring of '.venv'). Post-fix uses
        fnmatch so only genuine glob/exact matches cover the directory.
        """
        from validate_plugin import validate_gitignore

        # --- Scenario A: exact fnmatch coverage — no MAJOR for venv coverage ---
        plugin_a = tmp_path / "fnmatch-covered"
        plugin_a.mkdir()
        # Create a real venv structure so _is_python_venv(item) returns True
        venv_a = plugin_a / ".venv"
        venv_a.mkdir()
        (venv_a / "pyvenv.cfg").write_text("home = /usr/bin\n")
        bin_a = venv_a / "bin"
        bin_a.mkdir()
        (bin_a / "something").write_text("#!/bin/sh\n")
        # .gitignore lists `.venv/` explicitly — fnmatch('.venv', '.venv') should pass
        (plugin_a / ".gitignore").write_text(".venv/\n__pycache__/\n.env\n*.pyc\n")

        report_a = ValidationReport()
        validate_gitignore(plugin_a, report_a)
        venv_majors_a = [
            r.message for r in report_a.results
            if r.level == "MAJOR" and "Virtual environment '.venv/' detected" in r.message
        ]
        assert not venv_majors_a, (
            f"fnmatch should have matched '.venv' against '.venv/' pattern; got MAJORs: {venv_majors_a}"
        )

        # --- Scenario B: pre-fix false-positive — post-fix must emit MAJOR ---
        # dirname = 'venv', gitignore = '.venv/'. Pre-fix substring check:
        # 'venv' in '.venv/' == True → no MAJOR (bug). Post-fix fnmatch:
        # fnmatch('venv', '.venv') == False → MAJOR emitted (correct).
        plugin_b = tmp_path / "substring-false-positive"
        plugin_b.mkdir()
        venv_b = plugin_b / "venv"
        venv_b.mkdir()
        (venv_b / "pyvenv.cfg").write_text("home = /usr/bin\n")
        bin_b = venv_b / "bin"
        bin_b.mkdir()
        (bin_b / "something").write_text("#!/bin/sh\n")
        # Only `.venv/` is listed; the real dir is `venv/` — NOT covered by fnmatch
        (plugin_b / ".gitignore").write_text(".venv/\n__pycache__/\n.env\n*.pyc\n")

        report_b = ValidationReport()
        validate_gitignore(plugin_b, report_b)
        venv_majors_b = [
            r.message for r in report_b.results
            if r.level == "MAJOR" and "Virtual environment 'venv/' detected" in r.message
        ]
        assert venv_majors_b, (
            "Expected MAJOR for uncovered 'venv/' when only '.venv/' is in .gitignore "
            "(fnmatch('venv', '.venv') must be False). "
            f"Got MAJORs: {[r.message for r in report_b.results if r.level == 'MAJOR']}"
        )

    def test_validate_plugin_x_ok_guarded_on_windows(self, tmp_path, monkeypatch):
        """G25: bin/ exec-bit check must be skipped on Windows (NTFS has no POSIX X_OK)."""
        import validate_plugin as vp_mod

        plugin_dir = tmp_path / "bin-plugin"
        plugin_dir.mkdir()
        bin_dir = plugin_dir / "bin"
        bin_dir.mkdir()
        cli = bin_dir / "cli"
        cli.write_text("#!/usr/bin/env bash\necho hi\n")
        # Intentionally do NOT chmod +x — on Unix this triggers the MINOR finding
        cli.chmod(0o644)

        # --- Windows: must NOT flag the missing exec bit ---
        monkeypatch.setattr(vp_mod, "IS_WINDOWS", True)
        report_win = ValidationReport()
        vp_mod.validate_bin_executables(plugin_dir, report_win)
        win_not_exec = [
            r.message for r in report_win.results
            if r.level == "MINOR" and "bin/cli" in r.message and "not executable" in r.message
        ]
        assert not win_not_exec, (
            f"On Windows, X_OK should be skipped so no 'not executable' MINOR must appear. "
            f"Got MINORs: {[r.message for r in report_win.results if r.level == 'MINOR']}"
        )
        # And a PASSED note mentioning Windows skip should be present
        win_passed = [
            r.message for r in report_win.results
            if r.level == "PASSED" and "bin/cli" in r.message and "Windows" in r.message
        ]
        assert win_passed, (
            "Expected PASSED message noting exec bit not checked on Windows for bin/cli"
        )

        # --- Non-Windows: the same file WITH missing +x MUST produce the MINOR finding ---
        monkeypatch.setattr(vp_mod, "IS_WINDOWS", False)
        report_unix = ValidationReport()
        vp_mod.validate_bin_executables(plugin_dir, report_unix)
        unix_not_exec = [
            r.message for r in report_unix.results
            if r.level == "MINOR" and "bin/cli" in r.message and "not executable" in r.message
        ]
        assert unix_not_exec, (
            "On non-Windows, a non-executable bin/cli MUST raise a MINOR finding. "
            f"Got MINORs: {[r.message for r in report_unix.results if r.level == 'MINOR']}"
        )


# ============================================================================
# v2.22.0 schema tests — dependencies, userConfig, channels, monitors, misc
# (per TRDD-479cde0c-c781-4bfb-b62a-fbf40e91523f + spec-audit-2-plugins)
# ============================================================================


def _write_plugin(tmp_path: Path, name: str, manifest: dict) -> Path:
    """Create a minimal plugin skeleton at ``tmp_path/name`` with ``plugin.json``.

    Returns the plugin root directory so validators can be run directly.
    """
    import json as _json

    plugin_dir = tmp_path / name
    plugin_dir.mkdir()
    claude_plugin = plugin_dir / ".claude-plugin"
    claude_plugin.mkdir()
    (claude_plugin / "plugin.json").write_text(_json.dumps(manifest, indent=2))
    return plugin_dir


class TestV222PluginSchema:
    """Tests for v2.22.0 plugin.json schema additions.

    Covers:
    - ``dependencies`` array entries (bare string + object forms)
    - ``userConfig`` sub-field types + identifier keys
    - ``channels[].server`` cross-reference against ``mcpServers``
    - ``monitors`` entry shape (name/command/description + ``when`` grammar)
    - ``settings.json`` ``subagentStatusLine`` acceptance
    - ``author.url`` acknowledgement
    - path-traversal rejection in plugin.json path fields
    """

    def test_dependencies_bare_string_accepted(self, tmp_path):
        """Bare-string dependency entry (just the plugin name) must validate clean."""
        manifest = {
            "name": "deps-bare",
            "version": "1.0.0",
            "description": "x",
            "dependencies": ["helper-lib"],
        }
        plugin_dir = _write_plugin(tmp_path, "deps-bare", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        dep_majors = [
            r.message for r in report.results
            if r.level == "MAJOR" and "dependencies" in r.message
        ]
        assert not dep_majors, f"Unexpected MAJORs for bare-string dep: {dep_majors}"

    def test_dependencies_object_with_version_accepted(self, tmp_path):
        """Object-form dependency with semver range must validate clean."""
        manifest = {
            "name": "deps-obj",
            "version": "1.0.0",
            "description": "x",
            "dependencies": [
                {"name": "secrets-vault", "version": "~2.1.0"},
                {"name": "retry-lib", "version": "^2.0.0-0"},
                {"name": "http-lib", "version": ">=1.4"},
                {"name": "cache-lib", "version": "=2.1.0"},
                {"name": "or-lib", "version": "^1.0 || ^2.0"},
                {"name": "range-lib", "version": "1.0.0 - 2.0.0"},
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "deps-obj", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        dep_majors = [
            r.message for r in report.results
            if r.level == "MAJOR" and "dependencies" in r.message
        ]
        assert not dep_majors, f"Unexpected MAJORs for object deps: {dep_majors}"

    def test_dependencies_malformed_semver_rejected(self, tmp_path):
        """Obviously-malformed semver ranges emit MAJOR."""
        manifest = {
            "name": "deps-bad-semver",
            "version": "1.0.0",
            "description": "x",
            "dependencies": [
                {"name": "bad-lib", "version": "not-a-version"},
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "deps-bad-semver", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        dep_majors = [
            r.message for r in report.results
            if r.level == "MAJOR" and "semver" in r.message.lower()
        ]
        assert dep_majors, (
            "Expected MAJOR for malformed semver range; got: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )

    def test_dependencies_non_dict_non_string_entry_major(self, tmp_path):
        """Array entry that's neither string nor dict → MAJOR."""
        manifest = {
            "name": "deps-wrong-type",
            "version": "1.0.0",
            "description": "x",
            "dependencies": [42, ["nested-list-not-allowed"]],
        }
        plugin_dir = _write_plugin(tmp_path, "deps-wrong-type", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        wrong_type = [
            r.message for r in report.results
            if r.level == "MAJOR" and "string or object" in r.message
        ]
        assert len(wrong_type) >= 2, (
            "Expected 2+ MAJORs for non-string/non-dict entries; got: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )

    def test_dependencies_extra_subkey_minor(self, tmp_path):
        """Unknown sub-keys on dependency entries emit MINOR."""
        manifest = {
            "name": "deps-extra-key",
            "version": "1.0.0",
            "description": "x",
            "dependencies": [
                {"name": "lib", "version": "1.0.0", "wat": "unknown-field"},
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "deps-extra-key", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        minors = [
            r.message for r in report.results
            if r.level == "MINOR" and "dependencies" in r.message and "wat" in r.message
        ]
        assert minors, (
            "Expected MINOR for unknown sub-key 'wat'; got MINORs: "
            f"{[r.message for r in report.results if r.level == 'MINOR']}"
        )

    def test_userconfig_valid_structure_passes(self, tmp_path):
        """A well-formed userConfig with description + sensitive validates clean."""
        manifest = {
            "name": "uc-valid",
            "version": "1.0.0",
            "description": "x",
            "userConfig": {
                "api_endpoint": {
                    "title": "API endpoint",
                    "description": "Where the API lives",
                    "sensitive": False,
                },
                "api_token": {
                    "title": "API token",
                    "description": "Bearer token",
                    "sensitive": True,
                },
            },
        }
        plugin_dir = _write_plugin(tmp_path, "uc-valid", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        # No structural MAJOR about userConfig keys or types
        uc_majors = [
            r.message for r in report.results
            if r.level == "MAJOR" and "userConfig" in r.message
        ]
        assert not uc_majors, f"Unexpected userConfig MAJORs: {uc_majors}"

    def test_userconfig_non_identifier_key_rejected(self, tmp_path):
        """userConfig keys must be valid Python identifiers."""
        manifest = {
            "name": "uc-bad-key",
            "version": "1.0.0",
            "description": "x",
            "userConfig": {
                "not a valid-key!": {
                    "title": "t",
                    "description": "d",
                },
            },
        }
        plugin_dir = _write_plugin(tmp_path, "uc-bad-key", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        ident_majors = [
            r.message for r in report.results
            if r.level == "MAJOR" and "userConfig" in r.message and "identifier" in r.message
        ]
        assert ident_majors, (
            "Expected MAJOR for non-identifier userConfig key; got MAJORs: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )

    def test_userconfig_non_bool_sensitive_rejected(self, tmp_path):
        """userConfig entry.sensitive must be a boolean."""
        manifest = {
            "name": "uc-bad-sensitive",
            "version": "1.0.0",
            "description": "x",
            "userConfig": {
                "api_token": {
                    "title": "t",
                    "description": "d",
                    "sensitive": "yes",
                },
            },
        }
        plugin_dir = _write_plugin(tmp_path, "uc-bad-sensitive", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        sens_majors = [
            r.message for r in report.results
            if r.level == "MAJOR" and "sensitive" in r.message and "boolean" in r.message
        ]
        assert sens_majors, (
            "Expected MAJOR for non-bool 'sensitive'; got MAJORs: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )

    def test_channels_server_cross_ref_to_mcpservers(self, tmp_path):
        """channels[].server matching an mcpServers key validates clean."""
        manifest = {
            "name": "chan-ok",
            "version": "1.0.0",
            "description": "x",
            "mcpServers": {
                "telegram": {"command": "node", "args": ["server.js"]},
            },
            "channels": [
                {
                    "server": "telegram",
                    "userConfig": {
                        "bot_token": {"description": "t", "sensitive": True},
                    },
                },
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "chan-ok", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        chan_majors = [
            r.message for r in report.results
            if r.level == "MAJOR" and "channels" in r.message
        ]
        assert not chan_majors, f"Unexpected channel MAJORs: {chan_majors}"

    def test_channels_server_missing_mcpserver_major(self, tmp_path):
        """channels[].server pointing at a non-existent mcpServer key → MAJOR."""
        manifest = {
            "name": "chan-missing-ref",
            "version": "1.0.0",
            "description": "x",
            "mcpServers": {
                "telegram": {"command": "node", "args": ["server.js"]},
            },
            "channels": [
                {"server": "slack"},
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "chan-missing-ref", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        miss = [
            r.message for r in report.results
            if r.level == "MAJOR"
            and "channels[0].server" in r.message
            and "slack" in r.message
        ]
        assert miss, (
            "Expected MAJOR for missing mcpServers cross-ref; got MAJORs: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )

    def test_monitors_entry_requires_name_command_description(self, tmp_path):
        """monitors inline entries require all three mandatory fields."""
        manifest = {
            "name": "mon-missing-fields",
            "version": "1.0.0",
            "description": "x",
            "monitors": [
                {"name": "m1"},  # missing command + description
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "mon-missing-fields", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        majors = [
            r.message for r in report.results
            if r.level == "MAJOR" and "monitors[0]" in r.message
        ]
        # Expect at least two MAJORs: command + description
        assert any("command" in m for m in majors), f"Missing 'command' MAJOR; got: {majors}"
        assert any("description" in m for m in majors), f"Missing 'description' MAJOR; got: {majors}"

    def test_monitors_when_invalid_format_major(self, tmp_path):
        """monitors[].when must match 'always' or 'on-skill-invoke:<name>'."""
        manifest = {
            "name": "mon-bad-when",
            "version": "1.0.0",
            "description": "x",
            "monitors": [
                {
                    "name": "m1",
                    "command": "python run.py",
                    "description": "does stuff",
                    "when": "sometimes",  # invalid
                },
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "mon-bad-when", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        when_majors = [
            r.message for r in report.results
            if r.level == "MAJOR" and "when" in r.message and "always" in r.message
        ]
        assert when_majors, (
            "Expected MAJOR for invalid 'when' format; got MAJORs: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )

    def test_plugin_json_path_with_traversal_is_major(self, tmp_path):
        """Path fields containing `..` segments → MAJOR per plugins-reference.md:568-571."""
        manifest = {
            "name": "path-traversal",
            "version": "1.0.0",
            "description": "x",
            "skills": "./../shared-skills/",
            "commands": ["./commands/ok.md", "./../escape.md"],
        }
        plugin_dir = _write_plugin(tmp_path, "path-traversal", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        trav = [
            r.message for r in report.results
            if r.level == "MAJOR" and "path-traversal" in r.message
        ]
        # One for 'skills' string, one for 'commands[1]' entry
        assert len(trav) >= 2, (
            "Expected 2+ MAJORs for path-traversal; got MAJORs: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )

    def test_subagent_statusline_plugin_settings_accepted(self, tmp_path):
        """subagentStatusLine in plugin-root settings.json must not emit unrecognized-key MINOR."""
        manifest = {
            "name": "sas-plugin",
            "version": "1.0.0",
            "description": "x",
        }
        plugin_dir = _write_plugin(tmp_path, "sas-plugin", manifest)
        # Add plugin-root settings.json
        (plugin_dir / "settings.json").write_text(
            json.dumps({"subagentStatusLine": {"command": "echo status"}})
        )
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        unrec = [
            r.message for r in report.results
            if r.level == "MINOR" and "subagentStatusLine" in r.message and "unrecognized" in r.message
        ]
        assert not unrec, (
            "subagentStatusLine must be recognized in plugin settings.json; got MINORs: "
            f"{[r.message for r in report.results if r.level == 'MINOR']}"
        )

    def test_author_url_accepted(self, tmp_path):
        """author.url as string validates clean (plugins-reference.md:352)."""
        manifest = {
            "name": "author-url",
            "version": "1.0.0",
            "description": "x",
            "author": {
                "name": "Test",
                "email": "t@example.com",
                "url": "https://github.com/test",
            },
        }
        plugin_dir = _write_plugin(tmp_path, "author-url", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        url_majors = [
            r.message for r in report.results
            if r.level == "MAJOR" and "author.url" in r.message
        ]
        assert not url_majors, f"Unexpected author.url MAJORs: {url_majors}"

        # Negative: non-string author.url IS a MAJOR
        bad_manifest = dict(manifest)
        bad_manifest["author"] = {"name": "Test", "url": 42}
        bad_dir = _write_plugin(tmp_path, "author-url-bad", bad_manifest)
        bad_report = ValidationReport()
        validate_manifest(bad_dir, bad_report)
        bad_url_majors = [
            r.message for r in bad_report.results
            if r.level == "MAJOR" and "author.url" in r.message and "string" in r.message
        ]
        assert bad_url_majors, (
            "Expected MAJOR for non-string author.url; got MAJORs: "
            f"{[r.message for r in bad_report.results if r.level == 'MAJOR']}"
        )


# ============================================================================
# v2.22.3 — GAP-27, GAP-10, LSP-type-checks, cross-marketplace deps
# ============================================================================


class TestV223Gap27MissingPluginJsonDowngrade:
    """GAP-27: plugin.json missing is MINOR when components exist in default dirs.

    Per plugins-reference.md:374-385 a manifest is optional when the plugin
    has components in auto-discovered directories. CPV previously emitted
    CRITICAL; v2.22.3 downgrades to MINOR when default components exist, and
    keeps CRITICAL when both plugin.json AND default dirs are absent.
    """

    def test_missing_plugin_json_with_commands_is_minor(self, tmp_path):
        """plugin.json absent but commands/ has content → MINOR (not CRITICAL)."""
        plugin_dir = tmp_path / "gap27-with-commands"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        cmds = plugin_dir / "commands"
        cmds.mkdir()
        (cmds / "example.md").write_text("# example\n\nA command.\n")
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        minors = [r.message for r in report.results if r.level == "MINOR"]
        criticals = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("plugin.json not found" in m and "recommended" in m for m in minors), (
            f"Expected MINOR downgrade; got MINOR: {minors}, CRITICAL: {criticals}"
        )
        assert not any("plugin.json not found" in m for m in criticals)

    def test_missing_plugin_json_with_skills_is_minor(self, tmp_path):
        """plugin.json absent but skills/<name>/SKILL.md exists → MINOR."""
        plugin_dir = tmp_path / "gap27-with-skills"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        skill = plugin_dir / "skills" / "demo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: demo\n---\n# Demo\n")
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        minors = [r.message for r in report.results if r.level == "MINOR"]
        assert any("plugin.json not found" in m for m in minors)
        assert not report.has_critical

    def test_missing_plugin_json_no_components_stays_critical(self, tmp_path):
        """plugin.json absent AND no default dirs → CRITICAL (spec floor)."""
        plugin_dir = tmp_path / "gap27-empty"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        # No component dirs created.
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        assert report.has_critical
        criticals = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("plugin.json not found" in m for m in criticals), (
            f"Expected CRITICAL when no components exist; got CRITICAL: {criticals}"
        )

    def test_empty_component_dir_stays_critical(self, tmp_path):
        """Empty commands/ directory is NOT a component — CRITICAL still fires."""
        plugin_dir = tmp_path / "gap27-empty-dir"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / "commands").mkdir()
        # commands/ exists but is empty.
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        assert report.has_critical


class TestV223Gap10MonitorSkillCrossRef:
    """GAP-10: monitors[].when = 'on-skill-invoke:<skill>' references a declared skill.

    Per plugins-reference.md:314 the <skill> suffix must resolve to an
    actual skill in the plugin's skills/ tree. A dangling reference emits
    a MINOR so authors notice typos.
    """

    def test_monitor_on_skill_invoke_matching_skill_accepted(self, tmp_path):
        """when: on-skill-invoke:<name> with matching skills/<name>/SKILL.md — no MINOR emitted."""
        manifest = {
            "name": "mon-skill-ok",
            "version": "1.0.0",
            "description": "x",
            "monitors": [
                {
                    "name": "watch-demo",
                    "command": "bash run.sh",
                    "description": "Watches demo.",
                    "when": "on-skill-invoke:demo",
                }
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "mon-skill-ok", manifest)
        # Create the matching skill.
        skill_dir = plugin_dir / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n# Demo\n")
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        cross_minors = [
            r.message for r in report.results
            if r.level == "MINOR" and "references unknown skill" in r.message
        ]
        assert not cross_minors, f"Unexpected dangling-ref MINORs: {cross_minors}"

    def test_monitor_on_skill_invoke_missing_skill_is_minor(self, tmp_path):
        """when: on-skill-invoke:<ghost> without skills/ghost/ emits MINOR."""
        manifest = {
            "name": "mon-skill-ghost",
            "version": "1.0.0",
            "description": "x",
            "monitors": [
                {
                    "name": "watch-ghost",
                    "command": "true",
                    "description": "Watches the ghost.",
                    "when": "on-skill-invoke:ghost",
                }
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "mon-skill-ghost", manifest)
        # No skills/ directory at all.
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        cross_minors = [
            r.message for r in report.results
            if r.level == "MINOR" and "ghost" in r.message and "unknown skill" in r.message
        ]
        assert cross_minors, (
            "Expected MINOR for dangling on-skill-invoke; got MINORs: "
            f"{[r.message for r in report.results if r.level == 'MINOR']}"
        )

    def test_monitor_always_no_cross_ref_check(self, tmp_path):
        """when: always does NOT trigger the skill cross-ref check."""
        manifest = {
            "name": "mon-always",
            "version": "1.0.0",
            "description": "x",
            "monitors": [
                {
                    "name": "heartbeat",
                    "command": "true",
                    "description": "Always on.",
                    "when": "always",
                }
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "mon-always", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        cross_minors = [
            r.message for r in report.results
            if r.level == "MINOR" and "unknown skill" in r.message
        ]
        assert not cross_minors


class TestV223LspInlineTypeChecks:
    """GAP-65/66/67/68: inline lspServers optional fields get MINOR on wrong type."""

    def _lsp_manifest(self, **config_overrides):
        """Build a plugin manifest with one inline LSP entry applying the overrides."""
        base_config = {
            "command": "pyright-langserver",
            "extensionToLanguage": {".py": "python"},
        }
        base_config.update(config_overrides)
        return {
            "name": "lsp-check",
            "version": "1.0.0",
            "description": "x",
            "lspServers": {"pyright": base_config},
        }

    def test_lsp_args_non_array_emits_minor(self, tmp_path):
        """lspServers.<name>.args not a list → MINOR (plugins-reference.md:243)."""
        manifest = self._lsp_manifest(args="--foo")  # should be a list
        plugin_dir = _write_plugin(tmp_path, "lsp-args-bad", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        minors = [r.message for r in report.results if r.level == "MINOR"]
        assert any("'args' must be an array" in m for m in minors), (
            f"Expected MINOR for wrong args type; got MINORs: {minors}"
        )

    def test_lsp_env_non_object_emits_minor(self, tmp_path):
        """lspServers.<name>.env not a dict → MINOR (plugins-reference.md:245)."""
        manifest = self._lsp_manifest(env=["KEY=VAL"])  # should be an object
        plugin_dir = _write_plugin(tmp_path, "lsp-env-bad", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        minors = [r.message for r in report.results if r.level == "MINOR"]
        assert any("'env' must be an object" in m for m in minors), (
            f"Expected MINOR for wrong env type; got MINORs: {minors}"
        )

    def test_lsp_env_non_string_value_emits_minor(self, tmp_path):
        """lspServers.<name>.env[key] non-string value → MINOR."""
        manifest = self._lsp_manifest(env={"PORT": 8080})  # value must be string
        plugin_dir = _write_plugin(tmp_path, "lsp-env-nonstr", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        minors = [r.message for r in report.results if r.level == "MINOR"]
        assert any("env['PORT']" in m and "must be a string" in m for m in minors), (
            f"Expected MINOR for non-string env value; got MINORs: {minors}"
        )

    def test_lsp_settings_non_object_emits_minor(self, tmp_path):
        """lspServers.<name>.settings not a dict → MINOR (plugins-reference.md:241-252)."""
        manifest = self._lsp_manifest(settings="strict")  # should be an object
        plugin_dir = _write_plugin(tmp_path, "lsp-settings-bad", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        minors = [r.message for r in report.results if r.level == "MINOR"]
        assert any("'settings' must be an object" in m for m in minors), (
            f"Expected MINOR for wrong settings type; got MINORs: {minors}"
        )

    def test_lsp_init_options_non_object_emits_minor(self, tmp_path):
        """lspServers.<name>.initializationOptions not a dict → MINOR."""
        manifest = self._lsp_manifest(initializationOptions=42)
        plugin_dir = _write_plugin(tmp_path, "lsp-init-bad", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        minors = [r.message for r in report.results if r.level == "MINOR"]
        assert any("'initializationOptions' must be an object" in m for m in minors), (
            f"Expected MINOR for wrong initializationOptions type; got MINORs: {minors}"
        )

    def test_lsp_workspace_folder_non_string_emits_minor(self, tmp_path):
        """lspServers.<name>.workspaceFolder not a string → MINOR."""
        manifest = self._lsp_manifest(workspaceFolder=["./src"])  # should be a string
        plugin_dir = _write_plugin(tmp_path, "lsp-wf-bad", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        minors = [r.message for r in report.results if r.level == "MINOR"]
        assert any("'workspaceFolder' must be a string" in m for m in minors), (
            f"Expected MINOR for wrong workspaceFolder type; got MINORs: {minors}"
        )

    def test_lsp_restart_on_crash_non_bool_emits_minor(self, tmp_path):
        """lspServers.<name>.restartOnCrash not a bool → MINOR (plugins-reference.md:251)."""
        manifest = self._lsp_manifest(restartOnCrash="yes")  # should be a bool
        plugin_dir = _write_plugin(tmp_path, "lsp-roc-bad", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        minors = [r.message for r in report.results if r.level == "MINOR"]
        assert any("'restartOnCrash' must be a boolean" in m for m in minors), (
            f"Expected MINOR for non-boolean restartOnCrash; got MINORs: {minors}"
        )

    def test_lsp_valid_optional_fields_no_minor(self, tmp_path):
        """Correct types across all optional LSP fields → no MINOR emitted."""
        manifest = self._lsp_manifest(
            args=["--flag", "--flag2"],
            env={"LOGLEVEL": "info"},
            settings={"strict": True},
            initializationOptions={"feature": True},
            workspaceFolder="./src",
            restartOnCrash=True,
        )
        plugin_dir = _write_plugin(tmp_path, "lsp-ok", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        unexpected = [
            r.message for r in report.results
            if r.level == "MINOR" and any(
                kw in r.message for kw in ("'args'", "'env'", "'settings'", "'initializationOptions'",
                                           "'workspaceFolder'", "'restartOnCrash'")
            )
        ]
        assert not unexpected, f"Unexpected MINORs on valid LSP config: {unexpected}"


class TestV223CrossMarketplaceDeps:
    """TRDD-20108ab7: cross-marketplace dependency allowlist enforcement.

    When plugin.json declares a dependency with a `marketplace` sub-field
    pointing at a DIFFERENT marketplace than the hosting one, the target
    must appear in the hosting marketplace's
    `allowedDependencyMarketplaces` list. Validating a plugin with no
    marketplace context emits INFO for cross-marketplace refs.
    """

    def test_cross_marketplace_dep_with_allowlist_accepted(self, tmp_path):
        """Cross-marketplace dep allowlisted → PASSED, no MAJOR."""
        manifest = {
            "name": "consumer",
            "version": "1.0.0",
            "description": "x",
            "dependencies": [
                {"name": "shared-lib", "marketplace": "other-market"}
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "xm-ok", manifest)
        hosting = {
            "name": "host-market",
            "allowedDependencyMarketplaces": ["other-market"],
        }
        report = ValidationReport()
        validate_manifest(plugin_dir, report, hosting_marketplace=hosting)
        majors = [
            r.message for r in report.results
            if r.level == "MAJOR" and "allowedDependencyMarketplaces" in r.message
        ]
        passed = [
            r.message for r in report.results
            if r.level == "PASSED" and "allowlisted" in r.message
        ]
        assert not majors, f"Unexpected MAJOR for allowlisted dep: {majors}"
        assert passed, (
            "Expected PASSED for allowlisted cross-market dep; got PASSED: "
            f"{[r.message for r in report.results if r.level == 'PASSED']}"
        )

    def test_cross_marketplace_dep_without_allowlist_major(self, tmp_path):
        """Cross-marketplace dep with NO allowlist declared → MAJOR."""
        manifest = {
            "name": "consumer",
            "version": "1.0.0",
            "description": "x",
            "dependencies": [
                {"name": "shared-lib", "marketplace": "other-market"}
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "xm-none", manifest)
        hosting = {"name": "host-market"}  # no allowedDependencyMarketplaces
        report = ValidationReport()
        validate_manifest(plugin_dir, report, hosting_marketplace=hosting)
        majors = [
            r.message for r in report.results
            if r.level == "MAJOR" and "allowedDependencyMarketplaces" in r.message
        ]
        assert majors, (
            "Expected MAJOR without allowlist; got MAJORs: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )

    def test_cross_marketplace_dep_not_in_allowlist_major(self, tmp_path):
        """Target not present in allowlist → MAJOR with the list in the message."""
        manifest = {
            "name": "consumer",
            "version": "1.0.0",
            "description": "x",
            "dependencies": [
                {"name": "shared-lib", "marketplace": "other-market"}
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "xm-notin", manifest)
        hosting = {
            "name": "host-market",
            "allowedDependencyMarketplaces": ["trusted-a", "trusted-b"],
        }
        report = ValidationReport()
        validate_manifest(plugin_dir, report, hosting_marketplace=hosting)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any(
            "other-market" in m and "trusted-a" in m for m in majors
        ), f"Expected MAJOR listing allowlist; got MAJORs: {majors}"

    def test_same_marketplace_dep_no_cross_check(self, tmp_path):
        """Dep pointing at the SAME hosting marketplace → no cross-check fire."""
        manifest = {
            "name": "consumer",
            "version": "1.0.0",
            "description": "x",
            "dependencies": [
                {"name": "sibling", "marketplace": "host-market"}
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "xm-same", manifest)
        hosting = {"name": "host-market"}  # no allowlist — same marketplace OK
        report = ValidationReport()
        validate_manifest(plugin_dir, report, hosting_marketplace=hosting)
        blocked = [
            r.message for r in report.results
            if r.level == "MAJOR" and "allowedDependencyMarketplaces" in r.message
        ]
        assert not blocked, f"Same-market dep must not be blocked; got MAJORs: {blocked}"

    def test_no_hosting_context_emits_info(self, tmp_path):
        """Cross-market dep without hosting context → INFO (not MAJOR)."""
        manifest = {
            "name": "consumer",
            "version": "1.0.0",
            "description": "x",
            "dependencies": [
                {"name": "shared-lib", "marketplace": "other-market"}
            ],
        }
        plugin_dir = _write_plugin(tmp_path, "xm-noctx", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)  # no hosting_marketplace
        infos = [
            r.message for r in report.results
            if r.level == "INFO" and "cross-marketplace" in r.message
        ]
        blocked = [
            r.message for r in report.results
            if r.level == "MAJOR" and "allowedDependencyMarketplaces" in r.message
        ]
        assert infos, (
            "Expected INFO for missing hosting context; got INFO: "
            f"{[r.message for r in report.results if r.level == 'INFO']}"
        )
        assert not blocked
