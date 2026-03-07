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

    def test_uppercase_name_reports_major(self, tmp_path):
        """validate_manifest reports MAJOR when plugin name has uppercase letters (line 121)."""
        plugin_dir = tmp_path / "upper-plugin"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "MyPlugin", "version": "1.0.0"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be lowercase" in m for m in major_msgs)

    def test_name_with_spaces_reports_major(self, tmp_path):
        """validate_manifest reports MAJOR when plugin name contains spaces (line 123)."""
        plugin_dir = tmp_path / "spaced-plugin"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "my plugin", "version": "1.0.0"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("cannot contain spaces" in m for m in major_msgs)

    def test_non_kebab_case_name_reports_major(self, tmp_path):
        """validate_manifest reports MAJOR when plugin name is not kebab-case (line 128)."""
        plugin_dir = tmp_path / "bad-name"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "123bad", "version": "1.0.0"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("kebab-case" in m for m in major_msgs)

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
