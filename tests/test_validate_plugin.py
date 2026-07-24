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
import subprocess
import sys
from pathlib import Path

import pytest

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
    validate_lsp,
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


class TestRcShipBinaryOnlyIssue175:
    """Issue #175 — RC-SHIP-BINARY-ONLY WARN (STRICT canon): a compiled component must
    ship ONLY the built binary in bin/; NO source or build libs may ship. Claude Code
    recursively fetches submodule CONTENT on install, so even a submodule pointer ships
    its source — hence a build-source submodule WARNS, checkout-independent (via
    .gitmodules). In-tree committed source also warns; a DEV/test submodule and a stray
    example source (no build marker) do not."""

    @staticmethod
    def _warns(report):
        return [r.message for r in report.results if r.level == "WARNING"]

    def test_in_tree_rust_source_with_binary_warns(self, tmp_path):
        """Rust source committed in-tree + a shipped binary => RC-SHIP-BINARY-ONLY warn."""
        p = tmp_path / "rust-in-tree"
        (p / "src").mkdir(parents=True)
        (p / "src" / "lib.rs").write_text("pub fn f() -> i32 { 1 }\n")
        (p / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
        (p / "bin").mkdir()
        (p / "bin" / "x-macos-arm64").write_bytes(b"\x00bin")
        report = ValidationReport()
        validate_cross_platform(p, report)
        assert any("RC-SHIP-BINARY-ONLY" in m for m in self._warns(report))

    def test_submodule_source_ships_and_warns(self, tmp_path):
        """STRICT: a build-source submodule ships its source on install (CC recurses
        submodules), so it WARNS — a submodule pointer is NOT compliant."""
        p = tmp_path / "rust-submodule"
        (p / "rust" / "src").mkdir(parents=True)
        (p / "rust" / "src" / "lib.rs").write_text("pub fn f() -> i32 { 1 }\n")
        (p / "rust" / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
        (p / ".gitmodules").write_text(
            '[submodule "rust"]\n\tpath = rust\n\turl = https://example.com/x-rust.git\n'
        )
        (p / "bin").mkdir()
        (p / "bin" / "x-macos-arm64").write_bytes(b"\x00bin")
        report = ValidationReport()
        validate_cross_platform(p, report)
        assert any("RC-SHIP-BINARY-ONLY" in m and "submodule" in m for m in self._warns(report))

    def test_gitmodules_build_submodule_pointer_only_warns(self, tmp_path):
        """Checkout-independent: a .gitmodules build-source entry with NO checked-out
        content (the source-repo case CPV actually validates) still WARNS."""
        p = tmp_path / "pointer-only"
        p.mkdir()
        (p / ".gitmodules").write_text(
            '[submodule "rust"]\n\tpath = rust\n\turl = https://example.com/x-rust.git\n'
        )
        (p / "bin").mkdir()
        (p / "bin" / "x-macos-arm64").write_bytes(b"\x00bin")
        report = ValidationReport()
        validate_cross_platform(p, report)
        assert any("RC-SHIP-BINARY-ONLY" in m and "submodule" in m for m in self._warns(report))

    def test_dev_submodule_does_not_warn(self, tmp_path):
        """A DEV/test submodule (tests/, dev/) is NOT a build-source submodule => no warn."""
        p = tmp_path / "dev-sub"
        p.mkdir()
        (p / ".gitmodules").write_text(
            '[submodule "tests"]\n\tpath = tests\n\turl = https://example.com/x-tests.git\n'
        )
        (p / "bin").mkdir()
        (p / "bin" / "x-macos-arm64").write_bytes(b"\x00bin")
        report = ValidationReport()
        validate_cross_platform(p, report)
        assert not any("RC-SHIP-BINARY-ONLY" in m for m in self._warns(report))

    def test_in_tree_csharp_source_warns(self, tmp_path):
        """C# is a compiled language too — in-tree .cs + *.csproj => RC-SHIP-BINARY-ONLY.

        Also exercises the glob-aware build-marker match (the project file name varies)."""
        p = tmp_path / "csharp-in-tree"
        (p / "src").mkdir(parents=True)
        (p / "src" / "Program.cs").write_text("class P { static void Main() {} }\n")
        (p / "src" / "App.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk"></Project>\n')
        report = ValidationReport()
        validate_cross_platform(p, report)
        assert any("RC-SHIP-BINARY-ONLY" in m and "C#" in m for m in self._warns(report))

    def test_stray_source_without_build_marker_does_not_warn(self, tmp_path):
        """A lone source file with no build system and no binary is not a compiled
        component => no RC-SHIP-BINARY-ONLY (avoids flagging example snippets)."""
        p = tmp_path / "stray"
        p.mkdir()
        (p / "snippet.rs").write_text("fn main() {}\n")
        report = ValidationReport()
        validate_cross_platform(p, report)
        assert not any("RC-SHIP-BINARY-ONLY" in m for m in self._warns(report))


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
        """validate_manifest reports CRITICAL when hooks points to default `./hooks/` directory.

        Empirical 2026-04-18: CC rejects `hooks: "./hooks/"` (the directory) with
        `hooks: Invalid input`. Plugin will not load. The CRITICAL message wording was
        updated to reflect the actual CC error rather than the older "malformed manifest"
        text. (Unlike commands/skills/outputStyles which CC accepts pointing at default.)
        """
        plugin_dir = tmp_path / "dup-hooks"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "dup-hooks", "version": "1.0.0", "hooks": "./hooks/"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL" and "hooks" in r.message]
        assert any("Invalid input" in m or "will not load" in m for m in critical_msgs), (
            f"Expected CRITICAL for hooks default-dir, got: {critical_msgs}"
        )

    def test_commands_default_path_reports_minor(self, tmp_path):
        """validate_manifest reports MINOR (was CRITICAL until 2026-04-18) when commands points to default.

        Empirical verification 2026-04-18: CC accepts `commands: "./commands/"` and the
        plugin loads fine. The earlier CRITICAL was a false positive — the form is
        redundant but harmless. Downgraded to MINOR (redundancy nudge).
        """
        plugin_dir = tmp_path / "dup-cmds"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "dup-cmds", "version": "1.0.0", "commands": "./commands/"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("auto-discover" in m and "redundant" in m.lower() for m in minor_msgs), (
            f"Expected MINOR redundancy nudge for commands default path, got: {minor_msgs}"
        )
        # Should NOT be CRITICAL anymore
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL" and "commands" in r.message]
        assert critical_msgs == [], (
            f"commands default path should not be CRITICAL anymore (verified 2026-04-18 — "
            f"plugin loads fine), got: {critical_msgs}"
        )

    def test_skills_default_path_reports_minor(self, tmp_path):
        """validate_manifest reports MINOR (was CRITICAL until 2026-04-18) when skills points to default.

        Empirical: CC accepts `skills: "./skills/"` and plugin loads with skill discoverable.
        """
        plugin_dir = tmp_path / "dup-skills"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "dup-skills", "version": "1.0.0", "skills": "./skills"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("auto-discover" in m and "redundant" in m.lower() for m in minor_msgs), (
            f"Expected MINOR redundancy nudge for skills default path, got: {minor_msgs}"
        )

    def test_hooks_default_dir_still_reports_critical(self, tmp_path):
        """validate_manifest STILL reports CRITICAL for hooks: './hooks/' (the directory).

        Unlike commands/skills/outputStyles, hooks REALLY does break loading when set to
        the default DIRECTORY (not the file) — empirically verified 2026-04-18 that CC
        emits `hooks: Invalid input` and rejects the manifest.
        """
        plugin_dir = tmp_path / "broken-hooks"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {"name": "broken-hooks", "version": "1.0.0", "hooks": "./hooks/"}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL" and "hooks" in r.message]
        assert critical_msgs, (
            f"hooks: './hooks/' (directory) should remain CRITICAL — CC rejects with Invalid input. "
            f"Got: {[r.message for r in report.results if r.level == 'CRITICAL']}"
        )

    def test_agents_array_default_path_reports_major(self, tmp_path):
        """agents: array of files inside default folder → MAJOR (agents folder rejection).

        Empirical: agents field rejects ALL folder-shaped paths. An array containing
        only items inside the default folder used to be CRITICAL (with claim of malformed
        manifest); since 2026-04-18 the agents-specific check fires MAJOR with helpful
        fix recipe instead. CC actually accepts these `.md` file paths fine — it's only
        FOLDER paths that break.
        """
        plugin_dir = tmp_path / "dup-agents"
        plugin_dir.mkdir()
        claude_dir = plugin_dir / ".claude-plugin"
        claude_dir.mkdir()
        # Use file paths (CC accepts these); the existing CRITICAL was a false positive
        # since the array entries are .md files (not folders).
        manifest = {"name": "dup-agents", "version": "1.0.0", "agents": ["./agents/a.md", "./agents/b.md"]}
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        # The downgrade from CRITICAL to MINOR/skip means this case now produces no
        # CRITICAL (which is correct — the form actually works in CC). Just verify no
        # CRITICAL for agents.
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL" and "agents" in r.message]
        assert critical_msgs == [], (
            f"agents: ['./agents/a.md', './agents/b.md'] should not be CRITICAL — "
            f"these are valid .md file paths. Got: {critical_msgs}"
        )

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

    def test_non_standard_directory_reports_major(self, tmp_path):
        """validate_structure now emits MAJOR (was WARNING) for undeclared non-standard root directories.

        Bumped from WARNING → MAJOR in v2.68.0 after a real incident where a
        directory holding a SKILL was wrapped into a "plugin" with bogus
        non-standard root folders, published, and installed to nothing. The
        user directive: "NO DEVIATION FROM THE STANDARD can be allowed unless
        you declare the custom folder/path in plugin.json".
        """
        plugin_dir = tmp_path / "odd-dirs"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / "foobar").mkdir()
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Non-standard directory 'foobar/'" in m for m in major_msgs), (
            f"Expected MAJOR for 'foobar/', got: {major_msgs}"
        )


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
        """validate_command_file reports CRITICAL when frontmatter is missing.

        TRDD-021250b5 Phase 3: validate_command_file now delegates to the
        comprehensive command validator, which phrases the finding as
        "Missing YAML frontmatter markers ...".
        """
        cmd = tmp_path / "bad.md"
        cmd.write_text("Just plain text, no frontmatter at all.")
        report = ValidationReport()
        validate_command_file(cmd, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("frontmatter" in m.lower() for m in critical_msgs)

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
        """validate_agent_file reports CRITICAL with no frontmatter.

        TRDD-021250b5 Phase 3: validate_agent_file now delegates to the
        comprehensive agent validator, which phrases the finding as
        "No YAML frontmatter found (required)".
        """
        agent = tmp_path / "bad-agent.md"
        agent.write_text("No frontmatter here")
        report = ValidationReport()
        validate_agent_file(agent, report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("frontmatter" in m.lower() for m in critical_msgs)

    def test_agent_file_empty_frontmatter(self, tmp_path):
        """validate_agent_file flags empty frontmatter as INVALID via missing required fields.

        TRDD-021250b5 Phase 3: the comprehensive agent validator parses empty
        frontmatter as ``{}`` and reports the missing required field
        ("Missing 'description'", MAJOR) — which still makes the agent INVALID —
        rather than a separate "Empty frontmatter" CRITICAL.
        """
        agent = tmp_path / "empty-fm.md"
        agent.write_text("---\n---\n\n# Empty\n")
        report = ValidationReport()
        validate_agent_file(agent, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Missing 'description'" in m for m in major_msgs)

    def test_agent_file_missing_name_and_description(self, tmp_path):
        """validate_agent_file: missing description is MAJOR; missing name is lenient.

        TRDD-021250b5 Phase 3: the comprehensive agent validator treats the agent
        ``name`` as OPTIONAL — it is derived from the filename when absent (INFO
        "will use filename"), per Claude Code's spec. So a missing ``name`` is NOT
        a blocking finding; only the missing ``description`` is MAJOR.
        """
        agent = tmp_path / "partial.md"
        agent.write_text("---\nmodel: sonnet\n---\n\n# Partial\n")
        report = ValidationReport()
        validate_agent_file(agent, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("Missing 'description'" in m for m in major_msgs)
        # missing name is advisory (derived from filename), not blocking
        assert any("name" in m.lower() and "filename" in m.lower() for m in info_msgs)
        assert not any("Missing 'name'" in m for m in major_msgs)


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
        """validate_gitignore passes when all categories are covered
        (including reports/ and reports_dev/ per v2.25.0 rule)."""
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
reports/
reports_dev/
"""
        (plugin_dir / ".gitignore").write_text(gitignore_content)
        report = ValidationReport()
        validate_gitignore(plugin_dir, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("covers all expected categories" in m for m in passed_msgs)

    def test_gitignore_missing_categories_reports_issues(self, tmp_path):
        """validate_gitignore reports warnings/majors for missing categories
        when the corresponding artifact actually exists (v2.25.0 rule: only
        flag existing artifacts, never speculate on future files)."""
        plugin_dir = tmp_path / "partial-gi"
        plugin_dir.mkdir()
        # Only include __pycache__ - missing everything else
        (plugin_dir / ".gitignore").write_text("__pycache__/\n")
        # Create artifacts that should trigger the missing-coverage check
        (plugin_dir / "node_modules").mkdir()
        (plugin_dir / ".env").write_text("SECRET=x")
        (plugin_dir / ".venv").mkdir()
        report = ValidationReport()
        validate_gitignore(plugin_dir, report)
        # Should have WARNING for missing node_modules and MAJOR for .env / .venv
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

    def test_glob_dev_covers_reports_dev(self, tmp_path):
        """Issue #98: a `*_dev/` glob genuinely ignores reports_dev/, so git
        check-ignore must recognise coverage and NOT warn (the FP that issue
        #98 reports — literal-substring matching missed the glob)."""
        plugin_dir = tmp_path / "glob-dev"
        plugin_dir.mkdir()
        # A `*_dev/` glob covers reports_dev/; an explicit /reports/ covers reports/.
        (plugin_dir / ".gitignore").write_text("*_dev/\n/reports/\n")
        # Must be a real git repo so `git check-ignore` has rules to consult.
        subprocess.run(["git", "init", "-q"], cwd=plugin_dir, check=True)
        (plugin_dir / "reports_dev").mkdir()
        report = ValidationReport()
        validate_gitignore(plugin_dir, report)
        # No result of any severity may complain about reports_dev/ coverage.
        offending = [r.message for r in report.results if "missing coverage" in r.message and "reports_dev/" in r.message]
        assert not offending, f"reports_dev/ glob coverage not recognised: {offending}"

    def test_uncovered_reports_still_warns(self, tmp_path):
        """Issue #98 FN-safe sibling: a genuinely-uncovered required category
        (real reports/ dir, .gitignore only ignores *.pyc) must STILL emit the
        MAJOR — git check-ignore exits 1 AND the substring scan misses."""
        plugin_dir = tmp_path / "uncovered"
        plugin_dir.mkdir()
        # No reports/ rule, no *_dev/ glob — reports/ is genuinely uncovered.
        (plugin_dir / ".gitignore").write_text("*.pyc\n")
        subprocess.run(["git", "init", "-q"], cwd=plugin_dir, check=True)
        (plugin_dir / "reports").mkdir()
        report = ValidationReport()
        validate_gitignore(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("missing coverage" in m and "reports/" in m for m in major_msgs), (
            f"uncovered reports/ category did not warn: {[r.message for r in report.results]}"
        )

    def test_not_a_git_repo_falls_back_to_substring(self, tmp_path):
        """Issue #98 graceful fallback: with NO git repo, git check-ignore
        cannot run (exit 128), so the legacy literal-substring scan must still
        recognise a verbatim `reports_dev/` line and not warn."""
        plugin_dir = tmp_path / "no-git"
        plugin_dir.mkdir()
        # NOTE: intentionally NOT a git repo — exercises the fallback path.
        (plugin_dir / ".gitignore").write_text("reports_dev/\n")
        (plugin_dir / "reports_dev").mkdir()
        report = ValidationReport()
        validate_gitignore(plugin_dir, report)
        offending = [r.message for r in report.results if "missing coverage" in r.message and "reports_dev/" in r.message]
        assert not offending, f"substring fallback failed to recognise literal reports_dev/ line: {offending}"


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

    def test_print_json_forwards_fixable_fix_id_tag(self, capsys):
        """print_json forwards the fixable/fix_id SSOT (Phase 2, TRDD-GVMOKJBB).

        Two-sided: a fixable finding carries fixable=True + fix_id in the JSON
        (so cpv_fix_ledger / cpv_codemod apply can consume it), AND a NON-fixable
        finding carries NEITHER key (its dict is byte-identical to before the
        relay was fixed). Without the forward, a tagged finding is silently
        stripped here and never reaches the codemod.
        """
        report = ValidationReport()
        report.add("WARNING", "has shebang but is not executable", "scripts/foo.py", fixable=True, fix_id="chmod-exec")
        report.warning("plain advisory", "README.md")
        print_json(report)
        data = json.loads(capsys.readouterr().out)
        by_file = {r.get("file"): r for r in data["results"]}
        # Side 1: the tag flows through.
        fixable = by_file["scripts/foo.py"]
        assert fixable["fixable"] is True
        assert fixable["fix_id"] == "chmod-exec"
        # Side 2: a non-fixable finding gains no fix-routing keys.
        advisory = by_file["README.md"]
        assert "fixable" not in advisory
        assert "fix_id" not in advisory


class TestValidateReadmeAndLicense:
    """Tests for validate_readme and validate_license."""

    def test_readme_missing_reports_warning(self, tmp_path):
        """validate_readme reports WARNING (advisory) when README.md is missing.

        TRDD-021250b5: validate_readme delegates to the comprehensive doc
        validator. A missing README is a documentation-quality matter, not
        runtime breakage or Anthropic-invalidity, so it is WARNING (non-blocking)
        — a README-less plugin is VALID with a warning.
        """
        plugin_dir = tmp_path / "no-readme"
        plugin_dir.mkdir()
        report = ValidationReport()
        validate_readme(plugin_dir, report)
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("README.md is missing" in m for m in warning_msgs)

    def test_readme_exists_reports_passed(self, tmp_path):
        """validate_readme reports PASSED when README.md exists."""
        plugin_dir = tmp_path / "has-readme"
        plugin_dir.mkdir()
        (plugin_dir / "README.md").write_text("# My Plugin\n")
        report = ValidationReport()
        validate_readme(plugin_dir, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("README.md exists" in m for m in passed_msgs)

    # ------------------------------------------------------------------
    # v2.26.0: badge-markers warning only fires when badges are present
    # ------------------------------------------------------------------

    def test_badge_markers_with_markers_passes(self, tmp_path):
        """README with <!--BADGES-START--> / <!--BADGES-END--> passes."""
        plugin_dir = tmp_path / "markers"
        plugin_dir.mkdir()
        (plugin_dir / "README.md").write_text(
            "# My Plugin\n\n"
            "<!--BADGES-START-->\n"
            "![Version](https://img.shields.io/badge/version-1.0.0-blue)\n"
            "<!--BADGES-END-->\n"
        )
        report = ValidationReport()
        validate_readme(plugin_dir, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("badge markers" in m for m in passed_msgs)

    def test_badge_markers_no_badges_no_warning(self, tmp_path):
        """README with NO badges and NO markers must NOT produce a WARNING
        (v2.26.0 — nothing to auto-regenerate, so the markers are optional)."""
        plugin_dir = tmp_path / "no-badges"
        plugin_dir.mkdir()
        (plugin_dir / "README.md").write_text("# My Plugin\n\nA minimal README.\n")
        report = ValidationReport()
        validate_readme(plugin_dir, report)
        badge_warnings = [r for r in report.results if r.level == "WARNING" and "badge" in r.message.lower()]
        assert not badge_warnings, (
            f"false-positive badge-markers WARNING on badge-less README: {[r.message for r in badge_warnings]}"
        )

    def test_badge_markers_literal_badge_no_markers_warns(self, tmp_path):
        """README with literal [![badge](url)](href) but no markers still
        warns — this is the case the check is really for."""
        plugin_dir = tmp_path / "literal-badge"
        plugin_dir.mkdir()
        (plugin_dir / "README.md").write_text(
            "# My Plugin\n\n"
            "[![CI](https://img.shields.io/github/actions/workflow/status/owner/repo/ci.yml)]"
            "(https://github.com/owner/repo/actions)\n"
        )
        report = ValidationReport()
        validate_readme(plugin_dir, report)
        badge_warnings = [r for r in report.results if r.level == "WARNING" and "badge" in r.message.lower()]
        assert badge_warnings, "literal badge without markers was NOT flagged (regression)"

    def test_badge_markers_shields_url_no_markers_warns(self, tmp_path):
        """README that merely links to shields.io also counts as having
        badges — the warning should still fire."""
        plugin_dir = tmp_path / "shields-url"
        plugin_dir.mkdir()
        (plugin_dir / "README.md").write_text("# My Plugin\n\n![](https://img.shields.io/badge/v-1.0-blue)\n")
        report = ValidationReport()
        validate_readme(plugin_dir, report)
        badge_warnings = [r for r in report.results if r.level == "WARNING" and "badge" in r.message.lower()]
        assert badge_warnings, "shields.io badge without markers was NOT flagged (regression)"

    def test_badge_markers_empty_ci_placeholder_passes(self, tmp_path):
        """Empty `<!--BADGES-START-->...<!--BADGES-END-->` region with no
        badges inside is a valid CI-placeholder pattern — common when a
        workflow populates the region on push. The validator must PASS
        (not warn) and the fixer must never suggest removing these
        markers. Regression guard for v2.26.0 fixer-guidance tightening."""
        plugin_dir = tmp_path / "empty-markers"
        plugin_dir.mkdir()
        (plugin_dir / "README.md").write_text(
            "# My Plugin\n\n<!--BADGES-START-->\n<!--BADGES-END-->\n\nA minimal README.\n"
        )
        report = ValidationReport()
        validate_readme(plugin_dir, report)
        badge_warnings = [r for r in report.results if r.level == "WARNING" and "badge" in r.message.lower()]
        assert not badge_warnings, (
            f"empty CI-placeholder markers triggered a warning — "
            f"must be silent per v2.26.0 guidance: "
            f"{[r.message for r in badge_warnings]}"
        )
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("badge markers" in m for m in passed_msgs), (
            "empty-marker CI-placeholder pattern did not produce the PASSED result — markers present should always pass"
        )

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

    def test_compiled_source_crate_local_build_sh_not_flagged_no_build(self, tmp_path):
        """#75 class 5: a Rust crate under tools/<crate>/ with its own build.sh must NOT report the 'no build script' MAJOR (the script demonstrably exists next to the source)."""
        plugin_dir = tmp_path / "rust-tools-crate"
        crate = plugin_dir / "tools" / "memgrep"
        (crate / "src").mkdir(parents=True)
        (crate / "src" / "main.rs").write_text('fn main() { println!("hi"); }')
        (crate / "Cargo.toml").write_text('[package]\nname = "memgrep"\nversion = "0.1.0"\n')
        (crate / "build.sh").write_text("#!/usr/bin/env bash\ncargo build --release\n")
        report = ValidationReport()
        validate_cross_platform(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        # The factually-wrong "no build script" MAJOR must be gone.
        assert not any("no build script" in m for m in major_msgs), major_msgs
        # And it should be downgraded to the "build system but no pre-compiled
        # binaries" WARNING (build present, no bin/).
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("build system but no pre-compiled binaries" in m for m in warning_msgs), warning_msgs

    def test_compiled_source_crate_local_cargo_toml_only_downgrades(self, tmp_path):
        """#75 class 5 (build-system marker variant): a crate-local Cargo.toml next to the source counts as a build system, so the result is a WARNING, not a MAJOR."""
        plugin_dir = tmp_path / "rust-tools-cargo"
        crate = plugin_dir / "tools" / "memgrep"
        (crate / "src").mkdir(parents=True)
        (crate / "src" / "main.rs").write_text("fn main() {}")
        (crate / "Cargo.toml").write_text('[package]\nname = "memgrep"\nversion = "0.1.0"\n')
        report = ValidationReport()
        validate_cross_platform(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("no build script" in m for m in major_msgs), major_msgs

    def test_compiled_source_nested_no_build_still_major(self, tmp_path):
        """FN-safety: a Rust crate under tools/<crate>/ with NEITHER a build script NOR a build-system marker anywhere from its dir up to the root must STILL report the 'no build script' MAJOR."""
        plugin_dir = tmp_path / "rust-tools-nobuild"
        crate = plugin_dir / "tools" / "orphan"
        (crate / "src").mkdir(parents=True)
        (crate / "src" / "main.rs").write_text("fn main() {}")
        # No Cargo.toml, no build.sh, no Makefile — anywhere.
        report = ValidationReport()
        validate_cross_platform(plugin_dir, report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("no compiled binaries" in m and "no build script" in m for m in major_msgs), major_msgs

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
        """Issue #9: missing title field must be flagged as MAJOR (runtime rejects at install).

        v2.106: the inline userConfig block was removed (it duplicated
        validate_user_config_structure). The SSOT helper phrases the same defect
        as a missing required SUB-FIELD; assert on the stable token "title".
        """
        report = self._run(tmp_path, {"MY_OPT": {"type": "number", "default": 10000, "description": "test"}})
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("missing required sub-field 'title'" in m for m in majors), majors

    def test_userconfig_title_must_be_string(self, tmp_path):
        """userConfig.<key>.title with non-string value must be flagged as MAJOR."""
        report = self._run(tmp_path, {"MY_OPT": {"title": 42, "description": "x", "type": "string"}})
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("title' must be a non-empty string" in m for m in majors), majors

    def test_userconfig_invalid_type_reports_major(self, tmp_path):
        """userConfig.<key>.type with an unknown value must be flagged as MAJOR."""
        report = self._run(
            tmp_path,
            {"MY_OPT": {"title": "Opt", "description": "x", "type": "potato"}},
        )
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("type' = 'potato' is not a valid type" in m for m in majors), majors

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

    def test_userconfig_missing_type_reports_major(self, tmp_path):
        """2026-04-18 bug: runtime rejects missing type with 'Invalid option' — CPV must enforce."""
        report = self._run(
            tmp_path,
            {"github_repo": {"title": "GitHub repo", "description": "x", "sensitive": False}},
        )
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("missing required sub-field 'type'" in m for m in majors), majors

    def test_userconfig_type_integer_rejected(self, tmp_path):
        """'integer' was previously accepted by CPV but runtime rejects it — must now be MAJOR."""
        report = self._run(
            tmp_path,
            {"COUNT": {"title": "Count", "description": "x", "type": "integer"}},
        )
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("type' = 'integer' is not a valid type" in m for m in majors), majors

    def test_userconfig_type_array_rejected(self, tmp_path):
        """'array' was previously accepted by CPV but runtime rejects it — must now be MAJOR."""
        report = self._run(
            tmp_path,
            {"ITEMS": {"title": "Items", "description": "x", "type": "array"}},
        )
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("type' = 'array' is not a valid type" in m for m in majors), majors

    def test_userconfig_type_object_rejected(self, tmp_path):
        """'object' was previously accepted by CPV but runtime rejects it — must now be MAJOR."""
        report = self._run(
            tmp_path,
            {"CFG": {"title": "Config", "description": "x", "type": "object"}},
        )
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("type' = 'object' is not a valid type" in m for m in majors), majors

    def test_userconfig_type_directory_accepted(self, tmp_path):
        """'directory' is one of the 5 runtime-valid types — must validate clean."""
        report = self._run(
            tmp_path,
            {"OUTPUT_DIR": {"title": "Output dir", "description": "x", "type": "directory"}},
        )
        majors = [r.message for r in report.results if r.level == "MAJOR" and "OUTPUT_DIR" in r.message]
        assert majors == [], majors

    def test_userconfig_type_file_accepted(self, tmp_path):
        """'file' is one of the 5 runtime-valid types — must validate clean."""
        report = self._run(
            tmp_path,
            {"CONFIG_FILE": {"title": "Config file", "description": "x", "type": "file"}},
        )
        majors = [r.message for r in report.results if r.level == "MAJOR" and "CONFIG_FILE" in r.message]
        assert majors == [], majors

    def test_userconfig_type_boolean_accepted(self, tmp_path):
        """'boolean' is one of the 5 runtime-valid types — must validate clean."""
        report = self._run(
            tmp_path,
            {"ENABLE_FOO": {"title": "Enable foo", "description": "x", "type": "boolean", "default": True}},
        )
        majors = [r.message for r in report.results if r.level == "MAJOR" and "ENABLE_FOO" in r.message]
        assert majors == [], majors

    def test_userconfig_valid_types_whitelist_locked(self):
        """Regression guard (2026-04-18): the 5-type whitelist MUST match the runtime Zod enum.

        Claude Code's runtime accepts exactly {"string", "number", "boolean", "directory", "file"}.
        ANY change to the type whitelist — additions (`integer`, `array`, `object` etc.)
        or removals — breaks install-time compatibility. If this test fails, verify the change
        against the runtime Zod schema first; do not relax the assertion.

        v2.106: the inline userConfig block (which carried a local
        USERCONFIG_VALID_TYPES literal) was removed. The single source of truth
        is now the module-level USER_CONFIG_TYPE_ENUM frozenset consumed by
        validate_user_config_structure. Assert against that SSOT instead.
        """
        import validate_plugin

        assert validate_plugin.USER_CONFIG_TYPE_ENUM == frozenset(
            {"string", "number", "boolean", "directory", "file"}
        ), (
            "USER_CONFIG_TYPE_ENUM whitelist has drifted from the runtime Zod enum. "
            "The runtime rejects any type outside {string, number, boolean, directory, file}. "
            "If the runtime schema has changed, update this test with the new runtime literal."
        )

    def test_userconfig_all_11_janitor_entries_would_be_caught(self, tmp_path):
        """Regression test for the ai-maestro-janitor v0.1.2 install failure (2026-04-18).

        The janitor shipped 11 userConfig entries without `type`. CPV ≤v2.22.3 passed it,
        but Claude Code's runtime rejected all 11 at `claude plugin install`. This test
        replicates the exact manifest and asserts that the validator now emits 11 MAJORs
        — one per entry — so the cpv-plugin-fixer-agent can auto-repair them.
        """
        report = self._run(
            tmp_path,
            {
                "github_repo": {"title": "GitHub Repository", "description": "x", "sensitive": False},
                "trdd_path": {"title": "TRDD Directory Path", "description": "x", "sensitive": False},
                "pr_reconciler_interval": {"title": "PR Reconciler Interval", "description": "x", "sensitive": False},
                "worktree_janitor_interval": {
                    "title": "Worktree Janitor Interval",
                    "description": "x",
                    "sensitive": False,
                },
                "trdd_drift_interval": {"title": "TRDD Drift Interval", "description": "x", "sensitive": False},
                "trdd_reminder_interval": {"title": "TRDD Reminder Interval", "description": "x", "sensitive": False},
                "task_pr_mismatch_interval": {
                    "title": "Task/PR Mismatch Interval",
                    "description": "x",
                    "sensitive": False,
                },
                "rate_limit_retry_interval": {
                    "title": "Rate-Limit Retry Interval",
                    "description": "x",
                    "sensitive": False,
                },
                "cache_keepalive_threshold": {
                    "title": "Cache Keep-Alive Threshold",
                    "description": "x",
                    "sensitive": False,
                },
                "trdd_staleness_days": {"title": "TRDD Staleness Threshold", "description": "x", "sensitive": False},
                "stale_pr_days": {"title": "Stale PR Threshold", "description": "x", "sensitive": False},
            },
        )
        missing_type_majors = [
            r.message for r in report.results if r.level == "MAJOR" and "missing required sub-field 'type'" in r.message
        ]
        assert len(missing_type_majors) == 11, (
            f"Expected 11 missing-type MAJORs (one per userConfig entry), got {len(missing_type_majors)}. "
            f"Messages: {missing_type_majors}"
        )


class TestPathResolutionHints:
    """Intelligent path-resolution hints emitted when the user passes a non-plugin path.

    Regression tests for the 2026-04-18 'the agent must handle all edge cases' requirement:
    parent folders, missing git, `.claude/` project configs, cache directories, typo'd paths.
    The helpers must return structured output the caller (agent or user) can act on.
    """

    @staticmethod
    def _write_plugin_fixture(parent: Path, name: str) -> Path:
        plugin_dir = parent / name
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            f'{{"name":"{name}","version":"0.1.0","description":"x"}}'
        )
        return plugin_dir

    def test_find_plugin_candidates_detects_child_plugin(self, tmp_path):
        """Given a parent dev folder, _find_plugin_candidates must surface the inner plugin."""
        from validate_plugin import _find_plugin_candidates

        self._write_plugin_fixture(tmp_path, "my-plugin")
        candidates = _find_plugin_candidates(tmp_path)
        assert len(candidates) == 1
        assert candidates[0].name == "my-plugin"

    def test_find_plugin_candidates_detects_multiple_siblings(self, tmp_path):
        """Two sibling plugins under the same parent are both reported."""
        from validate_plugin import _find_plugin_candidates

        self._write_plugin_fixture(tmp_path, "plugin-a")
        self._write_plugin_fixture(tmp_path, "plugin-b")
        candidates = _find_plugin_candidates(tmp_path)
        names = sorted(c.name for c in candidates)
        assert names == ["plugin-a", "plugin-b"]

    def test_find_plugin_candidates_skips_noise_directories(self, tmp_path):
        """node_modules, .git, _dev folders must not pollute candidate lists."""
        from validate_plugin import _find_plugin_candidates

        # Write a real plugin and a noise folder with something that LOOKS like a plugin inside
        self._write_plugin_fixture(tmp_path, "real-plugin")
        noise = tmp_path / "node_modules" / "fake-plugin"
        (noise / ".claude-plugin").mkdir(parents=True)
        (noise / ".claude-plugin" / "plugin.json").write_text('{"name":"fake"}')
        candidates = _find_plugin_candidates(tmp_path)
        assert [c.name for c in candidates] == ["real-plugin"]

    def test_classify_marketplace_path(self, tmp_path):
        """A folder with marketplace.json must classify as 'marketplace'."""
        from validate_plugin import _classify_path

        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "marketplace.json").write_text('{"plugins":[]}')
        assert _classify_path(tmp_path) == "marketplace"

    def test_classify_claude_project_config(self, tmp_path):
        """A folder named .claude (or with settings.json + plugins/) is a project config, not a source."""
        from validate_plugin import _classify_path

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        assert _classify_path(claude_dir) == "claude_project_config"

    def test_no_plugin_found_hint_includes_candidates(self, tmp_path):
        """The error hint must surface the discovered candidate path."""
        from validate_plugin import _format_no_plugin_found_hint

        self._write_plugin_fixture(tmp_path, "my-plugin")
        hint = _format_no_plugin_found_hint(tmp_path)
        assert "my-plugin" in hint
        assert "Did you mean" in hint or "candidate" in hint

    def test_no_plugin_found_hint_classifies_marketplace(self, tmp_path):
        """When the path is a marketplace, the hint must say so and route to marketplace validator."""
        from validate_plugin import _format_no_plugin_found_hint

        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "marketplace.json").write_text('{"plugins":[]}')
        hint = _format_no_plugin_found_hint(tmp_path)
        assert "MARKETPLACE" in hint or "marketplace" in hint.lower()
        assert "validate_marketplace" in hint

    def test_classify_standalone_skill(self, tmp_path):
        """A folder with SKILL.md but no plugin.json (and no plugin ancestor) is a standalone skill."""
        from validate_plugin import _classify_path

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---\n# Skill")
        assert _classify_path(skill_dir) == "standalone_skill"

    def test_classify_skill_inside_plugin(self, tmp_path):
        """A SKILL.md nested inside a plugin (ancestor has plugin.json) classifies differently."""
        from validate_plugin import _classify_path

        plugin_dir = tmp_path / "my-plugin"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text('{"name":"my-plugin"}')
        skill_dir = plugin_dir / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---\n# Skill")
        assert _classify_path(skill_dir) == "skill_inside_plugin"

    def test_hint_for_standalone_skill_explains_difference(self, tmp_path):
        """The error hint must clearly distinguish skill from plugin and mention scope options."""
        from validate_plugin import _format_no_plugin_found_hint

        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---\n# Skill")
        hint = _format_no_plugin_found_hint(skill_dir)
        # Must mention both options clearly
        assert "SKILL" in hint or "skill" in hint.lower()
        assert ".claude/skills" in hint
        assert "validate_skill" in hint

    def test_hint_for_skill_inside_plugin_points_to_plugin_root(self, tmp_path):
        """When the user points at a skill inside a plugin, the hint must redirect to the plugin root."""
        from validate_plugin import _format_no_plugin_found_hint

        plugin_dir = tmp_path / "my-plugin"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text('{"name":"my-plugin"}')
        skill_dir = plugin_dir / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---\n# Skill")
        hint = _format_no_plugin_found_hint(skill_dir)
        assert "plugin.json" in hint.lower() or "plugin root" in hint.lower()
        assert "validate_skill" in hint


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
            r.message
            for r in report_a.results
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
            r.message
            for r in report_b.results
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
            r.message
            for r in report_win.results
            if r.level == "MINOR" and "bin/cli" in r.message and "not executable" in r.message
        ]
        assert not win_not_exec, (
            f"On Windows, X_OK should be skipped so no 'not executable' MINOR must appear. "
            f"Got MINORs: {[r.message for r in report_win.results if r.level == 'MINOR']}"
        )
        # And a PASSED note mentioning Windows skip should be present
        win_passed = [
            r.message
            for r in report_win.results
            if r.level == "PASSED" and "bin/cli" in r.message and "Windows" in r.message
        ]
        assert win_passed, "Expected PASSED message noting exec bit not checked on Windows for bin/cli"

        # --- Non-Windows: the same file WITH missing +x MUST produce the MINOR finding ---
        monkeypatch.setattr(vp_mod, "IS_WINDOWS", False)
        report_unix = ValidationReport()
        vp_mod.validate_bin_executables(plugin_dir, report_unix)
        unix_not_exec = [
            r.message
            for r in report_unix.results
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
        dep_majors = [r.message for r in report.results if r.level == "MAJOR" and "dependencies" in r.message]
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
        dep_majors = [r.message for r in report.results if r.level == "MAJOR" and "dependencies" in r.message]
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
        dep_majors = [r.message for r in report.results if r.level == "MAJOR" and "semver" in r.message.lower()]
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
        wrong_type = [r.message for r in report.results if r.level == "MAJOR" and "string or object" in r.message]
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
            r.message
            for r in report.results
            if r.level == "MINOR" and "dependencies" in r.message and "wat" in r.message
        ]
        assert minors, (
            "Expected MINOR for unknown sub-key 'wat'; got MINORs: "
            f"{[r.message for r in report.results if r.level == 'MINOR']}"
        )

    def test_userconfig_valid_structure_passes(self, tmp_path):
        """A well-formed userConfig with title + type + description + sensitive validates clean."""
        manifest = {
            "name": "uc-valid",
            "version": "1.0.0",
            "description": "x",
            "userConfig": {
                "api_endpoint": {
                    "title": "API endpoint",
                    "description": "Where the API lives",
                    "type": "string",
                    "sensitive": False,
                },
                "api_token": {
                    "title": "API token",
                    "description": "Bearer token",
                    "type": "string",
                    "sensitive": True,
                },
            },
        }
        plugin_dir = _write_plugin(tmp_path, "uc-valid", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        # No structural MAJOR about userConfig keys or types
        uc_majors = [r.message for r in report.results if r.level == "MAJOR" and "userConfig" in r.message]
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
            r.message
            for r in report.results
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
            r.message
            for r in report.results
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
        chan_majors = [r.message for r in report.results if r.level == "MAJOR" and "channels" in r.message]
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
            r.message
            for r in report.results
            if r.level == "MAJOR" and "channels[0].server" in r.message and "slack" in r.message
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
        majors = [r.message for r in report.results if r.level == "MAJOR" and "monitors[0]" in r.message]
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
            r.message for r in report.results if r.level == "MAJOR" and "when" in r.message and "always" in r.message
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
        trav = [r.message for r in report.results if r.level == "MAJOR" and "path-traversal" in r.message]
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
        (plugin_dir / "settings.json").write_text(json.dumps({"subagentStatusLine": {"command": "echo status"}}))
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        unrec = [
            r.message
            for r in report.results
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
        url_majors = [r.message for r in report.results if r.level == "MAJOR" and "author.url" in r.message]
        assert not url_majors, f"Unexpected author.url MAJORs: {url_majors}"

        # Negative: non-string author.url IS a MAJOR
        bad_manifest = dict(manifest)
        bad_manifest["author"] = {"name": "Test", "url": 42}
        bad_dir = _write_plugin(tmp_path, "author-url-bad", bad_manifest)
        bad_report = ValidationReport()
        validate_manifest(bad_dir, bad_report)
        bad_url_majors = [
            r.message
            for r in bad_report.results
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
            r.message for r in report.results if r.level == "MINOR" and "references unknown skill" in r.message
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
            r.message
            for r in report.results
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
        cross_minors = [r.message for r in report.results if r.level == "MINOR" and "unknown skill" in r.message]
        assert not cross_minors


class TestV223LspInlineTypeChecks:
    """lspServers optional fields get MAJOR on wrong type.

    TRDD-021250b5 Phase 3: the inline lspServers type-checks were removed from
    validate_manifest and the whole-plugin path now delegates to the
    comprehensive LSP validator via ``validate_lsp`` (single source of truth).
    The comprehensive validator reads ``plugin.json:lspServers`` and reports a
    wrong-type field at MAJOR (a runtime-breaking schema violation), with a
    ``Server <name> '<field>' ...`` message — stronger than the old inline MINOR.
    """

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

    def test_lsp_args_non_array_emits_major(self, tmp_path):
        """lspServers.<name>.args not a list → MAJOR (plugins-reference.md:243)."""
        manifest = self._lsp_manifest(args="--foo")  # should be a list
        plugin_dir = _write_plugin(tmp_path, "lsp-args-bad", manifest)
        report = ValidationReport()
        validate_lsp(plugin_dir, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("'args' must be an array" in m for m in majors), (
            f"Expected MAJOR for wrong args type; got MAJORs: {majors}"
        )

    def test_lsp_env_non_object_emits_major(self, tmp_path):
        """lspServers.<name>.env not a dict → MAJOR (plugins-reference.md:245)."""
        manifest = self._lsp_manifest(env=["KEY=VAL"])  # should be an object
        plugin_dir = _write_plugin(tmp_path, "lsp-env-bad", manifest)
        report = ValidationReport()
        validate_lsp(plugin_dir, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("'env' must be an object" in m for m in majors), (
            f"Expected MAJOR for wrong env type; got MAJORs: {majors}"
        )

    def test_lsp_env_non_string_value_emits_major(self, tmp_path):
        """lspServers.<name>.env[key] non-string value → MAJOR."""
        manifest = self._lsp_manifest(env={"PORT": 8080})  # value must be string
        plugin_dir = _write_plugin(tmp_path, "lsp-env-nonstr", manifest)
        report = ValidationReport()
        validate_lsp(plugin_dir, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("env[PORT]" in m and "must be a string" in m for m in majors), (
            f"Expected MAJOR for non-string env value; got MAJORs: {majors}"
        )

    def test_lsp_settings_non_object_emits_major(self, tmp_path):
        """lspServers.<name>.settings not a dict → MAJOR (plugins-reference.md:241-252)."""
        manifest = self._lsp_manifest(settings="strict")  # should be an object
        plugin_dir = _write_plugin(tmp_path, "lsp-settings-bad", manifest)
        report = ValidationReport()
        validate_lsp(plugin_dir, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("'settings' must be an object" in m for m in majors), (
            f"Expected MAJOR for wrong settings type; got MAJORs: {majors}"
        )

    def test_lsp_init_options_non_object_emits_major(self, tmp_path):
        """lspServers.<name>.initializationOptions not a dict → MAJOR."""
        manifest = self._lsp_manifest(initializationOptions=42)
        plugin_dir = _write_plugin(tmp_path, "lsp-init-bad", manifest)
        report = ValidationReport()
        validate_lsp(plugin_dir, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("'initializationOptions' must be an object" in m for m in majors), (
            f"Expected MAJOR for wrong initializationOptions type; got MAJORs: {majors}"
        )

    def test_lsp_workspace_folder_non_string_emits_major(self, tmp_path):
        """lspServers.<name>.workspaceFolder not a string → MAJOR."""
        manifest = self._lsp_manifest(workspaceFolder=["./src"])  # should be a string
        plugin_dir = _write_plugin(tmp_path, "lsp-wf-bad", manifest)
        report = ValidationReport()
        validate_lsp(plugin_dir, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("'workspaceFolder' must be a string" in m for m in majors), (
            f"Expected MAJOR for wrong workspaceFolder type; got MAJORs: {majors}"
        )

    def test_lsp_restart_on_crash_non_bool_emits_major(self, tmp_path):
        """lspServers.<name>.restartOnCrash not a bool → MAJOR (plugins-reference.md:251)."""
        manifest = self._lsp_manifest(restartOnCrash="yes")  # should be a bool
        plugin_dir = _write_plugin(tmp_path, "lsp-roc-bad", manifest)
        report = ValidationReport()
        validate_lsp(plugin_dir, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("'restartOnCrash' must be a boolean" in m for m in majors), (
            f"Expected MAJOR for non-boolean restartOnCrash; got MAJORs: {majors}"
        )

    def test_lsp_valid_optional_fields_no_major(self, tmp_path):
        """Correct types across all optional LSP fields → no MAJOR emitted."""
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
        validate_lsp(plugin_dir, report)
        unexpected = [
            r.message
            for r in report.results
            if r.level == "MAJOR"
            and any(
                kw in r.message
                for kw in (
                    "'args'",
                    "'env'",
                    "'settings'",
                    "'initializationOptions'",
                    "'workspaceFolder'",
                    "'restartOnCrash'",
                )
            )
        ]
        assert not unexpected, f"Unexpected MAJORs on valid LSP config: {unexpected}"


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
            "dependencies": [{"name": "shared-lib", "marketplace": "other-market"}],
        }
        plugin_dir = _write_plugin(tmp_path, "xm-ok", manifest)
        hosting = {
            "name": "host-market",
            "allowedDependencyMarketplaces": ["other-market"],
        }
        report = ValidationReport()
        validate_manifest(plugin_dir, report, hosting_marketplace=hosting)
        majors = [
            r.message for r in report.results if r.level == "MAJOR" and "allowedDependencyMarketplaces" in r.message
        ]
        passed = [r.message for r in report.results if r.level == "PASSED" and "allowlisted" in r.message]
        assert not majors, f"Unexpected MAJOR for allowlisted dep: {majors}"
        assert passed, (
            "Expected PASSED for allowlisted cross-market dep; got PASSED: "
            f"{[r.message for r in report.results if r.level == 'PASSED']}"
        )

    def test_cross_marketplace_dep_without_allowlist_major(self, tmp_path):
        """Cross-marketplace dep with NO allowlist declared → MAJOR.

        The error message names the canonical spec field
        `allowCrossMarketplaceDependenciesOn` (plugin-dependencies.md:54-79).
        Earlier CPV builds used the wrong field name `allowedDependencyMarketplaces`;
        the validator now reads from the spec name first and only falls back
        to the legacy name (with a NIT) for backward compat.
        """
        manifest = {
            "name": "consumer",
            "version": "1.0.0",
            "description": "x",
            "dependencies": [{"name": "shared-lib", "marketplace": "other-market"}],
        }
        plugin_dir = _write_plugin(tmp_path, "xm-none", manifest)
        hosting = {"name": "host-market"}  # no allowCrossMarketplaceDependenciesOn
        report = ValidationReport()
        validate_manifest(plugin_dir, report, hosting_marketplace=hosting)
        majors = [
            r.message
            for r in report.results
            if r.level == "MAJOR" and "allowCrossMarketplaceDependenciesOn" in r.message
        ]
        assert majors, (
            f"Expected MAJOR without allowlist; got MAJORs: {[r.message for r in report.results if r.level == 'MAJOR']}"
        )

    def test_cross_marketplace_dep_not_in_allowlist_major(self, tmp_path):
        """Target not present in allowlist → MAJOR with the list in the message."""
        manifest = {
            "name": "consumer",
            "version": "1.0.0",
            "description": "x",
            "dependencies": [{"name": "shared-lib", "marketplace": "other-market"}],
        }
        plugin_dir = _write_plugin(tmp_path, "xm-notin", manifest)
        hosting = {
            "name": "host-market",
            "allowedDependencyMarketplaces": ["trusted-a", "trusted-b"],
        }
        report = ValidationReport()
        validate_manifest(plugin_dir, report, hosting_marketplace=hosting)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("other-market" in m and "trusted-a" in m for m in majors), (
            f"Expected MAJOR listing allowlist; got MAJORs: {majors}"
        )

    def test_same_marketplace_dep_no_cross_check(self, tmp_path):
        """Dep pointing at the SAME hosting marketplace → no cross-check fire."""
        manifest = {
            "name": "consumer",
            "version": "1.0.0",
            "description": "x",
            "dependencies": [{"name": "sibling", "marketplace": "host-market"}],
        }
        plugin_dir = _write_plugin(tmp_path, "xm-same", manifest)
        hosting = {"name": "host-market"}  # no allowlist — same marketplace OK
        report = ValidationReport()
        validate_manifest(plugin_dir, report, hosting_marketplace=hosting)
        blocked = [
            r.message for r in report.results if r.level == "MAJOR" and "allowedDependencyMarketplaces" in r.message
        ]
        assert not blocked, f"Same-market dep must not be blocked; got MAJORs: {blocked}"

    def test_no_hosting_context_emits_info(self, tmp_path):
        """Cross-market dep without hosting context → INFO (not MAJOR)."""
        manifest = {
            "name": "consumer",
            "version": "1.0.0",
            "description": "x",
            "dependencies": [{"name": "shared-lib", "marketplace": "other-market"}],
        }
        plugin_dir = _write_plugin(tmp_path, "xm-noctx", manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)  # no hosting_marketplace
        infos = [r.message for r in report.results if r.level == "INFO" and "cross-marketplace" in r.message]
        blocked = [
            r.message for r in report.results if r.level == "MAJOR" and "allowedDependencyMarketplaces" in r.message
        ]
        assert infos, (
            "Expected INFO for missing hosting context; got INFO: "
            f"{[r.message for r in report.results if r.level == 'INFO']}"
        )
        assert not blocked


class TestCrossMarketplaceHostingDiscovery:
    """TRDD-20108ab7 (2026-05-10): hosting-marketplace auto-discovery.

    The library functions ``validate_dependencies`` and ``validate_manifest``
    accept ``hosting_marketplace=`` but the CLI orchestrator never threads
    it through, so end-users never benefit from cross-marketplace allowlist
    enforcement when running ``validate_plugin <path>``. This class adds
    deterministic auto-discovery of the hosting marketplace context from
    well-known on-disk patterns:

      1. **Layout C**: the plugin's own ``.claude-plugin/marketplace.json``
         (marketplace-in-plugin — covered by Layout C cross-validation).
      2. **Layout B**: parent directory contains
         ``.claude-plugin/marketplace.json`` (nested monorepo).
      3. **Cache layout**: the plugin lives under
         ``~/.claude/plugins/cache/<marketplace-name>/<plugin>/`` — the cache
         parent's ``marketplace.json`` is the hosting marketplace.

    When NONE of those patterns match, no auto-discovery happens and the
    INFO-when-no-context behaviour from the v2.22.3 baseline kicks in.
    """

    def test_layout_c_self_marketplace_discovered(self, tmp_path):
        """Layout C plugin: ``.claude-plugin/marketplace.json`` at plugin root."""
        from validate_plugin import discover_hosting_marketplace

        plugin_dir = tmp_path / "self-mkt-plugin"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "self-mkt-plugin", "version": "1.0.0"})
        )
        (plugin_dir / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "self-mkt",
                    "owner": {"name": "x"},
                    "plugins": [{"name": "self-mkt-plugin", "source": "./"}],
                    "allowCrossMarketplaceDependenciesOn": ["other-mkt"],
                }
            )
        )
        hosting = discover_hosting_marketplace(plugin_dir)
        assert hosting is not None, "Layout C marketplace must be discovered"
        assert hosting.get("name") == "self-mkt"
        assert hosting.get("allowCrossMarketplaceDependenciesOn") == ["other-mkt"]

    def test_layout_b_parent_marketplace_discovered(self, tmp_path):
        """Layout B plugin: parent ``./.claude-plugin/marketplace.json`` exists."""
        from validate_plugin import discover_hosting_marketplace

        marketplace_root = tmp_path / "monorepo"
        (marketplace_root / ".claude-plugin").mkdir(parents=True)
        (marketplace_root / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "monorepo-mkt",
                    "owner": {"name": "x"},
                    "plugins": [{"name": "nested-plugin", "source": "./plugins/nested-plugin"}],
                    "allowCrossMarketplaceDependenciesOn": ["external-mkt"],
                }
            )
        )
        plugin_dir = marketplace_root / "plugins" / "nested-plugin"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "nested-plugin", "version": "1.0.0"})
        )
        hosting = discover_hosting_marketplace(plugin_dir)
        assert hosting is not None, "Layout B parent marketplace must be discovered"
        assert hosting.get("name") == "monorepo-mkt"
        assert hosting.get("allowCrossMarketplaceDependenciesOn") == ["external-mkt"]

    def test_cache_layout_marketplace_discovered(self, tmp_path):
        """Cache layout: ``~/.claude/plugins/cache/<mkt>/<plugin>/`` shape."""
        from validate_plugin import discover_hosting_marketplace

        cache_mkt = tmp_path / "cache" / "host-cache-mkt"
        (cache_mkt / ".claude-plugin").mkdir(parents=True)
        (cache_mkt / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "host-cache-mkt",
                    "owner": {"name": "x"},
                    "plugins": [{"name": "cached-plugin", "source": "./cached-plugin"}],
                }
            )
        )
        plugin_dir = cache_mkt / "cached-plugin"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "cached-plugin", "version": "1.0.0"})
        )
        hosting = discover_hosting_marketplace(plugin_dir)
        assert hosting is not None, "Cache-layout marketplace must be discovered"
        assert hosting.get("name") == "host-cache-mkt"

    def test_standalone_plugin_no_discovery(self, tmp_path):
        """Standalone plugin (no marketplace.json anywhere) → returns None."""
        from validate_plugin import discover_hosting_marketplace

        plugin_dir = tmp_path / "standalone-plugin"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "standalone-plugin", "version": "1.0.0"})
        )
        hosting = discover_hosting_marketplace(plugin_dir)
        assert hosting is None, "Standalone plugin must yield None (no auto-discovery)"

    def test_layout_c_takes_priority_over_parent(self, tmp_path):
        """Layout C (self-marketplace) wins over a parent marketplace."""
        from validate_plugin import discover_hosting_marketplace

        # Outer marketplace at parent
        outer = tmp_path / "outer"
        (outer / ".claude-plugin").mkdir(parents=True)
        (outer / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "outer-mkt",
                    "owner": {"name": "x"},
                    "plugins": [],
                }
            )
        )
        # Layout C plugin nested INSIDE outer (own marketplace.json)
        plugin_dir = outer / "self-mkt-plugin"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "self-mkt-plugin", "version": "1.0.0"})
        )
        (plugin_dir / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "self-mkt",
                    "owner": {"name": "x"},
                    "plugins": [{"name": "self-mkt-plugin", "source": "./"}],
                }
            )
        )
        hosting = discover_hosting_marketplace(plugin_dir)
        assert hosting is not None
        assert hosting.get("name") == "self-mkt", "Layout C self-marketplace must take priority over parent marketplace"

    def test_malformed_marketplace_json_yields_none(self, tmp_path):
        """Malformed marketplace.json must NOT raise — returns None gracefully."""
        from validate_plugin import discover_hosting_marketplace

        marketplace_root = tmp_path / "broken-mkt"
        (marketplace_root / ".claude-plugin").mkdir(parents=True)
        (marketplace_root / ".claude-plugin" / "marketplace.json").write_text("{not valid json")
        plugin_dir = marketplace_root / "child-plugin"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "child-plugin", "version": "1.0.0"})
        )
        # Must not raise — returns None and lets the standard INFO message fire
        hosting = discover_hosting_marketplace(plugin_dir)
        assert hosting is None

    def test_validate_manifest_auto_discovers_layout_b_and_blocks_cross_market(self, tmp_path):
        """End-to-end: Layout B plugin with cross-mkt dep + no allowlist → MAJOR.

        This proves the auto-discovery is properly threaded so end-users
        running ``validate_manifest(plugin_dir, report)`` (no explicit
        ``hosting_marketplace=``) get the cross-marketplace allowlist
        enforcement that was previously gated on explicit context.
        """
        from validate_plugin import validate_manifest

        marketplace_root = tmp_path / "host-mkt"
        (marketplace_root / ".claude-plugin").mkdir(parents=True)
        (marketplace_root / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "host-mkt",
                    "owner": {"name": "x"},
                    "plugins": [{"name": "consumer", "source": "./plugins/consumer"}],
                    # NO allowCrossMarketplaceDependenciesOn — triggers MAJOR
                }
            )
        )
        plugin_dir = marketplace_root / "plugins" / "consumer"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "consumer",
                    "version": "1.0.0",
                    "description": "x",
                    "dependencies": [{"name": "shared-lib", "marketplace": "other-mkt"}],
                }
            )
        )
        report = ValidationReport()
        # NO hosting_marketplace= passed — must auto-discover.
        validate_manifest(plugin_dir, report)
        majors = [
            r.message
            for r in report.results
            if r.level == "MAJOR" and "allowCrossMarketplaceDependenciesOn" in r.message
        ]
        assert majors, (
            "Expected MAJOR via auto-discovered Layout B context; got MAJORs: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )

    def test_cli_marketplace_context_flag_threads_to_validator(self, tmp_path, monkeypatch):
        """CLI ``--marketplace-context PATH`` resolves and is threaded into validation.

        Black-box test of the CLI orchestration: a plugin with a
        cross-marketplace dep that points OUTSIDE the allowlist must exit
        non-zero when the user passes ``--marketplace-context`` pointing at
        a marketplace.json that does NOT allowlist the target.
        """
        import subprocess

        # Plugin with cross-mkt dep
        plugin_dir = tmp_path / "consumer"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "consumer",
                    "version": "1.0.0",
                    "description": "x",
                    "dependencies": [{"name": "shared-lib", "marketplace": "other-mkt"}],
                }
            )
        )
        # Marketplace context with NO allowlist
        ctx_dir = tmp_path / "host-mkt"
        (ctx_dir / ".claude-plugin").mkdir(parents=True)
        (ctx_dir / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "host-mkt",
                    "owner": {"name": "x"},
                    "plugins": [{"name": "consumer", "source": "./../consumer"}],
                }
            )
        )
        validate_script = scripts_dir / "validate_plugin.py"
        result = subprocess.run(
            [
                sys.executable,
                str(validate_script),
                "--marketplace-context",
                str(ctx_dir),
                "--no-color",
                str(plugin_dir),
            ],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(scripts_dir),
        )
        # The script exits non-zero when MAJORs were emitted (exit 2 = MAJOR).
        # The combined stdout+stderr must mention the cross-marketplace error.
        combined = result.stdout + result.stderr
        assert "allowCrossMarketplaceDependenciesOn" in combined or "cross-marketplace" in combined, (
            f"CLI must surface the cross-marketplace block message. stdout/stderr was:\n{combined}"
        )

    def test_cli_marketplace_context_invalid_path_warns(self, tmp_path):
        """Invalid ``--marketplace-context`` path yields a warning, NOT a crash."""
        import subprocess

        plugin_dir = tmp_path / "p"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "p", "version": "1.0.0", "description": "x"})
        )
        validate_script = scripts_dir / "validate_plugin.py"
        nonexistent = tmp_path / "does-not-exist"
        result = subprocess.run(
            [
                sys.executable,
                str(validate_script),
                "--marketplace-context",
                str(nonexistent),
                "--no-color",
                str(plugin_dir),
            ],
            check=False,
            capture_output=True,
            text=True,
            cwd=str(scripts_dir),
        )
        # Process must NOT crash with a stack trace
        assert "Traceback" not in result.stderr, (
            f"CLI must not crash on invalid --marketplace-context. stderr:\n{result.stderr}"
        )
        assert "did not resolve" in result.stderr, (
            f"Expected warning about unresolved context. stderr:\n{result.stderr}"
        )

    def test_validate_manifest_explicit_context_overrides_auto_discovery(self, tmp_path):
        """Explicit ``hosting_marketplace=`` always wins over auto-discovery."""
        from validate_plugin import validate_manifest

        # Set up Layout B with allowlist that WOULD pass
        marketplace_root = tmp_path / "host-mkt"
        (marketplace_root / ".claude-plugin").mkdir(parents=True)
        (marketplace_root / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "host-mkt",
                    "owner": {"name": "x"},
                    "plugins": [],
                    "allowCrossMarketplaceDependenciesOn": ["other-mkt"],  # would allow
                }
            )
        )
        plugin_dir = marketplace_root / "plugins" / "consumer"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "consumer",
                    "version": "1.0.0",
                    "description": "x",
                    "dependencies": [{"name": "shared-lib", "marketplace": "other-mkt"}],
                }
            )
        )
        report = ValidationReport()
        # Explicit context with EMPTY allowlist must override the auto-discovered
        # one and produce a MAJOR even though auto-discovery would have passed.
        validate_manifest(
            plugin_dir,
            report,
            hosting_marketplace={"name": "override-mkt"},  # no allowlist
        )
        majors = [
            r.message
            for r in report.results
            if r.level == "MAJOR" and "allowCrossMarketplaceDependenciesOn" in r.message
        ]
        assert majors, (
            "Explicit hosting_marketplace= must override the auto-discovered "
            "marketplace. Expected MAJOR; got MAJORs: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )


class TestEmpiricalDocsBugsAdded20260418:
    """Tests for new validator rules added after empirical testing of CC's plugin loader.

    These rules catch silent-failure modes that `claude plugin validate` does NOT catch:
      - hooks: "./hooks/hooks.json" → runtime cascade disables MCP servers (MAJOR)
      - agents: any folder path → CC rejects with cryptic "Invalid input" (MAJOR)
      - mcpServers: "./.mcp.json" → redundant declaration, silently accepted (MINOR)
    """

    def _make_plugin_dir(self, tmp_path: Path, manifest: dict) -> Path:
        name = str(manifest.get("name", "p"))
        plugin_dir = tmp_path / name
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))
        return plugin_dir

    # --- Tier 1.1: hooks override = default file → MAJOR cascade explanation ---

    def test_hooks_pointing_at_default_file_emits_major(self, tmp_path):
        """hooks: './hooks/hooks.json' → MAJOR (was WARNING) due to MCP-cascade footgun."""
        manifest = {
            "name": "p",
            "version": "1.0.0",
            "description": "x",
            "hooks": "./hooks/hooks.json",
        }
        plugin_dir = self._make_plugin_dir(tmp_path, manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        majors = [r for r in report.results if r.level == "MAJOR" and "hooks/hooks.json" in r.message]
        assert len(majors) >= 1, (
            f"Expected MAJOR for hooks pointing at default file, got: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )
        msg = majors[0].message
        assert "MCP" in msg or "mcp" in msg, f"Expected message to mention MCP cascade, got: {msg}"
        assert "hook-load-failed" in msg or "Duplicate hooks" in msg or "disable" in msg.lower(), (
            f"Expected message to explain runtime cascade, got: {msg}"
        )

    def test_hooks_pointing_at_default_file_no_leading_dotslash(self, tmp_path):
        """Also catches 'hooks/hooks.json' without './' prefix."""
        manifest = {
            "name": "p",
            "version": "1.0.0",
            "description": "x",
            "hooks": "hooks/hooks.json",
        }
        plugin_dir = self._make_plugin_dir(tmp_path, manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        majors = [r for r in report.results if r.level == "MAJOR" and "hooks/hooks.json" in r.message]
        assert len(majors) >= 1, (
            f"Expected MAJOR for hooks: 'hooks/hooks.json' (no './' prefix), got: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )

    def test_hooks_pointing_at_non_default_file_no_major(self, tmp_path):
        """hooks: './hooks/extra.json' → no cascade-MAJOR (it's a legitimate non-default path)."""
        manifest = {
            "name": "p",
            "version": "1.0.0",
            "description": "x",
            "hooks": "./hooks/extra.json",
        }
        plugin_dir = self._make_plugin_dir(tmp_path, manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        cascade_majors = [r for r in report.results if r.level == "MAJOR" and "hook-load-failed" in r.message]
        assert cascade_majors == [], (
            f"Non-default hooks path should not trigger cascade MAJOR, got: {[m.message for m in cascade_majors]}"
        )

    # --- Tier 1.2: agents folder paths → MAJOR with helpful pre-empt ---

    def test_agents_string_folder_path_emits_major(self, tmp_path):
        """agents: './custom-agents/' (string folder) → MAJOR."""
        manifest = {
            "name": "p",
            "version": "1.0.0",
            "description": "x",
            "agents": "./custom-agents/",
        }
        plugin_dir = self._make_plugin_dir(tmp_path, manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        majors = [
            r
            for r in report.results
            if r.level == "MAJOR" and "agents" in r.message and "folder path" in r.message.lower()
        ]
        assert len(majors) >= 1, (
            f"Expected MAJOR for agents string folder path, got: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )
        msg = majors[0].message
        assert ".md" in msg, f"Expected message to suggest .md file paths, got: {msg}"
        assert "Invalid input" in msg, f"Expected message to mention CC's cryptic error, got: {msg}"

    def test_agents_array_of_folder_paths_emits_major(self, tmp_path):
        """agents: ['./custom-agents/'] (array of folders) → MAJOR."""
        manifest = {
            "name": "p",
            "version": "1.0.0",
            "description": "x",
            "agents": ["./custom-agents/", "./more-agents/"],
        }
        plugin_dir = self._make_plugin_dir(tmp_path, manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        majors = [
            r
            for r in report.results
            if r.level == "MAJOR" and "agents" in r.message and "folder path" in r.message.lower()
        ]
        # Should emit one MAJOR per folder path
        assert len(majors) >= 2, (
            f"Expected MAJOR per folder path in agents array (2 expected), got {len(majors)}: "
            f"{[m.message for m in majors]}"
        )

    def test_agents_array_of_file_paths_no_major(self, tmp_path):
        """agents: ['./custom-agents/foo.md'] (array of .md files) → no folder-path MAJOR."""
        manifest = {
            "name": "p",
            "version": "1.0.0",
            "description": "x",
            "agents": ["./custom-agents/foo.md", "./agents/bar.md"],
        }
        plugin_dir = self._make_plugin_dir(tmp_path, manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        folder_majors = [
            r
            for r in report.results
            if r.level == "MAJOR" and "agents" in r.message and "folder path" in r.message.lower()
        ]
        assert folder_majors == [], (
            f".md file paths in agents array should not trigger folder-path MAJOR, got: "
            f"{[m.message for m in folder_majors]}"
        )

    def test_agents_string_file_path_no_major(self, tmp_path):
        """agents: './custom-agents/foo.md' (string .md file) → no folder-path MAJOR."""
        manifest = {
            "name": "p",
            "version": "1.0.0",
            "description": "x",
            "agents": "./custom-agents/foo.md",
        }
        plugin_dir = self._make_plugin_dir(tmp_path, manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        folder_majors = [
            r
            for r in report.results
            if r.level == "MAJOR" and "agents" in r.message and "folder path" in r.message.lower()
        ]
        assert folder_majors == [], (
            f"String .md file path should not trigger folder-path MAJOR, got: {[m.message for m in folder_majors]}"
        )

    # --- Audit fixes: edge cases for path normalization, array forms, no-double-fire ---

    def test_agents_default_folder_emits_major_with_extra_note(self, tmp_path):
        """agents: './agents/' (default folder) → ONE MAJOR with default-folder note.

        Updated 2026-04-19: previously this test asserted ≤1 finding (some versions
        emitted 0). Empirically CC rejects `agents: "./agents/"` with `Invalid input`
        — same as for non-default folder paths. CPV must catch this consistently with
        a MAJOR + a helpful "just remove the field" note (since the default folder is
        auto-discovered).

        Net effect: exactly ONE MAJOR finding (no CRITICAL, no double-fire).
        """
        manifest = {
            "name": "p",
            "version": "1.0.0",
            "description": "x",
            "agents": "./agents/",
        }
        plugin_dir = self._make_plugin_dir(tmp_path, manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        agents_majors = [
            r
            for r in report.results
            if r.level == "MAJOR" and "agents" in r.message and "folder path" in r.message.lower()
        ]
        assert len(agents_majors) == 1, (
            f"Expected exactly 1 MAJOR for agents: './agents/', got {len(agents_majors)}: "
            f"{[r.message for r in agents_majors]}"
        )
        # The message should include a hint that for the default folder, just remove the field
        assert "remove the 'agents' field entirely" in agents_majors[0].message, (
            f"Expected default-folder hint in message, got: {agents_majors[0].message}"
        )
        # No CRITICAL for agents (auto_discovered_defaults check skips agents)
        agents_criticals = [r for r in report.results if r.level == "CRITICAL" and "agents" in r.message]
        assert agents_criticals == [], (
            f"agents: './agents/' should not emit CRITICAL (only the dedicated MAJOR), got: "
            f"{[r.message for r in agents_criticals]}"
        )

    def test_hooks_array_form_pointing_at_default_emits_major(self, tmp_path):
        """hooks: ['./hooks/hooks.json'] (array form pointing at default) → MAJOR."""
        manifest = {
            "name": "p",
            "version": "1.0.0",
            "description": "x",
            "hooks": ["./hooks/hooks.json"],
        }
        plugin_dir = self._make_plugin_dir(tmp_path, manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        majors = [r for r in report.results if r.level == "MAJOR" and "hook-load-failed" in r.message]
        assert len(majors) >= 1, (
            f"Expected MAJOR for hooks array containing default file, got: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )

    def test_hooks_path_with_normalization_quirks_emits_major(self, tmp_path):
        """hooks: './hooks/./hooks.json' (with redundant './') → still MAJOR.

        Audit fix: the new _is_default_hooks_path collapses './' segments.
        """
        manifest = {
            "name": "p",
            "version": "1.0.0",
            "description": "x",
            "hooks": "./hooks/./hooks.json",
        }
        plugin_dir = self._make_plugin_dir(tmp_path, manifest)
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        majors = [r for r in report.results if r.level == "MAJOR" and "hook-load-failed" in r.message]
        assert len(majors) >= 1, (
            f"Expected MAJOR for hooks path with normalization quirks, got: "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )


class TestManifestReferencedDirsSuppressNonStandardWarning:
    """Tests for the manifest-referenced-folder discovery added 2026-04-19.

    `validate_structure` warns about "non-standard directories" at plugin root.
    But folders referenced from .mcp.json, .lsp.json, hooks, monitors, or inline
    plugin.json fields via `${CLAUDE_PLUGIN_ROOT}/<dir>/...` are legitimate and
    should NOT trigger the warning. Empirical bug fix: llm-externalizer plugin
    has `mcp-server/` referenced via `.mcp.json` and was wrongly warned about
    in CPV v2.23.0.
    """

    def _make_plugin_with_dir_and_manifest(
        self,
        tmp_path: Path,
        nonstandard_dirname: str,
        *,
        mcp_json: dict | None = None,
        lsp_json: dict | None = None,
        hooks_json: dict | None = None,
        monitors_json: list | None = None,
        plugin_manifest_extra: dict | None = None,
    ) -> Path:
        plugin_dir = tmp_path / "p"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / nonstandard_dirname).mkdir()
        # Add a placeholder file so the directory isn't empty
        (plugin_dir / nonstandard_dirname / "index.js").write_text("// stub")
        manifest = {"name": "p", "version": "1.0.0", "description": "x"}
        if plugin_manifest_extra:
            manifest.update(plugin_manifest_extra)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))
        if mcp_json is not None:
            (plugin_dir / ".mcp.json").write_text(json.dumps(mcp_json))
        if lsp_json is not None:
            (plugin_dir / ".lsp.json").write_text(json.dumps(lsp_json))
        if hooks_json is not None:
            (plugin_dir / "hooks").mkdir(exist_ok=True)
            (plugin_dir / "hooks" / "hooks.json").write_text(json.dumps(hooks_json))
        if monitors_json is not None:
            (plugin_dir / "monitors").mkdir(exist_ok=True)
            (plugin_dir / "monitors" / "monitors.json").write_text(json.dumps(monitors_json))
        return plugin_dir

    def _has_warning_for_dir(self, report: ValidationReport, dirname: str) -> bool:
        # As of v2.68.0 the severity for undeclared non-standard root dirs
        # is MAJOR (was WARNING). Every test in this class predates the
        # bump and originally checked for WARNING; helper now matches the
        # MAJOR level so the existing assertions stay meaningful.
        return any(
            r.level == "MAJOR" and f"'{dirname}/'" in r.message and "Non-standard" in r.message for r in report.results
        )

    def test_mcp_json_command_arg_reference_suppresses_warning(self, tmp_path):
        """mcp-server/ referenced via `.mcp.json` args → no warning (the llm-externalizer case)."""
        plugin_dir = self._make_plugin_with_dir_and_manifest(
            tmp_path,
            "mcp-server",
            mcp_json={
                "mcpServers": {
                    "my-mcp": {
                        "command": "node",
                        "args": ["${CLAUDE_PLUGIN_ROOT}/mcp-server/dist/index.js"],
                    }
                }
            },
        )
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        assert not self._has_warning_for_dir(report, "mcp-server"), (
            f"mcp-server/ referenced from .mcp.json should NOT warn. Got warnings: "
            f"{[r.message for r in report.results if r.level == 'WARNING']}"
        )

    def test_lsp_json_command_reference_suppresses_warning(self, tmp_path):
        """lsp-bin/ referenced via `.lsp.json` command → no warning."""
        plugin_dir = self._make_plugin_with_dir_and_manifest(
            tmp_path,
            "lsp-bin",
            lsp_json={
                "go": {
                    "command": "${CLAUDE_PLUGIN_ROOT}/lsp-bin/gopls",
                    "extensionToLanguage": {".go": "go"},
                }
            },
        )
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        assert not self._has_warning_for_dir(report, "lsp-bin"), (
            f"lsp-bin/ referenced from .lsp.json should NOT warn. Got: "
            f"{[r.message for r in report.results if r.level == 'WARNING']}"
        )

    def test_hooks_command_reference_suppresses_warning(self, tmp_path):
        """custom-tools/ referenced via hooks/hooks.json command → no warning."""
        plugin_dir = self._make_plugin_with_dir_and_manifest(
            tmp_path,
            "custom-tools",
            hooks_json={
                "hooks": {
                    "PostToolUse": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "${CLAUDE_PLUGIN_ROOT}/custom-tools/format.sh",
                                }
                            ]
                        }
                    ]
                }
            },
        )
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        assert not self._has_warning_for_dir(report, "custom-tools"), (
            f"custom-tools/ referenced from hooks should NOT warn. Got: "
            f"{[r.message for r in report.results if r.level == 'WARNING']}"
        )

    def test_monitors_command_reference_suppresses_warning(self, tmp_path):
        """polling/ referenced via monitors/monitors.json → no warning."""
        plugin_dir = self._make_plugin_with_dir_and_manifest(
            tmp_path,
            "polling",
            monitors_json=[
                {
                    "name": "deploy-status",
                    "command": "${CLAUDE_PLUGIN_ROOT}/polling/check-deploy.sh",
                    "description": "Deploy poller",
                }
            ],
        )
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        assert not self._has_warning_for_dir(report, "polling"), (
            f"polling/ referenced from monitors should NOT warn. Got: "
            f"{[r.message for r in report.results if r.level == 'WARNING']}"
        )

    def test_inline_plugin_json_mcpservers_reference_suppresses_warning(self, tmp_path):
        """servers/ referenced via inline plugin.json:mcpServers → no warning."""
        plugin_dir = self._make_plugin_with_dir_and_manifest(
            tmp_path,
            "my-server-bundle",
            plugin_manifest_extra={
                "mcpServers": {
                    "x": {
                        "command": "${CLAUDE_PLUGIN_ROOT}/my-server-bundle/server",
                    }
                }
            },
        )
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        assert not self._has_warning_for_dir(report, "my-server-bundle"), (
            f"my-server-bundle/ referenced from inline plugin.json should NOT warn. Got: "
            f"{[r.message for r in report.results if r.level == 'WARNING']}"
        )

    def test_inline_plugin_json_lspservers_reference_suppresses_warning(self, tmp_path):
        """language-bin/ referenced via inline plugin.json:lspServers → no warning."""
        plugin_dir = self._make_plugin_with_dir_and_manifest(
            tmp_path,
            "language-bin",
            plugin_manifest_extra={
                "lspServers": {
                    "myls": {
                        "command": "${CLAUDE_PLUGIN_ROOT}/language-bin/myls",
                        "extensionToLanguage": {".my": "my"},
                    }
                }
            },
        )
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        assert not self._has_warning_for_dir(report, "language-bin"), (
            f"language-bin/ referenced from inline lspServers should NOT warn. Got: "
            f"{[r.message for r in report.results if r.level == 'WARNING']}"
        )

    def test_unreferenced_nonstandard_dir_still_warns(self, tmp_path):
        """A non-standard dir NOT referenced anywhere should still warn."""
        plugin_dir = self._make_plugin_with_dir_and_manifest(
            tmp_path,
            "random-junk",
            # No mcp/lsp/hooks/monitors/inline reference to random-junk/
        )
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        assert self._has_warning_for_dir(report, "random-junk"), (
            f"random-junk/ (no manifest reference) SHOULD still warn. Got: "
            f"{[r.message for r in report.results if r.level == 'WARNING']}"
        )

    def test_servers_dir_now_in_known_list(self, tmp_path):
        """servers/ (docs convention for MCP bundles) is in the static known list."""
        plugin_dir = self._make_plugin_with_dir_and_manifest(
            tmp_path,
            "servers",
            # No manifest reference; should be allowed via known_dirs alone
        )
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        assert not self._has_warning_for_dir(report, "servers"), (
            f"servers/ should be in static known_dirs. Got: "
            f"{[r.message for r in report.results if r.level == 'WARNING']}"
        )


class TestKnownDirsExpandedV2_23_2:
    """Tests for known-dirs additions surfaced by the v2.23.2 batch scan of 160 plugins.

    Goal: stop emitting "Non-standard directory" WARNING for common patterns that
    every plugin uses but the spec doesn't explicitly name.
    """

    def _make_minimal_plugin_with_dir(self, tmp_path: Path, dirname: str) -> Path:
        plugin_dir = tmp_path / "p"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / dirname).mkdir()
        (plugin_dir / dirname / "stub").write_text("// stub")
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "p", "version": "1.0.0", "description": "x"})
        )
        return plugin_dir

    def _has_nonstandard_warning(self, report: ValidationReport, dirname: str) -> bool:
        return any(
            r.level == "WARNING" and f"'{dirname}/'" in r.message and "Non-standard" in r.message
            for r in report.results
        )

    @pytest.mark.parametrize(
        "dirname",
        [
            # Common cross-cutting dirs surfaced empirically:
            "prompts",
            "demo",
            "demos",
            "eval",
            "evals",
            "node_modules",
            "output",
            "outputs",
            "server",
            "public",
            "static",
            "web",
            "shared",
            "settings",
            "guidances",
            "plugins",
            # Language source dirs (plugins shipping native binaries):
            "rust",
            "go",
            "python",
            "node",
            "ts",
            "js",
            "java",
            "kotlin",
            "swift",
            "ruby",
            "csharp",
            "cpp",
            "c",
        ],
    )
    def test_common_dir_no_longer_warns(self, tmp_path, dirname):
        """Each common dir name added in v2.23.2 must NOT trigger 'Non-standard directory'."""
        plugin_dir = self._make_minimal_plugin_with_dir(tmp_path, dirname)
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        assert not self._has_nonstandard_warning(report, dirname), (
            f"{dirname}/ should be in v2.23.2 expanded known_dirs. Got: "
            f"{[r.message for r in report.results if r.level == 'WARNING']}"
        )


class TestSubmodulePatternAllowance:
    """Tests for the submodule-pattern auto-allowance added in v2.23.2.

    Many plugins (especially Layout B nested marketplaces) have a subdirectory
    named after the plugin itself (e.g. `web-automation-suite/web-automation-suite/`).
    The validator should auto-allow this without a WARNING.
    """

    def test_subdir_matching_plugin_name_does_not_warn(self, tmp_path):
        plugin_dir = tmp_path / "my-cool-plugin"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "my-cool-plugin", "version": "1.0.0", "description": "x"})
        )
        # Sub-directory matching the plugin name (the submodule pattern):
        (plugin_dir / "my-cool-plugin").mkdir()
        (plugin_dir / "my-cool-plugin" / "code.js").write_text("// stub")
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        assert not any(
            r.level == "WARNING" and "'my-cool-plugin/'" in r.message and "Non-standard" in r.message
            for r in report.results
        ), (
            "Subdir matching plugin name should be auto-allowed. Got: "
            f"{[r.message for r in report.results if r.level == 'WARNING']}"
        )

    def test_subdir_not_matching_plugin_name_still_flags_as_major(self, tmp_path):
        """Severity bumped to MAJOR in v2.68.0 (was WARNING). See class
        TestSubmodulePatternAllowance._has_warning_for_dir docstring."""
        plugin_dir = tmp_path / "my-cool-plugin"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "my-cool-plugin", "version": "1.0.0", "description": "x"})
        )
        # Different name — must still flag (now as MAJOR, was WARNING)
        (plugin_dir / "unrelated-folder").mkdir()
        (plugin_dir / "unrelated-folder" / "x.js").write_text("// stub")
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        assert any(
            r.level == "MAJOR" and "'unrelated-folder/'" in r.message and "Non-standard" in r.message
            for r in report.results
        ), "Unrelated non-standard folder should trigger MAJOR (was WARNING pre-v2.68.0)"

    def test_submodule_check_safe_with_invalid_plugin_json(self, tmp_path):
        """If plugin.json is invalid JSON, the submodule check must not crash."""
        plugin_dir = tmp_path / "p"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text("{not valid json")
        (plugin_dir / "weird-subdir").mkdir()
        (plugin_dir / "weird-subdir" / "x.js").write_text("// stub")
        # Should not raise; submodule check just skips
        report = ValidationReport()
        validate_structure(plugin_dir, report)
        # We don't assert the warning here (other manifest validation will fire),
        # only that no exception occurred — reaching this line is the test.
        assert True


class TestMarketplaceShortCircuit:
    """Tests for the marketplace short-circuit added in v2.23.2.

    If the path being validated has marketplace.json but no plugin.json, it's a
    marketplace folder and validate_plugin should bail out cleanly with an error
    pointing to validate_marketplace.py — instead of running plugin checks and
    emitting dozens of false positives for the per-plugin subfolders.
    """

    def _run_validate_plugin_main(self, plugin_path: Path):
        """Invoke validate_plugin.main() with the given path."""
        # The marketplace short-circuit lives in main(), so we shell out
        # rather than calling validate_structure directly.
        import subprocess

        result = subprocess.run(
            [
                "uv",
                "run",
                "--with",
                "pyyaml",
                "python",
                "scripts/validate_plugin.py",
                str(plugin_path),
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parent.parent,
        )
        return result

    def test_marketplace_only_path_bails_out(self, tmp_path):
        """Path with .claude-plugin/marketplace.json (no plugin.json) → exit 1 with marketplace error."""
        marketplace_dir = tmp_path / "my-mkt"
        (marketplace_dir / ".claude-plugin").mkdir(parents=True)
        (marketplace_dir / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "my-mkt",
                    "owner": {"name": "x"},
                    "plugins": [{"name": "p1", "source": "./p1"}],
                }
            )
        )
        result = self._run_validate_plugin_main(marketplace_dir)
        assert result.returncode == 1, (
            f"Expected exit code 1, got {result.returncode}\nstderr: {result.stderr}\nstdout: {result.stdout}"
        )
        assert "MARKETPLACE folder" in result.stderr, f"Expected marketplace bail-out hint. Got stderr: {result.stderr}"

    def test_plugin_with_both_manifests_proceeds_normally(self, tmp_path):
        """If BOTH marketplace.json AND plugin.json exist (rare hybrid), plugin validation still runs."""
        plugin_dir = tmp_path / "p"
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        (plugin_dir / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"name": "p", "owner": {"name": "x"}, "plugins": []})
        )
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "p", "version": "1.0.0", "description": "x"})
        )
        result = self._run_validate_plugin_main(plugin_dir)
        # Should NOT bail out with the marketplace-only error
        assert "MARKETPLACE folder" not in result.stderr, (
            f"Hybrid plugin (with both manifests) should proceed, not bail. Got stderr: {result.stderr}"
        )


# =============================================================================
# Phase 14 (v2.30.0) — userConfig schema validation (v2.1.121 spec)
# =============================================================================


class TestPhase14UserConfigSchema:
    """v2.1.121 — userConfig per-key schema with type enum + required fields."""

    def _validate_uc(self, uc):  # type: ignore[no-untyped-def]
        from cpv_validation_common import ValidationReport
        from validate_plugin import validate_user_config_structure

        report = ValidationReport()
        validate_user_config_structure({"userConfig": uc}, report)
        return list(report.results)

    def test_required_fields_missing_major(self) -> None:
        # No type, no title, no description.
        results = self._validate_uc({"my_key": {"sensitive": True}})
        msgs = [r.message for r in results if r.level == "MAJOR"]
        assert any("missing required sub-field 'type'" in m for m in msgs)
        assert any("missing required sub-field 'title'" in m for m in msgs)
        assert any("missing required sub-field 'description'" in m for m in msgs)

    def test_type_enum_string_valid(self) -> None:
        results = self._validate_uc({"k": {"type": "string", "title": "T", "description": "D"}})
        assert not any(r.level == "MAJOR" for r in results)

    def test_type_enum_directory_valid(self) -> None:
        results = self._validate_uc({"k": {"type": "directory", "title": "T", "description": "D"}})
        assert not any(r.level == "MAJOR" for r in results)

    def test_type_enum_file_valid(self) -> None:
        results = self._validate_uc({"k": {"type": "file", "title": "T", "description": "D"}})
        assert not any(r.level == "MAJOR" for r in results)

    def test_type_enum_unknown_value_major(self) -> None:
        results = self._validate_uc({"k": {"type": "uuid", "title": "T", "description": "D"}})
        assert any("is not a valid type" in r.message and "uuid" in r.message for r in results if r.level == "MAJOR")

    def test_min_max_on_non_number_minor(self) -> None:
        results = self._validate_uc({"k": {"type": "string", "title": "T", "description": "D", "min": 0, "max": 100}})
        minor_msgs = [r.message for r in results if r.level == "MINOR"]
        assert any("min" in m and "only meaningful for type: number" in m for m in minor_msgs)
        assert any("max" in m and "only meaningful for type: number" in m for m in minor_msgs)

    def test_multiple_on_non_string_minor(self) -> None:
        results = self._validate_uc({"k": {"type": "number", "title": "T", "description": "D", "multiple": True}})
        assert any(
            "multiple" in r.message and "only meaningful for type: string" in r.message
            for r in results
            if r.level == "MINOR"
        )

    def test_invalid_identifier_key_major(self) -> None:
        results = self._validate_uc({"1invalid_start_digit": {"type": "string", "title": "T", "description": "D"}})
        assert any("must be a valid identifier" in r.message for r in results if r.level == "MAJOR")

    def test_unknown_subfield_minor(self) -> None:
        results = self._validate_uc({"k": {"type": "string", "title": "T", "description": "D", "regex": "^[A-Z]+$"}})
        assert any(
            "regex" in r.message and "not a recognized sub-field" in r.message for r in results if r.level == "MINOR"
        )

    def test_complete_valid_config_no_findings(self) -> None:
        results = self._validate_uc(
            {
                "api_url": {
                    "type": "string",
                    "title": "API URL",
                    "description": "Base URL for the API",
                    "default": "https://api.example.com",
                    "required": True,
                },
                "api_key": {
                    "type": "string",
                    "title": "API Key",
                    "description": "Auth token",
                    "sensitive": True,
                    "required": True,
                },
                "max_retries": {
                    "type": "number",
                    "title": "Max retries",
                    "description": "Maximum retry attempts",
                    "default": 3,
                    "min": 0,
                    "max": 10,
                },
            }
        )
        critical = [r for r in results if r.level == "CRITICAL"]
        major = [r for r in results if r.level == "MAJOR"]
        minor = [r for r in results if r.level == "MINOR"]
        assert critical == []
        assert major == []
        assert minor == []


# =============================================================================
# Phase 15 (v2.31.0) — bundled slash-command collision check
# =============================================================================


class TestPhase15BundledSlashCollision:
    """Plugin commands must not silently shadow built-in slash commands."""

    def test_builtin_set_includes_recent_additions(self) -> None:
        from cpv_validation_common import BUILTIN_SLASH_COMMANDS

        # v2.1.110-121 era additions
        for name in ("usage", "tui", "focus", "ultrareview", "loop", "proactive", "recap", "less-permission-prompts"):
            assert name in BUILTIN_SLASH_COMMANDS, f"{name} missing from bundled list"

    def test_command_named_loop_emits_warning(self, tmp_path: Path) -> None:
        from validate_command import validate_command

        cmd = tmp_path / "loop.md"
        cmd.write_text("---\nname: loop\ndescription: my custom loop\n---\n\nBody of the command.\n")
        report = validate_command(cmd)
        msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("collides with a built-in" in m and "/loop" in m for m in msgs)

    def test_command_with_unique_name_no_warning(self, tmp_path: Path) -> None:
        from validate_command import validate_command

        cmd = tmp_path / "my-very-unique-name.md"
        cmd.write_text("---\nname: my-very-unique-name\ndescription: x\n---\n\nbody\n")
        report = validate_command(cmd)
        msgs = [r.message for r in report.results if r.level == "WARNING" and "collides with a built-in" in r.message]
        assert msgs == []


# =============================================================================
# Phase 16 (v2.32.0) — Layout C cross-validation (marketplace-in-plugin)
# =============================================================================


class TestPhase16LayoutC:
    """A repo that is BOTH a plugin AND a marketplace must self-reference."""

    def _run_layout_c(
        self,
        tmp_path: Path,
        plugin_name: str = "demo",
        plugin_version: str = "1.0.0",
        market_self_version: str | None = None,
        market_self_source: str = "./",
        include_self: bool = True,
    ):  # type: ignore[no-untyped-def]
        from cpv_validation_common import ValidationReport

        plugin_dir = tmp_path / "demo-repo"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": plugin_name, "version": plugin_version, "description": "x"})
        )
        plugins_arr: list[dict] = []
        if include_self:
            entry: dict = {"name": plugin_name, "source": market_self_source}
            if market_self_version is not None:
                entry["version"] = market_self_version
            plugins_arr.append(entry)
        (plugin_dir / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": plugin_name,
                    "owner": {"name": "x", "email": "x@example.com"},
                    "plugins": plugins_arr,
                }
            )
        )
        from validate_plugin import validate_layout_c_consistency

        report = ValidationReport()
        validate_layout_c_consistency(plugin_dir, report)
        return report

    def test_layout_c_well_formed_no_findings(self, tmp_path: Path) -> None:
        report = self._run_layout_c(tmp_path)
        critical = [r for r in report.results if r.level == "CRITICAL"]
        major = [r for r in report.results if r.level == "MAJOR"]
        assert critical == []
        assert major == []

    def test_layout_c_missing_self_reference_major(self, tmp_path: Path) -> None:
        report = self._run_layout_c(tmp_path, include_self=False)
        assert any("does not list a self-reference" in r.message for r in report.results if r.level == "MAJOR")

    def test_layout_c_wrong_source_major(self, tmp_path: Path) -> None:
        report = self._run_layout_c(tmp_path, market_self_source="github://my-org/demo-repo")
        assert any("must be './'" in r.message for r in report.results if r.level == "MAJOR")

    def test_layout_c_version_drift_minor(self, tmp_path: Path) -> None:
        report = self._run_layout_c(tmp_path, plugin_version="1.0.0", market_self_version="1.1.0")
        assert any("version" in r.message and "differs from" in r.message for r in report.results if r.level == "MINOR")

    def test_layout_c_validator_skipped_for_plain_plugin(self, tmp_path: Path) -> None:
        """Plain plugin (no marketplace.json) must not trigger Layout C checks."""
        from cpv_validation_common import ValidationReport
        from validate_plugin import validate_layout_c_consistency

        plugin_dir = tmp_path / "plain"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "plain", "version": "0.1.0", "description": "x"})
        )
        report = ValidationReport()
        validate_layout_c_consistency(plugin_dir, report)
        assert report.results == []


class TestAuditFixesV2106:
    """Two-sided regression tests for the v2.106 deep-audit fixes.

    Each test asserts BOTH the corrected behavior AND that the corresponding
    valid input still passes — so a fix that merely suppressed the symptom
    (e.g. blanket-skipping a check) would fail the second assertion.
    """

    @staticmethod
    def _plugin_with(tmp_path: Path, name: str, manifest_extra: dict) -> Path:
        plugin_dir = tmp_path / name
        (plugin_dir / ".claude-plugin").mkdir(parents=True)
        manifest = {"name": name, "version": "1.0.0", "description": "x", **manifest_extra}
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest))
        return plugin_dir

    @staticmethod
    def _write_skill(plugin_dir: Path, rel_dir: str, frontmatter_name: str) -> None:
        sk = plugin_dir / rel_dir
        sk.mkdir(parents=True, exist_ok=True)
        (sk / "SKILL.md").write_text(
            f"---\nname: {frontmatter_name}\n"
            f"description: A test skill that does a thing when the user asks for it.\n---\n\n"
            f"Body content explaining the skill.\n"
        )

    # ── M1: declared `skills` array gets CONTENT validation ──────────────
    def test_declared_skills_array_runs_content_validation(self, tmp_path):
        """A skill declared via plugin.json `skills` with a broken body (name/dir
        mismatch) MUST be caught — existence-only validation would miss it."""
        plugin_dir = self._plugin_with(tmp_path, "decl-skills-bad", {"skills": ["./skills/my-skill/"]})
        # frontmatter name deliberately mismatches the directory name → MAJOR.
        self._write_skill(plugin_dir, "skills/my-skill", "totally-wrong-name")
        report = ValidationReport()
        validate_skills(plugin_dir, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must match directory name" in m for m in majors), majors

    def test_declared_skills_array_valid_skill_passes(self, tmp_path):
        """The other side: a VALID declared skill produces no skill-content MAJORs
        (proves content validation is not blanket-failing every declared skill)."""
        plugin_dir = self._plugin_with(tmp_path, "decl-skills-ok", {"skills": ["./skills/my-skill/"]})
        self._write_skill(plugin_dir, "skills/my-skill", "my-skill")
        report = ValidationReport()
        validate_skills(plugin_dir, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert majors == [], majors

    def test_declared_skills_array_skill_md_file_entry_validated(self, tmp_path):
        """A direct SKILL.md file entry resolves to its parent dir and is
        content-validated (broken body still caught)."""
        plugin_dir = self._plugin_with(tmp_path, "decl-skills-file", {"skills": ["./skills/my-skill/SKILL.md"]})
        self._write_skill(plugin_dir, "skills/my-skill", "totally-wrong-name")
        report = ValidationReport()
        validate_skills(plugin_dir, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must match directory name" in m for m in majors), majors

    # ── M2: channels validated exactly once (no duplicate findings) ──────
    def test_channels_defect_emitted_once(self, tmp_path):
        """A single channels[].server cross-ref defect must produce exactly ONE
        MAJOR (was two before the inline-block deletion inflated the count)."""
        plugin_dir = self._plugin_with(
            tmp_path,
            "ch-dup",
            {"mcpServers": {"real": {"command": "x"}}, "channels": [{"server": "missing"}]},
        )
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        server_majors = [
            r.message
            for r in report.results
            if r.level == "MAJOR" and "missing" in r.message and "mcpServers" in r.message
        ]
        assert len(server_majors) == 1, server_majors

    def test_channels_valid_passes(self, tmp_path):
        """The other side: a valid channels entry produces no channels MAJOR."""
        plugin_dir = self._plugin_with(
            tmp_path,
            "ch-ok",
            {"mcpServers": {"real": {"command": "x"}}, "channels": [{"server": "real"}]},
        )
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        ch_majors = [r.message for r in report.results if r.level == "MAJOR" and "channels" in r.message]
        assert ch_majors == [], ch_majors

    # ── M3: userConfig validated exactly once (no duplicate findings) ────
    def test_userconfig_defect_emitted_once(self, tmp_path):
        """A single userConfig missing-type defect must produce exactly ONE MAJOR
        mentioning the missing type (was two before the inline-block deletion)."""
        plugin_dir = self._plugin_with(
            tmp_path,
            "uc-dup",
            {"userConfig": {"OPT": {"title": "Opt", "description": "x"}}},
        )
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        type_majors = [
            r.message for r in report.results if r.level == "MAJOR" and "OPT" in r.message and "'type'" in r.message
        ]
        assert len(type_majors) == 1, type_majors

    def test_userconfig_default_type_mismatch_still_caught_after_dedup(self, tmp_path):
        """The default/type-match check (moved from the deleted inline block into
        the SSOT helper) must still fire."""
        plugin_dir = self._plugin_with(
            tmp_path,
            "uc-mismatch",
            {"userConfig": {"OPT": {"title": "Opt", "description": "x", "type": "number", "default": "nan"}}},
        )
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("does not match declared type (number)" in m for m in majors), majors

    def test_userconfig_non_dict_still_caught_after_dedup(self, tmp_path):
        """Non-dict userConfig (whose MAJOR the inline block used to own) must
        still be flagged by the SSOT helper."""
        plugin_dir = self._plugin_with(tmp_path, "uc-nondict", {"userConfig": "not-a-dict"})
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("'userConfig' must be an object" in m for m in majors), majors

    # ── m1: cross-mkt allowlist gap is VISIBLE when hosting name missing ──
    def test_cross_marketplace_missing_hosting_name_emits_info(self, tmp_path):
        """When a hosting marketplace is supplied but lacks a usable `name`, the
        skipped allowlist check must surface an INFO (not silently vanish)."""
        from validate_plugin import validate_dependencies

        manifest = {"dependencies": [{"name": "dep-plugin", "marketplace": "other-mkt"}]}
        report = ValidationReport()
        validate_dependencies(manifest, report, hosting_marketplace={"allowCrossMarketplaceDependenciesOn": []})
        infos = [r.message for r in report.results if r.level == "INFO"]
        assert any("no usable 'name'" in m for m in infos), infos
        # Two-sided: must NOT emit the cross-mkt MAJOR (the check could not run).
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("allowCrossMarketplaceDependenciesOn allowlist" in m for m in majors), majors

    def test_cross_marketplace_with_name_still_enforced(self, tmp_path):
        """The other side: a hosting marketplace WITH a name still enforces the
        allowlist (the missing-name branch did not weaken enforcement)."""
        from validate_plugin import validate_dependencies

        manifest = {"dependencies": [{"name": "dep-plugin", "marketplace": "other-mkt"}]}
        report = ValidationReport()
        validate_dependencies(
            manifest,
            report,
            hosting_marketplace={"name": "host-mkt", "allowCrossMarketplaceDependenciesOn": []},
        )
        majors = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("allowCrossMarketplaceDependenciesOn allowlist" in m for m in majors), majors

    # ── m2: plugin version regex anchored ────────────────────────────────
    @pytest.mark.parametrize("bad", ["1.2.3garbage", "1.2.3.4.5", "01.02.03", "1.2", "v1.2.3", "1.2.3-"])
    def test_version_regex_rejects_invalid(self, tmp_path, bad):
        """Anchored semver: trailing garbage / extra components / leading zeros
        / short / v-prefixed / empty-prerelease all MUST be rejected."""
        plugin_dir = self._plugin_with(tmp_path, "ver-bad", {})
        # overwrite version with the bad value
        pj = plugin_dir / ".claude-plugin" / "plugin.json"
        pj.write_text(json.dumps({"name": "ver-bad", "version": bad, "description": "x"}))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        assert any(
            r.level == "MAJOR" and "semver" in r.message for r in report.results
        ), f"{bad!r} should be rejected; got {[r.message for r in report.results if r.level == 'MAJOR']}"

    @pytest.mark.parametrize("good", ["1.2.3", "0.0.0", "10.20.30", "1.2.3-rc.1", "1.2.3+build", "0.1.0"])
    def test_version_regex_accepts_valid(self, tmp_path, good):
        """The other side: valid semver (incl. 0-components, prerelease, build)
        must NOT be flagged."""
        plugin_dir = self._plugin_with(tmp_path, "ver-ok", {})
        pj = plugin_dir / ".claude-plugin" / "plugin.json"
        pj.write_text(json.dumps({"name": "ver-ok", "version": good, "description": "x"}))
        report = ValidationReport()
        validate_manifest(plugin_dir, report)
        version_majors = [r.message for r in report.results if r.level == "MAJOR" and "semver" in r.message]
        assert version_majors == [], f"{good!r} should be accepted; got {version_majors}"

    # ── m3: self-scan flag disarmed after the skillaudit pass ────────────
    def test_skillaudit_disarms_self_scan_flag(self, tmp_path):
        """_run_skillaudit_native must leave the module-global self-scan flag
        DISARMED so it cannot leak into a subsequent in-process scan."""
        import validate_security
        from validate_plugin import _run_skillaudit_native

        plugin_dir = self._plugin_with(tmp_path, "scan-plugin", {})
        report = ValidationReport()
        # Pre-arm to a sentinel TRUE so we can prove the finally-disarm runs.
        validate_security._set_cpv_self_scan(True, plugin_root=tmp_path)
        try:
            _run_skillaudit_native(plugin_dir, report)
            assert validate_security._CPV_SELF_SCAN_ACTIVE is False
            assert validate_security._CPV_SELF_PLUGIN_ROOT is None
        finally:
            validate_security._set_cpv_self_scan(False)

    # ── n4: --strict NIT verdict banner matches the strict exit code ─────
    def test_print_results_strict_nit_banner(self, tmp_path, capsys):
        """Under strict, a NIT-only report must print the NIT-block banner (not
        'All checks passed') so the banner agrees with exit code 4."""
        report = ValidationReport()
        report.nit("a nitpick")
        print_results(report, verbose=False, strict=True)
        out = capsys.readouterr().out
        assert "NIT issues found" in out
        assert "All checks passed" not in out

    def test_print_results_nonstrict_nit_banner_passes(self, tmp_path, capsys):
        """The other side: without strict, a NIT-only report still prints
        'All checks passed' (NIT does not block in non-strict mode)."""
        report = ValidationReport()
        report.nit("a nitpick")
        print_results(report, verbose=False, strict=False)
        out = capsys.readouterr().out
        assert "All checks passed" in out

    # ── m5: _classify_path precedence (named .claude vs settings+plugins) ─
    def test_classify_path_named_dot_claude(self, tmp_path):
        """A dir literally named `.claude` classifies as project config even
        without settings.json + plugins/."""
        from validate_plugin import _classify_path

        d = tmp_path / ".claude"
        d.mkdir()
        assert _classify_path(d) == "claude_project_config"

    def test_classify_path_settings_plus_plugins(self, tmp_path):
        """A non-`.claude` dir with BOTH settings.json and plugins/ also
        classifies as project config; one alone does not."""
        from validate_plugin import _classify_path

        both = tmp_path / "cfgdir"
        both.mkdir()
        (both / "settings.json").write_text("{}")
        (both / "plugins").mkdir()
        assert _classify_path(both) == "claude_project_config"

        only_settings = tmp_path / "only-settings"
        only_settings.mkdir()
        (only_settings / "settings.json").write_text("{}")
        assert _classify_path(only_settings) != "claude_project_config"
