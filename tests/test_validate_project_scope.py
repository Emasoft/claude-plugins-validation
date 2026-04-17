#!/usr/bin/env python3
"""Tests for validate_project_scope.py.

All tests build a real git repo under ``tmp_path``, commit files with
real ``git add`` + ``git commit`` calls, and invoke the orchestrator
function directly on the resulting repo. No mocks per the project rule.

Coverage target: ~30 tests covering every CRITICAL, MAJOR, MINOR, NIT,
WARNING, and INFO finding the validator can emit.
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

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_project_scope import (  # noqa: E402
    validate_claude_md_file,
    validate_mcp_json_project_scope,
    validate_project_scope,
    validate_settings_json_project_scope,
)

# =============================================================================
# Test helpers
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
    """Write content to ``path`` relative to ``root``, git add + commit."""
    full = root / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    _git(root, "add", path)
    _git(root, "commit", "-m", f"add {path}", "--quiet")


def _write_untracked(root: Path, path: str, content: str) -> None:
    """Write content to ``path`` but do NOT add to git."""
    full = root / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def _messages(report: ValidationReport, level: str) -> list[str]:
    return [r.message for r in report.results if r.level == level]


# =============================================================================
# settings.json CRITICAL rules — project-rejected keys
# =============================================================================


class TestSettingsRejectedKeys:
    """Keys that Claude Code silently drops from project settings."""

    def test_auto_memory_directory_is_critical(self, project: Path) -> None:
        """autoMemoryDirectory in project settings is CRITICAL."""
        _commit(project, ".claude/settings.json", '{"autoMemoryDirectory": "/tmp/mem"}\n')
        report = ValidationReport()
        validate_project_scope(project, report)
        assert report.has_critical
        assert any("autoMemoryDirectory" in m for m in _messages(report, "CRITICAL"))

    def test_auto_mode_block_is_critical(self, project: Path) -> None:
        """autoMode block is rejected from project settings."""
        _commit(project, ".claude/settings.json", '{"autoMode": {"classifier": "fast"}}\n')
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("autoMode" in m for m in _messages(report, "CRITICAL"))

    def test_use_auto_mode_during_plan_is_critical(self, project: Path) -> None:
        """useAutoModeDuringPlan is rejected from project settings."""
        _commit(project, ".claude/settings.json", '{"useAutoModeDuringPlan": true}\n')
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("useAutoModeDuringPlan" in m for m in _messages(report, "CRITICAL"))

    def test_skip_dangerous_nested_is_critical(self, project: Path) -> None:
        """permissions.skipDangerousModePermissionPrompt is rejected."""
        _commit(
            project,
            ".claude/settings.json",
            '{"permissions": {"skipDangerousModePermissionPrompt": true}}\n',
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any(
            "skipDangerousModePermissionPrompt" in m for m in _messages(report, "CRITICAL")
        )


# =============================================================================
# settings.json MAJOR rules — managed-only + global-config keys
# =============================================================================


class TestSettingsManagedOnlyKeys:
    """Keys that only work in managed settings — MAJOR in project scope."""

    def test_allowed_mcp_servers_is_major(self, project: Path) -> None:
        """allowedMcpServers is a managed-only key."""
        _commit(project, ".claude/settings.json", '{"allowedMcpServers": []}\n')
        report = ValidationReport()
        validate_project_scope(project, report)
        assert report.has_major
        assert any("allowedMcpServers" in m for m in _messages(report, "MAJOR"))

    def test_denied_mcp_servers_is_major(self, project: Path) -> None:
        """deniedMcpServers is a managed-only key."""
        _commit(project, ".claude/settings.json", '{"deniedMcpServers": []}\n')
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("deniedMcpServers" in m for m in _messages(report, "MAJOR"))

    def test_allow_managed_hooks_only_is_major(self, project: Path) -> None:
        """allowManagedHooksOnly is a managed-only key."""
        _commit(project, ".claude/settings.json", '{"allowManagedHooksOnly": true}\n')
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("allowManagedHooksOnly" in m for m in _messages(report, "MAJOR"))


class TestSettingsGlobalConfigKeys:
    """Keys that belong in ~/.claude.json — MAJOR in settings.json."""

    def test_editor_mode_is_major(self, project: Path) -> None:
        """editorMode belongs in ~/.claude.json only."""
        _commit(project, ".claude/settings.json", '{"editorMode": "emacs"}\n')
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("editorMode" in m for m in _messages(report, "MAJOR"))

    def test_teammate_mode_is_major(self, project: Path) -> None:
        """teammateMode belongs in ~/.claude.json only."""
        _commit(project, ".claude/settings.json", '{"teammateMode": "solo"}\n')
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("teammateMode" in m for m in _messages(report, "MAJOR"))


# =============================================================================
# settings.json MINOR rules — secrets + absolute paths
# =============================================================================


class TestSettingsSecretsInEnv:
    """Literal credentials in env block are MINOR."""

    def test_literal_api_key_in_env_is_minor(self, project: Path) -> None:
        """A literal ghp_... token in env is flagged as MINOR."""
        payload = {"env": {"GITHUB_TOKEN": "ghp_" + "a" * 40}}
        _commit(project, ".claude/settings.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert report.has_minor
        assert any("GITHUB_TOKEN" in m for m in _messages(report, "MINOR"))

    def test_env_var_expansion_in_env_is_not_flagged(self, project: Path) -> None:
        """${VAR} expansion in env is the portable pattern and is NOT flagged."""
        payload = {"env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}}
        _commit(project, ".claude/settings.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        # No MINOR about secrets in env
        assert not any("GITHUB_TOKEN" in m for m in _messages(report, "MINOR"))


class TestSettingsAbsolutePaths:
    """Absolute user paths in command fields are MINOR."""

    def test_status_line_with_home_path_is_minor(self, project: Path) -> None:
        """statusLine.command with /Users/alice/... is flagged."""
        payload = {"statusLine": {"command": "/Users/alice/bin/status.sh"}}
        _commit(project, ".claude/settings.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("statusLine.command" in m for m in _messages(report, "MINOR"))

    def test_status_line_with_claude_project_dir_is_clean(self, project: Path) -> None:
        """statusLine.command using $CLAUDE_PROJECT_DIR is NOT flagged."""
        payload = {"statusLine": {"command": '"$CLAUDE_PROJECT_DIR"/bin/status.sh'}}
        _commit(project, ".claude/settings.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert not any("statusLine" in m for m in _messages(report, "MINOR"))

    def test_api_key_helper_with_home_path_is_minor(self, project: Path) -> None:
        """apiKeyHelper pointing at /home/bob/... is flagged."""
        payload = {"apiKeyHelper": "/home/bob/.claude/helper.sh"}
        _commit(project, ".claude/settings.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("apiKeyHelper" in m for m in _messages(report, "MINOR"))

    def test_hook_command_with_home_path_is_minor(self, project: Path) -> None:
        """A hook command with an absolute home path is flagged."""
        payload = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Write",
                        "hooks": [
                            {"type": "command", "command": "/Users/alice/scripts/check.sh"}
                        ],
                    }
                ]
            }
        }
        _commit(project, ".claude/settings.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("hooks" in m and "command" in m for m in _messages(report, "MINOR"))

    def test_additional_directories_home_path_is_minor(self, project: Path) -> None:
        """permissions.additionalDirectories with /Users/... is flagged."""
        payload = {"permissions": {"additionalDirectories": ["/Users/alice/other-project/"]}}
        _commit(project, ".claude/settings.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("additionalDirectories" in m for m in _messages(report, "MINOR"))


# =============================================================================
# settings.json NIT — missing $schema
# =============================================================================


class TestSettingsNits:
    """NIT-level findings."""

    def test_missing_schema_is_nit(self, project: Path) -> None:
        """settings.json without $schema emits a NIT."""
        _commit(project, ".claude/settings.json", '{"model": "claude-sonnet-4-6"}\n')
        report = ValidationReport()
        validate_project_scope(project, report)
        assert report.has_nit
        assert any("$schema" in m for m in _messages(report, "NIT"))

    def test_schema_present_no_nit(self, project: Path) -> None:
        """settings.json with $schema does not emit the schema NIT."""
        payload = {
            "$schema": "https://json.schemastore.org/claude-code-settings.json",
            "model": "claude-sonnet-4-6",
        }
        _commit(project, ".claude/settings.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert not any("$schema" in m for m in _messages(report, "NIT"))


# =============================================================================
# settings.json structural errors
# =============================================================================


class TestSettingsStructural:
    """Parse errors and shape errors."""

    def test_malformed_json_is_critical(self, project: Path) -> None:
        """Invalid JSON in settings.json is CRITICAL."""
        _commit(project, ".claude/settings.json", "{ not valid json\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("parse error" in m for m in _messages(report, "CRITICAL"))

    def test_non_object_root_is_critical(self, project: Path) -> None:
        """A JSON array at root is CRITICAL."""
        _commit(project, ".claude/settings.json", "[]\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("object" in m for m in _messages(report, "CRITICAL"))


# =============================================================================
# .mcp.json — project scope rules
# =============================================================================


class TestMcpJson:
    """Project-scope rules for .mcp.json."""

    def test_secret_in_mcp_env_is_minor(self, project: Path) -> None:
        """A literal API key in mcpServers.*.env is flagged MINOR."""
        payload = {
            "mcpServers": {
                "stripe": {
                    "type": "http",
                    "url": "https://mcp.stripe.com",
                    "env": {"API_KEY": "sk-ant-" + "a" * 40},
                }
            }
        }
        _commit(project, ".mcp.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("API_KEY" in m for m in _messages(report, "MINOR"))

    def test_absolute_home_path_in_mcp_command_is_minor(self, project: Path) -> None:
        """.mcp.json mcpServers.*.command with /Users/... is MINOR."""
        payload = {"mcpServers": {"local": {"command": "/Users/alice/bin/mcp"}}}
        _commit(project, ".mcp.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("mcpServers.local.command" in m for m in _messages(report, "MINOR"))

    def test_malformed_mcp_json_is_critical(self, project: Path) -> None:
        """Invalid JSON in .mcp.json is CRITICAL."""
        _commit(project, ".mcp.json", "{ broken\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("parse error" in m for m in _messages(report, "CRITICAL"))

    def test_mcp_missing_mcp_servers_key_is_major(self, project: Path) -> None:
        """.mcp.json without an mcpServers object is MAJOR."""
        _commit(project, ".mcp.json", "{}\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("mcpServers" in m for m in _messages(report, "MAJOR"))

    def test_untracked_mcp_json_emits_warning(self, project: Path) -> None:
        """.mcp.json that exists but is not committed is a WARNING per docs."""
        _write_untracked(project, ".mcp.json", '{"mcpServers": {}}\n')
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any(".mcp.json" in r.message for r in report.results if r.level == "WARNING")


# =============================================================================
# Markdown elements — agents / skills / commands / rules / CLAUDE.md
# =============================================================================


class TestMarkdownElements:
    """Frontmatter + home-path scans on .claude/<element>/*.md files."""

    def test_agent_with_valid_frontmatter_is_clean(self, project: Path) -> None:
        """A fully-specified tracked agent (frontmatter + ≥2 <example> blocks
        in body per Claude Code agent spec) emits no CRITICAL/MAJOR findings.

        TRDD-f4e2d385: deep validation via `validate_agent` enforces the
        full agent spec (including 2+ example blocks for good triggering).
        An agent without examples would — correctly — fail validation at
        project scope because teammates who receive the shared agent
        wouldn't know when it should fire. See fix-validation refs for
        remediation details.
        """
        _commit(
            project,
            ".claude/agents/alice.md",
            (
                "---\n"
                "name: alice\n"
                "description: A demonstration agent that performs example-driven "
                "validation of TRDD-f4e2d385 deep-validator plumbing.\n"
                "---\n"
                "Body of the agent.\n\n"
                "<example>\n"
                "Context: User asks Alice to validate a file.\n"
                "user: \"Validate foo.md\"\n"
                "assistant: \"I'll validate foo.md now.\"\n"
                "</example>\n\n"
                "<example>\n"
                "Context: User asks Alice to audit a directory.\n"
                "user: \"Audit the agents folder\"\n"
                "assistant: \"Running the audit on the agents folder.\"\n"
                "</example>\n"
            ),
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        blocking = [
            m for m in _messages(report, "CRITICAL") + _messages(report, "MAJOR")
            if ".claude/agents/alice.md" in m
        ]
        assert blocking == [], f"Fully-specified agent should have no CRITICAL/MAJOR; got: {blocking}"

    def test_agent_with_missing_name_is_minor(self, project: Path) -> None:
        """Agent missing 'name' in frontmatter → MINOR."""
        _commit(project, ".claude/agents/nameless.md", "---\ndescription: x\n---\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any(
            ".claude/agents/nameless.md" in m and "'name'" in m
            for m in _messages(report, "MINOR")
        )

    def test_agent_with_home_path_in_body_is_minor(self, project: Path) -> None:
        """Agent body containing /Users/alice/ is MINOR."""
        _commit(
            project,
            ".claude/agents/homepath.md",
            "---\nname: hp\ndescription: x\n---\nUse /Users/alice/bin/tool\n",
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any(
            ".claude/agents/homepath.md" in m for m in _messages(report, "MINOR")
        )

    def test_skill_with_no_frontmatter_is_minor(self, project: Path) -> None:
        """Skill SKILL.md without frontmatter → MINOR."""
        _commit(project, ".claude/skills/foo/SKILL.md", "# Just a body\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any(
            "SKILL.md" in m and "frontmatter" in m
            for m in _messages(report, "MINOR")
        )

    def test_rule_with_home_path_is_minor(self, project: Path) -> None:
        """Rule body with /home/bob/ is MINOR."""
        _commit(project, ".claude/rules/r1.md", "Run /home/bob/tool\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any(
            ".claude/rules/r1.md" in m for m in _messages(report, "MINOR")
        )

    def test_claude_md_with_home_path_is_minor(self, project: Path) -> None:
        """CLAUDE.md body with /Users/... is MINOR."""
        _commit(project, "CLAUDE.md", "Always use /Users/alice/bin/tool\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("CLAUDE.md" in m for m in _messages(report, "MINOR"))

    def test_claude_md_with_literal_secret_is_major(self, project: Path) -> None:
        """CLAUDE.md containing a ghp_... token is MAJOR."""
        _commit(
            project, "CLAUDE.md", f"Token: ghp_{'a' * 40}\n"
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("CLAUDE.md" in m for m in _messages(report, "MAJOR"))


# =============================================================================
# git-tracking filter — untracked files are skipped
# =============================================================================


class TestGitTrackingFilter:
    """validate_project_scope only validates tracked files."""

    def test_untracked_settings_is_not_validated(self, project: Path) -> None:
        """An untracked .claude/settings.json is skipped (emits INFO)."""
        _write_untracked(project, ".claude/settings.json", '{"autoMemoryDirectory": "/x"}\n')
        report = ValidationReport()
        validate_project_scope(project, report)
        # No CRITICAL because the file is not tracked
        assert not report.has_critical
        # INFO should note that it's skipped
        assert any(
            "not git-tracked" in r.message for r in report.results if r.level == "INFO"
        )

    def test_ignored_agents_folder_is_not_validated(self, project: Path) -> None:
        """A gitignored .claude/agents/ folder is skipped by the project validator."""
        _commit(project, ".gitignore", ".claude/agents/\n")
        _write_untracked(project, ".claude/agents/broken.md", "not yaml\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        # No MINOR about broken.md because the folder is local-scope
        assert not any(
            "broken.md" in r.message for r in report.results
        )

    def test_non_git_repo_emits_warning(self, tmp_path: Path) -> None:
        """A directory with no .git anywhere up the tree → WARNING + skip."""
        # Guard: skip if tmp_path itself lives inside a git repo
        for parent in [tmp_path, *tmp_path.parents]:
            if (parent / ".git").exists():
                pytest.skip("tmp_path lives inside a git checkout")
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / ".claude").mkdir()
        report = ValidationReport()
        validate_project_scope(plain, report)
        assert report.has_warning

    def test_missing_project_path_is_critical(self, tmp_path: Path) -> None:
        """A non-existent project path is CRITICAL."""
        report = ValidationReport()
        validate_project_scope(tmp_path / "nope", report)
        assert report.has_critical

    def test_empty_project_with_git_emits_info(self, project: Path) -> None:
        """An empty .claude/ in a real repo produces INFO + gitignore hints."""
        report = ValidationReport()
        validate_project_scope(project, report)
        assert not report.has_critical
        assert not report.has_major
        assert not report.has_minor


# =============================================================================
# .gitignore hygiene
# =============================================================================


class TestGitignoreHygiene:
    """INFO-level .gitignore recommendations."""

    def test_missing_settings_local_entry_is_info(self, project: Path) -> None:
        """.gitignore without settings.local.json entry → INFO."""
        _commit(project, ".gitignore", "node_modules/\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("settings.local.json" in m for m in _messages(report, "INFO"))

    def test_missing_claude_local_md_entry_is_info(self, project: Path) -> None:
        """.gitignore without CLAUDE.local.md entry → INFO."""
        _commit(project, ".gitignore", "node_modules/\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("CLAUDE.local.md" in m for m in _messages(report, "INFO"))

    def test_both_entries_present_no_info(self, project: Path) -> None:
        """.gitignore with both entries → no INFO findings."""
        _commit(
            project,
            ".gitignore",
            "node_modules/\n.claude/settings.local.json\nCLAUDE.local.md\n",
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        infos = _messages(report, "INFO")
        assert not any("settings.local.json" in m for m in infos)
        assert not any("CLAUDE.local.md" in m for m in infos)


# =============================================================================
# Direct-helper tests (called without the orchestrator)
# =============================================================================


class TestDirectHelpers:
    """Smoke tests for the per-element helper functions."""

    def test_validate_settings_json_rejects_array(self, tmp_path: Path) -> None:
        """validate_settings_json_project_scope on a JSON array is CRITICAL."""
        f = tmp_path / "settings.json"
        f.write_text("[]\n", encoding="utf-8")
        report = ValidationReport()
        validate_settings_json_project_scope(f, report)
        assert report.has_critical

    def test_validate_mcp_json_missing_file(self, tmp_path: Path) -> None:
        """validate_mcp_json_project_scope on an unreadable path is CRITICAL."""
        f = tmp_path / "missing.json"
        report = ValidationReport()
        validate_mcp_json_project_scope(f, report)
        assert report.has_critical

    def test_validate_claude_md_with_secret(self, tmp_path: Path) -> None:
        """validate_claude_md_file catches a ghp_ token on a line."""
        # Create a real git repo for relative_to to work
        if shutil.which("git") is None:
            pytest.skip("git not available")
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "--quiet")
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "T")
        md = repo / "CLAUDE.md"
        md.write_text(f"Token: ghp_{'a' * 40}\n", encoding="utf-8")
        report = ValidationReport()
        validate_claude_md_file(md, repo, report)
        assert report.has_major
