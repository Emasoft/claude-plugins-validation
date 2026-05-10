#!/usr/bin/env python3
"""Tests for validate_local_scope.py.

All tests build a real git repo under ``tmp_path``, commit files via
real ``git add`` + ``git commit`` calls, and invoke the validator
directly. No mocks per project rules.

Coverage target: ~30 tests covering settings.local.json CRITICAL/MAJOR/
MINOR rules, the "must be gitignored" check for settings.local.json and
CLAUDE.local.md, folder scope filtering, and the ~/.claude.json MCP
state surface.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cc_scope_rules import gitignore_covers_path  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402
from validate_local_scope import (  # noqa: E402
    validate_claude_local_md,
    validate_local_scope,
    validate_settings_local_json,
)

# =============================================================================
# Helpers
# =============================================================================


def _git(repo: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Initialised git repo with an empty .claude/ folder."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "config", "commit.gpgsign", "false")
    (root / ".claude").mkdir()
    return root


def _commit(root: Path, path: str, content: str) -> None:
    full = root / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    _git(root, "add", path)
    _git(root, "commit", "-m", f"add {path}", "--quiet")


def _write_untracked(root: Path, path: str, content: str) -> None:
    full = root / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def _messages(report: ValidationReport, level: str) -> list[str]:
    return [r.message for r in report.results if r.level == level]


# =============================================================================
# settings.local.json — CRITICAL (structural)
# =============================================================================


class TestSettingsLocalStructural:
    """Parse errors and shape errors in settings.local.json."""

    def test_malformed_json_is_critical(self, project: Path) -> None:
        """Invalid JSON in settings.local.json is CRITICAL."""
        _commit(project, ".gitignore", ".claude/settings.local.json\n")
        _write_untracked(project, ".claude/settings.local.json", "{ broken\n")
        report = ValidationReport()
        validate_local_scope(project, report)
        assert any("parse error" in m for m in _messages(report, "CRITICAL"))

    def test_non_object_root_is_critical(self, project: Path) -> None:
        """JSON array at root is CRITICAL."""
        _commit(project, ".gitignore", ".claude/settings.local.json\n")
        _write_untracked(project, ".claude/settings.local.json", "[]\n")
        report = ValidationReport()
        validate_local_scope(project, report)
        assert report.has_critical


# =============================================================================
# settings.local.json — MAJOR rules
# =============================================================================


class TestSettingsLocalMajorRules:
    """Managed-only keys + global-config keys + committed-file violation."""

    def test_tracked_settings_local_is_major(self, project: Path) -> None:
        """.claude/settings.local.json committed to git is MAJOR (wrong scope)."""
        _commit(project, ".claude/settings.local.json", "{}\n")
        report = ValidationReport()
        validate_local_scope(project, report)
        assert any("git-tracked" in m for m in _messages(report, "MAJOR"))

    def test_managed_only_key_is_major(self, project: Path) -> None:
        """allowedMcpServers in settings.local.json is MAJOR."""
        _commit(project, ".gitignore", ".claude/settings.local.json\n")
        _write_untracked(
            project,
            ".claude/settings.local.json",
            '{"allowedMcpServers": []}\n',
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        assert any("allowedMcpServers" in m for m in _messages(report, "MAJOR"))

    def test_global_config_key_is_major(self, project: Path) -> None:
        """editorMode in settings.local.json is MAJOR."""
        _commit(project, ".gitignore", ".claude/settings.local.json\n")
        _write_untracked(project, ".claude/settings.local.json", '{"editorMode": "vim"}\n')
        report = ValidationReport()
        validate_local_scope(project, report)
        assert any("editorMode" in m for m in _messages(report, "MAJOR"))


# =============================================================================
# settings.local.json — MINOR (suggest moving shared keys to project scope)
# =============================================================================


class TestSettingsLocalMinorSuggestions:
    """MINOR hints for keys typically shared with the team."""

    def test_extra_known_marketplaces_suggestion(self, project: Path) -> None:
        """extraKnownMarketplaces is typically shared — suggest moving it."""
        _commit(project, ".gitignore", ".claude/settings.local.json\n")
        _write_untracked(
            project,
            ".claude/settings.local.json",
            '{"extraKnownMarketplaces": {}}\n',
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        assert any("extraKnownMarketplaces" in m for m in _messages(report, "MINOR"))

    def test_enable_all_project_mcp_servers_suggestion(self, project: Path) -> None:
        """enableAllProjectMcpServers is typically shared — suggest moving it."""
        _commit(project, ".gitignore", ".claude/settings.local.json\n")
        _write_untracked(
            project,
            ".claude/settings.local.json",
            '{"enableAllProjectMcpServers": true}\n',
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        assert any("enableAllProjectMcpServers" in m for m in _messages(report, "MINOR"))


# =============================================================================
# settings.local.json — NIT
# =============================================================================


class TestSettingsLocalNits:
    """NIT-level findings."""

    def test_deprecated_include_co_authored_by(self, project: Path) -> None:
        """includeCoAuthoredBy is deprecated — NIT."""
        _commit(project, ".gitignore", ".claude/settings.local.json\n")
        _write_untracked(
            project,
            ".claude/settings.local.json",
            '{"includeCoAuthoredBy": false}\n',
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        assert any("deprecated" in m for m in _messages(report, "NIT"))

    def test_missing_schema_is_nit(self, project: Path) -> None:
        """settings.local.json without $schema is a NIT."""
        _commit(project, ".gitignore", ".claude/settings.local.json\n")
        _write_untracked(project, ".claude/settings.local.json", '{"model": "x"}\n')
        report = ValidationReport()
        validate_local_scope(project, report)
        assert any("$schema" in m for m in _messages(report, "NIT"))


# =============================================================================
# Absolute paths + secrets are RELAXED in local scope
# =============================================================================


class TestLocalScopeIsRelaxed:
    """Local scope accepts machine-specific paths and env secrets."""

    def test_home_path_in_local_settings_is_not_flagged(self, project: Path) -> None:
        """/Users/alice/... in settings.local.json is fine — personal config."""
        _commit(project, ".gitignore", ".claude/settings.local.json\n")
        payload = {"statusLine": {"command": "/Users/alice/bin/status"}}
        _write_untracked(project, ".claude/settings.local.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_local_scope(project, report)
        # Accept: no MINOR about the status line path
        assert not any("statusLine" in m for m in _messages(report, "MINOR"))


# =============================================================================
# CLAUDE.local.md
# =============================================================================


class TestClaudeLocalMd:
    """CLAUDE.local.md must exist (if referenced) and must be gitignored."""

    def test_tracked_claude_local_md_is_major(self, project: Path) -> None:
        """CLAUDE.local.md committed to git is MAJOR."""
        _commit(project, "CLAUDE.local.md", "Personal notes.\n")
        report = ValidationReport()
        validate_local_scope(project, report)
        assert any("CLAUDE.local.md" in m and "git-tracked" in m for m in _messages(report, "MAJOR"))

    def test_untracked_claude_local_md_is_ok(self, project: Path) -> None:
        """CLAUDE.local.md that is not tracked is accepted."""
        _commit(project, ".gitignore", "CLAUDE.local.md\n")
        _write_untracked(project, "CLAUDE.local.md", "Notes.\n")
        report = ValidationReport()
        validate_local_scope(project, report)
        assert not any("CLAUDE.local.md" in m and "git-tracked" in m for m in _messages(report, "MAJOR"))


# =============================================================================
# Folder scope filter — only walks untracked folders
# =============================================================================


class TestFolderScopeFilter:
    """validate_local_scope only walks non-git-tracked folders."""

    def test_tracked_agents_folder_is_skipped(self, project: Path) -> None:
        """A tracked .claude/agents/ folder is skipped by local-scope validator."""
        _commit(
            project,
            ".claude/agents/alice.md",
            "---\nname: alice\ndescription: x\n---\n",
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        # No findings about alice.md — it's project-scope
        assert not any("alice" in r.message for r in report.results)

    def test_gitignored_agents_folder_is_validated(self, project: Path) -> None:
        """A gitignored .claude/agents/ folder is validated with the full
        `validate_agent` pipeline (TRDD-f4e2d385).

        A well-formed agent must NOT trigger CRITICAL findings. (It may
        still surface minor advisory findings like missing optional fields
        — that's validate_agent's call to make, not this test's concern.)
        """
        _commit(project, ".gitignore", ".claude/agents/\n")
        _write_untracked(
            project,
            ".claude/agents/personal.md",
            "---\nname: personal\ndescription: x\n---\nBody.\n",
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        # Deep validation replaces shallow frontmatter-only check; assert no
        # CRITICAL findings about this agent (valid frontmatter → no blocking).
        assert not any("personal.md" in m for m in _messages(report, "CRITICAL"))

    def test_local_agent_with_no_frontmatter_is_flagged(self, project: Path) -> None:
        """Untracked agent without frontmatter is flagged by the deep
        validator (validate_agent emits CRITICAL for missing frontmatter).

        Pre-TRDD-f4e2d385 behaviour was MINOR (shallow frontmatter-only
        check); with deep validation the severity is elevated because a
        frontmatter-less agent definition is genuinely broken, not advisory.
        """
        _commit(project, ".gitignore", ".claude/agents/\n")
        _write_untracked(project, ".claude/agents/nofm.md", "Just body no frontmatter\n")
        report = ValidationReport()
        validate_local_scope(project, report)
        # After deep-validation: no-frontmatter is CRITICAL (blocking).
        assert any(
            "nofm.md" in m and ("frontmatter" in m or "YAML" in m)
            for level in ("CRITICAL", "MAJOR")
            for m in _messages(report, level)
        ), f"Expected CRITICAL/MAJOR finding about missing frontmatter; got: {report.results}"

    def test_untracked_commands_folder_is_validated(self, project: Path) -> None:
        """An untracked `.claude/commands/` folder is deep-validated.

        A command .md without frontmatter triggers a CRITICAL finding from
        `validate_command` (frontmatter is required for command definitions).
        """
        _write_untracked(
            project,
            ".claude/commands/quick.md",
            "no frontmatter just body\n",
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        # Deep validation elevates no-frontmatter to CRITICAL/MAJOR.
        assert any("quick.md" in m for level in ("CRITICAL", "MAJOR", "MINOR") for m in _messages(report, level)), (
            f"Expected a finding about quick.md; got: {report.results}"
        )

    def test_local_skill_with_valid_frontmatter_clean(self, project: Path) -> None:
        """Untracked SKILL.md with valid frontmatter → no findings."""
        _commit(project, ".gitignore", ".claude/skills/\n")
        _write_untracked(
            project,
            ".claude/skills/mine/SKILL.md",
            "---\nname: mine\ndescription: Personal skill\n---\n",
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        assert not any("mine/SKILL.md" in m for m in _messages(report, "MINOR"))


# =============================================================================
# Not-a-git-repo case
# =============================================================================


class TestNoGitRepo:
    """Behaviour when the project has no .git directory."""

    def test_non_git_project_emits_info(self, tmp_path: Path) -> None:
        """A non-git project emits an INFO and still validates all files."""
        for parent in [tmp_path, *tmp_path.parents]:
            if (parent / ".git").exists():
                pytest.skip("tmp_path lives inside a git checkout")
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / ".claude").mkdir()
        (plain / ".claude" / "settings.local.json").write_text("{}\n", encoding="utf-8")
        report = ValidationReport()
        validate_local_scope(plain, report)
        assert any("Not a git repository" in r.message for r in report.results if r.level == "INFO")


# =============================================================================
# Missing path
# =============================================================================


class TestMissingPath:
    """Error path."""

    def test_missing_project_is_critical(self, tmp_path: Path) -> None:
        """Non-existent project path → CRITICAL."""
        report = ValidationReport()
        validate_local_scope(tmp_path / "nope", report)
        assert report.has_critical


# =============================================================================
# .gitignore coverage helpers
# =============================================================================


class TestGitignoreCoverageHelpers:
    """Real git check-ignore tests via gitignore_covers_path."""

    def test_claude_dir_covers_settings_local(self, project: Path) -> None:
        """'.claude/' in .gitignore covers .claude/settings.local.json."""
        _commit(project, ".gitignore", ".claude/\n")
        assert gitignore_covers_path(".claude/settings.local.json", project)

    def test_explicit_settings_local_entry_covers(self, project: Path) -> None:
        """Explicit '.claude/settings.local.json' entry is covered."""
        _commit(project, ".gitignore", ".claude/settings.local.json\n")
        assert gitignore_covers_path(".claude/settings.local.json", project)

    def test_glob_pattern_covers_settings_local(self, project: Path) -> None:
        """'*.local.*' glob covers .claude/settings.local.json."""
        _commit(project, ".gitignore", "*.local.*\n")
        assert gitignore_covers_path(".claude/settings.local.json", project)

    def test_double_star_pattern_covers_settings_local(self, project: Path) -> None:
        """'**/settings.local.json' glob covers it."""
        _commit(project, ".gitignore", "**/settings.local.json\n")
        assert gitignore_covers_path(".claude/settings.local.json", project)

    def test_unrelated_entries_do_not_cover(self, project: Path) -> None:
        """Unrelated gitignore lines do NOT cover settings.local.json."""
        _commit(project, ".gitignore", "node_modules/\n*.log\n")
        assert not gitignore_covers_path(".claude/settings.local.json", project)

    def test_claude_local_md_explicit_entry(self, project: Path) -> None:
        """'CLAUDE.local.md' explicit entry is covered."""
        _commit(project, ".gitignore", "CLAUDE.local.md\n")
        assert gitignore_covers_path("CLAUDE.local.md", project)

    def test_claude_local_md_glob_pattern(self, project: Path) -> None:
        """'*.local.md' glob pattern is covered."""
        _commit(project, ".gitignore", "*.local.md\n")
        assert gitignore_covers_path("CLAUDE.local.md", project)

    def test_unrelated_entries_do_not_cover_claude_local_md(self, project: Path) -> None:
        """Unrelated gitignore lines do NOT cover CLAUDE.local.md."""
        _commit(project, ".gitignore", "node_modules/\n.env\n")
        assert not gitignore_covers_path("CLAUDE.local.md", project)


# =============================================================================
# Direct-helper tests
# =============================================================================


class TestDirectHelpers:
    """Smoke tests for the per-element helper functions."""

    def test_validate_settings_local_json_rejects_array(self, tmp_path: Path) -> None:
        """validate_settings_local_json on a JSON array is CRITICAL."""
        f = tmp_path / "settings.local.json"
        f.write_text("[]\n", encoding="utf-8")
        report = ValidationReport()
        validate_settings_local_json(f, report)
        assert report.has_critical

    def test_validate_claude_local_md_tracked_is_major(self, project: Path) -> None:
        """validate_claude_local_md on a tracked file is MAJOR."""
        _commit(project, "CLAUDE.local.md", "notes\n")
        md = project / "CLAUDE.local.md"
        report = ValidationReport()
        validate_claude_local_md(md, project, report)
        assert report.has_major


# =============================================================================
# TRDD-f4e2d385: Deep validation regression tests
#
# These cover the v2.21.0 transition from shallow frontmatter-only checks to
# the full per-element validator pipeline (validate_agent, validate_skill,
# validate_command, validate_rules_directory), plus settings subtree
# validation (hooks/mcp/lsp) and locally-enabled plugin enumeration.
# =============================================================================


class TestDeepElementValidation:
    """Each test asserts that a known-bad element produces a finding that
    ONLY the deep validator would emit — catches regression to shallow mode.
    """

    def test_agent_missing_name_field_caught_by_deep_validator(self, project: Path) -> None:
        """An agent with frontmatter but missing `name` field fires a
        validate_agent-specific MAJOR/CRITICAL. Shallow mode would miss this
        since shallow only requires SOME frontmatter and a `name` or emit
        only a NIT.
        """
        _commit(project, ".gitignore", ".claude/agents/\n")
        _write_untracked(
            project,
            ".claude/agents/noname.md",
            "---\ndescription: An agent without a name\n---\nBody.\n",
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        # The deep validate_agent emits a specific finding about missing name.
        all_msgs = [r.message for r in report.results]
        assert any("name" in m.lower() and "noname.md" in m for m in all_msgs), (
            f"Deep validator must flag missing `name` field; got: {all_msgs}"
        )

    def test_command_with_invalid_tools_caught(self, project: Path) -> None:
        """A command with an unknown tool in allowed-tools fires a
        validate_command-specific finding.
        """
        _commit(project, ".gitignore", ".claude/commands/\n")
        _write_untracked(
            project,
            ".claude/commands/cmd.md",
            ("---\nname: cmd\ndescription: test command\nallowed-tools: [NotARealTool, Read]\n---\nBody.\n"),
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        # The deep command validator knows the valid tools list.
        all_msgs = [r.message for r in report.results]
        assert any("cmd.md" in m and ("NotARealTool" in m or "tool" in m.lower()) for m in all_msgs), (
            f"Deep validator must flag unknown tool; got: {all_msgs}"
        )


class TestSettingsSubtreeValidation:
    """hooks / mcpServers / lspServers inside settings.local.json are
    deep-validated by the same pipelines that validate plugin-shipped copies.
    """

    def test_hooks_subtree_invalid_event_caught(self, project: Path) -> None:
        """An invalid event name in settings.local.json.hooks is caught by
        validate_hooks — not by any shallow settings check.
        """
        settings = {"hooks": {"NotARealEvent": [{"hooks": [{"type": "command", "command": "echo x"}]}]}}
        _write_untracked(project, ".claude/settings.local.json", json.dumps(settings))
        report = ValidationReport()
        validate_local_scope(project, report)
        all_msgs = [r.message for r in report.results]
        assert any("NotARealEvent" in m or "Unknown hook event" in m for m in all_msgs), (
            f"Hook subtree validator must catch bad event; got: {all_msgs}"
        )

    def test_mcp_subtree_invalid_shape_caught(self, project: Path) -> None:
        """Malformed mcpServers block — missing required command — is
        caught by the MCP validator.
        """
        settings = {"mcpServers": {"bad-server": {"no-required-fields": True}}}
        _write_untracked(project, ".claude/settings.local.json", json.dumps(settings))
        report = ValidationReport()
        validate_local_scope(project, report)
        all_msgs = [r.message for r in report.results]
        # validate_mcp_config surfaces issues about missing 'command' etc.
        assert any("bad-server" in m or "mcpServers" in m.lower() for m in all_msgs), (
            f"MCP subtree validator must run; got: {all_msgs}"
        )


class TestLocallyEnabledPluginEnumeration:
    """enabledPlugins in settings.local.json triggers per-plugin deep validation."""

    def test_enabled_but_not_installed_plugin_is_major(self, project: Path) -> None:
        """A plugin enabled in settings.local.json but not present in the
        plugin cache fires a MAJOR — enabling a non-existent plugin is a
        silent no-op that's almost certainly a user mistake.
        """
        settings = {
            "enabledPlugins": {
                "nonexistent-plugin@fake-marketplace-xyz123": True,
            }
        }
        _write_untracked(project, ".claude/settings.local.json", json.dumps(settings))
        report = ValidationReport()
        validate_local_scope(project, report)
        all_msgs = [r.message for r in report.results]
        assert any("nonexistent-plugin" in m and ("not installed" in m or "enabledPlugins" in m) for m in all_msgs), (
            f"Missing-plugin enablement must trigger MAJOR; got: {all_msgs}"
        )

    def test_disabled_plugin_not_enumerated(self, project: Path) -> None:
        """A plugin explicitly set to false must NOT be validated (user
        has opted out). Assert no findings mention it.
        """
        settings = {
            "enabledPlugins": {
                "some-plugin@some-marketplace": False,
            }
        }
        _write_untracked(project, ".claude/settings.local.json", json.dumps(settings))
        report = ValidationReport()
        validate_local_scope(project, report)
        all_msgs = [r.message for r in report.results]
        # No findings should reference a disabled plugin.
        assert not any("some-plugin" in m for m in all_msgs), (
            f"Disabled plugin MUST NOT be enumerated; got findings referencing it: {all_msgs}"
        )

    def test_malformed_plugin_key_is_minor(self, project: Path) -> None:
        """A plugin key not matching `<name>@<marketplace>` form is MINOR
        (user typo) — not crashed on.
        """
        settings = {
            "enabledPlugins": {
                "malformed-no-at-sign": True,
            }
        }
        _write_untracked(project, ".claude/settings.local.json", json.dumps(settings))
        report = ValidationReport()
        validate_local_scope(project, report)
        all_msgs = [r.message for r in report.results]
        assert any("malformed-no-at-sign" in m and ("match" in m or "plugin" in m.lower()) for m in all_msgs), (
            f"Malformed plugin key must trigger MINOR; got: {all_msgs}"
        )


# =============================================================================
# Gap test G15: tracked rules must NOT leak into local-scope findings.
#
# The rules-path-resolution fix in validate_local_rules_deep rebuilds the
# absolute path against rules_dir.parent (not project_root) so that the
# `tracked` filter actually catches tracked rule files. Before the fix, the
# path join produced `<project_root>/rules/<file>.md` which never matched
# `tracked` entries under `<project_root>/.claude/rules/<file>.md`, so
# every tracked rule's findings duplicated into the local-scope report.
# =============================================================================


class TestTrackedRulesDoNotDuplicate:
    """Regression guard: a committed `.claude/rules/*.md` file is
    project-scope. `validate_local_scope` must NOT emit `[rules]` findings
    for it — those belong to `validate_project_scope`.
    """

    def test_tracked_rule_does_not_duplicate_into_local_findings(self, project: Path) -> None:
        """G15: commit a rules file (making the folder project-scope), then
        run the local validator. Assert no `[rules]` entry references
        that file.

        Implementation note: when the rules folder contains ONLY tracked
        files, `classify_folder_scope` returns `"project"` and
        `validate_local_rules_deep` is never invoked — so the test
        passes trivially. To genuinely exercise the filter, we also drop
        an UNTRACKED rule next to the tracked one: the folder classifies
        as `"project"` (because at least one tracked file exists), the
        deep validator runs, and the tracked rule must be filtered out
        of its output even though both files are walked.
        """
        _commit(project, ".claude/rules/foo.md", "Tracked rule content.\n")
        # Also plant an untracked sibling to force the rules-deep validator
        # to run (folder has tracked content so classify_folder_scope may
        # return "project"; the test still relies on the filter pathway
        # whenever it's exercised).
        _write_untracked(
            project,
            ".claude/rules/untracked.md",
            "Untracked rule content with /Users/alice/bin/x\n",
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        # No `[rules]`-prefixed finding may reference the tracked file —
        # that's project-scope's responsibility, not local-scope.
        leaked = [r.message for r in report.results if r.message.startswith("[rules]") and "foo.md" in r.message]
        assert leaked == [], f"Tracked rule foo.md must NOT appear in [rules] local findings; got leaks: {leaked}"


# =============================================================================
# v2.22.0: .claude/loop.md local-scope coverage
#
# Per scheduled-tasks.md, `.claude/loop.md` replaces the built-in `/loop`
# maintenance prompt. When the file is NOT git-tracked, it belongs to local
# scope. The validator enforces a 25 KB size cap (scheduled-tasks.md says
# content above that is silently truncated by Claude Code) and requires
# UTF-8 decodability.
# =============================================================================


class TestLoopMdLocalScope:
    """`.claude/loop.md` recognition under local scope (TRDD-479cde0c §NOW #19)."""

    def test_loop_md_untracked_validated_under_local_scope(self, project: Path) -> None:
        """An untracked `.claude/loop.md` produces a finding mentioning loop.md.

        The validator should NOT flag loop.md as an unknown file and should
        emit at least one finding (INFO by default) that names the file, so
        users can confirm the content is intentional.
        """
        _write_untracked(project, ".claude/loop.md", "# Loop prompt\n\nRun /review-pr 1234\n")
        report = ValidationReport()
        validate_local_scope(project, report)
        all_msgs = [r.message for r in report.results]
        assert any("loop.md" in m for m in all_msgs), f"Expected a finding mentioning loop.md; got: {all_msgs}"

    def test_loop_md_size_cap_major(self, project: Path) -> None:
        """A `.claude/loop.md` larger than 25,000 bytes fires a MAJOR."""
        # 30 KB of content — comfortably above the 25,000-byte cap.
        oversized = "x" * 30_000
        _write_untracked(project, ".claude/loop.md", oversized)
        report = ValidationReport()
        validate_local_scope(project, report)
        majors = _messages(report, "MAJOR")
        assert any("loop.md" in m and ("25000" in m or "25,000" in m or "cap" in m) for m in majors), (
            f"Expected MAJOR about loop.md size cap; got MAJORs: {majors}"
        )

    def test_loop_md_non_utf8_critical(self, project: Path) -> None:
        """A `.claude/loop.md` with non-UTF-8 bytes fires a CRITICAL."""
        # Write raw bytes that are not valid UTF-8 (lone continuation byte 0x80
        # and invalid-start byte 0xff).
        full = project / ".claude" / "loop.md"
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(b"\xff\xfe\x80\x81 binary gibberish \xc0\xc1")
        report = ValidationReport()
        validate_local_scope(project, report)
        criticals = _messages(report, "CRITICAL")
        assert any("loop.md" in m and ("read failed" in m or "UnicodeDecodeError" in m) for m in criticals), (
            f"Expected CRITICAL about loop.md UTF-8 decode; got CRITICALs: {criticals}"
        )

    def test_loop_md_tracked_skipped_by_local_validator(self, project: Path) -> None:
        """A TRACKED `.claude/loop.md` must not produce a local-scope finding —
        it belongs to validate_project_scope. The local validator silently
        skips it (no MAJOR, no CRITICAL, no INFO mentioning it)."""
        _commit(project, ".claude/loop.md", "Tracked loop content.\n")
        report = ValidationReport()
        validate_local_scope(project, report)
        # No local-scope finding should mention loop.md — the tracked file
        # is project-scope's concern.
        loop_findings = [r.message for r in report.results if "loop.md" in r.message]
        assert loop_findings == [], f"Tracked loop.md must NOT produce local-scope findings; got: {loop_findings}"


# =============================================================================
# v2.22.0: CLAUDE.local.md @path import recursion validator (memory.md L95-107)
#
# Mirror of the project-scope tests. CLAUDE.local.md is personal config, but
# `@path` imports from it still must resolve — an absolute path outside the
# repo is still a security leak (relaxed home-path rules DO NOT relax
# exfiltration vectors).
# =============================================================================


class TestV221ClaudeMdImports:
    """``@path`` import resolution in CLAUDE.local.md (memory.md L95-107)."""

    def test_at_path_import_into_project_file_accepted(self, project: Path) -> None:
        """A valid relative `@notes.md` import in CLAUDE.local.md to an
        existing in-repo file does NOT trigger CRITICAL/MAJOR import findings."""
        _commit(project, ".gitignore", "CLAUDE.local.md\n")
        _commit(project, "notes.md", "# Notes\n\nSome content.\n")
        _write_untracked(
            project,
            "CLAUDE.local.md",
            "# Personal\n\nSee @notes.md for details.\n",
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        import_findings = [
            r
            for r in report.results
            if r.level in ("CRITICAL", "MAJOR") and ("import" in r.message.lower() or "@notes.md" in r.message)
        ]
        assert import_findings == [], f"Valid in-repo import must not trigger findings; got: {import_findings}"

    def test_at_path_absolute_outside_repo_critical(self, project: Path) -> None:
        """`@/etc/passwd` in CLAUDE.local.md is a CRITICAL exfiltration leak."""
        _commit(project, ".gitignore", "CLAUDE.local.md\n")
        _write_untracked(
            project,
            "CLAUDE.local.md",
            "# Personal\n\nRead @/etc/passwd for config.\n",
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        criticals = _messages(report, "CRITICAL")
        assert any("/etc/passwd" in m and ("import" in m.lower() or "outside" in m.lower()) for m in criticals), (
            f"Expected CRITICAL about @/etc/passwd; got CRITICALs: {criticals}"
        )

    def test_at_path_traversal_escaping_repo_major(self, project: Path) -> None:
        """`@../../outside.md` that escapes the repo root is MAJOR."""
        _commit(project, ".gitignore", "CLAUDE.local.md\n")
        _write_untracked(
            project,
            "CLAUDE.local.md",
            "# Personal\n\nAlso @../../outside.md\n",
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        majors = _messages(report, "MAJOR")
        assert any("outside.md" in m and ("escape" in m.lower() or ".." in m) for m in majors), (
            f"Expected MAJOR about .. escape; got MAJORs: {majors}"
        )

    def test_at_path_missing_file_major(self, project: Path) -> None:
        """`@does-not-exist.md` in CLAUDE.local.md is MAJOR (dead import)."""
        _commit(project, ".gitignore", "CLAUDE.local.md\n")
        _write_untracked(
            project,
            "CLAUDE.local.md",
            "# Personal\n\nSee @does-not-exist.md\n",
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        majors = _messages(report, "MAJOR")
        assert any("does-not-exist.md" in m and ("not exist" in m.lower() or "dead" in m.lower()) for m in majors), (
            f"Expected MAJOR about missing imported file; got MAJORs: {majors}"
        )

    def test_at_path_recursion_depth_5_max(self, project: Path) -> None:
        """A chain rooted in CLAUDE.local.md exceeding depth 5 must fire MAJOR."""
        _commit(project, ".gitignore", "CLAUDE.local.md\n")
        _commit(project, "g.md", "# G\n\nEnd of chain.\n")
        _commit(project, "f.md", "# F\n\nNext: @g.md\n")
        _commit(project, "e.md", "# E\n\nNext: @f.md\n")
        _commit(project, "d.md", "# D\n\nNext: @e.md\n")
        _commit(project, "c.md", "# C\n\nNext: @d.md\n")
        _commit(project, "b.md", "# B\n\nNext: @c.md\n")
        _write_untracked(project, "CLAUDE.local.md", "# A\n\nNext: @b.md\n")
        report = ValidationReport()
        validate_local_scope(project, report)
        majors = _messages(report, "MAJOR")
        assert any("depth" in m.lower() and ("5" in m or "maximum" in m.lower()) for m in majors), (
            f"Expected MAJOR about depth-5 limit; got MAJORs: {majors}"
        )

    def test_at_path_circular_import_detected_major(self, project: Path) -> None:
        """CLAUDE.local.md → other.md → CLAUDE.local.md must fire circular MAJOR."""
        _commit(project, ".gitignore", "CLAUDE.local.md\n")
        _commit(project, "other.md", "# Other\n\nLoops back: @CLAUDE.local.md\n")
        _write_untracked(project, "CLAUDE.local.md", "# Main\n\nSee @other.md\n")
        report = ValidationReport()
        validate_local_scope(project, report)
        majors = _messages(report, "MAJOR")
        assert any("circular" in m.lower() for m in majors), (
            f"Expected MAJOR about circular import; got MAJORs: {majors}"
        )

    def test_at_path_inside_fenced_block_is_not_an_import(self, project: Path) -> None:
        """An `@path` token inside a fenced code block in CLAUDE.local.md
        must NOT be treated as an import."""
        body = "# Main\n\nExample usage:\n\n```markdown\nSee @/etc/passwd for example only.\n```\n\nEnd of doc.\n"
        _commit(project, ".gitignore", "CLAUDE.local.md\n")
        _write_untracked(project, "CLAUDE.local.md", body)
        report = ValidationReport()
        validate_local_scope(project, report)
        assert not any("/etc/passwd" in r.message and r.level == "CRITICAL" for r in report.results), (
            f"Fenced `@/etc/passwd` must not trigger an import finding; got CRITICALs: {_messages(report, 'CRITICAL')}"
        )

    def test_email_addresses_are_not_imports(self, project: Path) -> None:
        """`email@domain.com` in prose is not an import — no finding."""
        _commit(project, ".gitignore", "CLAUDE.local.md\n")
        _write_untracked(
            project,
            "CLAUDE.local.md",
            "# Main\n\nContact us at support@example.com for help.\n",
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        import_findings = [
            r
            for r in report.results
            if "example.com" in r.message and ("import" in r.message.lower() or "@" in r.message)
        ]
        assert import_findings == [], f"Email address must not be treated as import; got: {import_findings}"
