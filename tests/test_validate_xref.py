#!/usr/bin/env python3
"""
Tests for validate_xref.py -- Cross-Reference Validator

Tests cover the 6 cross-reference validation rules:
1. Agent Task() calls must reference existing agents
2. Subagent_type must match actual agent filenames
3. Command agent references must be valid
4. Hook script references must exist
5. Full validate_cross_references entry point
Plus edge cases with missing refs and broken paths.

Coverage: 10 tests covering all major code paths with real filesystem fixtures.
No mocking -- all tests use tmp_path with real directory structures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add scripts directory to path for imports (matches conftest.py convention)
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_xref import (
    CrossReferenceValidationReport,
    validate_agent_task_refs,
    validate_command_agent_refs,
    validate_cross_references,
    validate_hook_script_refs,
    validate_subagent_type_matching,
)


class TestValidateAgentTaskRefs:
    """Tests for Rule 1: Agent Task() calls must reference existing agents."""

    def test_valid_task_ref_passes(self, tmp_path: Path):
        """When an agent file references another existing agent via subagent_type, validation passes."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        # Create the referenced agent
        (agents_dir / "helper-agent.md").write_text("---\nname: helper-agent\n---\n# Helper\n")
        # Create the referencing agent with a subagent_type reference
        (agents_dir / "orchestrator.md").write_text(
            '---\nname: orchestrator\n---\n# Orchestrator\n\nUse Task tool with subagent_type: "helper-agent" to delegate.\n'
        )

        report = CrossReferenceValidationReport()
        available_agents = {"helper-agent", "orchestrator"}
        validate_agent_task_refs(tmp_path, report, available_agents)

        assert not report.has_major
        assert not report.has_critical
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("helper-agent" in m for m in passed_msgs)
        # Verify agent_refs tracking
        assert "agents/orchestrator.md" in report.agent_refs
        assert "helper-agent" in report.agent_refs["agents/orchestrator.md"]

    def test_missing_task_ref_reports_major(self, tmp_path: Path):
        """When an agent file references a non-existent agent via subagent_type, a MAJOR issue is reported."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "caller.md").write_text(
            '---\nname: caller\n---\n# Caller\n\nsubagent_type: "ghost-agent"\n'
        )

        report = CrossReferenceValidationReport()
        available_agents = {"caller"}
        validate_agent_task_refs(tmp_path, report, available_agents)

        assert report.has_major
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("ghost-agent" in m for m in major_msgs)


class TestValidateSubagentTypeMatching:
    """Tests for Rule 2: subagent_type values must match actual agent filenames."""

    def test_subagent_type_without_matching_file_reports_major(self, tmp_path: Path):
        """When a markdown file references a subagent_type with no matching agents/NAME.md, a MAJOR issue is reported."""
        # Create a markdown file in plugin root that references a non-existent agent
        (tmp_path / "README.md").write_text(
            '# Plugin\n\nConfigure with subagent_type = "missing-bot"\n'
        )

        report = CrossReferenceValidationReport()
        available_agents = set()  # No agents available
        validate_subagent_type_matching(tmp_path, report, available_agents)

        assert report.has_major
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("missing-bot" in m for m in major_msgs)

    def test_subagent_type_with_matching_file_no_issue(self, tmp_path: Path):
        """When subagent_type references an agent that has a matching .md file, no issue is reported."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "real-agent.md").write_text("---\nname: real-agent\n---\n# Agent\n")
        (tmp_path / "docs.md").write_text(
            '# Docs\n\nUse subagent_type: "real-agent" for delegation.\n'
        )

        report = CrossReferenceValidationReport()
        available_agents = {"real-agent"}
        validate_subagent_type_matching(tmp_path, report, available_agents)

        assert not report.has_major
        assert not report.has_critical


class TestValidateCommandAgentRefs:
    """Tests for Rule 4: Commands must not reference non-existent agents."""

    def test_command_with_valid_subagent_ref_passes(self, tmp_path: Path):
        """When a command file references an existing agent via subagent_type, validation passes."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "worker.md").write_text("---\nname: worker\n---\n# Worker\n")
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "deploy.md").write_text(
            '---\nname: deploy\n---\n# Deploy\n\nsubagent_type: "worker"\n'
        )

        report = CrossReferenceValidationReport()
        available_agents = {"worker"}
        validate_command_agent_refs(tmp_path, report, available_agents)

        assert not report.has_critical
        assert not report.has_major
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("worker" in m for m in passed_msgs)

    def test_command_with_broken_subagent_ref_reports_critical(self, tmp_path: Path):
        """When a command file references a non-existent agent via subagent_type, a CRITICAL issue is reported."""
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "broken-cmd.md").write_text(
            '---\nname: broken-cmd\n---\n# Broken\n\nsubagent_type: "nonexistent-agent"\n'
        )

        report = CrossReferenceValidationReport()
        available_agents = set()
        validate_command_agent_refs(tmp_path, report, available_agents)

        assert report.has_critical
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("nonexistent-agent" in m and "BREAKING" in m for m in critical_msgs)

class TestValidateHookScriptRefs:
    """Tests for Rule 6: Hook script references in hooks.json must exist."""

    def test_hook_script_exists_passes(self, tmp_path: Path):
        """When hook script paths in hooks.json point to real files, validation passes."""
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        # Create the script that is referenced
        scripts_subdir = tmp_path / "scripts"
        scripts_subdir.mkdir()
        script_file = scripts_subdir / "lint.py"
        script_file.write_text("#!/usr/bin/env python3\nprint('lint')\n")
        # Create hooks.json with a reference to the script
        hooks_config = {
            "PreToolUse": [
                {"command": "${CLAUDE_PLUGIN_ROOT}/scripts/lint.py"}
            ]
        }
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks_config))

        report = CrossReferenceValidationReport()
        validate_hook_script_refs(tmp_path, report)

        assert not report.has_critical
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("scripts/lint.py" in m for m in passed_msgs)

    def test_hook_script_missing_reports_critical(self, tmp_path: Path):
        """When hooks.json references a script path that does not exist, a CRITICAL issue is reported."""
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hooks_config = {
            "PostToolUse": [
                {"command": "${CLAUDE_PLUGIN_ROOT}/scripts/missing-script.sh"}
            ]
        }
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks_config))

        report = CrossReferenceValidationReport()
        validate_hook_script_refs(tmp_path, report)

        assert report.has_critical
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("missing-script.sh" in m for m in critical_msgs)


class TestValidateCrossReferences:
    """Tests for the main validate_cross_references entry point."""

    def test_full_valid_plugin_passes(self, tmp_path: Path):
        """A complete plugin with valid cross-references produces no CRITICAL or MAJOR issues."""
        # Build a realistic plugin structure
        plugin = tmp_path / "my-plugin"
        plugin.mkdir()
        # .claude-plugin/plugin.json
        cp_dir = plugin / ".claude-plugin"
        cp_dir.mkdir()
        (cp_dir / "plugin.json").write_text(json.dumps({
            "name": "my-plugin",
            "version": "1.0.0",
            "description": "Test plugin",
        }))
        # agents/
        agents_dir = plugin / "agents"
        agents_dir.mkdir()
        (agents_dir / "builder.md").write_text(
            '---\nname: builder\n---\n# Builder\n\nsubagent_type: "builder"\n'
        )
        # commands/
        commands_dir = plugin / "commands"
        commands_dir.mkdir()
        (commands_dir / "build.md").write_text(
            '---\nname: build\n---\n# Build\n\nsubagent_type: "builder"\n'
        )
        # README
        (plugin / "README.md").write_text("# my-plugin\n\nVersion: 1.0.0\n")

        report = validate_cross_references(plugin)

        assert not report.has_critical
        assert not report.has_major

    def test_nonexistent_plugin_path_reports_critical(self, tmp_path: Path):
        """When validate_cross_references is called with a non-existent path, a CRITICAL issue is reported."""
        bogus = tmp_path / "does-not-exist"
        report = validate_cross_references(bogus)

        assert report.has_critical
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("does not exist" in m for m in critical_msgs)

    def test_plugin_path_is_file_reports_critical(self, tmp_path: Path):
        """When validate_cross_references is given a file path instead of a directory, a CRITICAL issue is reported."""
        # Covers lines 640-642: plugin_root.is_dir() check
        fake_file = tmp_path / "not-a-dir.txt"
        fake_file.write_text("I am a file, not a directory")

        report = validate_cross_references(fake_file)

        assert report.has_critical
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("not a directory" in m for m in critical_msgs)


# =========================================================================
# Additional coverage tests (appended)
# =========================================================================

from validate_xref import (
    get_available_agents,
    get_available_skills,
    parse_yaml_frontmatter,
    validate_skill_refs,
    validate_version_sync,
)


class TestCrossReferenceValidationReportToDict:
    """Tests for CrossReferenceValidationReport.to_dict serialization."""

    def test_to_dict_includes_all_xref_fields(self, tmp_path: Path):
        """to_dict returns base fields plus plugin_path, agent_refs, skill_refs, version_sources, hook_script_refs."""
        # Covers lines 104-110
        report = CrossReferenceValidationReport()
        report.plugin_path = "/some/plugin"
        report.agent_refs = {"agents/a.md": ["helper"]}
        report.skill_refs = {"README.md": ["my-skill"]}
        report.version_sources = {"plugin.json": "1.0.0"}
        report.hook_script_refs = ["scripts/hook.py"]
        report.passed("sample check passed")

        d = report.to_dict()

        assert d["plugin_path"] == "/some/plugin"
        assert d["agent_refs"] == {"agents/a.md": ["helper"]}
        assert d["skill_refs"] == {"README.md": ["my-skill"]}
        assert d["version_sources"] == {"plugin.json": "1.0.0"}
        assert d["hook_script_refs"] == ["scripts/hook.py"]
        # Base class fields should also be present
        assert "results" in d
        assert "score" in d


class TestGetAvailableAgents:
    """Tests for get_available_agents helper."""

    def test_returns_agent_names_without_extension(self, tmp_path: Path):
        """get_available_agents returns stem names of .md files inside agents/ directory."""
        # Covers lines 127-136 (agent file loop)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "alpha.md").write_text("# Alpha")
        (agents_dir / "beta-bot.md").write_text("# Beta")
        (agents_dir / "not-agent.txt").write_text("ignored")

        result = get_available_agents(tmp_path)

        assert result == {"alpha", "beta-bot"}

    def test_returns_empty_set_when_no_agents_dir(self, tmp_path: Path):
        """get_available_agents returns empty set when agents/ directory does not exist."""
        # Covers line 129
        result = get_available_agents(tmp_path)
        assert result == set()


class TestGetAvailableSkills:
    """Tests for get_available_skills helper."""

    def test_returns_skill_directory_names(self, tmp_path: Path):
        """get_available_skills returns names of subdirectories inside skills/ (excluding hidden)."""
        # Covers lines 152-156
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "code-review").mkdir()
        (skills_dir / "deployment").mkdir()
        (skills_dir / ".hidden-skill").mkdir()
        # A file should be ignored (not a directory)
        (skills_dir / "README.md").write_text("# Skills")

        result = get_available_skills(tmp_path)

        assert result == {"code-review", "deployment"}


class TestParseYamlFrontmatter:
    """Tests for parse_yaml_frontmatter helper."""

    def test_parses_valid_frontmatter(self):
        """parse_yaml_frontmatter extracts YAML between --- delimiters and returns a dict."""
        # Covers lines 180-189
        content = "---\nname: my-agent\nversion: 1.0\n---\n# Agent Body\n"
        result = parse_yaml_frontmatter(content)

        assert result is not None
        assert result["name"] == "my-agent"
        assert result["version"] == 1.0

    def test_returns_none_for_no_frontmatter(self):
        """parse_yaml_frontmatter returns None when content does not start with ---."""
        # Covers line 181
        result = parse_yaml_frontmatter("# Just markdown\nNo frontmatter here")
        assert result is None

    def test_returns_none_for_invalid_yaml(self):
        """parse_yaml_frontmatter returns None when YAML between --- is malformed."""
        # Covers lines 190-191 (YAMLError branch)
        content = "---\n: invalid: yaml: [broken\n---\n# Body\n"
        result = parse_yaml_frontmatter(content)
        assert result is None


class TestValidateVersionSync:
    """Tests for Rule 3: Version synchronization across plugin files."""

    def test_version_mismatch_reports_major(self, tmp_path: Path):
        """When plugin.json and README.md have different versions, a MAJOR issue is reported."""
        # Covers lines 317-319 (plugin.json version), 329 (README version),
        # 361-363 (< 2 sources), 371-372 (mismatch branch)
        cp_dir = tmp_path / ".claude-plugin"
        cp_dir.mkdir()
        (cp_dir / "plugin.json").write_text(json.dumps({"name": "test", "version": "2.0.0"}))
        (tmp_path / "README.md").write_text("# Test Plugin\n\nVersion: 1.0.0\n")

        report = CrossReferenceValidationReport()
        validate_version_sync(tmp_path, report)

        assert report.has_major
        assert report.version_sources["plugin.json"] == "2.0.0"
        assert report.version_sources["README.md"] == "1.0.0"
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("mismatch" in m.lower() for m in major_msgs)

    def test_version_sync_with_marketplace_and_pyproject(self, tmp_path: Path):
        """When marketplace.json and pyproject.toml agree with plugin.json, all versions pass."""
        # Covers lines 336-346 (marketplace.json), 351-357 (pyproject.toml)
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()
        cp_dir = plugin_dir / ".claude-plugin"
        cp_dir.mkdir()
        (cp_dir / "plugin.json").write_text(json.dumps({"name": "my-plugin", "version": "3.1.0"}))
        (plugin_dir / "README.md").write_text("# Plugin\n\nVersion: 3.1.0\n")
        # marketplace.json lives in parent of plugin_dir
        marketplace = {
            "plugins": [
                {"name": "other-plugin", "version": "1.0.0"},
                {"name": "my-plugin", "version": "3.1.0"},
            ]
        }
        (tmp_path / "marketplace.json").write_text(json.dumps(marketplace))
        # pyproject.toml inside plugin
        (plugin_dir / "pyproject.toml").write_text('[project]\nname = "my-plugin"\nversion = "3.1.0"\n')

        report = CrossReferenceValidationReport()
        validate_version_sync(plugin_dir, report)

        assert not report.has_major
        assert not report.has_critical
        assert report.version_sources["plugin.json"] == "3.1.0"
        assert report.version_sources["marketplace.json"] == "3.1.0"
        assert report.version_sources["pyproject.toml"] == "3.1.0"
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("agree" in m for m in passed_msgs)

    def test_single_version_source_skips_sync_check(self, tmp_path: Path):
        """When only one version source is found, sync check is skipped with INFO."""
        # Covers lines 361-362 (< 2 sources branch)
        cp_dir = tmp_path / ".claude-plugin"
        cp_dir.mkdir()
        (cp_dir / "plugin.json").write_text(json.dumps({"name": "test", "version": "1.0.0"}))

        report = CrossReferenceValidationReport()
        validate_version_sync(tmp_path, report)

        assert not report.has_major
        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("sync check skipped" in m for m in info_msgs)


class TestValidateCommandAgentRefsExtended:
    """Extended tests for Rule 4: command agent references edge cases."""

    def test_no_commands_dir_reports_info(self, tmp_path: Path):
        """When commands/ directory does not exist, an INFO message is logged and check is skipped."""
        # Covers lines 396-398
        report = CrossReferenceValidationReport()
        validate_command_agent_refs(tmp_path, report, set())

        info_msgs = [r.message for r in report.results if r.level == "INFO"]
        assert any("No commands/" in m for m in info_msgs)

    def test_command_with_unknown_spawn_pattern_reports_major(self, tmp_path: Path):
        """When a command uses 'spawn unknown-bot agent' pattern with non-builtin agent, a MAJOR issue is reported."""
        # Covers lines 424-435 (AGENT_SPAWN_PATTERN + builtin check)
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "run.md").write_text(
            '---\nname: run\n---\n# Run\n\nPlease spawn "custom-bot" agent to handle this task.\n'
        )

        report = CrossReferenceValidationReport()
        validate_command_agent_refs(tmp_path, report, set())

        assert report.has_major
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("custom-bot" in m for m in major_msgs)


class TestValidateSkillRefs:
    """Tests for Rule 5: Skill references in code must point to existing skills."""

    def test_valid_skill_ref_passes(self, tmp_path: Path):
        """When a file references skills/my-skill and skills/my-skill/ dir exists, validation passes."""
        # Covers lines 468-469, 475-476, 478-489
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "lint-check").mkdir()
        (tmp_path / "README.md").write_text("# Plugin\n\nUses skills/lint-check for linting.\n")

        report = CrossReferenceValidationReport()
        validate_skill_refs(tmp_path, report, {"lint-check"})

        assert not report.has_major
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("lint-check" in m for m in passed_msgs)
        assert "README.md" in report.skill_refs
        assert "lint-check" in report.skill_refs["README.md"]

    def test_missing_skill_ref_reports_major(self, tmp_path: Path):
        """When a file references skills/ghost-skill that does not exist, a MAJOR issue is reported."""
        # Covers lines 479-484 (non-existent skill branch)
        (tmp_path / "setup.py").write_text("# References skills/ghost-skill for setup\n")

        report = CrossReferenceValidationReport()
        validate_skill_refs(tmp_path, report, set())

        assert report.has_major
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("ghost-skill" in m for m in major_msgs)


class TestValidateHookScriptRefsExtended:
    """Extended tests for hook script validation edge cases."""

    def test_hooks_from_plugin_json_string_path(self, tmp_path: Path):
        """When plugin.json has hooks as a string path, that hooks file is also validated."""
        # Covers lines 523-530 (hooks in plugin.json as string)
        cp_dir = tmp_path / ".claude-plugin"
        cp_dir.mkdir()
        custom_hooks_dir = tmp_path / "custom"
        custom_hooks_dir.mkdir()
        hooks_config = {"PreToolUse": [{"command": "${CLAUDE_PLUGIN_ROOT}/scripts/check.py"}]}
        (custom_hooks_dir / "my-hooks.json").write_text(json.dumps(hooks_config))
        (cp_dir / "plugin.json").write_text(json.dumps({
            "name": "test",
            "hooks": "./custom/my-hooks.json",
        }))
        # Script does NOT exist, so we expect a CRITICAL
        report = CrossReferenceValidationReport()
        validate_hook_script_refs(tmp_path, report)

        assert report.has_critical
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("scripts/check.py" in m for m in critical_msgs)

    def test_shell_script_not_executable_reports_minor(self, tmp_path: Path):
        """When a .sh hook script exists but is not executable, a MINOR issue is reported."""
        # Covers lines 562-569 (executable check for .sh scripts)
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        sh_script = scripts_dir / "setup.sh"
        sh_script.write_text("#!/bin/bash\necho setup\n")
        # Explicitly remove execute permission
        sh_script.chmod(0o644)

        hooks_config = {"PreToolUse": [{"command": "${CLAUDE_PLUGIN_ROOT}/scripts/setup.sh"}]}
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks_config))

        report = CrossReferenceValidationReport()
        validate_hook_script_refs(tmp_path, report)

        assert not report.has_critical
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("not executable" in m for m in minor_msgs)

    def test_shell_script_executable_passes(self, tmp_path: Path):
        """When a .sh hook script exists and is executable, validation passes with PASSED."""
        # Covers lines 570-573 (executable .sh passes)
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        sh_script = scripts_dir / "deploy.sh"
        sh_script.write_text("#!/bin/bash\necho deploy\n")
        sh_script.chmod(0o755)

        hooks_config = {"PreToolUse": [{"command": "${CLAUDE_PLUGIN_ROOT}/scripts/deploy.sh"}]}
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks_config))

        report = CrossReferenceValidationReport()
        validate_hook_script_refs(tmp_path, report)

        assert not report.has_critical
        assert not report.has_minor
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("executable" in m for m in passed_msgs)

    def test_malformed_hooks_json_reports_minor(self, tmp_path: Path):
        """When hooks.json contains invalid JSON, a MINOR issue is reported."""
        # Covers lines 540-542 (json parse error)
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "hooks.json").write_text("{invalid json content!!!")

        report = CrossReferenceValidationReport()
        validate_hook_script_refs(tmp_path, report)

        assert report.has_minor
        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any("Could not parse" in m for m in minor_msgs)
