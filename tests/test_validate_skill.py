#!/usr/bin/env python3
"""Tests for validate_skill.py - core skill validation functions.

Coverage: 10 tests covering validate_frontmatter, validate_name_field,
validate_skill_content, validate_directory_structure, validate_skill (main),
plus edge cases for missing SKILL.md, invalid frontmatter, and oversized content.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_skill import (  # noqa: E402
    MAX_SKILL_LINES,
    SkillValidationReport,
    parse_frontmatter,
    print_json,
    print_results,
    validate_agent_field,
    validate_allowed_tools_field,
    validate_argument_hint_field,
    validate_boolean_field,
    validate_context_field,
    validate_description_field,
    validate_directory_structure,
    validate_frontmatter,
    validate_hooks_field,
    validate_model_field,
    validate_name_field,
    validate_skill,
    validate_skill_content,
    validate_supporting_files,
)


def _make_report() -> ValidationReport:
    """Create a fresh ValidationReport for individual function tests."""
    return SkillValidationReport(skill_path="test")


class TestValidateFrontmatter:
    """Tests for validate_frontmatter parsing and field validation."""

    def test_valid_frontmatter_parsed_correctly(self, tmp_path):
        """Valid YAML frontmatter with known fields should parse without errors."""
        content = """---
name: my-skill
description: A skill that does things
model: sonnet
---
# Body content
"""
        report = _make_report()
        result = validate_frontmatter(tmp_path, content, report)
        assert result is not None
        assert result["name"] == "my-skill"
        assert result["description"] == "A skill that does things"
        assert result["model"] == "sonnet"
        assert not report.has_critical
        assert not report.has_major
        # Should log passed for valid frontmatter
        assert any(r.level == "PASSED" and "Valid YAML frontmatter" in r.message for r in report.results)

    def test_malformed_frontmatter_reports_critical(self, tmp_path):
        """Frontmatter starting with --- but missing closing --- should report CRITICAL."""
        content = "---\nname: broken\nno closing delimiter"
        report = _make_report()
        result = validate_frontmatter(tmp_path, content, report)
        assert result is None
        assert report.has_critical
        assert any("Malformed YAML frontmatter" in r.message for r in report.results)


class TestValidateNameField:
    """Tests for validate_name_field format and content rules."""

    def test_valid_lowercase_hyphenated_name(self):
        """A valid lowercase hyphenated name matching the directory should pass cleanly."""
        frontmatter = {"name": "my-cool-skill"}
        report = _make_report()
        validate_name_field(frontmatter, "my-cool-skill", report)
        assert not report.has_critical
        assert not report.has_major
        assert any(r.level == "PASSED" and "'name' field present" in r.message for r in report.results)

    def test_uppercase_name_reports_critical(self):
        """A name containing uppercase letters should report CRITICAL issue."""
        frontmatter = {"name": "My-Skill"}
        report = _make_report()
        validate_name_field(frontmatter, "my-skill", report)
        assert report.has_critical
        msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("uppercase" in m.lower() for m in msgs)


class TestValidateSkillContent:
    """Tests for validate_skill_content body validation."""

    def test_normal_content_passes(self):
        """Content with frontmatter and a reasonable body should pass line count check."""
        body_lines = "\n".join([f"Line {i}" for i in range(50)])
        content = f"---\nname: test\n---\n{body_lines}\n"
        report = _make_report()
        validate_skill_content(content, report)
        assert not report.has_major
        assert any(r.level == "PASSED" and "line count OK" in r.message for r in report.results)

    def test_empty_body_reports_major(self):
        """Content with frontmatter but empty body should report MAJOR issue."""
        content = "---\nname: test\n---\n   \n  \n"
        report = _make_report()
        validate_skill_content(content, report)
        assert report.has_major
        assert any("no content after frontmatter" in r.message for r in report.results if r.level == "MAJOR")

    def test_oversized_content_reports_minor(self):
        """Content exceeding MAX_SKILL_LINES should report MINOR issue."""
        lines = "\n".join([f"# Line {i}" for i in range(MAX_SKILL_LINES + 100)])
        content = f"---\nname: test\n---\n{lines}\n"
        report = _make_report()
        validate_skill_content(content, report)
        assert report.has_minor
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("recommended: under" in m for m in minor_msgs)


class TestValidateDirectoryStructure:
    """Tests for validate_directory_structure script executability checks."""

    def test_non_executable_script_reports_major(self, tmp_path):
        """A .py script without executable bit should report MAJOR issue."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        scripts = skill_dir / "scripts"
        scripts.mkdir()
        script_file = scripts / "run.py"
        script_file.write_text("#!/usr/bin/env python3\nprint('hi')")
        # Ensure NOT executable
        script_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

        report = _make_report()
        validate_directory_structure(skill_dir, report)
        assert report.has_major
        assert any("Script not executable" in r.message for r in report.results if r.level == "MAJOR")


class TestValidateSkillMainEntry:
    """Tests for validate_skill end-to-end orchestration."""

    def test_valid_skill_directory_passes(self, tmp_path):
        """A well-formed skill directory with valid frontmatter and body should pass."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: my-skill
description: A useful skill that processes data
---
# My Skill

This skill helps you process data efficiently.

## Usage

Follow these steps to use the skill.
""")
        report = validate_skill(skill_dir)
        assert not report.has_critical
        assert not report.has_major
        assert report.exit_code == 0

    def test_missing_skill_md_reports_critical(self, tmp_path):
        """A skill directory without SKILL.md should report CRITICAL and stop early."""
        skill_dir = tmp_path / "empty-skill"
        skill_dir.mkdir()
        # No SKILL.md created
        report = validate_skill(skill_dir)
        assert report.has_critical
        assert report.exit_code == 1
        assert any("SKILL.md not found" in r.message for r in report.results if r.level == "CRITICAL")
        # Should have stopped early - only the SKILL.md check result
        critical_results = [r for r in report.results if r.level == "CRITICAL"]
        assert len(critical_results) == 1


# =============================================================================
# Additional tests targeting uncovered lines (79, 89, 94-95, 102-103, 117,
# 135-140, 148-149, 152, 168, 178-188, 192-196, 199, 205, 218-234, 242,
# 248-269, 284-293, 301-319, 327-336, 344-353, 361-370, 396-397, 424,
# 434, 443-458, 474-475, 516-579, 584-596, 601-628, 632)
# =============================================================================


class TestParseFrontmatter:
    """Tests for parse_frontmatter standalone parsing logic."""

    def test_no_frontmatter_returns_none_and_original_content(self):
        """Content not starting with --- should return None frontmatter and full content as body."""
        content = "# Just a heading\nSome body text."
        fm, body, end_line = parse_frontmatter(content)
        assert fm is None
        assert body == content
        assert end_line == 0

    def test_empty_frontmatter_returns_empty_dict(self):
        """Frontmatter with --- delimiters but no fields should return empty dict (line 88-89)."""
        content = "---\n---\n# Body here"
        fm, body, end_line = parse_frontmatter(content)
        assert fm == {}
        assert "Body here" in body

    def test_yaml_error_returns_none(self):
        """Invalid YAML between --- delimiters should return None (lines 94-95)."""
        content = "---\n: :\n  bad:\n    - [\n---\nBody"
        fm, body, end_line = parse_frontmatter(content)
        assert fm is None
        assert body == content
        assert end_line == 0


class TestValidateFrontmatterExtended:
    """Extended tests for validate_frontmatter edge cases."""

    def test_no_frontmatter_reports_info(self, tmp_path):
        """Content without --- prefix should report INFO about missing frontmatter (lines 101-103)."""
        content = "# No Frontmatter\nJust content."
        report = _make_report()
        result = validate_frontmatter(tmp_path, content, report)
        assert result is None
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("No YAML frontmatter found" in m for m in info_msgs)

    def test_unknown_field_reports_warning(self, tmp_path):
        """Frontmatter with unknown fields should report WARNING (line 117+)."""
        content = "---\nname: my-skill\ncustom-field: something\nfoo: bar\n---\n# Body"
        report = _make_report()
        result = validate_frontmatter(tmp_path, content, report)
        assert result is not None
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("custom-field" in m for m in warning_msgs)
        assert any("foo" in m for m in warning_msgs)


class TestValidateNameFieldExtended:
    """Extended tests for validate_name_field edge cases."""

    def test_missing_name_falls_back_to_directory_name(self):
        """Missing 'name' field should use directory name and report INFO (lines 135-140)."""
        frontmatter = {"description": "A skill"}
        report = _make_report()
        validate_name_field(frontmatter, "my-dir-skill", report)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("my-dir-skill" in m for m in info_msgs)
        assert not report.has_critical

    def test_non_string_name_reports_critical(self):
        """Name field that is not a string should report CRITICAL (lines 148-149)."""
        frontmatter = {"name": 42}
        report = _make_report()
        validate_name_field(frontmatter, "some-dir", report)
        assert report.has_critical
        crit_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("must be a string" in m for m in crit_msgs)

    def test_name_exceeding_64_chars_reports_major(self):
        """Name longer than 64 characters should report MAJOR."""
        long_name = "a" + "-bcde" * 12 + "-bcd"  # 65 chars, valid kebab-case
        assert len(long_name) == 65
        frontmatter = {"name": long_name}
        report = _make_report()
        validate_name_field(frontmatter, long_name, report)
        assert report.has_major
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("exceeds 64" in m for m in major_msgs)

    def test_name_differs_from_directory_reports_major(self):
        """Name that differs from directory name should report MAJOR."""
        frontmatter = {"name": "actual-name"}
        report = _make_report()
        validate_name_field(frontmatter, "directory-name", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must match directory name" in m for m in major_msgs)


class TestValidateDescriptionField:
    """Tests for validate_description_field covering lines 178-208."""

    def test_missing_description_with_body_reports_info(self):
        """No description but body content present should report INFO fallback (lines 178-182)."""
        frontmatter = {"name": "test-skill"}
        report = _make_report()
        validate_description_field(frontmatter, "# Some Body\nText here", report)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("first paragraph" in m for m in info_msgs)

    def test_missing_description_no_body_reports_major(self):
        """No description and no body content should report MAJOR (lines 184-187)."""
        frontmatter = {"name": "test-skill"}
        report = _make_report()
        validate_description_field(frontmatter, "   ", report)
        assert report.has_major
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("No 'description' field and no body content" in m for m in major_msgs)

    def test_non_string_description_reports_major(self):
        """Description that is not a string should report MAJOR (lines 192-196)."""
        frontmatter = {"description": ["a", "list"]}
        report = _make_report()
        validate_description_field(frontmatter, "body", report)
        assert report.has_major
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be a string" in m for m in major_msgs)

    def test_short_description_reports_minor(self):
        """Description shorter than 10 characters should report MINOR (line 199)."""
        frontmatter = {"description": "Short"}
        report = _make_report()
        validate_description_field(frontmatter, "body", report)
        assert report.has_minor

    def test_long_description_reports_minor(self):
        """Description longer than 500 characters should report MINOR (line 205)."""
        frontmatter = {"description": "A" * 501}
        report = _make_report()
        validate_description_field(frontmatter, "body", report)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("long" in m for m in minor_msgs)


class TestValidateContextField:
    """Tests for validate_context_field covering lines 218-234."""

    def test_valid_context_fork_passes(self):
        """Context value 'fork' (the only valid value) should pass (line 234)."""
        frontmatter = {"context": "fork"}
        report = _make_report()
        validate_context_field(frontmatter, report)
        assert not report.has_critical
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("context" in m and "fork" in m for m in passed_msgs)

    def test_invalid_context_value_reports_critical(self):
        """An invalid context value should report CRITICAL (lines 228-231)."""
        frontmatter = {"context": "inline"}
        report = _make_report()
        validate_context_field(frontmatter, report)
        assert report.has_critical
        crit_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("Invalid 'context' value" in m for m in crit_msgs)

    def test_non_string_context_reports_critical(self):
        """Context value that is not a string should report CRITICAL (lines 220-225)."""
        frontmatter = {"context": 123}
        report = _make_report()
        validate_context_field(frontmatter, report)
        assert report.has_critical
        crit_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("must be a string" in m for m in crit_msgs)


class TestValidateAgentField:
    """Tests for validate_agent_field covering lines 237-272."""

    def test_agent_without_context_fork_reports_major(self):
        """Agent field without context:fork should report MAJOR (lines 258-262)."""
        frontmatter = {"agent": "Explore"}
        report = _make_report()
        validate_agent_field(frontmatter, report)
        assert report.has_major
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("no effect without" in m for m in major_msgs)

    def test_builtin_agent_with_fork_passes(self):
        """Built-in agent type with context:fork should pass (lines 265-266)."""
        frontmatter = {"agent": "Explore", "context": "fork"}
        report = _make_report()
        validate_agent_field(frontmatter, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("built-in" in m for m in passed_msgs)

    def test_custom_agent_reports_info(self):
        """Non-built-in agent type should report INFO about custom agent (lines 269-271)."""
        frontmatter = {"agent": "my-custom-agent", "context": "fork"}
        report = _make_report()
        validate_agent_field(frontmatter, report)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("not a built-in type" in m for m in info_msgs)

    def test_non_string_agent_reports_critical(self):
        """Agent field that is not a string should report CRITICAL (lines 250-255)."""
        frontmatter = {"agent": True, "context": "fork"}
        report = _make_report()
        validate_agent_field(frontmatter, report)
        assert report.has_critical

    def test_no_agent_with_fork_context_reports_info(self):
        """Missing agent field with context:fork should report INFO (lines 241-245)."""
        frontmatter = {"context": "fork"}
        report = _make_report()
        validate_agent_field(frontmatter, report)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("not specified with context: fork" in m for m in info_msgs)


class TestValidateBooleanField:
    """Tests for validate_boolean_field covering lines 284-293."""

    def test_valid_boolean_true_passes(self):
        """A valid boolean true value should pass (line 293)."""
        frontmatter = {"user-invocable": True}
        report = _make_report()
        validate_boolean_field(frontmatter, "user-invocable", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("user-invocable" in m for m in passed_msgs)

    def test_non_boolean_value_reports_critical(self):
        """A non-boolean value for boolean field should report CRITICAL (lines 287-290)."""
        frontmatter = {"disable-model-invocation": "yes"}
        report = _make_report()
        validate_boolean_field(frontmatter, "disable-model-invocation", report)
        assert report.has_critical
        crit_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("must be a boolean" in m for m in crit_msgs)


class TestValidateAllowedToolsField:
    """Tests for validate_allowed_tools_field covering lines 301-319."""

    def test_string_tool_list_passes(self):
        """Comma-separated string tool list should parse and pass (lines 303-305, 319)."""
        frontmatter = {"allowed-tools": "Bash, Read, Write"}
        report = _make_report()
        validate_allowed_tools_field(frontmatter, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("3 tool(s)" in m for m in passed_msgs)

    def test_list_tool_list_passes(self):
        """YAML list of tools should pass (lines 306-307)."""
        frontmatter = {"allowed-tools": ["Bash", "Read"]}
        report = _make_report()
        validate_allowed_tools_field(frontmatter, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("2 tool(s)" in m for m in passed_msgs)

    def test_invalid_type_reports_major(self):
        """Non-string non-list allowed-tools should report MAJOR (lines 309-312)."""
        frontmatter = {"allowed-tools": 42}
        report = _make_report()
        validate_allowed_tools_field(frontmatter, report)
        assert report.has_major

    def test_empty_list_reports_minor(self):
        """Empty allowed-tools list should report MINOR (lines 315-316)."""
        frontmatter = {"allowed-tools": []}
        report = _make_report()
        validate_allowed_tools_field(frontmatter, report)
        assert report.has_minor
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("empty" in m for m in minor_msgs)


class TestValidateModelField:
    """Tests for validate_model_field covering lines 327-336."""

    def test_valid_model_string_passes(self):
        """A valid model string should pass (line 336)."""
        frontmatter = {"model": "sonnet"}
        report = _make_report()
        validate_model_field(frontmatter, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("model" in m for m in passed_msgs)

    def test_non_string_model_reports_major(self):
        """Non-string model value should report MAJOR (lines 329-333)."""
        frontmatter = {"model": 3.5}
        report = _make_report()
        validate_model_field(frontmatter, report)
        assert report.has_major


class TestValidateArgumentHintField:
    """Tests for validate_argument_hint_field covering lines 344-353."""

    def test_valid_argument_hint_passes(self):
        """A valid argument-hint string should pass (line 353)."""
        frontmatter = {"argument-hint": "<file-path>"}
        report = _make_report()
        validate_argument_hint_field(frontmatter, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("argument-hint" in m for m in passed_msgs)

    def test_non_string_argument_hint_reports_major(self):
        """Non-string argument-hint should report MAJOR (lines 346-350)."""
        frontmatter = {"argument-hint": 99}
        report = _make_report()
        validate_argument_hint_field(frontmatter, report)
        assert report.has_major


class TestValidateHooksField:
    """Tests for validate_hooks_field covering lines 361-370."""

    def test_valid_hooks_dict_passes(self):
        """A valid hooks dict should pass (line 370)."""
        frontmatter = {"hooks": {"PreToolUse": {"Bash": "check_safety.sh"}}}
        report = _make_report()
        validate_hooks_field(frontmatter, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("hooks" in m for m in passed_msgs)

    def test_non_dict_hooks_reports_major(self):
        """Non-dict hooks value should report MAJOR (lines 363-367)."""
        frontmatter = {"hooks": "not-a-dict"}
        report = _make_report()
        validate_hooks_field(frontmatter, report)
        assert report.has_major
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be an object" in m for m in major_msgs)


class TestValidateSkillContentExtended:
    """Extended tests for validate_skill_content covering lines 395-397."""

    def test_task_oriented_content_without_arguments_placeholder_reports_info(self):
        """Skill with numbered steps but no $ARGUMENTS should report INFO (lines 395-397)."""
        content = "---\nname: deploy-skill\n---\n# Deploy\n\n1. Run the deployment\n2. Check status\n"
        report = _make_report()
        validate_skill_content(content, report)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("$ARGUMENTS" in m for m in info_msgs)


class TestValidateDirectoryStructureExtended:
    """Extended tests for validate_directory_structure covering line 424."""

    def test_executable_script_passes(self, tmp_path):
        """An executable .sh script should pass the executability check (line 424)."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        scripts = skill_dir / "scripts"
        scripts.mkdir()
        script_file = scripts / "deploy.sh"
        script_file.write_text("#!/bin/bash\necho 'deploy'")
        script_file.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

        report = _make_report()
        validate_directory_structure(skill_dir, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Script executable" in m for m in passed_msgs)


class TestValidateSupportingFiles:
    """Tests for validate_supporting_files covering lines 430-458."""

    def test_valid_local_reference_passes(self, tmp_path):
        """A local file reference that exists should pass (lines 457-458)."""
        skill_dir = tmp_path / "ref-skill"
        skill_dir.mkdir()
        (skill_dir / "examples").mkdir()
        (skill_dir / "examples" / "usage.md").write_text("# Example usage")
        (skill_dir / "SKILL.md").write_text(
            "---\nname: ref-skill\n---\n# Skill\n\nSee [usage](examples/usage.md) for details.\n"
        )
        report = _make_report()
        validate_supporting_files(skill_dir, report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("examples/usage.md" in m for m in passed_msgs)

    def test_missing_local_reference_reports_major(self, tmp_path):
        """A local file reference that does not exist should report MAJOR (lines 452-456)."""
        skill_dir = tmp_path / "broken-ref-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: broken-ref\n---\n# Skill\n\nSee [config](config/settings.yaml) for setup.\n"
        )
        report = _make_report()
        validate_supporting_files(skill_dir, report)
        assert report.has_major
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Referenced file not found" in m for m in major_msgs)

    def test_external_url_references_are_skipped(self, tmp_path):
        """External HTTP/HTTPS URLs should be skipped, not checked as local files (lines 443-444)."""
        skill_dir = tmp_path / "url-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: url-skill\n---\n# Skill\n\nSee [docs](https://example.com/docs).\n"
        )
        report = _make_report()
        validate_supporting_files(skill_dir, report)
        # No MAJOR issues since external URLs are skipped
        assert not report.has_major
        assert not report.has_critical

    def test_anchor_references_are_skipped(self, tmp_path):
        """Anchor links (#section) should be skipped (lines 447-448)."""
        skill_dir = tmp_path / "anchor-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: anchor-skill\n---\n# Skill\n\nSee [section](#usage) below.\n")
        report = _make_report()
        validate_supporting_files(skill_dir, report)
        assert not report.has_major


class TestValidateSkillEndToEnd:
    """End-to-end tests for validate_skill covering lines 474-475 and full orchestration."""

    def test_non_directory_path_reports_critical(self, tmp_path):
        """A path that is not a directory should report CRITICAL (lines 473-475)."""
        file_path = tmp_path / "not-a-dir.txt"
        file_path.write_text("I am a file")
        report = validate_skill(file_path)
        assert report.has_critical
        assert report.exit_code == 1
        crit_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("not a directory" in m for m in crit_msgs)

    def test_skill_without_frontmatter_still_validates_content(self, tmp_path):
        """Skill with no frontmatter should skip field validation but still validate body."""
        skill_dir = tmp_path / "no-fm-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# My Skill\n\nThis is a skill with useful content.\n")
        report = validate_skill(skill_dir)
        # No critical issues - missing frontmatter is INFO not critical
        assert not report.has_critical


class TestPrintResults:
    """Tests for print_results covering lines 516-579."""

    def test_print_results_non_verbose(self, capsys, tmp_path):
        """print_results in non-verbose mode should hide PASSED and INFO (lines 551-554)."""
        skill_dir = tmp_path / "print-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: print-skill\ndescription: A test skill for print output\n---\n# Content\nReal body.\n"
        )
        report = validate_skill(skill_dir)
        skill_report = SkillValidationReport(skill_path=str(skill_dir))
        skill_report.results = report.results
        print_results(skill_report, verbose=False)
        captured = capsys.readouterr()
        assert "Skill Validation" in captured.out
        assert "CRITICAL:" in captured.out
        # PASSED lines should not appear in non-verbose
        assert "[PASSED]" not in captured.out

    def test_print_results_verbose_shows_passed(self, capsys, tmp_path):
        """print_results in verbose mode should show PASSED and INFO (lines 544-546)."""
        skill_dir = tmp_path / "verbose-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: verbose-skill\ndescription: A test skill for verbose output\n---\n# Content\nReal body.\n"
        )
        report = validate_skill(skill_dir)
        skill_report = SkillValidationReport(skill_path=str(skill_dir))
        skill_report.results = report.results
        print_results(skill_report, verbose=True)
        captured = capsys.readouterr()
        assert "[PASSED]" in captured.out

    def test_print_results_exit_code_messages(self, capsys):
        """print_results should show correct status for each exit code level (lines 564-577)."""
        # Test exit_code == 0
        report_ok = SkillValidationReport(skill_path="test-ok")
        report_ok.passed("All good", "SKILL.md")
        print_results(report_ok)
        captured = capsys.readouterr()
        assert "passed" in captured.out

        # Test exit_code == 1 (critical)
        report_crit = SkillValidationReport(skill_path="test-crit")
        report_crit.critical("Something broke", "SKILL.md")
        print_results(report_crit)
        captured = capsys.readouterr()
        assert "CRITICAL" in captured.out

        # Test exit_code == 2 (major)
        report_maj = SkillValidationReport(skill_path="test-maj")
        report_maj.major("Significant problem", "SKILL.md")
        print_results(report_maj)
        captured = capsys.readouterr()
        assert "MAJOR" in captured.out

        # Test exit_code == 3 (minor only)
        report_min = SkillValidationReport(skill_path="test-min")
        report_min.minor("Small issue", "SKILL.md")
        print_results(report_min)
        captured = capsys.readouterr()
        assert "MINOR" in captured.out


class TestPrintJson:
    """Tests for print_json covering lines 584-596."""

    def test_print_json_output_structure(self, capsys, tmp_path):
        """print_json should output valid JSON with correct structure (lines 584-596)."""
        import json as json_mod

        skill_dir = tmp_path / "json-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: json-skill\ndescription: A skill to test JSON output\n---\n# JSON Skill\nContent.\n"
        )
        report = validate_skill(skill_dir)
        skill_report = SkillValidationReport(skill_path=str(skill_dir))
        skill_report.results = report.results
        print_json(skill_report)
        captured = capsys.readouterr()
        output = json_mod.loads(captured.out)
        assert "skill_path" in output
        assert "exit_code" in output
        assert "counts" in output
        assert "results" in output
        assert "critical" in output["counts"]
        assert "major" in output["counts"]
        assert "minor" in output["counts"]
        assert isinstance(output["results"], list)


class TestMainEntryPoint:
    """Tests for main() entry point covering lines 601-628."""

    def test_main_with_nonexistent_path(self, monkeypatch, capsys):
        """main() with a nonexistent path should print error and return 1 (lines 615-617)."""
        from validate_skill import main

        monkeypatch.setattr("sys.argv", ["validate_skill.py", "/nonexistent/path/to/skill"])
        result = main()
        assert result == 1
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

    def test_main_with_valid_skill_json_output(self, monkeypatch, capsys, tmp_path):
        """main() with --json flag should produce JSON output (lines 621-622)."""
        from validate_skill import main

        skill_dir = tmp_path / "main-json-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: main-json-skill\ndescription: Testing main with JSON output flag\n---\n# Skill\nBody content here.\n"
        )
        monkeypatch.setattr("sys.argv", ["validate_skill.py", str(skill_dir), "--json"])
        import json as json_mod

        result = main()
        captured = capsys.readouterr()
        output = json_mod.loads(captured.out)
        assert output["exit_code"] == result
        assert "results" in output

    def test_main_with_verbose_flag(self, monkeypatch, capsys, tmp_path):
        """main() with --verbose should show PASSED results (lines 623-624)."""
        from validate_skill import main

        skill_dir = tmp_path / "main-verbose-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: main-verbose-skill\ndescription: Testing main verbose flag output\n---\n# Skill\nBody.\n"
        )
        monkeypatch.setattr("sys.argv", ["validate_skill.py", str(skill_dir), "--verbose"])
        main()
        captured = capsys.readouterr()
        assert "[PASSED]" in captured.out

    def test_main_strict_mode_with_nit(self, monkeypatch, capsys, tmp_path):
        """main() with --strict should use exit_code_strict (lines 626-627)."""
        from validate_skill import main

        skill_dir = tmp_path / "strict-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: strict-skill\ndescription: Testing strict mode with nit issues\n---\n# Skill\nBody content.\n"
        )
        monkeypatch.setattr("sys.argv", ["validate_skill.py", str(skill_dir), "--strict"])
        result = main()
        # Should return integer exit code
        assert isinstance(result, int)


class TestNamedArgSubstitutionShellVarHeuristic:
    """Tests for the $<name> substitution check shell-variable heuristic.

    The check is meant to catch undeclared skill arguments (which silently
    expand to "" at runtime). It must NOT trigger on:
      - $VAR references inside fenced code blocks
      - $VAR references inside inline backtick spans
      - ALL_UPPERCASE shell-variable names ($MAIN_ROOT, $REPORT, $PWD, etc.)
      - Known Claude env vars ($CLAUDE_PROJECT_DIR, $CLAUDE_PLUGIN_ROOT, etc.)
    """

    def test_undeclared_lowercase_arg_is_flagged(self):
        """Undeclared lowercase $myarg in body should still be flagged."""
        content = "---\nname: my-skill\n---\nThe value of $myarg drives the flow.\n"
        report = _make_report()
        validate_skill_content(content, report, declared_args=[])
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("$myarg" in m and "not declared" in m for m in major_msgs)

    def test_declared_lowercase_arg_passes(self):
        """Declared lowercase $myarg in body should NOT be flagged."""
        content = "---\nname: my-skill\n---\nThe value of $myarg drives the flow.\n"
        report = _make_report()
        validate_skill_content(content, report, declared_args=["myarg"])
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("$myarg" in m for m in major_msgs)

    def test_uppercase_shell_var_is_skipped(self):
        """ALL_UPPERCASE $MAIN_ROOT outside backticks must NOT be flagged."""
        content = "---\nname: my-skill\n---\nAnchor reports to $MAIN_ROOT, which is the main checkout root.\n"
        report = _make_report()
        validate_skill_content(content, report, declared_args=[])
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("MAIN_ROOT" in m for m in major_msgs)

    def test_common_uppercase_shell_vars_skipped(self):
        """$REPORT, $TIMESTAMP, $TS, $PWD, $HOME must NOT be flagged."""
        content = "---\nname: my-skill\n---\nSet $REPORT, $TIMESTAMP, $TS, $PWD, $HOME — all shell vars.\n"
        report = _make_report()
        validate_skill_content(content, report, declared_args=[])
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        for var in ("REPORT", "TIMESTAMP", "TS", "PWD", "HOME"):
            assert not any(f"${var}" in m for m in major_msgs), f"${var} was wrongly flagged"

    def test_inline_backtick_span_is_stripped(self):
        """`$myarg` inside inline backticks must NOT be flagged."""
        content = "---\nname: my-skill\n---\nThe substitution `$myarg` is documented as an example.\n"
        report = _make_report()
        validate_skill_content(content, report, declared_args=[])
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("$myarg" in m and "not declared" in m for m in major_msgs)

    def test_fenced_code_block_is_stripped(self):
        """$myarg inside ```bash fenced block must NOT be flagged."""
        content = "---\nname: my-skill\n---\n```bash\nrun --flag $myarg\n```\n"
        report = _make_report()
        validate_skill_content(content, report, declared_args=[])
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("$myarg" in m and "not declared" in m for m in major_msgs)

    def test_known_env_vars_skipped(self):
        """$CLAUDE_PROJECT_DIR, $CLAUDE_PLUGIN_ROOT, etc. must NOT be flagged."""
        content = (
            "---\nname: my-skill\n---\nUse $CLAUDE_PROJECT_DIR for project root and $CLAUDE_PLUGIN_ROOT for plugin.\n"
        )
        report = _make_report()
        validate_skill_content(content, report, declared_args=[])
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        for var in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT"):
            assert not any(f"${var}" in m for m in major_msgs)

    def test_arguments_placeholder_skipped(self):
        """$ARGUMENTS is well-known and must never be flagged."""
        content = "---\nname: my-skill\n---\nThe full args: $ARGUMENTS\n"
        report = _make_report()
        validate_skill_content(content, report, declared_args=[])
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("$ARGUMENTS" in m for m in major_msgs)

    def test_brace_form_already_safe(self):
        """${MAIN_ROOT} brace form was always safe (regex doesn't match {)."""
        content = "---\nname: my-skill\n---\nAnchor to ${MAIN_ROOT}/reports — brace form.\n"
        report = _make_report()
        validate_skill_content(content, report, declared_args=[])
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("MAIN_ROOT" in m for m in major_msgs)

    def test_mixed_case_undeclared_arg_still_flagged(self):
        """$MyArg (mixed case, not lowercase, not all-upper) should be flagged."""
        content = "---\nname: my-skill\n---\nThe value of $myArg drives the flow.\n"
        report = _make_report()
        validate_skill_content(content, report, declared_args=[])
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("$myArg" in m and "not declared" in m for m in major_msgs)
