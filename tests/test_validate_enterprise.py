"""Tests for validate_enterprise.py - Enterprise Compliance Validator.

Tests cover:
- parse_frontmatter: YAML frontmatter parsing from markdown content
- validate_skill_compliance: Full skill compliance validation
- validate_agent_compliance: Agent compliance validation
- validate_enterprise_compliance: Main entry point for plugin-wide validation
- validate_required_metadata: Required metadata field validation (name, description)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_enterprise import (
    EnterpriseComplianceReport,
    SkillComplianceResult,
    parse_frontmatter,
    print_json,
    print_results,
    validate_agent_compliance,
    validate_agent_field,
    validate_author_field,
    validate_context_field,
    validate_enterprise_compliance,
    validate_license_field,
    validate_mode_field,
    validate_required_metadata,
    validate_skill_compliance,
    validate_tags_field,
    validate_user_invocable_field,
)


class TestParseFrontmatter:
    """Tests for parse_frontmatter function."""

    def test_parse_valid_frontmatter(self):
        """parse_frontmatter returns parsed dict and body for valid YAML frontmatter."""
        content = "---\nname: my-skill\ndescription: A skill\nauthor: Test\nlicense: MIT\n---\n\n# Body"
        fm, body = parse_frontmatter(content)
        assert fm is not None
        assert fm["name"] == "my-skill"
        assert fm["description"] == "A skill"
        assert fm["author"] == "Test"
        assert fm["license"] == "MIT"
        assert "# Body" in body

    def test_parse_no_frontmatter(self):
        """parse_frontmatter returns None and full content when no frontmatter markers exist."""
        content = "# Just a heading\n\nSome text."
        fm, body = parse_frontmatter(content)
        assert fm is None
        assert body == content


class TestValidateRequiredMetadata:
    """Tests for validate_required_metadata function."""

    def test_valid_name_and_description(self):
        """validate_required_metadata reports PASSED when both name and description are valid strings."""
        frontmatter = {"name": "my-skill", "description": "A useful skill for testing"}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/skill", skill_name="test-skill", is_compliant=True)
        validate_required_metadata(frontmatter, "skills/test-skill/SKILL.md", report, result)
        assert len(result.missing_required) == 0
        assert len(result.invalid_fields) == 0
        passed_messages = [r.message for r in report.results if r.level == "PASSED"]
        assert any("name" in m for m in passed_messages)

    def test_missing_name_and_description(self):
        """validate_required_metadata flags missing name and description as required fields."""
        frontmatter = {}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/skill", skill_name="test-skill", is_compliant=True)
        validate_required_metadata(frontmatter, "skills/test-skill/SKILL.md", report, result)
        assert "name" in result.missing_required
        assert "description" in result.missing_required


class TestValidateSkillCompliance:
    """Tests for validate_skill_compliance function."""

    def test_compliant_skill(self, tmp_path):
        """validate_skill_compliance returns compliant result for a skill with all required fields."""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            "---\nname: my-skill\ndescription: A test skill\nauthor: Test Author\nlicense: MIT\ntags:\n  - testing\n---\n\n# My Skill\n\nContent here.\n",
            encoding="utf-8",
        )
        report = EnterpriseComplianceReport()
        result = validate_skill_compliance(skill_dir, report)
        assert result.is_compliant is True
        assert result.skill_name == "my-skill"
        assert len(result.missing_required) == 0

    def test_missing_skill_md(self, tmp_path):
        """validate_skill_compliance flags missing SKILL.md as non-compliant."""
        skill_dir = tmp_path / "empty-skill"
        skill_dir.mkdir()
        report = EnterpriseComplianceReport()
        result = validate_skill_compliance(skill_dir, report)
        assert result.is_compliant is False
        assert "SKILL.md" in result.missing_required


class TestValidateAgentCompliance:
    """Tests for validate_agent_compliance function."""

    def test_compliant_agent(self, tmp_path):
        """validate_agent_compliance returns compliant result for agent with name and description."""
        agent_file = tmp_path / "my-agent.md"
        agent_file.write_text("---\nname: my-agent\ndescription: A test agent\n---\n\n# Agent instructions\n", encoding="utf-8")
        report = EnterpriseComplianceReport()
        result = validate_agent_compliance(agent_file, report)
        assert result.is_compliant is True
        assert result.agent_name == "my-agent"
        assert len(result.missing_required) == 0

    def test_agent_missing_required_fields(self, tmp_path):
        """validate_agent_compliance flags agent missing name and description as non-compliant."""
        agent_file = tmp_path / "bad-agent.md"
        agent_file.write_text("---\nsome_other: value\n---\n\n# Agent\n", encoding="utf-8")
        report = EnterpriseComplianceReport()
        result = validate_agent_compliance(agent_file, report)
        assert result.is_compliant is False
        assert "name" in result.missing_required
        assert "description" in result.missing_required


class TestValidateEnterpriseCompliance:
    """Tests for validate_enterprise_compliance main entry point."""

    def test_plugin_with_compliant_skills_and_agents(self, tmp_path):
        """validate_enterprise_compliance reports full compliance for a properly structured plugin."""
        plugin_dir = tmp_path / "good-plugin"
        plugin_dir.mkdir()
        skills_dir = plugin_dir / "skills" / "my-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: Skill desc\nauthor: Author\nlicense: MIT\n---\n\n# Body\n", encoding="utf-8")
        agents_dir = plugin_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "my-agent.md").write_text("---\nname: my-agent\ndescription: Agent desc\n---\n\n# Agent\n", encoding="utf-8")
        report = validate_enterprise_compliance(plugin_dir)
        assert report.total_skills == 1
        assert report.compliant_skills == 1
        assert report.total_agents == 1
        assert report.compliant_agents == 1
        assert report.overall_compliance is True
        assert report.compliance_percentage == 100.0

    def test_nonexistent_plugin_directory(self, tmp_path):
        """validate_enterprise_compliance returns critical error for nonexistent path."""
        report = validate_enterprise_compliance(tmp_path / "does-not-exist")
        assert report.has_critical is True
        assert report.overall_compliance is False


# =============================================================================
# Additional tests targeting uncovered lines
# =============================================================================


class TestCompliancePercentageZeroItems:
    """Tests for EnterpriseComplianceReport.compliance_percentage with zero items."""

    def test_compliance_percentage_returns_100_when_no_skills_or_agents(self):
        """compliance_percentage returns 100.0 when total_skills and total_agents are both 0 (line 177)."""
        report = EnterpriseComplianceReport()
        assert report.total_skills == 0
        assert report.total_agents == 0
        assert report.compliance_percentage == 100.0


class TestReportToDict:
    """Tests for EnterpriseComplianceReport.to_dict serialization."""

    def test_to_dict_includes_all_enterprise_fields(self, tmp_path):
        """to_dict returns dict with plugin_path, strict_mode, skill_results, agent_results (lines 183-216)."""
        plugin_dir = tmp_path / "plugin-dict-test"
        plugin_dir.mkdir()
        skills_dir = plugin_dir / "skills" / "s1"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: s1\ndescription: Skill one\nauthor: Dev\nlicense: Apache-2.0\n---\n\n# Skill\n",
            encoding="utf-8",
        )
        agents_dir = plugin_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "a1.md").write_text(
            "---\nname: a1\ndescription: Agent one\n---\n\n# Agent\n",
            encoding="utf-8",
        )
        report = validate_enterprise_compliance(plugin_dir, strict=True)
        d = report.to_dict()
        assert d["plugin_path"] == str(plugin_dir)
        assert d["strict_mode"] is True
        assert d["overall_compliance"] is True
        assert d["compliance_percentage"] == 100.0
        assert d["total_skills"] == 1
        assert d["compliant_skills"] == 1
        assert d["total_agents"] == 1
        assert d["compliant_agents"] == 1
        assert len(d["skill_results"]) == 1
        assert d["skill_results"][0]["skill_name"] == "s1"
        assert d["skill_results"][0]["is_compliant"] is True
        assert len(d["agent_results"]) == 1
        assert d["agent_results"][0]["agent_name"] == "a1"


class TestValidateRequiredMetadataEdgeCases:
    """Tests for validate_required_metadata edge cases: non-string and empty values."""

    def test_name_non_string_flagged_invalid(self):
        """validate_required_metadata flags non-string name as invalid_fields (lines 349-351)."""
        frontmatter = {"name": 12345, "description": "valid desc"}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_required_metadata(frontmatter, "skills/s/SKILL.md", report, result)
        assert "name" in result.invalid_fields
        error_msgs = [r.message for r in report.results if r.level in ("CRITICAL", "MAJOR")]
        assert any("must be a string" in m for m in error_msgs)

    def test_empty_name_flagged_invalid(self):
        """validate_required_metadata flags empty-string name as invalid (lines 353-355)."""
        frontmatter = {"name": "   ", "description": "valid desc"}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_required_metadata(frontmatter, "skills/s/SKILL.md", report, result)
        assert "name" in result.invalid_fields
        error_msgs = [r.message for r in report.results if r.level in ("CRITICAL", "MAJOR")]
        assert any("cannot be empty" in m for m in error_msgs)

    def test_description_non_string_flagged_invalid(self):
        """validate_required_metadata flags non-string description as invalid (lines 365-367)."""
        frontmatter = {"name": "good-name", "description": ["not", "a", "string"]}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_required_metadata(frontmatter, "skills/s/SKILL.md", report, result)
        assert "description" in result.invalid_fields

    def test_description_empty_flagged_invalid(self):
        """validate_required_metadata flags empty description as invalid (lines 369-371)."""
        frontmatter = {"name": "good-name", "description": "  "}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_required_metadata(frontmatter, "skills/s/SKILL.md", report, result)
        assert "description" in result.invalid_fields


class TestValidateAuthorField:
    """Tests for validate_author_field covering missing, empty, dict, and invalid types."""

    def test_missing_author_adds_to_missing_required(self):
        """validate_author_field flags missing author as missing_required (lines 384-387)."""
        frontmatter = {"name": "s1", "description": "desc"}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_author_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "author" in result.missing_required

    def test_empty_author_string_flagged_invalid(self):
        """validate_author_field flags empty author string as invalid (lines 394-396)."""
        frontmatter = {"author": "   "}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_author_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "author" in result.invalid_fields

    def test_author_dict_with_name_and_email_passes(self):
        """validate_author_field accepts author as dict with name and email (lines 399-411)."""
        frontmatter = {"author": {"name": "Jane Doe", "email": "jane@example.com"}}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_author_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "author" not in result.invalid_fields
        assert "author" not in result.missing_required
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Jane Doe" in m and "jane@example.com" in m for m in passed_msgs)

    def test_author_dict_missing_name_flagged_invalid(self):
        """validate_author_field flags author dict without name key (lines 401-404)."""
        frontmatter = {"author": {"email": "no-name@example.com"}}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_author_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "author" in result.invalid_fields

    def test_author_invalid_type_flagged(self):
        """validate_author_field flags non-string non-dict author type (lines 413-415)."""
        frontmatter = {"author": 42}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_author_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "author" in result.invalid_fields


class TestValidateLicenseField:
    """Tests for validate_license_field covering missing, non-string, empty, unknown SPDX."""

    def test_missing_license_adds_to_missing_required(self):
        """validate_license_field flags missing license (lines 426-429)."""
        frontmatter = {}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_license_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "license" in result.missing_required

    def test_non_string_license_flagged_invalid(self):
        """validate_license_field flags non-string license (lines 434-437)."""
        frontmatter = {"license": 123}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_license_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "license" in result.invalid_fields

    def test_empty_license_flagged_invalid(self):
        """validate_license_field flags empty license string (lines 440-443)."""
        frontmatter = {"license": "  "}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_license_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "license" in result.invalid_fields

    def test_unknown_spdx_license_gets_minor_warning(self):
        """validate_license_field issues minor for unknown SPDX identifier (line 450)."""
        frontmatter = {"license": "My-Custom-License-v3"}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_license_field(frontmatter, "skills/s/SKILL.md", report, result)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("not a common SPDX" in m for m in minor_msgs)
        assert "license" not in result.invalid_fields


class TestValidateContextField:
    """Tests for validate_context_field covering non-string and invalid values."""

    def test_non_string_context_flagged_invalid(self):
        """validate_context_field flags non-string context value (lines 471-474)."""
        frontmatter = {"context": ["fork"]}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_context_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "context" in result.invalid_fields

    def test_invalid_context_value_flagged(self):
        """validate_context_field flags invalid context value like 'inline' (lines 477-481)."""
        frontmatter = {"context": "inline"}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_context_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "context" in result.invalid_fields

    def test_valid_fork_context_passes(self):
        """validate_context_field accepts 'fork' as valid context (line 483)."""
        frontmatter = {"context": "fork"}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_context_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "context" not in result.invalid_fields
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("context" in m for m in passed_msgs)


class TestValidateAgentField:
    """Tests for validate_agent_field covering non-string, unknown custom, and enterprise types."""

    def test_non_string_agent_flagged_invalid(self):
        """validate_agent_field flags non-string agent value (lines 501-504)."""
        frontmatter = {"agent": 99, "context": "fork"}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_agent_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "agent" in result.invalid_fields

    def test_agent_without_context_fork_gets_major(self):
        """validate_agent_field reports major when agent is set but context is not fork (line 509)."""
        frontmatter = {"agent": "test-engineer"}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_agent_field(frontmatter, "skills/s/SKILL.md", report, result)
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("no effect without" in m for m in major_msgs)

    def test_custom_agent_non_strict_gets_info(self):
        """validate_agent_field emits info for unknown agent type in non-strict mode (lines 526-527)."""
        frontmatter = {"agent": "my-custom-agent", "context": "fork"}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_agent_field(frontmatter, "skills/s/SKILL.md", report, result)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("custom agent" in m for m in info_msgs)

    def test_custom_agent_strict_gets_minor(self):
        """validate_agent_field emits minor for unknown agent type in strict mode (lines 519-525)."""
        frontmatter = {"agent": "my-custom-agent", "context": "fork"}
        report = EnterpriseComplianceReport()
        report.strict_mode = True
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_agent_field(frontmatter, "skills/s/SKILL.md", report, result)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("not a known type" in m for m in minor_msgs)


class TestValidateUserInvocableField:
    """Tests for validate_user_invocable_field covering non-boolean values."""

    def test_non_boolean_user_invocable_flagged(self):
        """validate_user_invocable_field flags non-boolean value (lines 540-546)."""
        frontmatter = {"user-invocable": "yes"}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_user_invocable_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "user-invocable" in result.invalid_fields

    def test_boolean_user_invocable_passes(self):
        """validate_user_invocable_field accepts boolean true (line 548)."""
        frontmatter = {"user-invocable": True}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_user_invocable_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "user-invocable" not in result.invalid_fields
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("user-invocable" in m for m in passed_msgs)


class TestValidateTagsField:
    """Tests for validate_tags_field covering non-list, empty list, non-string elements."""

    def test_non_list_tags_flagged(self):
        """validate_tags_field flags non-list tags value (lines 567-569)."""
        frontmatter = {"tags": "just-a-string"}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_tags_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "tags" in result.invalid_fields

    def test_empty_tags_list_gets_minor(self):
        """validate_tags_field issues minor for empty tags array (lines 572-573)."""
        frontmatter = {"tags": []}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_tags_field(frontmatter, "skills/s/SKILL.md", report, result)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("empty" in m for m in minor_msgs)

    def test_tags_with_non_string_elements_gets_minor(self):
        """validate_tags_field issues minor for tags containing non-string values (lines 578-579)."""
        frontmatter = {"tags": ["valid", 123, True]}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_tags_field(frontmatter, "skills/s/SKILL.md", report, result)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("non-string" in m for m in minor_msgs)


class TestValidateModeField:
    """Tests for validate_mode_field covering non-string, invalid, and valid values."""

    def test_non_string_mode_flagged(self):
        """validate_mode_field flags non-string mode value (lines 597-601)."""
        frontmatter = {"mode": 42}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_mode_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "mode" in result.invalid_fields

    def test_invalid_mode_value_flagged(self):
        """validate_mode_field flags invalid mode value like 'execute' (lines 603-607)."""
        frontmatter = {"mode": "execute"}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_mode_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "mode" in result.invalid_fields

    def test_valid_mode_read_write_passes(self):
        """validate_mode_field accepts 'read-write' as valid mode (line 609)."""
        frontmatter = {"mode": "read-write"}
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_mode_field(frontmatter, "skills/s/SKILL.md", report, result)
        assert "mode" not in result.invalid_fields
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("mode" in m for m in passed_msgs)


class TestValidateAgentComplianceEdgeCases:
    """Tests for validate_agent_compliance with no-frontmatter and unreadable files."""

    def test_agent_without_frontmatter_non_compliant(self, tmp_path):
        """validate_agent_compliance flags agent file with no YAML frontmatter (lines 646-650)."""
        agent_file = tmp_path / "no-fm-agent.md"
        agent_file.write_text("# Just a plain markdown file\nNo frontmatter here.\n", encoding="utf-8")
        report = EnterpriseComplianceReport()
        result = validate_agent_compliance(agent_file, report)
        assert result.is_compliant is False
        assert "name" in result.missing_required
        assert "description" in result.missing_required

    def test_agent_unreadable_file_flagged_critical(self, tmp_path):
        """validate_agent_compliance flags unreadable file as critical (lines 637-640)."""
        agent_file = tmp_path / "unreadable-agent.md"
        agent_file.write_text("---\nname: x\n---\n", encoding="utf-8")
        agent_file.chmod(0o000)
        report = EnterpriseComplianceReport()
        result = validate_agent_compliance(agent_file, report)
        # Restore permissions for cleanup
        agent_file.chmod(0o644)
        assert result.is_compliant is False
        assert report.has_critical is True


class TestValidateEnterpriseComplianceEdgeCases:
    """Tests for validate_enterprise_compliance covering path-is-file, no-skills, no-agents, strict."""

    def test_path_is_file_not_directory(self, tmp_path):
        """validate_enterprise_compliance flags a file path as critical error (lines 705-706)."""
        file_path = tmp_path / "not-a-dir.txt"
        file_path.write_text("hello", encoding="utf-8")
        report = validate_enterprise_compliance(file_path)
        assert report.has_critical is True
        crit_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("not a directory" in m for m in crit_msgs)

    def test_empty_skills_dir_reports_info(self, tmp_path):
        """validate_enterprise_compliance reports info when skills/ exists but is empty (line 722)."""
        plugin_dir = tmp_path / "empty-skills-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "skills").mkdir()
        report = validate_enterprise_compliance(plugin_dir)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("No skills found" in m for m in info_msgs)

    def test_no_skills_dir_reports_info(self, tmp_path):
        """validate_enterprise_compliance reports info when no skills/ directory (line 726)."""
        plugin_dir = tmp_path / "no-skills-plugin"
        plugin_dir.mkdir()
        report = validate_enterprise_compliance(plugin_dir)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("No skills/ directory" in m for m in info_msgs)

    def test_empty_agents_dir_reports_info(self, tmp_path):
        """validate_enterprise_compliance reports info when agents/ exists but has no .md files (line 742)."""
        plugin_dir = tmp_path / "empty-agents-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "agents").mkdir()
        report = validate_enterprise_compliance(plugin_dir)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("No agents found" in m for m in info_msgs)

    def test_no_agents_dir_reports_info(self, tmp_path):
        """validate_enterprise_compliance reports info when no agents/ directory (line 746)."""
        plugin_dir = tmp_path / "no-agents-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "skills").mkdir()
        report = validate_enterprise_compliance(plugin_dir)
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("No agents/ directory" in m for m in info_msgs)

    def test_no_skills_no_agents_gets_minor(self, tmp_path):
        """validate_enterprise_compliance issues minor when no skills or agents found (line 750)."""
        plugin_dir = tmp_path / "totally-empty-plugin"
        plugin_dir.mkdir()
        report = validate_enterprise_compliance(plugin_dir)
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("No skills or agents found" in m for m in minor_msgs)

    def test_skill_no_frontmatter_strict_mode(self, tmp_path):
        """validate_skill_compliance in strict mode reports CRITICAL for missing frontmatter (lines 300-304)."""
        plugin_dir = tmp_path / "strict-plugin"
        plugin_dir.mkdir()
        skills_dir = plugin_dir / "skills" / "bad-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("# No frontmatter at all\n", encoding="utf-8")
        report = validate_enterprise_compliance(plugin_dir, strict=True)
        assert report.has_critical is True
        assert report.compliant_skills == 0


class TestPrintResults:
    """Tests for print_results output covering various report states (lines 763-850)."""

    def test_print_results_compliant_plugin(self, tmp_path, capsys):
        """print_results outputs SUCCESS line for a fully compliant plugin (line 835)."""
        plugin_dir = tmp_path / "print-test-plugin"
        plugin_dir.mkdir()
        skills_dir = plugin_dir / "skills" / "s1"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: s1\ndescription: Skill\nauthor: Dev\nlicense: MIT\ntags:\n  - test\n---\n\n# S\n",
            encoding="utf-8",
        )
        agents_dir = plugin_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "a1.md").write_text(
            "---\nname: a1\ndescription: Agent\n---\n\n# A\n",
            encoding="utf-8",
        )
        report = validate_enterprise_compliance(plugin_dir)
        print_results(report, verbose=True)
        captured = capsys.readouterr().out
        assert "SUCCESS" in captured
        assert "Compliance Summary" in captured
        assert "Skill Compliance" in captured
        assert "Agent Compliance" in captured
        assert "Detailed Results" in captured

    def test_print_results_non_compliant_shows_missing(self, tmp_path, capsys):
        """print_results outputs FAILED and missing fields for non-compliant plugin (lines 806-810, 836-839)."""
        plugin_dir = tmp_path / "print-fail-plugin"
        plugin_dir.mkdir()
        skills_dir = plugin_dir / "skills" / "bad"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nsome_field: value\n---\n\n# Bad skill\n",
            encoding="utf-8",
        )
        report = validate_enterprise_compliance(plugin_dir)
        print_results(report)
        captured = capsys.readouterr().out
        assert "FAILED" in captured or "WARNING" in captured
        assert "Missing:" in captured

    def test_print_results_strict_mode_header(self, tmp_path, capsys):
        """print_results outputs STRICT mode banner when strict is enabled (line 781)."""
        plugin_dir = tmp_path / "strict-print-plugin"
        plugin_dir.mkdir()
        report = validate_enterprise_compliance(plugin_dir, strict=True)
        print_results(report)
        captured = capsys.readouterr().out
        assert "STRICT" in captured


class TestPrintJson:
    """Tests for print_json output (lines 853-855)."""

    def test_print_json_outputs_valid_json(self, tmp_path, capsys):
        """print_json outputs parseable JSON with all enterprise fields (line 855)."""
        import json as json_mod

        plugin_dir = tmp_path / "json-test-plugin"
        plugin_dir.mkdir()
        skills_dir = plugin_dir / "skills" / "js"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: js\ndescription: JSON test\nauthor: Dev\nlicense: MIT\n---\n\n# J\n",
            encoding="utf-8",
        )
        report = validate_enterprise_compliance(plugin_dir)
        print_json(report)
        captured = capsys.readouterr().out
        parsed = json_mod.loads(captured)
        assert "plugin_path" in parsed
        assert "skill_results" in parsed
        assert "compliance_percentage" in parsed


class TestMainEntryPoint:
    """Tests for main() CLI entry point (lines 865-919)."""

    def test_main_nonexistent_path_returns_critical_exit(self, monkeypatch):
        """main() returns EXIT_CRITICAL for nonexistent plugin path (lines 908-910)."""
        from validate_enterprise import main

        monkeypatch.setattr("sys.argv", ["validate_enterprise", "/tmp/nonexistent_path_xyz_12345"])
        exit_code = main()
        assert exit_code == 1

    def test_main_valid_plugin_json_output(self, tmp_path, monkeypatch, capsys):
        """main() with --json flag outputs valid JSON for a real plugin (lines 914-915)."""
        import json as json_mod

        from validate_enterprise import main

        plugin_dir = tmp_path / "cli-plugin"
        plugin_dir.mkdir()
        skills_dir = plugin_dir / "skills" / "cs"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: cs\ndescription: CLI skill\nauthor: Dev\nlicense: Apache-2.0\ntags:\n  - cli\n---\n\n# CS\n",
            encoding="utf-8",
        )
        monkeypatch.setattr("sys.argv", ["validate_enterprise", str(plugin_dir), "--json"])
        main()
        captured = capsys.readouterr().out
        parsed = json_mod.loads(captured)
        assert parsed["compliance_percentage"] == 100.0
        assert parsed["total_skills"] == 1

    def test_main_strict_verbose_output(self, tmp_path, monkeypatch, capsys):
        """main() with --strict --verbose flags outputs detailed report (lines 904, 912, 916-917)."""
        from validate_enterprise import main

        plugin_dir = tmp_path / "sv-plugin"
        plugin_dir.mkdir()
        monkeypatch.setattr("sys.argv", ["validate_enterprise", str(plugin_dir), "--strict", "--verbose"])
        main()
        captured = capsys.readouterr().out
        assert "STRICT" in captured
        assert "Enterprise Compliance Validation" in captured


class TestParseFrontmatterEdgeCases:
    """Tests for parse_frontmatter edge cases: empty YAML and invalid YAML."""

    def test_empty_yaml_returns_empty_dict(self):
        """parse_frontmatter returns empty dict for frontmatter with empty YAML block (lines 241-242)."""
        content = "---\n---\n\n# Body"
        fm, body = parse_frontmatter(content)
        assert fm == {}
        assert "# Body" in body

    def test_invalid_yaml_returns_none(self):
        """parse_frontmatter returns None for malformed YAML in frontmatter (lines 245-246)."""
        content = "---\n: invalid:\n  - [broken yaml\n---\n\n# Body"
        fm, body = parse_frontmatter(content)
        assert fm is None
