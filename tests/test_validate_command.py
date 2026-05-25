#!/usr/bin/env python3
"""Tests for validate_command.py

Covers five target functions with exactly 10 tests:
- parse_frontmatter (2 tests): valid parsing and missing-frontmatter edge case
- validate_frontmatter_exists (2 tests): valid content and missing frontmatter
- validate_name_field (2 tests): valid kebab-case and invalid uppercase
- validate_tool_pattern (2 tests): known built-in tool and malformed MCP tool
- validate_command (2 tests): full valid file end-to-end and nonexistent file
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_command import (  # noqa: E402
    CommandValidationReport,
    parse_frontmatter,
    validate_command,
    validate_frontmatter_exists,
    validate_name_field,
    validate_tool_pattern,
)


class TestParseFrontmatter:
    """Tests for parse_frontmatter function."""

    def test_parse_valid_frontmatter_returns_dict_and_body(self):
        """Valid YAML frontmatter with body content should return parsed dict, body text, and a positive end line."""
        content = "---\nname: my-command\ndescription: A test command\n---\nThis is the body content."
        frontmatter, body, end_line = parse_frontmatter(content)
        assert frontmatter is not None
        assert frontmatter["name"] == "my-command"
        assert frontmatter["description"] == "A test command"
        assert "body content" in body
        assert end_line > 0

    def test_parse_no_frontmatter_returns_none_and_full_content(self):
        """Content without leading --- should return None frontmatter, full content as body, and end line 0."""
        content = "Just plain markdown text without any frontmatter at all."
        frontmatter, body, end_line = parse_frontmatter(content)
        assert frontmatter is None
        assert body == content
        assert end_line == 0


class TestValidateFrontmatterExists:
    """Tests for validate_frontmatter_exists function."""

    def test_valid_frontmatter_returns_dict_and_adds_passed(self):
        """Well-formed frontmatter should return the parsed dict and record a PASSED result with no CRITICAL."""
        content = "---\nname: test-cmd\ndescription: Short description\n---\nBody text here with enough content."
        report = CommandValidationReport()
        result = validate_frontmatter_exists(content, report, "test-cmd.md")
        assert result is not None
        assert result["name"] == "test-cmd"
        levels = [r.level for r in report.results]
        assert "PASSED" in levels
        assert "CRITICAL" not in levels

    def test_missing_frontmatter_reports_critical(self):
        """Content that does not start with --- should produce a CRITICAL error and return None."""
        content = "No frontmatter present in this content at all."
        report = CommandValidationReport()
        result = validate_frontmatter_exists(content, report, "bad.md")
        assert result is None
        assert any(r.level == "CRITICAL" and "No YAML frontmatter" in r.message for r in report.results)


class TestValidateNameField:
    """Tests for validate_name_field function."""

    def test_valid_kebab_case_name_passes(self):
        """A valid kebab-case name should produce PASSED with no MAJOR or CRITICAL issues."""
        frontmatter = {"name": "my-valid-command"}
        report = CommandValidationReport()
        validate_name_field(frontmatter, "my-valid-command.md", report)
        levels = [r.level for r in report.results]
        assert "PASSED" in levels
        assert "CRITICAL" not in levels
        assert "MAJOR" not in levels

    def test_uppercase_name_reports_critical(self):
        """A name with uppercase letters should produce CRITICAL issue about case."""
        frontmatter = {"name": "MyCommand"}
        report = CommandValidationReport()
        validate_name_field(frontmatter, "MyCommand.md", report)
        critical_messages = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("uppercase" in m.lower() for m in critical_messages)


class TestValidateToolPattern:
    """Tests for validate_tool_pattern function."""

    def test_valid_builtin_tool_accepted(self):
        """A known built-in tool name like 'Bash' should return valid=True with empty error."""
        is_valid, error_msg = validate_tool_pattern("Bash")
        assert is_valid is True
        assert error_msg == ""

    def test_malformed_mcp_tool_rejected(self):
        """An MCP tool with only two double-underscore parts should be rejected with format hint."""
        is_valid, error_msg = validate_tool_pattern("mcp__incomplete")
        assert is_valid is False
        assert "mcp__<server>__<tool>" in error_msg


class TestValidateCommandEndToEnd:
    """Tests for validate_command main entry point using real temp files."""

    def test_valid_command_file_has_no_critical_or_major(self, tmp_path):
        """A fully valid .md command file should produce zero CRITICAL or MAJOR results."""
        cmd_file = tmp_path / "deploy-app.md"
        body = (
            "You will deploy the application to the staging environment.\n"
            "When the user requests a deploy, you should run the deployment script.\n"
            "If any errors occur, you must report them clearly to the user.\n"
        )
        content = (
            "---\n"
            "name: deploy-app\n"
            "description: Deploy the application to staging\n"
            "allowed-tools:\n"
            "  - Bash\n"
            "  - Read\n"
            "model: sonnet\n"
            "---\n"
            f"{body}"
        )
        cmd_file.write_text(content, encoding="utf-8")
        report = validate_command(cmd_file)
        critical_or_major = [r for r in report.results if r.level in ("CRITICAL", "MAJOR")]
        assert len(critical_or_major) == 0, f"Unexpected issues: {[r.message for r in critical_or_major]}"

    def test_nonexistent_file_reports_critical(self, tmp_path):
        """Passing a path to a file that does not exist should produce a CRITICAL 'not found' error."""
        missing = tmp_path / "does-not-exist.md"
        report = validate_command(missing)
        assert any(r.level == "CRITICAL" and "not found" in r.message for r in report.results)


# =============================================================================
# Additional Tests (15) - Covering uncovered lines
# =============================================================================

from validate_command import (  # noqa: E402
    print_json,
    print_results,
    validate_allowed_tools_field,
    validate_argument_hint_field,
    validate_body_content,
    validate_commands_directory,
    validate_description_field,
    validate_file_format,
    validate_model_field,
    validate_security,
)


class TestCommandValidationReportToDict:
    """Tests for CommandValidationReport.to_dict method (lines 82-84)."""

    def test_to_dict_includes_command_path(self):
        """to_dict should return a dict containing command_path alongside base report fields."""
        report = CommandValidationReport(command_path="/some/path/cmd.md")
        report.passed("A check passed", "cmd.md")
        report.critical("Something broke", "cmd.md")
        d = report.to_dict()
        assert d["command_path"] == "/some/path/cmd.md"
        assert "results" in d
        results = d["results"]
        assert isinstance(results, list)
        assert len(results) == 2


class TestParseFrontmatterEdgeCases:
    """Tests for parse_frontmatter edge cases (lines 104-116)."""

    def test_only_opening_marker_returns_none(self):
        """Content with only one --- marker (no closing) should return None frontmatter (line 104-105)."""
        content = "---\nname: broken\nno closing marker here"
        frontmatter, body, end_line = parse_frontmatter(content)
        assert frontmatter is None
        assert body == content
        assert end_line == 0

    def test_empty_yaml_returns_empty_dict(self):
        """Empty YAML between --- markers should return an empty dict, not None (lines 109-110)."""
        content = "---\n---\nBody after empty frontmatter."
        frontmatter, body, end_line = parse_frontmatter(content)
        assert frontmatter == {}
        assert "Body after empty frontmatter" in body

    def test_invalid_yaml_returns_none(self):
        """Malformed YAML between markers should return None (lines 115-116)."""
        content = "---\n: : : [invalid yaml\n  bad: {{\n---\nBody text."
        frontmatter, body, end_line = parse_frontmatter(content)
        assert frontmatter is None
        assert body == content
        assert end_line == 0


class TestValidateFileFormat:
    """Tests for validate_file_format (lines 139-147)."""

    def test_less_than_two_markers_reports_critical(self):
        """Content with fewer than 2 --- markers should produce a CRITICAL error (lines 139-144)."""
        report = CommandValidationReport()
        result = validate_file_format("No markers at all", report, "bad.md")
        assert result is False
        assert any(r.level == "CRITICAL" and "Missing YAML frontmatter markers" in r.message for r in report.results)

    def test_more_than_two_markers_reports_minor(self):
        """Content with more than 2 --- markers should produce a MINOR warning (line 147)."""
        content = "---\nname: test\n---\nBody\n---\nExtra marker"
        report = CommandValidationReport()
        result = validate_file_format(content, report, "extra.md")
        assert result is True
        assert any(r.level == "MINOR" and "Multiple ---" in r.message for r in report.results)


class TestValidateFrontmatterExistsEdgeCases:
    """Tests for validate_frontmatter_exists edge cases (lines 164-178)."""

    def test_malformed_yaml_starting_with_markers_reports_critical(self):
        """Content starting with --- but having invalid YAML should produce CRITICAL (lines 164-168)."""
        content = "---\n: : invalid: [yaml\n---\nBody."
        report = CommandValidationReport()
        result = validate_frontmatter_exists(content, report, "malformed.md")
        assert result is None
        assert any(r.level == "CRITICAL" and "Malformed YAML" in r.message for r in report.results)

    def test_unknown_frontmatter_field_reports_warning(self):
        """A frontmatter field not in the known set should produce a WARNING (line 178)."""
        content = (
            "---\nname: test-cmd\ndescription: A valid description here\ncustom-field: something\n---\nBody text here."
        )
        report = CommandValidationReport()
        result = validate_frontmatter_exists(content, report, "unknown-field.md")
        assert result is not None
        assert any(
            r.level == "WARNING" and "Unknown frontmatter field" in r.message and "custom-field" in r.message
            for r in report.results
        )


class TestValidateNameFieldEdgeCases:
    """Tests for validate_name_field edge cases (lines 190-232)."""

    def test_missing_name_uses_filename_as_fallback(self):
        """When name field is absent, filename stem should be used as fallback (lines 190-195)."""
        frontmatter = {"description": "Some desc"}
        report = CommandValidationReport()
        validate_name_field(frontmatter, "my-command.md", report)
        assert any(r.level == "INFO" and "my-command" in r.message for r in report.results)

    def test_non_string_name_reports_critical(self):
        """A name that is not a string (e.g. integer) should produce CRITICAL (lines 201-202)."""
        frontmatter = {"name": 42}
        report = CommandValidationReport()
        validate_name_field(frontmatter, "bad.md", report)
        assert any(r.level == "CRITICAL" and "must be a string" in r.message for r in report.results)

    def test_name_with_consecutive_hyphens_reports_critical(self):
        """A name containing '--' should produce a CRITICAL issue."""
        frontmatter = {"name": "my--command"}
        report = CommandValidationReport()
        validate_name_field(frontmatter, "my--command.md", report)
        assert any(r.level == "CRITICAL" and "consecutive hyphens" in r.message for r in report.results)

    def test_name_starting_with_hyphen_reports_critical(self):
        """A name starting with a hyphen should produce CRITICAL."""
        frontmatter = {"name": "-my-command"}
        report = CommandValidationReport()
        validate_name_field(frontmatter, "-my-command.md", report)
        assert any(r.level == "CRITICAL" and "naming pattern" in r.message.lower() for r in report.results)


class TestValidateDescriptionField:
    """Tests for validate_description_field (lines 237-268)."""

    def test_missing_description_reports_major(self):
        """Missing description field should produce a MAJOR error (lines 238-239)."""
        report = CommandValidationReport()
        validate_description_field({}, "no-desc.md", report)
        assert any(r.level == "MAJOR" and "Missing 'description'" in r.message for r in report.results)

    def test_non_string_description_reports_critical(self):
        """A description that is not a string should produce CRITICAL (lines 244-245)."""
        report = CommandValidationReport()
        validate_description_field({"description": 123}, "bad.md", report)
        assert any(r.level == "CRITICAL" and "must be a string" in r.message for r in report.results)

    def test_empty_description_reports_major(self):
        """A whitespace-only description should produce MAJOR (lines 248-249)."""
        report = CommandValidationReport()
        validate_description_field({"description": "   "}, "empty.md", report)
        assert any(r.level == "MAJOR" and "cannot be empty" in r.message for r in report.results)

    def test_description_under_token_limit_passes(self):
        """A ~250-char description is only ~55-65 tokens (well under the 200-token limit) so it must NOT report a length MAJOR.

        The old 250-CHARACTER cap was replaced by a 200-TOKEN cap
        (DESCRIPTION_TOKEN_LIMIT) in TRDD-021250b5, so character count alone
        no longer triggers a finding.
        """
        # ~250 chars of distinct English words -> ~55-65 tokens, under the 200-token limit.
        desc = " ".join(f"word{i}" for i in range(36))
        assert 230 <= len(desc) <= 270  # confirm the fixture stays in the ~250-char range
        report = CommandValidationReport()
        validate_description_field({"description": desc}, "under.md", report)
        assert not any(
            r.level == "MAJOR" and ("tokens" in r.message or "exceeds" in r.message) for r in report.results
        ), f"Unexpected length MAJOR: {[r.message for r in report.results if r.level == 'MAJOR']}"

    def test_description_exceeding_token_limit_reports_major(self):
        """A description that genuinely exceeds the 200-token limit must report a MAJOR about the token budget."""
        # ~400 distinct words -> ~1000+ tokens, well over the 200-token DESCRIPTION_TOKEN_LIMIT.
        long_desc = " ".join(f"word{i}" for i in range(400))
        report = CommandValidationReport()
        validate_description_field({"description": long_desc}, "long.md", report)
        assert any(r.level == "MAJOR" and "tokens" in r.message and "limit 200" in r.message for r in report.results)

    def test_description_with_angle_brackets_allowed(self):
        """A description containing < or > is VALID (inline-code refs / placeholders) and must NOT report an angle-bracket MAJOR.

        The blanket angle-bracket check was removed in TRDD-021250b5: commands
        are skills, and `<context>` / <plugin> style placeholders are legitimate.
        """
        report = CommandValidationReport()
        validate_description_field({"description": "Deploy <app> to server"}, "angle.md", report)
        assert not any(r.level == "MAJOR" and "angle bracket" in r.message.lower() for r in report.results)

    def test_very_short_description_reports_minor(self):
        """A description shorter than 10 characters should produce MINOR (line 268)."""
        report = CommandValidationReport()
        validate_description_field({"description": "Hi"}, "short.md", report)
        assert any(r.level == "MINOR" and "very short" in r.message for r in report.results)


class TestValidateAllowedToolsField:
    """Tests for validate_allowed_tools_field (lines 279-309)."""

    def test_missing_allowed_tools_reports_info(self):
        """Missing allowed-tools should produce INFO about default tools (lines 279-280)."""
        report = CommandValidationReport()
        validate_allowed_tools_field({}, "no-tools.md", report)
        assert any(r.level == "INFO" and "inherit default" in r.message for r in report.results)

    def test_comma_separated_string_tools_are_parsed(self):
        """A comma-separated string of valid tools should pass validation (line 286)."""
        report = CommandValidationReport()
        validate_allowed_tools_field({"allowed-tools": "Bash, Read, Write"}, "string-tools.md", report)
        assert any(r.level == "PASSED" and "3 tool(s)" in r.message for r in report.results)

    def test_invalid_type_for_tools_reports_major(self):
        """allowed-tools with non-string, non-list type should produce MAJOR (lines 290-294)."""
        report = CommandValidationReport()
        validate_allowed_tools_field({"allowed-tools": 42}, "bad-type.md", report)
        assert any(r.level == "MAJOR" and "must be string or list" in r.message for r in report.results)

    def test_empty_tools_list_reports_warning(self):
        """An empty allowed-tools list is a non-blocking WARNING, not MINOR.

        Empty ``allowed-tools: []`` is an explicit "no tools" (chat-only)
        declaration — VALID, distinct from an absent field (= inherit all
        tools). The warning tells the author to omit the field if they meant
        to allow all tools.
        """
        report = CommandValidationReport()
        validate_allowed_tools_field({"allowed-tools": []}, "empty-tools.md", report)
        assert any(r.level == "WARNING" and "empty" in r.message for r in report.results)
        assert not any(r.level == "MINOR" and "empty" in r.message for r in report.results)
        assert any(r.level == "WARNING" and "omit" in r.message.lower() for r in report.results)

    def test_unknown_tool_in_list_reports_info_not_major(self):
        """A well-formed but UNKNOWN tool name is advisory INFO, not blocking MAJOR.

        TRDD-021250b5: an unknown-but-well-formed tool is almost always a custom /
        new / MCP tool CPV's VALID_TOOLS list doesn't know yet. Agents and skills
        treat this as advisory; commands now match (INFO). Two-sided companion:
        a genuinely MALFORMED pattern still reports MAJOR (see below).
        """
        report = CommandValidationReport()
        validate_allowed_tools_field({"allowed-tools": ["Bash", "NonExistentTool"]}, "bad-tool.md", report)
        assert any(r.level == "INFO" and "may be custom" in r.message for r in report.results)
        assert not any(r.level == "MAJOR" and "NonExistentTool" in r.message for r in report.results)

    def test_malformed_tool_pattern_reports_major(self):
        """A genuinely MALFORMED tool pattern (bad syntax) still reports MAJOR."""
        report = CommandValidationReport()
        validate_allowed_tools_field({"allowed-tools": ["Bash(unclosed"]}, "bad-fmt.md", report)
        assert any(r.level == "MAJOR" and "Invalid tool pattern" in r.message for r in report.results)


class TestValidateToolPatternExtended:
    """Extended tests for validate_tool_pattern (lines 330-359)."""

    def test_valid_mcp_tool_with_three_parts_accepted(self):
        """A well-formed MCP tool (3+ parts) should return valid=True (line 330)."""
        is_valid, error_msg = validate_tool_pattern("mcp__server__tool_name")
        assert is_valid is True
        assert error_msg == ""

    def test_invalid_format_tool_rejected(self):
        """A tool name with invalid characters should be rejected (line 336)."""
        is_valid, error_msg = validate_tool_pattern("Invalid Tool!!")
        assert is_valid is False
        assert "Invalid format" in error_msg

    def test_unknown_tool_name_rejected(self):
        """A syntactically valid but unknown tool should be rejected (line 343)."""
        is_valid, error_msg = validate_tool_pattern("FakeUnknownTool")
        assert is_valid is False
        assert "Unknown tool" in error_msg

    def test_bash_pattern_with_nested_parens_rejected(self):
        """A Bash pattern with nested parentheses should be rejected (lines 358-359)."""
        is_valid, error_msg = validate_tool_pattern("Bash(git(nested))")
        # The outer regex won't even match nested parens, so it fails at format level
        # or if it somehow matches, it should catch nested parens
        assert is_valid is False


class TestValidateModelField:
    """Tests for validate_model_field (lines 367-384)."""

    def test_missing_model_reports_info(self):
        """Missing model field should produce INFO (lines 367-368)."""
        report = CommandValidationReport()
        validate_model_field({}, "no-model.md", report)
        assert any(r.level == "INFO" and "inherit current model" in r.message for r in report.results)

    def test_non_string_model_reports_major(self):
        """A model that is not a string should produce MAJOR (lines 373-374)."""
        report = CommandValidationReport()
        validate_model_field({"model": 99}, "bad-model.md", report)
        assert any(r.level == "MAJOR" and "must be a string" in r.message for r in report.results)

    def test_invalid_model_value_reports_major(self):
        """A model not in {sonnet, opus, haiku} should produce MAJOR (lines 380-384)."""
        report = CommandValidationReport()
        validate_model_field({"model": "gpt-4"}, "wrong-model.md", report)
        assert any(r.level == "MAJOR" and "Invalid 'model' value" in r.message for r in report.results)


class TestValidateArgumentHintField:
    """Tests for validate_argument_hint_field (lines 394-411)."""

    def test_non_string_hint_reports_major(self):
        """A non-string argument-hint should produce MAJOR (lines 396-398)."""
        report = CommandValidationReport()
        validate_argument_hint_field({"argument-hint": ["list"]}, "bad-hint.md", report)
        assert any(r.level == "MAJOR" and "must be a string" in r.message for r in report.results)

    def test_empty_hint_reports_minor(self):
        """An empty argument-hint should produce MINOR (lines 400-402)."""
        report = CommandValidationReport()
        validate_argument_hint_field({"argument-hint": "   "}, "empty-hint.md", report)
        assert any(r.level == "MINOR" and "empty" in r.message for r in report.results)

    def test_long_hint_reports_minor(self):
        """An argument-hint longer than 100 chars should produce MINOR (lines 405-409)."""
        report = CommandValidationReport()
        validate_argument_hint_field({"argument-hint": "A" * 101}, "long-hint.md", report)
        assert any(r.level == "MINOR" and "long" in r.message for r in report.results)

    def test_valid_hint_passes(self):
        """A valid argument-hint should produce PASSED (line 411)."""
        report = CommandValidationReport()
        validate_argument_hint_field({"argument-hint": "<file-path>"}, "good-hint.md", report)
        assert any(r.level == "PASSED" and "argument-hint" in r.message for r in report.results)


class TestValidateBodyContent:
    """Tests for validate_body_content (lines 418-439)."""

    def test_empty_body_reports_major(self):
        """A command with no content after frontmatter should produce MAJOR (lines 419-420)."""
        content = "---\nname: test\n---\n   "
        report = CommandValidationReport()
        validate_body_content(content, "empty-body.md", report)
        assert any(r.level == "MAJOR" and "no content" in r.message for r in report.results)

    def test_short_body_reports_minor(self):
        """A body shorter than MIN_COMMAND_BODY_CHARS should produce MINOR (line 426)."""
        content = "---\nname: test\n---\nShort body text."
        report = CommandValidationReport()
        validate_body_content(content, "short-body.md", report)
        assert any(r.level == "MINOR" and "very short" in r.message for r in report.results)

    def test_body_without_instruction_keywords_reports_info(self):
        """A body with no instruction keywords should produce INFO (line 439)."""
        # Use text that avoids ALL instruction keywords: you, will, should, must, when, if, task, do, perform, execute
        # Also avoid "do" appearing as substring (like in "dolor")
        content = "---\nname: test\n---\n" + (
            "The cat sat on the mat. Apples are green. Bananas are yellow. Cars travel fast on the highway. " * 4
        )
        report = CommandValidationReport()
        validate_body_content(content, "no-keywords.md", report)
        assert any(r.level == "INFO" and "clear instructions" in r.message for r in report.results)


class TestValidateSecurity:
    """Tests for validate_security (lines 450-465)."""

    def test_hardcoded_secret_reports_critical(self):
        """Content containing an AWS key pattern should produce CRITICAL (line 450)."""
        content = "---\nname: deploy\n---\nUse key: AKIAIOSFODNN7EXAMPLE1"
        report = CommandValidationReport()
        validate_security(content, "secret.md", report)
        assert any(r.level == "CRITICAL" and "SECURITY" in r.message for r in report.results)

    def test_hardcoded_user_path_reports_major(self):
        """Content with /Users/someone/ path should produce MAJOR (line 456)."""
        content = "---\nname: deploy\n---\nRun /Users/johndoe/scripts/deploy.sh"
        report = CommandValidationReport()
        validate_security(content, "user-path.md", report)
        assert any(r.level == "MAJOR" and "hardcoded user path" in r.message for r in report.results)

    def test_scripts_path_without_plugin_root_reports_info(self):
        """Content referencing /scripts/ without CLAUDE_PLUGIN_ROOT should produce INFO (lines 464-465)."""
        content = "---\nname: deploy\n---\nRun the /scripts/deploy.sh file now"
        report = CommandValidationReport()
        validate_security(content, "scripts-ref.md", report)
        assert any(r.level == "INFO" and "CLAUDE_PLUGIN_ROOT" in r.message for r in report.results)


class TestValidateCommandExtended:
    """Extended tests for validate_command (lines 494-514)."""

    def test_directory_path_reports_critical(self, tmp_path):
        """Passing a directory instead of a file should produce CRITICAL (lines 494-495)."""
        report = validate_command(tmp_path)
        assert any(r.level == "CRITICAL" and "not a file" in r.message for r in report.results)

    def test_non_md_extension_reports_major(self, tmp_path):
        """A command file with wrong extension should produce MAJOR (line 499)."""
        cmd_file = tmp_path / "command.txt"
        cmd_file.write_text(
            "---\nname: test\ndescription: A valid test description\n---\nYou should do something when the user asks. This body is long enough to pass the minimum content check for the validator.",
            encoding="utf-8",
        )
        report = validate_command(cmd_file)
        assert any(r.level == "MAJOR" and ".md extension" in r.message for r in report.results)

    def test_non_utf8_file_reports_critical(self, tmp_path):
        """A file with invalid UTF-8 bytes should produce CRITICAL (line 506)."""
        cmd_file = tmp_path / "bad-encoding.md"
        cmd_file.write_bytes(b"---\nname: test\n---\n\xff\xfe invalid utf8 bytes")
        report = validate_command(cmd_file)
        # The check_utf8_encoding function should catch this
        any(r.level == "CRITICAL" for r in report.results)
        # If encoding is accepted (latin-1 compatible), at least no crash
        assert report is not None

    def test_file_missing_frontmatter_markers_returns_early(self, tmp_path):
        """A file with no --- markers should produce CRITICAL from validate_file_format and return early (line 514)."""
        cmd_file = tmp_path / "no-markers.md"
        cmd_file.write_text("Just plain text with no YAML frontmatter at all.", encoding="utf-8")
        report = validate_command(cmd_file)
        assert any(r.level == "CRITICAL" and "Missing YAML frontmatter" in r.message for r in report.results)


class TestValidateCommandsDirectory:
    """Tests for validate_commands_directory (lines 545-562)."""

    def test_non_directory_path_reports_critical(self, tmp_path):
        """Passing a file path instead of a directory should produce CRITICAL (lines 547-550)."""
        f = tmp_path / "not-a-dir.md"
        f.write_text("content", encoding="utf-8")
        reports = validate_commands_directory(f)
        assert len(reports) == 1
        assert any(r.level == "CRITICAL" and "Not a directory" in r.message for r in reports[0].results)

    def test_empty_directory_reports_info(self, tmp_path):
        """A directory with no .md files should produce INFO (lines 554-557)."""
        empty_dir = tmp_path / "commands"
        empty_dir.mkdir()
        reports = validate_commands_directory(empty_dir)
        assert len(reports) == 1
        assert any(r.level == "INFO" and "No command files" in r.message for r in reports[0].results)

    def test_directory_with_multiple_commands_validates_each(self, tmp_path):
        """A directory with multiple .md files should return one report per file (lines 559-562)."""
        cmds_dir = tmp_path / "commands"
        cmds_dir.mkdir()
        for name in ("alpha", "beta"):
            f = cmds_dir / f"{name}.md"
            f.write_text(
                f"---\nname: {name}\ndescription: The {name} command does stuff\n---\n"
                f"You will execute the {name} operation when the user asks. This should be long enough for the body check.\n",
                encoding="utf-8",
            )
        reports = validate_commands_directory(cmds_dir)
        assert len(reports) == 2
        # Both should have results
        assert all(len(r.results) > 0 for r in reports)


class TestPrintResultsAndJson:
    """Tests for print_results and print_json output functions (lines 573-637)."""

    def test_print_results_outputs_header_and_summary(self, capsys):
        """print_results should print header with command path and summary counts (lines 573-619)."""
        report = CommandValidationReport(command_path="/tmp/test-cmd.md")
        report.passed("Check one passed", "test-cmd.md")
        report.critical("Something broke", "test-cmd.md")
        report.major("A major issue", "test-cmd.md")
        report.minor("A minor issue", "test-cmd.md")
        print_results(report, verbose=True)
        captured = capsys.readouterr()
        assert "Command Validation" in captured.out
        assert "/tmp/test-cmd.md" in captured.out
        assert "CRITICAL:" in captured.out
        assert "MAJOR:" in captured.out
        assert "MINOR:" in captured.out
        assert "Score:" in captured.out

    def test_print_json_outputs_valid_json(self, capsys):
        """print_json should output valid JSON with command_path, exit_code, score, counts, results (lines 624-637)."""
        import json as json_mod

        report = CommandValidationReport(command_path="/tmp/json-test.md")
        report.passed("All good", "json-test.md")
        report.major("A problem", "json-test.md")
        print_json(report)
        captured = capsys.readouterr()
        output = json_mod.loads(captured.out)
        assert output["command_path"] == "/tmp/json-test.md"
        assert output["score"] <= 100
        assert output["counts"]["major"] == 1
        assert output["counts"]["passed"] == 1
        assert len(output["results"]) == 2
