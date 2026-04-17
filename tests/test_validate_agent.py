#!/usr/bin/env python3
"""Tests for validate_agent.py.

Tests the agent validation functions across the full agent validation pipeline,
including frontmatter parsing, field-level validators, body/example/security
checks, plugin-shipped agent restrictions, and deprecation warnings for
renamed/legacy tool names.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (  # noqa: E402
    PLUGIN_SHIPPED_AGENT_FORBIDDEN_FIELDS,
    ValidationReport,
    is_plugin_shipped_agent,
    validate_plugin_shipped_restrictions,
)
from validate_agent import (  # noqa: E402
    AgentValidationReport,
    parse_frontmatter,
    print_json,
    print_results,
    validate_agent,
    validate_agent_field,
    validate_agents_directory,
    validate_background_field,
    validate_body_content,
    validate_capabilities_field,
    validate_color_field,
    validate_context_field,
    validate_description_field,
    validate_disallowed_tools_field,
    validate_effort_field,
    validate_example_blocks,
    validate_frontmatter_exists,
    validate_hooks_field,
    validate_isolation_field,
    validate_max_turns_field,
    validate_memory_field,
    validate_model_field,
    validate_name_field,
    validate_permission_mode_field,
    validate_security,
    validate_skills_field,
    validate_system_prompt_field,
    validate_task_tool_prohibition,
    validate_tools_field,
    validate_user_invocable_field,
)

# ---------------------------------------------------------------------------
# Realistic agent content used across tests
# ---------------------------------------------------------------------------

VALID_AGENT_CONTENT = """\
---
name: code-reviewer
description: Use when the user asks for a code review. Specialized in Python and JS.
model: sonnet
tools:
  - Read
  - Grep
  - Glob
  - Bash
color: "#4A90D9"
---

# Code Reviewer Agent

You are a meticulous code reviewer specializing in Python and JavaScript projects.

## Capabilities

- Static analysis of code for common anti-patterns
- Security vulnerability detection
- Performance bottleneck identification

## Workflow

1. Read the files under review
2. Analyze for issues
3. Provide structured feedback

<example>
user: Review the authentication module in src/auth.py
assistant: I will review src/auth.py for security and code quality issues.
<commentary>The agent reads the file and produces a structured review.</commentary>
</example>

<example>
user: Check the test coverage for the utils package
assistant: I will analyze tests/test_utils.py against src/utils/ for coverage gaps.
<commentary>The agent cross-references test files with source modules.</commentary>
</example>
"""


class TestParseFrontmatter:
    """Tests for parse_frontmatter function."""

    def test_parse_valid_frontmatter_returns_dict_body_and_line(self):
        """parse_frontmatter returns (dict, body, end_line) for valid YAML frontmatter."""
        content = "---\nname: my-agent\ndescription: A test agent\n---\n\n# Body here\n"
        frontmatter, body, end_line = parse_frontmatter(content)

        assert frontmatter is not None
        assert isinstance(frontmatter, dict)
        assert frontmatter["name"] == "my-agent"
        assert frontmatter["description"] == "A test agent"
        assert "# Body here" in body
        # end_line should be > 0 since frontmatter occupies several lines
        assert end_line > 0


class TestValidateFrontmatterExists:
    """Tests for validate_frontmatter_exists function."""

    def test_valid_frontmatter_returns_dict_and_passes(self):
        """validate_frontmatter_exists returns parsed dict and records PASSED for valid content."""
        report = AgentValidationReport()
        content = "---\nname: test-agent\ndescription: Valid agent\n---\nBody content\n"
        result = validate_frontmatter_exists(content, report, "test-agent.md")

        assert result is not None
        assert result["name"] == "test-agent"
        # Should have at least one PASSED result
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Valid YAML frontmatter" in m for m in passed_msgs)

    def test_missing_frontmatter_reports_critical(self):
        """validate_frontmatter_exists reports CRITICAL when content has no YAML frontmatter."""
        report = AgentValidationReport()
        content = "# No frontmatter here\nJust plain markdown.\n"
        result = validate_frontmatter_exists(content, report, "bad-agent.md")

        assert result is None
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("No YAML frontmatter" in m for m in critical_msgs)


class TestValidateNameField:
    """Tests for validate_name_field function."""

    def test_valid_kebab_case_name_passes(self):
        """validate_name_field accepts a proper kebab-case name like 'code-reviewer'."""
        report = AgentValidationReport()
        frontmatter = {"name": "code-reviewer"}
        validate_name_field(frontmatter, "code-reviewer.md", report)

        # Should have PASSED and no MAJOR issues
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert len(major_msgs) == 0
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'name' field present" in m for m in passed_msgs)

    def test_uppercase_name_reports_critical(self):
        """validate_name_field reports CRITICAL when name contains uppercase letters."""
        report = AgentValidationReport()
        frontmatter = {"name": "Code-Reviewer"}
        validate_name_field(frontmatter, "Code-Reviewer.md", report)

        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("uppercase" in m.lower() for m in critical_msgs)


class TestValidateToolsField:
    """Tests for validate_tools_field function."""

    def test_valid_tools_list_passes(self):
        """validate_tools_field accepts a list of known built-in tools."""
        report = AgentValidationReport()
        frontmatter = {"tools": ["Read", "Write", "Bash", "Grep"]}
        validate_tools_field(frontmatter, "agent.md", report)

        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'tools' field valid" in m for m in passed_msgs)
        # No MAJOR issues for known tools
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert len(major_msgs) == 0


class TestValidateExampleBlocks:
    """Tests for validate_example_blocks function."""

    def test_sufficient_examples_passes(self):
        """validate_example_blocks passes when content has >= 2 properly structured examples."""
        report = AgentValidationReport()
        validate_example_blocks(VALID_AGENT_CONTENT, "code-reviewer.md", report)

        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("2 <example> block(s)" in m for m in passed_msgs)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert len(major_msgs) == 0

    def test_no_examples_reports_major(self):
        """validate_example_blocks reports MAJOR when body has zero example blocks."""
        content = "---\nname: bare-agent\ndescription: No examples\n---\n\nYou are a test agent.\n"
        report = AgentValidationReport()
        validate_example_blocks(content, "bare-agent.md", report)

        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("No <example> blocks found" in m for m in major_msgs)


class TestValidateAgent:
    """Tests for validate_agent main entry function."""

    def test_valid_agent_file_has_no_critical_issues(self, tmp_path):
        """validate_agent returns a report with no CRITICAL issues for a well-formed agent file."""
        agent_file = tmp_path / "code-reviewer.md"
        agent_file.write_text(VALID_AGENT_CONTENT, encoding="utf-8")

        report = validate_agent(agent_file)

        assert report.agent_path == str(agent_file)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert len(critical_msgs) == 0
        # exit_code should not be 1 (CRITICAL)
        assert report.exit_code != 1


class TestValidateAgentsDirectory:
    """Tests for validate_agents_directory function."""

    def test_directory_with_agents_returns_per_file_reports(self, tmp_path):
        """validate_agents_directory returns one report per .md file found in the directory."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        # Write two agent files
        (agents_dir / "agent-one.md").write_text(VALID_AGENT_CONTENT, encoding="utf-8")
        second_content = VALID_AGENT_CONTENT.replace("code-reviewer", "agent-two")
        (agents_dir / "agent-two.md").write_text(second_content, encoding="utf-8")

        reports = validate_agents_directory(agents_dir)

        assert len(reports) == 2
        paths = {r.agent_path for r in reports}
        assert str(agents_dir / "agent-one.md") in paths
        assert str(agents_dir / "agent-two.md") in paths


# ---------------------------------------------------------------------------
# Additional tests (20) targeting uncovered lines
# ---------------------------------------------------------------------------


class TestAgentValidationReportToDict:
    """Tests for AgentValidationReport.to_dict method (lines 114-117)."""

    def test_to_dict_includes_agent_path(self):
        """to_dict returns a dict containing agent_path alongside base fields."""
        report = AgentValidationReport(agent_path="/tmp/my-agent.md")
        report.passed("check passed", "my-agent.md")
        d = report.to_dict()
        assert d["agent_path"] == "/tmp/my-agent.md"
        assert "results" in d
        assert isinstance(d["results"], list)
        assert len(d["results"]) == 1


class TestParseFrontmatterEdgeCases:
    """Additional edge-case tests for parse_frontmatter (lines 128, 133, 138, 143-144)."""

    def test_parse_returns_none_when_no_closing_dashes(self):
        """parse_frontmatter returns (None, content, 0) when closing --- is missing."""
        content = "---\nname: broken\n"
        fm, body, end = parse_frontmatter(content)
        assert fm is None
        assert body == content
        assert end == 0

    def test_parse_returns_empty_dict_for_empty_yaml(self):
        """parse_frontmatter returns empty dict when YAML block is blank."""
        content = "---\n---\nBody text here\n"
        fm, body, end = parse_frontmatter(content)
        assert fm == {}
        assert "Body text here" in body

    def test_parse_returns_none_for_invalid_yaml(self):
        """parse_frontmatter returns None for YAML that causes a parse error."""
        content = "---\n: [invalid yaml\n---\nBody\n"
        fm, body, end = parse_frontmatter(content)
        assert fm is None


class TestValidateFrontmatterMalformed:
    """Tests for malformed frontmatter path (lines 156-160)."""

    def test_malformed_yaml_reports_critical(self):
        """validate_frontmatter_exists reports CRITICAL for malformed YAML with opening --- but invalid content."""
        report = AgentValidationReport()
        content = "---\n: [broken yaml {{{\n---\nBody\n"
        result = validate_frontmatter_exists(content, report, "bad.md")
        assert result is None
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("Malformed YAML" in m for m in critical_msgs)


class TestValidateDescriptionField:
    """Tests for validate_description_field (lines 226-270)."""

    def test_missing_description_reports_major(self):
        """validate_description_field reports MAJOR when description key is absent."""
        report = AgentValidationReport()
        validate_description_field({}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Missing 'description'" in m for m in major_msgs)

    def test_non_string_description_reports_critical(self):
        """validate_description_field reports CRITICAL when description is not a string."""
        report = AgentValidationReport()
        validate_description_field({"description": 42}, "agent.md", report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("must be a string" in m for m in critical_msgs)

    def test_empty_description_reports_major(self):
        """validate_description_field reports MAJOR for empty/whitespace description."""
        report = AgentValidationReport()
        validate_description_field({"description": "   "}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("cannot be empty" in m for m in major_msgs)

    def test_angle_brackets_in_description_reports_major(self):
        """validate_description_field reports MAJOR when description has < or > characters."""
        report = AgentValidationReport()
        validate_description_field(
            {"description": "Use when <user> asks for help doing something complex"}, "agent.md", report
        )
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("angle brackets" in m for m in major_msgs)

    def test_proactive_description_gets_passed(self):
        """validate_description_field records PASSED when description has 'proactively' hint."""
        report = AgentValidationReport()
        validate_description_field(
            {"description": "Use proactively when the user needs code review, specialized in Python"},
            "agent.md",
            report,
        )
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("proactive" in m for m in passed_msgs)


class TestValidateModelField:
    """Tests for validate_model_field (lines 324-339)."""

    def test_invalid_model_reports_major(self):
        """validate_model_field reports MAJOR for an unrecognized model name."""
        report = AgentValidationReport()
        validate_model_field({"model": "gpt-4"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Invalid 'model'" in m for m in major_msgs)

    def test_valid_model_passes(self):
        """validate_model_field records PASSED for a valid model like 'opus'."""
        report = AgentValidationReport()
        validate_model_field({"model": "opus"}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'model' field valid" in m for m in passed_msgs)


class TestValidateColorField:
    """Tests for validate_color_field (lines 347-362)."""

    def test_invalid_hex_color_reports_major(self):
        """validate_color_field reports MAJOR for a non-hex color string."""
        report = AgentValidationReport()
        validate_color_field({"color": "red"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("hex format" in m for m in major_msgs)

    def test_valid_hex_color_passes(self):
        """validate_color_field records PASSED for a proper #RRGGBB value."""
        report = AgentValidationReport()
        validate_color_field({"color": "#FF00AA"}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'color' field valid" in m for m in passed_msgs)


class TestValidateCapabilitiesField:
    """Tests for validate_capabilities_field (lines 372-388)."""

    def test_non_list_capabilities_reports_major(self):
        """validate_capabilities_field reports MAJOR when capabilities is not a list."""
        report = AgentValidationReport()
        validate_capabilities_field({"capabilities": "not-a-list"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be an array" in m for m in major_msgs)

    def test_non_string_item_in_capabilities_reports_major(self):
        """validate_capabilities_field reports MAJOR when a capability item is not a string."""
        report = AgentValidationReport()
        validate_capabilities_field({"capabilities": ["valid-cap", 123]}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be a string" in m for m in major_msgs)

    def test_valid_capabilities_passes(self):
        """validate_capabilities_field records PASSED for a list of valid string capabilities."""
        report = AgentValidationReport()
        validate_capabilities_field({"capabilities": ["code-analysis", "refactoring"]}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'capabilities' field valid" in m for m in passed_msgs)


class TestValidateContextField:
    """Tests for validate_context_field (lines 401-414)."""

    def test_invalid_context_value_reports_major(self):
        """validate_context_field reports MAJOR for an unrecognized context value."""
        report = AgentValidationReport()
        validate_context_field({"context": "spawn"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Invalid 'context'" in m for m in major_msgs)

    def test_valid_context_fork_passes(self):
        """validate_context_field records PASSED for context: fork."""
        report = AgentValidationReport()
        validate_context_field({"context": "fork"}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'context' field valid" in m for m in passed_msgs)


class TestValidateAgentFieldValues:
    """Tests for validate_agent_field (lines 428-440)."""

    def test_nonstandard_agent_value_reports_info(self):
        """validate_agent_field reports INFO for a non-standard agent value."""
        report = AgentValidationReport()
        validate_agent_field({"agent": "custom-reviewer"}, "agent.md", report)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("Non-standard" in m for m in info_msgs)

    def test_standard_agent_value_passes(self):
        """validate_agent_field records PASSED for a standard agent value like 'Explore'."""
        report = AgentValidationReport()
        validate_agent_field({"agent": "Explore"}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'agent' field valid" in m for m in passed_msgs)


class TestValidateUserInvocableField:
    """Tests for validate_user_invocable_field (lines 452-462)."""

    def test_boolean_true_passes(self):
        """validate_user_invocable_field records PASSED for boolean true."""
        report = AgentValidationReport()
        validate_user_invocable_field({"user-invocable": True}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'user-invocable' field valid" in m for m in passed_msgs)

    def test_string_true_reports_minor(self):
        """validate_user_invocable_field reports MINOR when value is string 'true' instead of boolean."""
        report = AgentValidationReport()
        validate_user_invocable_field({"user-invocable": "true"}, "agent.md", report)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("should be boolean" in m for m in minor_msgs)

    def test_invalid_type_reports_major(self):
        """validate_user_invocable_field reports MAJOR for a non-boolean non-string value."""
        report = AgentValidationReport()
        validate_user_invocable_field({"user-invocable": 42}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be boolean" in m for m in major_msgs)


class TestValidateSystemPromptField:
    """Tests for validate_system_prompt_field (lines 477-497)."""

    def test_placeholder_in_prompt_reports_major(self):
        """validate_system_prompt_field reports MAJOR when prompt contains TODO placeholder."""
        report = AgentValidationReport()
        validate_system_prompt_field(
            {"system-prompt": "You are an agent. TODO: add more details about role."}, "agent.md", report
        )
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("placeholder" in m for m in major_msgs)

    def test_valid_system_prompt_passes(self):
        """validate_system_prompt_field records PASSED for a clean prompt without placeholders."""
        report = AgentValidationReport()
        validate_system_prompt_field(
            {"system-prompt": "You are a specialized code reviewer for Python projects."}, "agent.md", report
        )
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'system-prompt' field valid" in m for m in passed_msgs)

    def test_empty_system_prompt_reports_major(self):
        """validate_system_prompt_field reports MAJOR for empty string prompt."""
        report = AgentValidationReport()
        validate_system_prompt_field({"system-prompt": "  "}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("cannot be empty" in m for m in major_msgs)


class TestValidateSkillsField:
    """Tests for validate_skills_field (lines 510-534)."""

    def test_non_list_skills_reports_major(self):
        """validate_skills_field reports MAJOR when skills is not a list."""
        report = AgentValidationReport()
        validate_skills_field({"skills": "commit"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be a list" in m for m in major_msgs)

    def test_empty_skills_list_reports_minor(self):
        """validate_skills_field reports MINOR for an empty skills list."""
        report = AgentValidationReport()
        validate_skills_field({"skills": []}, "agent.md", report)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("empty" in m for m in minor_msgs)

    def test_valid_skills_list_passes(self):
        """validate_skills_field records PASSED for a list of valid string skill names."""
        report = AgentValidationReport()
        validate_skills_field({"skills": ["commit", "review-pr"]}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'skills' field valid" in m for m in passed_msgs)

    def test_invalid_items_in_skills_reports_major(self):
        """validate_skills_field reports MAJOR when skills list contains non-string items."""
        report = AgentValidationReport()
        validate_skills_field({"skills": ["commit", 42, ""]}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("invalid items" in m for m in major_msgs)


class TestValidatePermissionModeField:
    """Tests for validate_permission_mode_field (lines 551-571)."""

    def test_invalid_permission_mode_reports_major(self):
        """validate_permission_mode_field reports MAJOR for an invalid mode string."""
        report = AgentValidationReport()
        validate_permission_mode_field({"permissionMode": "yolo"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Invalid 'permissionMode'" in m for m in major_msgs)

    def test_bypass_permissions_reports_minor_warning(self):
        """validate_permission_mode_field reports MINOR caution for bypassPermissions."""
        report = AgentValidationReport()
        validate_permission_mode_field({"permissionMode": "bypassPermissions"}, "agent.md", report)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("bypassPermissions" in m for m in minor_msgs)
        # Still passes overall
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'permissionMode' field valid" in m for m in passed_msgs)

    def test_valid_permission_mode_passes(self):
        """validate_permission_mode_field records PASSED for a valid mode like 'acceptEdits'."""
        report = AgentValidationReport()
        validate_permission_mode_field({"permissionMode": "acceptEdits"}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'permissionMode' field valid" in m for m in passed_msgs)


class TestValidateMemoryField:
    """Tests for validate_memory_field (lines 579-589)."""

    def test_invalid_memory_scope_reports_major(self):
        """validate_memory_field reports MAJOR for an unrecognized memory scope value."""
        report = AgentValidationReport()
        validate_memory_field({"memory": "global"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Invalid 'memory'" in m for m in major_msgs)

    def test_valid_memory_scope_passes(self):
        """validate_memory_field records PASSED for a valid scope like 'project'."""
        report = AgentValidationReport()
        validate_memory_field({"memory": "project"}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Valid memory scope" in m for m in passed_msgs)


class TestValidateIsolationField:
    """Tests for validate_isolation_field (lines 597-607)."""

    def test_invalid_isolation_reports_major(self):
        """validate_isolation_field reports MAJOR for an unrecognized isolation value."""
        report = AgentValidationReport()
        validate_isolation_field({"isolation": "sandbox"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Invalid 'isolation'" in m for m in major_msgs)

    def test_valid_isolation_passes(self):
        """validate_isolation_field records PASSED for isolation: worktree."""
        report = AgentValidationReport()
        validate_isolation_field({"isolation": "worktree"}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Valid isolation mode" in m for m in passed_msgs)


class TestValidateMaxTurnsField:
    """Tests for validate_max_turns_field (lines 615-620)."""

    def test_invalid_max_turns_reports_major(self):
        """validate_max_turns_field reports MAJOR for a non-positive or non-integer value."""
        report = AgentValidationReport()
        validate_max_turns_field({"maxTurns": -5}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("positive integer" in m for m in major_msgs)

    def test_valid_max_turns_passes(self):
        """validate_max_turns_field records PASSED for a positive integer like 10."""
        report = AgentValidationReport()
        validate_max_turns_field({"maxTurns": 10}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Valid maxTurns" in m for m in passed_msgs)


class TestValidateBackgroundField:
    """Tests for validate_background_field (lines 628-633)."""

    def test_non_boolean_background_reports_major(self):
        """validate_background_field reports MAJOR when value is not boolean."""
        report = AgentValidationReport()
        validate_background_field({"background": "yes"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be a boolean" in m for m in major_msgs)

    def test_valid_boolean_background_passes(self):
        """validate_background_field records PASSED for boolean true."""
        report = AgentValidationReport()
        validate_background_field({"background": True}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Valid background" in m for m in passed_msgs)


class TestValidateDisallowedToolsField:
    """Tests for validate_disallowed_tools_field (lines 646-677)."""

    def test_valid_disallowed_tools_list_passes(self):
        """validate_disallowed_tools_field records PASSED for a valid list of tool names."""
        report = AgentValidationReport()
        validate_disallowed_tools_field({"disallowedTools": ["Bash", "Write"]}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'disallowedTools' field valid" in m for m in passed_msgs)

    def test_comma_separated_string_disallowed_tools_passes(self):
        """validate_disallowed_tools_field accepts comma-separated string format."""
        report = AgentValidationReport()
        validate_disallowed_tools_field({"disallowedTools": "Bash, Write"}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'disallowedTools' field valid" in m for m in passed_msgs)

    def test_invalid_type_disallowed_tools_reports_major(self):
        """validate_disallowed_tools_field reports MAJOR for a non-string non-list value."""
        report = AgentValidationReport()
        validate_disallowed_tools_field({"disallowedTools": 42}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be string or list" in m for m in major_msgs)

    def test_empty_disallowed_tools_reports_minor(self):
        """validate_disallowed_tools_field reports MINOR for an empty list."""
        report = AgentValidationReport()
        validate_disallowed_tools_field({"disallowedTools": []}, "agent.md", report)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("empty" in m for m in minor_msgs)


class TestValidateHooksField:
    """Tests for validate_hooks_field (lines 692-764)."""

    def test_valid_hooks_structure_passes(self):
        """validate_hooks_field records PASSED for a properly structured hooks block."""
        report = AgentValidationReport()
        hooks = {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "echo pre-check"}],
                }
            ]
        }
        validate_hooks_field({"hooks": hooks}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'hooks' field structure valid" in m for m in passed_msgs)

    def test_invalid_hook_event_reports_major(self):
        """validate_hooks_field reports MAJOR for an unknown hook event name."""
        report = AgentValidationReport()
        hooks = {"InvalidEvent": [{"hooks": [{"type": "command", "command": "echo test"}]}]}
        validate_hooks_field({"hooks": hooks}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Invalid hook event" in m for m in major_msgs)

    def test_non_dict_hooks_reports_major(self):
        """validate_hooks_field reports MAJOR when hooks value is not a dict."""
        report = AgentValidationReport()
        validate_hooks_field({"hooks": "not-a-dict"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be an object" in m for m in major_msgs)

    def test_missing_hooks_array_in_matcher_reports_major(self):
        """validate_hooks_field reports MAJOR when a matcher block lacks 'hooks' array."""
        report = AgentValidationReport()
        hooks = {
            "PreToolUse": [
                {"matcher": "Bash"}  # missing 'hooks' array
            ]
        }
        validate_hooks_field({"hooks": hooks}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("missing required 'hooks' array" in m for m in major_msgs)

    def test_invalid_hook_type_reports_major(self):
        """validate_hooks_field reports MAJOR for a hook with unrecognized type value."""
        report = AgentValidationReport()
        hooks = {"PostToolUse": [{"hooks": [{"type": "webhook", "url": "https://example.com"}]}]}
        validate_hooks_field({"hooks": hooks}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Invalid hook type" in m for m in major_msgs)

    def test_hook_missing_type_field_reports_major(self):
        """validate_hooks_field reports MAJOR when individual hook object lacks 'type'."""
        report = AgentValidationReport()
        hooks = {
            "Stop": [
                {"hooks": [{"command": "echo done"}]}  # missing 'type'
            ]
        }
        validate_hooks_field({"hooks": hooks}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("missing required 'type'" in m for m in major_msgs)


class TestValidateTaskToolProhibition:
    """Tests for validate_task_tool_prohibition (lines 778-798)."""

    def test_fork_agent_with_task_tool_reports_major(self):
        """validate_task_tool_prohibition reports MAJOR when a fork agent has Task in tools."""
        report = AgentValidationReport()
        fm = {"context": "fork", "tools": ["Read", "Task", "Bash"]}
        validate_task_tool_prohibition(fm, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("infinite recursion" in m for m in major_msgs)

    def test_fork_agent_without_task_tool_has_no_issues(self):
        """validate_task_tool_prohibition produces no issues for fork agent without Task."""
        report = AgentValidationReport()
        fm = {"context": "fork", "tools": ["Read", "Bash"]}
        validate_task_tool_prohibition(fm, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert len(major_msgs) == 0


class TestValidateBodyContent:
    """Tests for validate_body_content (lines 869-911)."""

    def test_empty_body_reports_major(self):
        """validate_body_content reports MAJOR when there is no content after frontmatter."""
        content = "---\nname: empty-body\n---\n"
        report = AgentValidationReport()
        validate_body_content(content, "empty-body.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("no content" in m for m in major_msgs)

    def test_body_without_you_are_reports_minor(self):
        """validate_body_content reports MINOR when body lacks role definition."""
        content = "---\nname: no-role\n---\n\n" + ("This agent handles code reviews. " * 20) + "\n"
        report = AgentValidationReport()
        validate_body_content(content, "no-role.md", report)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("role definition" in m for m in minor_msgs)

    def test_body_with_sections_records_passed(self):
        """validate_body_content records PASSED for recognized sections like Capabilities and Workflow."""
        content = (
            "---\nname: good-body\n---\n\nYou are a code reviewer.\n\n## Capabilities\n\n- Review code\n\n## Workflow\n\n1. Read\n2. Review\n\n"
            + ("Extra content here. " * 10)
        )
        report = AgentValidationReport()
        validate_body_content(content, "good-body.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Capabilities" in m for m in passed_msgs)
        assert any("Workflow" in m for m in passed_msgs)


class TestValidateSecurity:
    """Tests for validate_security (lines 925-939)."""

    def test_hardcoded_user_path_reports_major(self):
        """validate_security reports MAJOR when content contains a hardcoded user path."""
        content = "---\nname: sec-test\n---\nRun /Users/johndoe/scripts/deploy.sh to deploy.\n"
        report = AgentValidationReport()
        validate_security(content, "sec-test.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("hardcoded user path" in m for m in major_msgs)

    def test_secret_pattern_reports_critical(self):
        """validate_security reports CRITICAL when content contains an AWS key pattern."""
        content = "---\nname: sec-test\n---\nUse key AKIAIOSFODNN7EXAMPLE to authenticate.\n"
        report = AgentValidationReport()
        validate_security(content, "sec-test.md", report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("AWS Access Key" in m for m in critical_msgs)


class TestValidateAgentFileEdgeCases:
    """Tests for validate_agent with file-level edge cases (lines 959-968)."""

    def test_nonexistent_file_reports_critical(self, tmp_path):
        """validate_agent reports CRITICAL when the agent file does not exist."""
        report = validate_agent(tmp_path / "nonexistent.md")
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("not found" in m for m in critical_msgs)

    def test_directory_path_reports_critical(self, tmp_path):
        """validate_agent reports CRITICAL when given a directory instead of a file."""
        report = validate_agent(tmp_path)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("not a file" in m for m in critical_msgs)

    def test_non_md_extension_reports_major(self, tmp_path):
        """validate_agent reports MAJOR for a file with .txt extension instead of .md."""
        f = tmp_path / "agent.txt"
        f.write_text(
            "---\nname: test-agent\ndescription: Valid agent for testing purposes\n---\nYou are a test agent.\n",
            encoding="utf-8",
        )
        report = validate_agent(f)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any(".md extension" in m for m in major_msgs)


class TestValidateAgentsDirectoryEdgeCases:
    """Tests for validate_agents_directory edge cases (lines 1038-1047)."""

    def test_non_directory_path_reports_critical(self, tmp_path):
        """validate_agents_directory reports CRITICAL when given a file instead of a directory."""
        f = tmp_path / "not-a-dir.md"
        f.write_text("content", encoding="utf-8")
        reports = validate_agents_directory(f)
        assert len(reports) == 1
        critical_msgs = [r.message for r in reports[0].results if r.level == "CRITICAL"]
        assert any("Not a directory" in m for m in critical_msgs)

    def test_empty_directory_reports_info(self, tmp_path):
        """validate_agents_directory reports INFO when directory contains no .md files."""
        empty_dir = tmp_path / "empty-agents"
        empty_dir.mkdir()
        reports = validate_agents_directory(empty_dir)
        assert len(reports) == 1
        info_msgs = [r.message for r in reports[0].results if r.level == "INFO"]
        assert any("No agent files" in m for m in info_msgs)


class TestPrintResultsAndJson:
    """Tests for print_results and print_json output functions (lines 1058-1126)."""

    def test_print_results_no_crash(self, capsys):
        """print_results runs without error and produces terminal output for a report with mixed results."""
        report = AgentValidationReport(agent_path="/tmp/test-agent.md")
        report.passed("check1 passed", "test-agent.md")
        report.major("Something is wrong", "test-agent.md")
        report.minor("Something is meh", "test-agent.md")
        print_results(report, verbose=True)
        captured = capsys.readouterr()
        assert "Agent Validation" in captured.out
        assert "CRITICAL" in captured.out
        assert "check1 passed" in captured.out

    def test_print_results_non_verbose_hides_passed(self, capsys):
        """print_results in non-verbose mode does not show PASSED or INFO lines."""
        report = AgentValidationReport(agent_path="/tmp/test-agent.md")
        report.passed("hidden pass", "test-agent.md")
        report.info("hidden info", "test-agent.md")
        print_results(report, verbose=False)
        captured = capsys.readouterr()
        assert "hidden pass" not in captured.out
        assert "hidden info" not in captured.out

    def test_print_json_produces_valid_json(self, capsys):
        """print_json outputs valid JSON containing agent_path, exit_code, score, and results."""
        import json

        report = AgentValidationReport(agent_path="/tmp/json-test.md")
        report.passed("all good", "json-test.md")
        report.major("a problem", "json-test.md")
        print_json(report)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["agent_path"] == "/tmp/json-test.md"
        assert "exit_code" in data
        assert "score" in data
        assert data["counts"]["major"] == 1
        assert data["counts"]["passed"] == 1


class TestValidateToolsFieldEdgeCases:
    """Additional tests for validate_tools_field edge cases (lines 290-302)."""

    def test_comma_separated_string_tools_passes(self):
        """validate_tools_field accepts a comma-separated string of tool names."""
        report = AgentValidationReport()
        validate_tools_field({"tools": "Read, Write, Bash"}, "agent.md", report)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("'tools' field valid" in m for m in passed_msgs)

    def test_invalid_type_tools_reports_major(self):
        """validate_tools_field reports MAJOR when tools is an integer."""
        report = AgentValidationReport()
        validate_tools_field({"tools": 42}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be string or list" in m for m in major_msgs)

    def test_empty_tools_list_reports_minor(self):
        """validate_tools_field reports MINOR for an empty tools list."""
        report = AgentValidationReport()
        validate_tools_field({"tools": []}, "agent.md", report)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("empty" in m for m in minor_msgs)

    def test_mcp_tool_prefix_accepted(self):
        """validate_tools_field accepts mcp__ prefixed tools without warning them as unknown."""
        report = AgentValidationReport()
        validate_tools_field({"tools": ["mcp__serena__find_symbol", "Read"]}, "agent.md", report)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        # mcp__ tools should NOT be reported as unknown
        unknown_msgs = [m for m in info_msgs if "Unknown tools" in m]
        assert len(unknown_msgs) == 0


class TestValidateNameFieldEdgeCases:
    """Additional tests for validate_name_field edge cases (lines 182-220)."""

    def test_missing_name_uses_filename_stem(self):
        """validate_name_field uses filename stem when name field is absent."""
        report = AgentValidationReport()
        validate_name_field({}, "my-agent.md", report)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("my-agent" in m for m in info_msgs)

    def test_consecutive_hyphens_reports_critical(self):
        """validate_name_field reports CRITICAL for names with consecutive hyphens."""
        report = AgentValidationReport()
        validate_name_field({"name": "my--agent"}, "my--agent.md", report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("consecutive hyphens" in m for m in critical_msgs)

    def test_name_starting_with_hyphen_reports_critical(self):
        """validate_name_field reports CRITICAL for names starting with a hyphen."""
        report = AgentValidationReport()
        validate_name_field({"name": "-my-agent"}, "-my-agent.md", report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("naming pattern" in m.lower() for m in critical_msgs)

    def test_non_string_name_reports_critical(self):
        """validate_name_field reports CRITICAL when name is not a string."""
        report = AgentValidationReport()
        validate_name_field({"name": 123}, "agent.md", report)
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("must be a string" in m for m in critical_msgs)


class TestExampleBlockStructureValidation:
    """Tests for example block structure checks (lines 846-858)."""

    def test_example_missing_user_line_reports_minor(self):
        """validate_example_blocks reports MINOR when an example block lacks user: line."""
        content = "---\nname: ex-test\n---\n\n<example>\nassistant: I will do the thing.\n<commentary>No user line</commentary>\n</example>\n\n<example>\nuser: Do the other thing\nassistant: Done.\n<commentary>This one is fine.</commentary>\n</example>\n"
        report = AgentValidationReport()
        validate_example_blocks(content, "ex-test.md", report)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("missing 'user:'" in m for m in minor_msgs)

    def test_example_missing_commentary_reports_info(self):
        """validate_example_blocks reports INFO when an example block has no commentary."""
        content = "---\nname: ex-test\n---\n\n<example>\nuser: Review this\nassistant: I will review it.\n</example>\n\n<example>\nuser: Also this\nassistant: Done.\n</example>\n"
        report = AgentValidationReport()
        validate_example_blocks(content, "ex-test.md", report)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("commentary" in m for m in info_msgs)


# ---------------------------------------------------------------------------
# Changelog-driven tests: full model ID support in validate_agent.py
# ---------------------------------------------------------------------------


class TestValidateModelFieldFullIds:
    """Tests for validate_model_field accepting full model IDs (v2.1.74+)."""

    def test_full_model_id_accepted(self):
        """validate_model_field accepts a full model ID like 'claude-opus-4-6' without error."""
        report = AgentValidationReport()
        validate_model_field({"model": "claude-opus-4-6"}, "agent.md", report)
        # Must not produce MAJOR
        assert not report.has_major
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("claude-opus-4-6" in m for m in passed_msgs)

    def test_invalid_full_model_id_rejected(self):
        """validate_model_field rejects non-Claude model IDs like 'gpt-4' with MAJOR."""
        report = AgentValidationReport()
        validate_model_field({"model": "gpt-4"}, "agent.md", report)
        assert report.has_major
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Invalid 'model' value" in m for m in major_msgs)

    def test_short_model_names_still_accepted(self):
        """validate_model_field still accepts short names: haiku, sonnet, opus, inherit."""
        for short_name in ("haiku", "sonnet", "opus", "inherit"):
            report = AgentValidationReport()
            validate_model_field({"model": short_name}, "agent.md", report)
            assert not report.has_major, f"Short name '{short_name}' should be accepted"

    def test_claude_sonnet_full_id_accepted(self):
        """validate_model_field accepts 'claude-sonnet-4-6' as a valid full model ID."""
        report = AgentValidationReport()
        validate_model_field({"model": "claude-sonnet-4-6"}, "agent.md", report)
        assert not report.has_major


# ---------------------------------------------------------------------------
# Plugin-shipped agent restrictions (plugins-reference.md)
# ---------------------------------------------------------------------------


class TestPluginShippedAgentRestrictions:
    """Tests for plugin-shipped agent field restrictions.

    Per plugins-reference.md: hooks, mcpServers, and permissionMode are not
    supported for plugin-shipped agents for security reasons.
    """

    def _make_plugin_agent(self, tmp_path: Path, frontmatter_yaml: str) -> Path:
        """Create a plugin-shaped directory tree with an agent file and return its path."""
        plugin_root = tmp_path / "my-plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            '{"name": "my-plugin", "version": "0.1.0"}',
            encoding="utf-8",
        )
        agents_dir = plugin_root / "agents"
        agents_dir.mkdir()
        agent_file = agents_dir / "test-agent.md"
        body = (
            "\n\n# Test Agent\n\nThis agent is used for plugin-shipped restriction tests. "
            "It contains a sufficiently long body with real content so that body-content "
            "validation passes without flagging minimum-length issues.\n\n"
            "<example>\nuser: Do a thing\nassistant: I will do the thing.\n"
            "<commentary>This example demonstrates the agent's purpose.</commentary>\n"
            "</example>\n\n<example>\nuser: Do another thing\n"
            "assistant: I will do the other thing.\n"
            "<commentary>Second example.</commentary>\n</example>\n"
        )
        agent_file.write_text(f"---\n{frontmatter_yaml}---\n{body}", encoding="utf-8")
        return agent_file

    def test_plugin_agent_with_hooks_reports_major(self, tmp_path: Path):
        """validate_agent reports MAJOR when a plugin-shipped agent defines 'hooks'."""
        fm = (
            "name: test-agent\n"
            "description: Use when testing plugin-shipped restrictions to ensure forbidden fields are flagged.\n"
            "hooks:\n"
            "  PreToolUse:\n"
            "    - type: command\n"
            "      command: echo hi\n"
        )
        agent_file = self._make_plugin_agent(tmp_path, fm)
        report = validate_agent(agent_file)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("'hooks' is not supported for plugin-shipped agents" in m for m in major_msgs)

    def test_plugin_agent_with_mcp_servers_reports_major(self, tmp_path: Path):
        """validate_agent reports MAJOR when a plugin-shipped agent defines 'mcpServers'."""
        fm = (
            "name: test-agent\n"
            "description: Use when testing plugin-shipped restrictions to ensure forbidden fields are flagged.\n"
            "mcpServers:\n"
            "  - serena\n"
        )
        agent_file = self._make_plugin_agent(tmp_path, fm)
        report = validate_agent(agent_file)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("'mcpServers' is not supported for plugin-shipped agents" in m for m in major_msgs)

    def test_plugin_agent_with_permission_mode_reports_major(self, tmp_path: Path):
        """validate_agent reports MAJOR when a plugin-shipped agent defines 'permissionMode'."""
        fm = (
            "name: test-agent\n"
            "description: Use when testing plugin-shipped restrictions to ensure forbidden fields are flagged.\n"
            "permissionMode: acceptEdits\n"
        )
        agent_file = self._make_plugin_agent(tmp_path, fm)
        report = validate_agent(agent_file)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("'permissionMode' is not supported for plugin-shipped agents" in m for m in major_msgs)

    def test_standalone_agent_with_hooks_is_allowed(self, tmp_path: Path):
        """validate_agent does NOT flag hooks for a non-plugin (standalone) agent file."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        agent_file = agents_dir / "standalone.md"
        body = (
            "\n\n# Standalone Agent\n\nThis agent lives outside of any plugin directory "
            "so the plugin-shipped restrictions must not apply. The body has enough real "
            "content for body validation to pass without minimum-length warnings.\n\n"
            "<example>\nuser: Do a thing\nassistant: I will do the thing.\n"
            "<commentary>Example one.</commentary>\n</example>\n\n"
            "<example>\nuser: Do another\nassistant: Done.\n"
            "<commentary>Example two.</commentary>\n</example>\n"
        )
        fm = (
            "name: standalone\n"
            "description: Use when testing that standalone agents can define hooks freely.\n"
            "hooks:\n"
            "  PreToolUse:\n"
            "    - type: command\n"
            "      command: echo hi\n"
        )
        agent_file.write_text(f"---\n{fm}---\n{body}", encoding="utf-8")
        report = validate_agent(agent_file)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("not supported for plugin-shipped agents" in m for m in major_msgs)


# ---------------------------------------------------------------------------
# Deprecation warnings: TaskOutput and Task tools
# ---------------------------------------------------------------------------


class TestDeprecatedToolWarnings:
    """Tests for deprecation warnings on renamed/deprecated tool names."""

    def test_task_output_emits_warning(self):
        """validate_tools_field emits WARNING for the deprecated TaskOutput tool."""
        report = AgentValidationReport()
        validate_tools_field({"tools": ["Read", "TaskOutput"]}, "agent.md", report)
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("TaskOutput" in m and "deprecated" in m for m in warning_msgs)

    def test_task_emits_rename_warning(self):
        """validate_tools_field emits WARNING when the renamed Task tool is still used."""
        report = AgentValidationReport()
        validate_tools_field({"tools": ["Read", "Task"]}, "agent.md", report)
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("Task" in m and "renamed to 'Agent'" in m for m in warning_msgs)

    def test_agent_tool_emits_no_warning(self):
        """validate_tools_field does NOT emit deprecation warnings for the new Agent tool."""
        report = AgentValidationReport()
        validate_tools_field({"tools": ["Read", "Agent"]}, "agent.md", report)
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert not any("deprecated" in m or "renamed" in m for m in warning_msgs)


# ---------------------------------------------------------------------------
# Direct unit tests for the shared plugin-shipped helpers
# (is_plugin_shipped_agent, validate_plugin_shipped_restrictions)
# ---------------------------------------------------------------------------


class TestIsPluginShippedAgent:
    """Direct unit tests for is_plugin_shipped_agent heuristic."""

    def test_ancestor_with_claude_plugin_manifest_returns_true(self, tmp_path: Path):
        """Walking up from an agent file finds .claude-plugin/plugin.json in an ancestor."""
        plugin_root = tmp_path / "my-plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            '{"name": "my-plugin", "version": "0.1.0"}',
            encoding="utf-8",
        )
        agents_dir = plugin_root / "agents"
        agents_dir.mkdir()
        agent_file = agents_dir / "agent.md"
        agent_file.write_text("---\nname: x\n---\n# x\n", encoding="utf-8")

        assert is_plugin_shipped_agent(agent_file) is True

    def test_nested_agent_inside_plugin_returns_true(self, tmp_path: Path):
        """Agents under plugin/agents/subdir/ still get detected."""
        plugin_root = tmp_path / "nested-plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            '{"name": "nested-plugin", "version": "0.1.0"}',
            encoding="utf-8",
        )
        subdir = plugin_root / "agents" / "sub"
        subdir.mkdir(parents=True)
        agent_file = subdir / "agent.md"
        agent_file.write_text("---\nname: x\n---\n# x\n", encoding="utf-8")

        assert is_plugin_shipped_agent(agent_file) is True

    def test_non_plugin_ancestor_returns_false(self, tmp_path: Path):
        """Standalone agent files outside any plugin return False."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        agent_file = agents_dir / "standalone.md"
        agent_file.write_text("---\nname: x\n---\n# x\n", encoding="utf-8")

        assert is_plugin_shipped_agent(agent_file) is False

    def test_bare_plugin_json_is_not_a_false_positive(self, tmp_path: Path):
        """A bare `plugin.json` in a parent directory must NOT trigger detection.

        This guards against false positives from unrelated projects (e.g. Node
        projects with their own plugin.json) per audit item M6.
        """
        fake_root = tmp_path / "not-a-plugin"
        fake_root.mkdir()
        # Drop a bare plugin.json NOT inside .claude-plugin/
        (fake_root / "plugin.json").write_text(
            '{"name": "something-else"}',
            encoding="utf-8",
        )
        agents_dir = fake_root / "agents"
        agents_dir.mkdir()
        agent_file = agents_dir / "agent.md"
        agent_file.write_text("---\nname: x\n---\n# x\n", encoding="utf-8")

        assert is_plugin_shipped_agent(agent_file) is False

    def test_walk_hitting_filesystem_root_returns_false(self, tmp_path: Path):
        """The walk terminates at filesystem root without error and returns False."""
        # Very deep path with no plugin manifest anywhere in any ancestor.
        deep = tmp_path / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        agent_file = deep / "agent.md"
        agent_file.write_text("---\nname: x\n---\n# x\n", encoding="utf-8")

        assert is_plugin_shipped_agent(agent_file) is False


class TestValidatePluginShippedRestrictionsUnit:
    """Direct unit tests for validate_plugin_shipped_restrictions."""

    def test_is_plugin_shipped_false_never_flags(self):
        """When is_plugin_shipped=False, no MAJOR is emitted even if forbidden fields exist."""
        report = ValidationReport()
        frontmatter = {
            "name": "x",
            "hooks": {"PreToolUse": []},
            "mcpServers": ["foo"],
            "permissionMode": "acceptEdits",
        }
        validate_plugin_shipped_restrictions(frontmatter, "agent.md", report, is_plugin_shipped=False)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert major_msgs == []

    def test_is_plugin_shipped_true_flags_every_forbidden_field(self):
        """Every forbidden field present gets its own MAJOR entry."""
        report = ValidationReport()
        frontmatter = {
            "name": "x",
            "hooks": {"PreToolUse": []},
            "mcpServers": ["foo"],
            "permissionMode": "acceptEdits",
        }
        validate_plugin_shipped_restrictions(frontmatter, "agent.md", report, is_plugin_shipped=True)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        for field in PLUGIN_SHIPPED_AGENT_FORBIDDEN_FIELDS:
            assert any(f"'{field}' is not supported for plugin-shipped agents" in m for m in major_msgs)


# ---------------------------------------------------------------------------
# Monitor tool acceptance (audit item Mi6)
# ---------------------------------------------------------------------------


class TestMonitorToolValidation:
    """Tests that the v2.1.98 Monitor tool is recognized by validate_tools_field."""

    def test_monitor_tool_passes_validation(self):
        """`tools: [Monitor]` passes without MAJOR/unknown-tool errors."""
        report = AgentValidationReport()
        validate_tools_field({"tools": ["Read", "Monitor"]}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert not any("Monitor" in m for m in major_msgs)
        assert not any("Unknown tool 'Monitor'" in m for m in info_msgs)


# ---------------------------------------------------------------------------
# v2.21.2 audit-fix regression (commit c9b869a) — G33
# ---------------------------------------------------------------------------


class TestV2212NonDictFrontmatter:
    """G33 (CRITICAL): list/scalar YAML frontmatter must not crash .keys()."""

    def test_validate_agent_non_dict_frontmatter_does_not_crash(self, tmp_path):
        """Agent .md with list-valued frontmatter must produce CRITICAL, not crash."""
        agent_file = tmp_path / "weird-agent.md"
        # Frontmatter parses to a YAML list, not a mapping. Pre-fix the
        # downstream `.keys()` iteration crashed with AttributeError.
        agent_file.write_text(
            "---\n- list\n- frontmatter\n---\nbody content\n",
            encoding="utf-8",
        )

        # Must not raise AttributeError — the validator has to handle non-dict FM.
        report = validate_agent(agent_file)

        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("Frontmatter must be a YAML mapping" in m for m in critical_msgs), (
            f"Expected CRITICAL about non-dict frontmatter, got CRITICALs: {critical_msgs}"
        )


class TestV22AgentFrontmatterUpdates:
    """v2.22.0: effort: xhigh (Opus 4.7), memory enum, isolation enum guardrails.

    Spec sources:
      - sub-agents.md L244 + cli-reference.md --effort — effort: xhigh added v2.1.111.
      - claude-directory.md L374, L656 + memory.md L35 — memory: project|local|user.
      - plugins-reference.md:70 — isolation: worktree is the only documented value.
    """

    # --- effort: xhigh / max / unknown ---

    def test_effort_xhigh_accepted(self):
        """effort: xhigh is accepted (v2.1.111 Opus 4.7 addition) — no MAJOR."""
        report = AgentValidationReport()
        # Use an Opus model so the xhigh Opus-only guard does NOT fire a secondary MAJOR.
        validate_effort_field({"effort": "xhigh", "model": "opus"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("Invalid 'effort'" in m for m in major_msgs), (
            f"xhigh must be accepted per sub-agents.md L244; got MAJORs: {major_msgs}"
        )
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Valid effort: xhigh" in m for m in passed_msgs), (
            f"Expected PASSED 'Valid effort: xhigh', got PASSEDs: {passed_msgs}"
        )

    def test_effort_max_accepted(self):
        """effort: max is still accepted (Opus 4.6 legacy) — backward compat preserved."""
        report = AgentValidationReport()
        validate_effort_field({"effort": "max", "model": "opus"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("Invalid 'effort'" in m for m in major_msgs), (
            f"max must remain accepted for Opus 4.6 compat; got MAJORs: {major_msgs}"
        )
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Valid effort: max" in m for m in passed_msgs)

    def test_effort_unknown_value_rejected(self):
        """effort: turbo (not in {low, medium, high, xhigh, max}) must emit MAJOR."""
        report = AgentValidationReport()
        validate_effort_field({"effort": "turbo"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Invalid 'effort' value: 'turbo'" in m for m in major_msgs), (
            f"Expected MAJOR rejecting 'turbo'; got MAJORs: {major_msgs}"
        )

    # --- memory: project | local | user ---

    def test_memory_project_accepted(self):
        """memory: project is a valid scope per claude-directory.md L374."""
        report = AgentValidationReport()
        validate_memory_field({"memory": "project"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("Invalid 'memory'" in m for m in major_msgs)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Valid memory scope: project" in m for m in passed_msgs)

    def test_memory_local_accepted(self):
        """memory: local is a valid scope per memory.md L35."""
        report = AgentValidationReport()
        validate_memory_field({"memory": "local"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("Invalid 'memory'" in m for m in major_msgs)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Valid memory scope: local" in m for m in passed_msgs)

    def test_memory_user_accepted(self):
        """memory: user is a valid scope per claude-directory.md L656."""
        report = AgentValidationReport()
        validate_memory_field({"memory": "user"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("Invalid 'memory'" in m for m in major_msgs)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Valid memory scope: user" in m for m in passed_msgs)

    def test_memory_invalid_value_rejected(self):
        """memory: cluster (not in {project, local, user}) must emit MAJOR."""
        report = AgentValidationReport()
        validate_memory_field({"memory": "cluster"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Invalid 'memory'" in m and "cluster" in m for m in major_msgs), (
            f"Expected MAJOR rejecting 'cluster'; got MAJORs: {major_msgs}"
        )

    # --- isolation: worktree is the only valid value ---

    def test_isolation_worktree_accepted(self):
        """isolation: worktree is the sole documented value per plugins-reference.md:70."""
        report = AgentValidationReport()
        validate_isolation_field({"isolation": "worktree"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("Invalid 'isolation'" in m for m in major_msgs)
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Valid isolation mode: worktree" in m for m in passed_msgs)

    def test_isolation_other_value_major(self):
        """isolation: container must emit MAJOR — 'worktree' is the only valid value."""
        report = AgentValidationReport()
        validate_isolation_field({"isolation": "container"}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Invalid 'isolation'" in m and "container" in m for m in major_msgs), (
            f"Expected MAJOR rejecting 'container'; got MAJORs: {major_msgs}"
        )
        # The error message must surface the 'worktree' guidance so authors know
        # what to use — otherwise the diagnostic is useless.
        assert any("worktree" in m for m in major_msgs), (
            f"Expected MAJOR message to cite 'worktree' as the valid value; got: {major_msgs}"
        )

    def test_isolation_empty_string_major(self):
        """isolation: '' (empty string) must emit MAJOR — empty is not 'worktree'."""
        report = AgentValidationReport()
        validate_isolation_field({"isolation": ""}, "agent.md", report)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("isolation" in m and ("empty" in m.lower() or "Invalid" in m) for m in major_msgs), (
            f"Expected MAJOR for empty isolation; got MAJORs: {major_msgs}"
        )
