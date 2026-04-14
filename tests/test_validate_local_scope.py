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
    result = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=False
    )
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
        assert any(
            "git-tracked" in m for m in _messages(report, "MAJOR")
        )

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
        _write_untracked(
            project, ".claude/settings.local.json", '{"editorMode": "vim"}\n'
        )
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
        assert any(
            "extraKnownMarketplaces" in m for m in _messages(report, "MINOR")
        )

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
        assert any(
            "enableAllProjectMcpServers" in m for m in _messages(report, "MINOR")
        )


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
        assert any(
            "deprecated" in m for m in _messages(report, "NIT")
        )

    def test_missing_schema_is_nit(self, project: Path) -> None:
        """settings.local.json without $schema is a NIT."""
        _commit(project, ".gitignore", ".claude/settings.local.json\n")
        _write_untracked(
            project, ".claude/settings.local.json", '{"model": "x"}\n'
        )
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
        _write_untracked(
            project, ".claude/settings.local.json", json.dumps(payload) + "\n"
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        # Accept: no MINOR about the status line path
        assert not any(
            "statusLine" in m for m in _messages(report, "MINOR")
        )


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
        assert any(
            "CLAUDE.local.md" in m and "git-tracked" in m
            for m in _messages(report, "MAJOR")
        )

    def test_untracked_claude_local_md_is_ok(self, project: Path) -> None:
        """CLAUDE.local.md that is not tracked is accepted."""
        _commit(project, ".gitignore", "CLAUDE.local.md\n")
        _write_untracked(project, "CLAUDE.local.md", "Notes.\n")
        report = ValidationReport()
        validate_local_scope(project, report)
        assert not any(
            "CLAUDE.local.md" in m and "git-tracked" in m
            for m in _messages(report, "MAJOR")
        )


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
        """A gitignored .claude/agents/ folder is validated with relaxed rules."""
        _commit(project, ".gitignore", ".claude/agents/\n")
        _write_untracked(
            project,
            ".claude/agents/personal.md",
            "---\nname: personal\ndescription: x\n---\nBody.\n",
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        # Well-formed frontmatter → no MINOR about personal.md
        assert not any(
            "personal.md" in m for m in _messages(report, "MINOR")
        )

    def test_local_agent_with_no_frontmatter_is_minor(self, project: Path) -> None:
        """Untracked agent without frontmatter is flagged MINOR even at local scope."""
        _commit(project, ".gitignore", ".claude/agents/\n")
        _write_untracked(
            project, ".claude/agents/nofm.md", "Just body no frontmatter\n"
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        assert any(
            "nofm.md" in m and "frontmatter" in m
            for m in _messages(report, "MINOR")
        )

    def test_untracked_commands_folder_is_validated(self, project: Path) -> None:
        """An untracked .claude/commands/ folder is walked by local validator."""
        _write_untracked(
            project,
            ".claude/commands/quick.md",
            "no frontmatter just body\n",
        )
        report = ValidationReport()
        validate_local_scope(project, report)
        assert any(
            "quick.md" in m for m in _messages(report, "MINOR")
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
        assert not any(
            "mine/SKILL.md" in m for m in _messages(report, "MINOR")
        )


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
        assert any(
            "Not a git repository" in r.message for r in report.results if r.level == "INFO"
        )


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
