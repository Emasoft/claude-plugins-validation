#!/usr/bin/env python3
"""Regression tests for the audit false-positive recalibrations (TRDD-021250b5).

These pin the FP fixes surfaced by the 10-agent audit so they can't regress:
- parse_frontmatter must NOT corrupt a value that contains `---` (4 validators)
- validate_plugin must NOT impose Nixtla strict mode (blocking) on a minimal
  Anthropic-valid skill
- a valid agent `mcp_tool` hook type must be accepted
Two-sided where applicable (valid passes / a real defect still flagged).
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402


class TestFrontmatterDashesInValue:
    """parse_frontmatter splits on the closing `---` LINE, not a `---` substring."""

    _CONTENT = "---\nname: test\ndescription: use --- as a separator here\n---\n\nBody line one.\n"

    def test_agent_parse_frontmatter_keeps_value_with_dashes(self):
        from validate_agent import parse_frontmatter

        fm, body, _ = parse_frontmatter(self._CONTENT)
        assert fm is not None, "valid frontmatter with '---' in a value must parse"
        assert fm["description"] == "use --- as a separator here"
        assert "Body line one." in body

    def test_command_parse_frontmatter_keeps_value_with_dashes(self):
        from validate_command import parse_frontmatter

        fm, body, _ = parse_frontmatter(self._CONTENT)
        assert fm is not None
        assert fm["description"] == "use --- as a separator here"
        assert "Body line one." in body

    def test_skill_parse_frontmatter_keeps_value_with_dashes(self):
        from validate_skill import parse_frontmatter

        fm, body, _ = parse_frontmatter(self._CONTENT)
        assert fm is not None
        assert fm["description"] == "use --- as a separator here"
        assert "Body line one." in body

    def test_unterminated_frontmatter_still_rejected(self):
        """Two-sided: genuinely-unterminated frontmatter still returns None."""
        from validate_agent import parse_frontmatter

        fm, _, _ = parse_frontmatter("---\nname: test\ndescription: no closing fence\n\nBody.\n")
        assert fm is None


class TestStrictModeNotBlockingValidSkills:
    """validate_plugin must not block a minimal Anthropic-valid skill on Nixtla strict mode."""

    def _make_plugin_with_minimal_skill(self, tmp_path: Path) -> Path:
        root = tmp_path / "p"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(
            '{"name": "p", "version": "1.0.0", "description": "x"}', encoding="utf-8"
        )
        skill = root / "skills" / "minimal"
        skill.mkdir(parents=True)
        # A minimal but ANThropic-VALID skill: name + description + a short body.
        # It has NONE of the Nixtla enterprise "required sections" (## Instructions,
        # ## Output, …) and uses no enforced first/second-person voice.
        skill.joinpath("SKILL.md").write_text(
            "---\nname: minimal\ndescription: A minimal valid skill that greets the user.\n---\n\n"
            "Greet the user politely when asked.\n",
            encoding="utf-8",
        )
        return root

    def test_minimal_valid_skill_has_no_required_section_major(self, tmp_path):
        from validate_plugin import validate_skills

        root = self._make_plugin_with_minimal_skill(tmp_path)
        report = ValidationReport()
        validate_skills(root, report)
        # No blocking "Required section" / Nixtla-strict MAJOR on a valid minimal skill.
        strict_majors = [
            r.message
            for r in report.results
            if r.level == "MAJOR" and ("Required section" in r.message or "strict mode" in r.message.lower())
        ]
        assert not strict_majors, f"minimal valid skill should not get Nixtla strict MAJORs: {strict_majors}"


class TestAgentMcpToolHookAccepted:
    """A valid agent-scoped `mcp_tool` hook type is accepted (not a false MAJOR)."""

    def test_mcp_tool_hook_type_not_rejected(self, tmp_path):
        from validate_agent import validate_agent

        agent = tmp_path / "a.md"
        agent.write_text(
            "---\n"
            "name: a\n"
            "description: An agent that uses an mcp_tool hook to call a connected MCP server.\n"
            "hooks:\n"
            "  PostToolUse:\n"
            "    - matcher: '*'\n"
            "      hooks:\n"
            "        - type: mcp_tool\n"
            "          server: my-server\n"
            "          tool: my-tool\n"
            "---\n\n"
            "You are a helper agent.\n",
            encoding="utf-8",
        )
        report = validate_agent(agent)
        hook_type_majors = [
            r.message for r in report.results if r.level == "MAJOR" and "Invalid hook type" in r.message
        ]
        assert not hook_type_majors, f"mcp_tool is a valid hook type; should not be rejected: {hook_type_majors}"

    def test_bogus_hook_type_still_rejected(self, tmp_path):
        """Two-sided: an actually-invalid hook type still reports MAJOR."""
        from validate_agent import validate_agent

        agent = tmp_path / "b.md"
        agent.write_text(
            "---\n"
            "name: b\n"
            "description: An agent with a bogus hook type for the negative test case.\n"
            "hooks:\n"
            "  PostToolUse:\n"
            "    - matcher: '*'\n"
            "      hooks:\n"
            "        - type: not_a_real_hook_type\n"
            "          command: echo hi\n"
            "---\n\n"
            "You are a helper agent.\n",
            encoding="utf-8",
        )
        report = validate_agent(agent)
        assert any(r.level == "MAJOR" and "Invalid hook type" in r.message for r in report.results), (
            "an unknown hook type must still be rejected"
        )
