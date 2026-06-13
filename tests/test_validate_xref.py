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

    def test_missing_task_ref_reports_critical(self, tmp_path: Path):
        """When an agent file references a non-existent agent via subagent_type, a CRITICAL RC-GHOST-DISPATCH-001 finding is emitted (per TRDD-25b9be90)."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "caller.md").write_text('---\nname: caller\n---\n# Caller\n\nsubagent_type: "ghost-agent"\n')

        report = CrossReferenceValidationReport()
        available_agents = {"caller"}
        validate_agent_task_refs(tmp_path, report, available_agents)

        assert report.has_critical
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("ghost-agent" in m for m in critical_msgs)
        assert any("RC-GHOST-DISPATCH-001" in m for m in critical_msgs)


class TestValidateSubagentTypeMatching:
    """Tests for Rule 2: subagent_type values must match actual agent filenames."""

    def test_subagent_type_without_matching_file_reports_critical(self, tmp_path: Path):
        """When an agent file references a subagent_type with no matching agents/NAME.md, a CRITICAL RC-GHOST-DISPATCH-001 finding is emitted (per TRDD-25b9be90).

        Note: per the TRDD-25b9be90 scope narrowing, only executable
        directories (agents/, commands/, skills/) are scanned — README.md
        at plugin root is no longer scanned.
        """
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "host.md").write_text(
            '---\nname: host\n---\n# Host\n\nConfigure with subagent_type = "missing-bot"\n'
        )

        report = CrossReferenceValidationReport()
        available_agents: set[str] = {"host"}
        validate_subagent_type_matching(tmp_path, report, available_agents)

        assert report.has_critical
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("missing-bot" in m for m in critical_msgs)
        assert any("RC-GHOST-DISPATCH-001" in m for m in critical_msgs)

    def test_subagent_type_with_matching_file_no_issue(self, tmp_path: Path):
        """When subagent_type references an agent that has a matching .md file, no issue is reported.

        Note: per TRDD-25b9be90 the scope is narrowed to agents/, commands/,
        skills/ — fixture file lives in agents/ so it is scanned.
        """
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "real-agent.md").write_text("---\nname: real-agent\n---\n# Agent\n")
        (agents_dir / "caller.md").write_text(
            '---\nname: caller\n---\n# Caller\n\nUse subagent_type: "real-agent" for delegation.\n'
        )

        report = CrossReferenceValidationReport()
        available_agents = {"real-agent", "caller"}
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
        (commands_dir / "deploy.md").write_text('---\nname: deploy\n---\n# Deploy\n\nsubagent_type: "worker"\n')

        report = CrossReferenceValidationReport()
        available_agents = {"worker"}
        validate_command_agent_refs(tmp_path, report, available_agents)

        assert not report.has_critical
        assert not report.has_major
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("worker" in m for m in passed_msgs)

    def test_command_with_broken_subagent_ref_reports_critical(self, tmp_path: Path):
        """When a command file references a non-existent agent via subagent_type, a CRITICAL RC-GHOST-DISPATCH-001 finding is emitted (per TRDD-25b9be90)."""
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "broken-cmd.md").write_text(
            '---\nname: broken-cmd\n---\n# Broken\n\nsubagent_type: "nonexistent-agent"\n'
        )

        report = CrossReferenceValidationReport()
        available_agents: set[str] = set()
        validate_command_agent_refs(tmp_path, report, available_agents)

        assert report.has_critical
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("nonexistent-agent" in m and "BREAKING" in m for m in critical_msgs)
        assert any("RC-GHOST-DISPATCH-001" in m for m in critical_msgs)


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
        hooks_config = {"PreToolUse": [{"command": "${CLAUDE_PLUGIN_ROOT}/scripts/lint.py"}]}
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
        hooks_config = {"PostToolUse": [{"command": "${CLAUDE_PLUGIN_ROOT}/scripts/missing-script.sh"}]}
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks_config))

        report = CrossReferenceValidationReport()
        validate_hook_script_refs(tmp_path, report)

        assert report.has_critical
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("missing-script.sh" in m for m in critical_msgs)

    def test_hook_script_in_hidden_dir_no_false_critical(self, tmp_path: Path):
        """M4 regression: a hook target inside a hidden dir (leading-dot segment)
        must resolve correctly and NOT produce a false CRITICAL.

        ``str.lstrip("./")`` strips a char SET, so ".config/run.sh" was mangled
        into "config/run.sh" — a path that doesn't exist — yielding a false
        'non-existent script' CRITICAL. The fix strips only a single optional
        "./" PREFIX.
        """
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hidden = tmp_path / ".config"
        hidden.mkdir()
        (hidden / "run.sh").write_text("#!/usr/bin/env bash\necho run\n")
        hooks_config = {"PreToolUse": [{"command": "${CLAUDE_PLUGIN_ROOT}/.config/run.sh"}]}
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks_config))

        report = CrossReferenceValidationReport()
        validate_hook_script_refs(tmp_path, report)

        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert not any(".config/run.sh" in m for m in critical_msgs), (
            f"hidden-dir hook target mangled into a false CRITICAL: {critical_msgs}"
        )

    def test_hook_script_dot_slash_prefix_still_resolves(self, tmp_path: Path):
        """M4 two-sided: a legitimate leading './' prefix is still stripped and
        the script resolves (the fix must keep handling the common './' case)."""
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        scripts_subdir = tmp_path / "scripts"
        scripts_subdir.mkdir()
        (scripts_subdir / "go.sh").write_text("#!/usr/bin/env bash\necho go\n")
        hooks_config = {"PreToolUse": [{"command": "${CLAUDE_PLUGIN_ROOT}/./scripts/go.sh"}]}
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks_config))

        report = CrossReferenceValidationReport()
        validate_hook_script_refs(tmp_path, report)

        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert not any("go.sh" in m for m in critical_msgs), (
            f"'./'-prefixed hook target failed to resolve: {critical_msgs}"
        )


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
        (cp_dir / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "my-plugin",
                    "version": "1.0.0",
                    "description": "Test plugin",
                }
            )
        )
        # agents/
        agents_dir = plugin / "agents"
        agents_dir.mkdir()
        (agents_dir / "builder.md").write_text('---\nname: builder\n---\n# Builder\n\nsubagent_type: "builder"\n')
        # commands/
        commands_dir = plugin / "commands"
        commands_dir.mkdir()
        (commands_dir / "build.md").write_text('---\nname: build\n---\n# Build\n\nsubagent_type: "builder"\n')
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

    def test_readme_four_segment_version_not_truncated(self, tmp_path: Path):
        """m3 regression: a 4-segment README version must NOT be truncated to a
        3-segment match that falsely agrees with plugin.json.

        Before the fix, VERSION_PATTERN matched '1.2.3' out of 'Version: 1.2.3.4',
        so README '1.2.3.4' + plugin.json '1.2.3' were recorded equal and the
        sync check falsely passed. The boundary now rejects the 4-segment form,
        so the README source is simply not captured (it doesn't masquerade as
        '1.2.3').
        """
        cp_dir = tmp_path / ".claude-plugin"
        cp_dir.mkdir()
        (cp_dir / "plugin.json").write_text(json.dumps({"name": "test", "version": "1.2.3"}))
        (tmp_path / "README.md").write_text("# Test Plugin\n\nVersion: 1.2.3.4\n")

        report = CrossReferenceValidationReport()
        validate_version_sync(tmp_path, report)

        # The README's 4-segment version must NOT be recorded as the truncated
        # '1.2.3' (which would be a false agreement with plugin.json).
        assert report.version_sources.get("README.md") != "1.2.3", (
            "VERSION_PATTERN truncated a 4-segment version into a false match"
        )

    def test_readme_three_segment_version_still_captured(self, tmp_path: Path):
        """m3 two-sided: a normal 3-segment README version is still captured and
        compared (the boundary fix must not break the common case)."""
        cp_dir = tmp_path / ".claude-plugin"
        cp_dir.mkdir()
        (cp_dir / "plugin.json").write_text(json.dumps({"name": "test", "version": "2.5.0"}))
        (tmp_path / "README.md").write_text("# Test Plugin\n\nVersion: 2.5.0\n")

        report = CrossReferenceValidationReport()
        validate_version_sync(tmp_path, report)

        assert report.version_sources.get("README.md") == "2.5.0"
        assert not report.has_major

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

    def test_command_with_unknown_spawn_pattern_reports_warning(self, tmp_path: Path):
        """A PROSE spawn/invoke mention of an unknown agent is an advisory WARNING,
        not a blocking MAJOR (audit doc #6 recalibration — the heuristic fires on
        innocuous English like 'use the browser agent')."""
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "run.md").write_text(
            '---\nname: run\n---\n# Run\n\nPlease spawn "custom-bot" agent to handle this task.\n'
        )

        report = CrossReferenceValidationReport()
        validate_command_agent_refs(tmp_path, report, set())

        # Recalibrated to WARNING — must NOT block (no MAJOR), but stays visible.
        assert not report.has_major
        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        assert any("custom-bot" in m for m in warning_msgs)

    def test_command_prose_word_adjacent_to_agent_does_not_warn(self, tmp_path: Path):
        """Issue #110: a bare English word adjacent to 'agent' ('explicit',
        'specific', 'single') is prose, not a dispatch target — it must NOT
        raise the advisory agent-name WARNING. A hyphenated unknown agent
        ('evil-exfil-agent') still warns (FN-safe)."""
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        # FP that must CLEAR: bare prose words adjacent to 'agent'.
        (commands_dir / "prose.md").write_text(
            "---\nname: prose\n---\n# Prose\n\n"
            "Use the explicit agent list, then use the specific agent registry, "
            "then use the single agent fallback.\n"
        )
        # Real reference that must STILL warn: a hyphenated unknown agent.
        (commands_dir / "real.md").write_text(
            "---\nname: real\n---\n# Real\n\nspawn the evil-exfil-agent agent to do the work.\n"
        )

        report = CrossReferenceValidationReport()
        validate_command_agent_refs(tmp_path, report, set())

        warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
        # FP cleared: no advisory warning naming a bare prose word.
        assert not any(
            f"'{w}'" in m for m in warning_msgs for w in ("explicit", "specific", "single")
        )
        # Real signal preserved: the hyphenated unknown agent still warns, advisory-only.
        assert any("evil-exfil-agent" in m for m in warning_msgs)
        assert not report.has_major


class TestValidateSkillRefs:
    """Tests for Rule 5: Skill references in code must point to existing skills."""

    def test_valid_skill_ref_passes(self, tmp_path: Path):
        """When a file references skills/my-skill and skills/my-skill/ dir exists, validation passes.

        Note: per TRDD-25b9be90 Phase 5, validate_skill_refs scope is narrowed
        to executable directories (agents/, commands/, skills/). Fixture lives
        in agents/ so it is scanned.
        """
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "lint-check").mkdir()
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "host.md").write_text("---\nname: host\n---\n# Host\n\nUses skills/lint-check for linting.\n")

        report = CrossReferenceValidationReport()
        validate_skill_refs(tmp_path, report, {"lint-check"})

        assert not report.has_major
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("lint-check" in m for m in passed_msgs)
        assert "agents/host.md" in report.skill_refs
        assert "lint-check" in report.skill_refs["agents/host.md"]

    def test_missing_skill_ref_reports_major(self, tmp_path: Path):
        """When a file references skills/ghost-skill that does not exist, a MAJOR issue is reported.

        Note: per TRDD-25b9be90 Phase 5, fixture lives in commands/ (scanned).
        """
        commands_dir = tmp_path / "commands"
        commands_dir.mkdir()
        (commands_dir / "host.md").write_text("---\nname: host\n---\n# Host\n\nUses skills/ghost-skill for setup\n")

        report = CrossReferenceValidationReport()
        validate_skill_refs(tmp_path, report, set())

        assert report.has_major
        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("ghost-skill" in m for m in major_msgs)

    def test_mixed_case_skill_ref_resolves_no_false_major(self, tmp_path: Path):
        """m5 regression: a ref to a mixed-case skill dir must resolve.

        The reference name is lowercased before lookup; before the fix the
        available-skills set kept raw directory names, so 'MySkill' (dir) vs
        'myskill' (lowercased ref) never matched and produced a false
        'non-existent skill' MAJOR. The lookup set is now lowercased on both
        sides.
        """
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "host.md").write_text("---\nname: host\n---\n# Host\n\nUses skills/MySkill here.\n")

        report = CrossReferenceValidationReport()
        validate_skill_refs(tmp_path, report, {"MySkill"})  # raw dir name, mixed case

        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert not any("MySkill" in m for m in major_msgs), (
            f"mixed-case skill dir produced a false 'non-existent skill': {major_msgs}"
        )

    def test_truly_missing_skill_still_major_under_case_fold(self, tmp_path: Path):
        """m5 two-sided: case-insensitive lookup must NOT mask a genuinely
        absent skill — a ref with no matching dir (any case) is still MAJOR."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "host.md").write_text("---\nname: host\n---\n# Host\n\nUses skills/Absent here.\n")

        report = CrossReferenceValidationReport()
        validate_skill_refs(tmp_path, report, {"present"})  # 'Absent' is not present in any case

        major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Absent" in m for m in major_msgs)


class TestStripNoiseCRLF:
    """m4 regression: _strip_noise must blank CRLF-authored frontmatter.

    Tested at the unit level because _strip_noise is the function with the
    defect — the literal '---\\n' (LF) gate skipped CRLF frontmatter, leaving
    metadata tokens (subagent_type:, skills/...) in the content the dispatch /
    skill-ref workers subsequently scan.
    """

    def test_crlf_frontmatter_is_blanked(self):
        """CRLF frontmatter content must be blanked out (not scanned as body)."""
        from validate_xref import _strip_noise

        crlf = '---\r\nname: caller\r\nsubagent_type: "ghost"\r\nskills/secret-skill\r\n---\r\n# Body\r\n\r\nrealcontent\r\n'
        out = _strip_noise(crlf)
        frontmatter_region = out.split("# Body")[0]
        assert "ghost" not in frontmatter_region, "CRLF frontmatter subagent_type was not stripped"
        assert "secret-skill" not in frontmatter_region, "CRLF frontmatter skills ref was not stripped"

    def test_crlf_strip_preserves_body_and_line_count(self):
        """m4 two-sided: the body after CRLF frontmatter is preserved, and line
        count is unchanged so downstream line numbers stay correct (\\r is
        blanked to a space, \\n is kept)."""
        from validate_xref import _strip_noise

        crlf = '---\r\nname: caller\r\nsubagent_type: "ghost"\r\n---\r\n# Body\r\n\r\nrealcontent\r\n'
        out = _strip_noise(crlf)
        assert "realcontent" in out, "body content was lost by the CRLF frontmatter strip"
        assert crlf.count("\n") == out.count("\n"), "line count changed — downstream line numbers would drift"

    def test_lf_frontmatter_still_blanked(self):
        """m4 two-sided: the original LF case must still be stripped (the fix
        broadens the match, it must not narrow it)."""
        from validate_xref import _strip_noise

        lf = '---\nname: caller\nsubagent_type: "ghost"\n---\n# Body\n\nrealcontent\n'
        out = _strip_noise(lf)
        assert "ghost" not in out.split("# Body")[0]
        assert "realcontent" in out


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
        (cp_dir / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "test",
                    "hooks": "./custom/my-hooks.json",
                }
            )
        )
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


# =========================================================================
# TRDD-25b9be90 — Ghost-agent dispatch detection (new behavior)
# =========================================================================

from validate_xref import (  # noqa: E402  — re-imported so the additions are self-contained
    BUILTIN_AGENTS,
    RC_GHOST_DISPATCH_CROSS_PLUGIN,
    RC_GHOST_DISPATCH_DYNAMIC,
    RC_GHOST_DISPATCH_UNRESOLVED,
    _classify_dispatch,
    _extract_dispatch_refs,
    _resolve_dispatch_ref,
    _strip_noise,
)


class TestStripNoise:
    """Tests for _strip_noise(): YAML frontmatter, noise fenced blocks, HTML comments."""

    def test_strips_yaml_frontmatter(self):
        """Leading YAML frontmatter is replaced with whitespace so its content cannot match."""
        content = '---\nname: thing\nsubagent_type: "should-not-match"\n---\n# Body\n'
        stripped = _strip_noise(content)
        # The frontmatter region is blanked out (whitespace) so no subagent_type literal survives there
        assert "should-not-match" not in stripped
        # Body remains
        assert "# Body" in stripped

    def test_strips_fenced_text_block(self):
        """Fenced code blocks marked ``text`` are blanked out (example output, not directives)."""
        content = '# Title\n\n```text\nsubagent_type: "fake"\n```\n\nReal: subagent_type: "real"\n'
        stripped = _strip_noise(content)
        assert "fake" not in stripped
        assert "real" in stripped

    def test_strips_fenced_output_block(self):
        """Fenced code blocks marked ``output`` are blanked out."""
        content = '```output\nsubagent_type="example-output-only"\n```\n'
        stripped = _strip_noise(content)
        assert "example-output-only" not in stripped

    def test_strips_fenced_console_block(self):
        """Fenced code blocks marked ``console`` are blanked out."""
        content = "```console\n$ task subagent_type=ignored\n```\n"
        stripped = _strip_noise(content)
        assert "ignored" not in stripped

    def test_strips_fenced_log_block(self):
        """Fenced code blocks marked ``log`` are blanked out."""
        content = '```log\n[2026-05-19] subagent_type="from-log"\n```\n'
        stripped = _strip_noise(content)
        assert "from-log" not in stripped

    def test_does_not_strip_python_block(self):
        """Fenced ``python`` blocks ARE directives — preserved."""
        content = '```python\nsubagent_type = "keep-me"\n```\n'
        stripped = _strip_noise(content)
        assert "keep-me" in stripped

    def test_strips_html_comments(self):
        """HTML comments are blanked out — they're hidden prose, not directives."""
        content = 'Real text. <!-- subagent_type: "hidden-in-comment" --> More text.\n'
        stripped = _strip_noise(content)
        assert "hidden-in-comment" not in stripped


class TestClassifyDispatch:
    """Tests for _classify_dispatch() helper."""

    def test_quoted_returns_literal(self):
        """A quoted match returns literal regardless of separator."""
        result = _classify_dispatch(True, "agent-name", ":")
        assert result == ("literal", "agent-name")

    def test_unquoted_kebab_yaml_returns_literal(self):
        """Bare YAML kebab-case value (`subagent_type: foo-bar`) is a literal."""
        result = _classify_dispatch(False, "foo-bar", ":")
        assert result == ("literal", "foo-bar")

    def test_unquoted_namespaced_returns_literal(self):
        """Bare value containing `:` (namespace) is a literal regardless of separator."""
        result = _classify_dispatch(False, "plugin:agent", "=")
        assert result == ("literal", "plugin:agent")

    def test_unquoted_variable_in_python_returns_dynamic(self):
        """Plain identifier in Python kwarg context (`subagent_type=foo`) is dynamic."""
        result = _classify_dispatch(False, "myvar", "=")
        assert result == ("dynamic", "myvar")

    def test_unquoted_word_in_yaml_returns_literal(self):
        """Plain identifier in YAML context (`subagent_type: foo`) is a literal (`foo` is the value)."""
        result = _classify_dispatch(False, "foo", ":")
        assert result == ("literal", "foo")


class TestExtractDispatchRefs:
    """Tests for _extract_dispatch_refs() — the 4-variant extractor."""

    def test_extracts_yaml_quoted_form(self):
        """Variant 1: `subagent_type: "agent-name"` is extracted as a literal."""
        refs = _extract_dispatch_refs('subagent_type: "alpha-bot"\n')
        assert ("literal", "alpha-bot") in refs

    def test_extracts_yaml_bare_kebab_form(self):
        """Variant 2: `subagent_type: alpha-bot` (unquoted kebab) is extracted as a literal."""
        refs = _extract_dispatch_refs("subagent_type: alpha-bot\n")
        assert ("literal", "alpha-bot") in refs

    def test_extracts_python_kwarg_quoted(self):
        """Variant 3a: `subagent_type="alpha-bot"` (Python quoted) is extracted as a literal."""
        refs = _extract_dispatch_refs('Task(subagent_type="alpha-bot")\n')
        assert ("literal", "alpha-bot") in refs

    def test_extracts_python_kwarg_dynamic(self):
        """Variant 3b: `subagent_type=myvar` (Python unquoted identifier) is extracted as dynamic."""
        refs = _extract_dispatch_refs("Task(subagent_type=myvar)\n")
        assert ("dynamic", "myvar") in refs

    def test_extracts_json_object_form(self):
        """Variant 4: `"subagent_type": "alpha-bot"` (JSON-object) is extracted as a literal."""
        refs = _extract_dispatch_refs('{"subagent_type": "alpha-bot"}\n')
        assert ("literal", "alpha-bot") in refs

    def test_extracts_namespaced_plugin_agent(self):
        """A plugin:agent namespaced reference is extracted as a single literal token."""
        refs = _extract_dispatch_refs('subagent_type: "my-plugin:my-agent"\n')
        assert ("literal", "my-plugin:my-agent") in refs

    def test_skips_examples_in_text_fenced_block(self):
        """Examples inside text-fenced blocks are skipped by the noise filter."""
        content = '```text\nsubagent_type: "example-only"\n```\nReal: `subagent_type: "real-agent"`\n'
        refs = _extract_dispatch_refs(content)
        names = [n for _, n in refs]
        assert "example-only" not in names
        assert "real-agent" in names

    def test_deduplicates_within_file(self):
        """Same (kind, name) tuple appearing twice is collapsed to one entry."""
        content = 'subagent_type: "duplicated"\nlater: subagent_type: "duplicated"\n'
        refs = _extract_dispatch_refs(content)
        # Should appear exactly once
        assert refs.count(("literal", "duplicated")) == 1


class TestBuiltinAgents:
    """Tests for the BUILTIN_AGENTS allow-list (TRDD-25b9be90)."""

    def test_general_purpose_is_builtin(self):
        """`general-purpose` is the universal catch-all and must be in BUILTIN_AGENTS."""
        assert "general-purpose" in BUILTIN_AGENTS

    def test_explore_is_builtin(self):
        """`explore` is the fast read-only search agent and must be in BUILTIN_AGENTS."""
        assert "explore" in BUILTIN_AGENTS

    def test_plan_is_builtin(self):
        """`plan` is the architect agent and must be in BUILTIN_AGENTS."""
        assert "plan" in BUILTIN_AGENTS

    def test_statusline_setup_is_builtin(self):
        """`statusline-setup` is the built-in status line config agent."""
        assert "statusline-setup" in BUILTIN_AGENTS

    def test_scout_is_not_builtin(self):
        """`scout` was wrongly in the old builtin list — must NOT be in BUILTIN_AGENTS (it's a user-scope agent)."""
        assert "scout" not in BUILTIN_AGENTS

    def test_oracle_is_not_builtin(self):
        """`oracle` was wrongly in the old builtin list — must NOT be in BUILTIN_AGENTS (it's a ghost agent)."""
        assert "oracle" not in BUILTIN_AGENTS


class TestResolveDispatchRef:
    """Tests for _resolve_dispatch_ref(): built-in / in-plugin / cross-plugin / ghost."""

    def test_builtin_general_purpose_resolves(self):
        """A bare reference to `general-purpose` resolves as ok via BUILTIN_AGENTS."""
        status, _ = _resolve_dispatch_ref("general-purpose", set())
        assert status == "ok"

    def test_builtin_case_insensitive(self):
        """Built-in lookup is case/separator-insensitive — `Explore` resolves as ok."""
        status, _ = _resolve_dispatch_ref("Explore", set())
        assert status == "ok"

    def test_in_plugin_agent_resolves(self):
        """A bare reference to an in-plugin agent resolves as ok."""
        status, _ = _resolve_dispatch_ref("local-agent", {"local-agent"})
        assert status == "ok"

    def test_in_plugin_fuzzy_resolves(self):
        """A reference using non-canonical case/separators resolves as ok-fuzzy with the canonical form."""
        status, canonical = _resolve_dispatch_ref("Local Agent", {"local-agent"})
        assert status == "ok-fuzzy"
        assert canonical == "local-agent"

    def test_unresolved_returns_ghost(self):
        """A reference that matches nothing returns ghost."""
        status, _ = _resolve_dispatch_ref("ghost-name", {"other-agent"})
        assert status == "ghost"

    def test_same_plugin_namespaced_resolves(self):
        """`<my-plugin>:agent` where my-plugin matches plugin_name resolves like a bare reference."""
        status, _ = _resolve_dispatch_ref(
            "my-plugin:my-agent",
            {"my-agent"},
            plugin_name="my-plugin",
        )
        assert status == "ok"

    def test_same_plugin_namespaced_ghost(self):
        """`<my-plugin>:agent` where my-plugin matches plugin_name but agent doesn't exist returns ghost."""
        status, _ = _resolve_dispatch_ref(
            "my-plugin:missing",
            {"only-this"},
            plugin_name="my-plugin",
        )
        assert status == "ghost"

    def test_cross_plugin_namespaced_returns_cross_plugin(self):
        """`<other-plugin>:agent` (different plugin) returns cross_plugin status — cannot statically verify."""
        status, _ = _resolve_dispatch_ref(
            "other-plugin:remote-agent",
            {"local-agent"},
            plugin_name="my-plugin",
        )
        assert status == "cross_plugin"

    def test_user_scope_agent_resolves_when_provided(self):
        """When user_scope_agents is provided, a bare reference matching a user-scope agent resolves as ok."""
        status, _ = _resolve_dispatch_ref(
            "kraken",
            set(),
            user_scope_agents={"kraken", "phoenix"},
        )
        assert status == "ok"


class TestDispatchFindingsInValidators:
    """End-to-end tests: the 3 dispatch validators emit the correct RC- codes."""

    def test_dynamic_dispatch_emits_minor_rc_002(self, tmp_path: Path):
        """A Python kwarg with an unquoted variable (dynamic) emits MINOR RC-GHOST-DISPATCH-002."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "host.md").write_text(
            "---\nname: host\n---\n# Host\n\n```python\nTask(subagent_type=picked)\n```\n"
        )

        report = CrossReferenceValidationReport()
        validate_agent_task_refs(tmp_path, report, {"host"})

        minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
        assert any(RC_GHOST_DISPATCH_DYNAMIC in m and "picked" in m for m in minor_msgs)

    def test_cross_plugin_emits_nit_rc_003(self, tmp_path: Path):
        """A `<other-plugin>:agent` reference (different plugin) emits NIT RC-GHOST-DISPATCH-003."""
        # Setup: plugin.json declares our plugin as my-plugin, agent references other-plugin:remote
        cp_dir = tmp_path / ".claude-plugin"
        cp_dir.mkdir()
        (cp_dir / "plugin.json").write_text(json.dumps({"name": "my-plugin", "version": "1.0.0"}))
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "host.md").write_text(
            '---\nname: host\n---\n# Host\n\nUse subagent_type: "other-plugin:remote-agent" for the work.\n'
        )

        report = CrossReferenceValidationReport()
        validate_agent_task_refs(tmp_path, report, {"host"})

        nit_msgs = [r.message for r in report.results if r.level == "NIT"]
        assert any(RC_GHOST_DISPATCH_CROSS_PLUGIN in m for m in nit_msgs)

    def test_builtin_general_purpose_passes(self, tmp_path: Path):
        """A reference to the `general-purpose` built-in resolves cleanly (no finding)."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "host.md").write_text(
            '---\nname: host\n---\n# Host\n\nUse subagent_type: "general-purpose" for catch-all work.\n'
        )

        report = CrossReferenceValidationReport()
        validate_agent_task_refs(tmp_path, report, {"host"})

        assert not report.has_critical
        # Should emit a PASSED finding
        passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
        assert any("general-purpose" in m for m in passed_msgs)

    def test_unresolved_emits_critical_rc_001(self, tmp_path: Path):
        """An unresolved bare reference emits CRITICAL RC-GHOST-DISPATCH-001."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "host.md").write_text(
            '---\nname: host\n---\n# Host\n\nUse subagent_type: "ghost-agent" for missing work.\n'
        )

        report = CrossReferenceValidationReport()
        validate_agent_task_refs(tmp_path, report, {"host"})

        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any(RC_GHOST_DISPATCH_UNRESOLVED in m and "ghost-agent" in m for m in critical_msgs)


class TestScopeNarrowing:
    """Tests for the scope-narrowing in validate_subagent_type_matching (per TRDD-25b9be90)."""

    def test_design_tasks_directory_not_scanned(self, tmp_path: Path):
        """Files under design/tasks/ (TRDDs) are NOT scanned by validate_subagent_type_matching."""
        design_dir = tmp_path / "design" / "tasks"
        design_dir.mkdir(parents=True)
        (design_dir / "trdd.md").write_text('# TRDD\n\nExample: subagent_type: "ghost-bot"\n')

        report = CrossReferenceValidationReport()
        validate_subagent_type_matching(tmp_path, report, set())

        # ghost-bot mention in design doc must NOT produce a finding
        assert not report.has_critical
        all_msgs = [r.message for r in report.results]
        assert not any("ghost-bot" in m for m in all_msgs)

    def test_reports_directory_not_scanned(self, tmp_path: Path):
        """Files under reports/ are NOT scanned (audit reports describing the rule itself)."""
        reports_dir = tmp_path / "reports" / "audits"
        reports_dir.mkdir(parents=True)
        (reports_dir / "audit.md").write_text('# Audit\n\nFound: subagent_type: "audit-ghost"\n')

        report = CrossReferenceValidationReport()
        validate_subagent_type_matching(tmp_path, report, set())

        assert not report.has_critical
        all_msgs = [r.message for r in report.results]
        assert not any("audit-ghost" in m for m in all_msgs)

    def test_agents_directory_is_scanned(self, tmp_path: Path):
        """Files under agents/ ARE scanned and unresolved refs emit CRITICAL."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "host.md").write_text(
            '---\nname: host\n---\n# Host\n\nsubagent_type: "missing-from-agents-dir"\n'
        )

        report = CrossReferenceValidationReport()
        validate_subagent_type_matching(tmp_path, report, {"host"})

        assert report.has_critical
        critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("missing-from-agents-dir" in m and RC_GHOST_DISPATCH_UNRESOLVED in m for m in critical_msgs)
