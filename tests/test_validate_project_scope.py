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
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cc_scope_rules import resolve_plugin_cache_dir  # noqa: E402
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
    # Neutralise the developer's GLOBAL excludes file. The gitignore-hygiene
    # checks shell out to git, which consults ~/.gitignore_global — so a dev
    # who ignores `**/.claude/settings.local.json` there sees the path report
    # as already-covered and the expected INFO never fires. Green in CI (no
    # global excludes), red only on their machine.
    _git(root, "config", "core.excludesFile", os.devnull)
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
        assert any("skipDangerousModePermissionPrompt" in m for m in _messages(report, "CRITICAL"))

    def test_skip_dangerous_nested_null_is_still_critical(self, project: Path) -> None:
        """M1 regression: the key present as explicit null must STILL be flagged.

        Claude Code keys on the KEY existing, not its value — a repo shipping
        ``{"permissions": {"skipDangerousModePermissionPrompt": null}}`` must
        not slip past the auto-bypass detector. (Before the fix the walker
        tested ``cursor is not None`` and missed the null case.)
        """
        _commit(
            project,
            ".claude/settings.json",
            '{"permissions": {"skipDangerousModePermissionPrompt": null}}\n',
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("skipDangerousModePermissionPrompt" in m for m in _messages(report, "CRITICAL"))

    def test_permissions_present_without_rejected_key_is_clean(self, project: Path) -> None:
        """M1 two-sided: a permissions block that does NOT contain the rejected
        nested key must produce NO false CRITICAL for it — the presence walker
        must only fire when the full path actually exists."""
        _commit(
            project,
            ".claude/settings.json",
            '{"permissions": {"defaultMode": "default"}}\n',
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        assert not any("skipDangerousModePermissionPrompt" in m for m in _messages(report, "CRITICAL"))


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

    def test_managed_nested_disable_auto_mode_is_major(self, project: Path) -> None:
        """permissions.disableAutoMode is a managed-only nested kill-switch — MAJOR."""
        _commit(project, ".claude/settings.json", '{"permissions": {"disableAutoMode": "disable"}}\n')
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("disableAutoMode" in m for m in _messages(report, "MAJOR"))

    def test_managed_nested_disable_auto_mode_null_is_still_major(self, project: Path) -> None:
        """M1 regression: managed-only nested key present as null is STILL flagged.

        Like the rejected-nested case, presence of the key (not its value) is
        what places it in the wrong scope, so a null value must not escape.
        """
        _commit(project, ".claude/settings.json", '{"permissions": {"disableAutoMode": null}}\n')
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("disableAutoMode" in m for m in _messages(report, "MAJOR"))


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
    """Literal credentials in env block are CRITICAL when the key name is a
    known secret, MINOR when the value merely matches a secret pattern.
    """

    def test_literal_api_key_in_env_is_critical(self, project: Path) -> None:
        """A literal credential for a known-secret env var is CRITICAL.

        v2.22.2 spec update: env-vars.md lists GITHUB_TOKEN, ANTHROPIC_API_KEY,
        CLAUDE_CODE_OAUTH_TOKEN, AWS_BEARER_TOKEN_BEDROCK and related names
        as secrets by definition. A hard-coded literal in a shared
        settings.json ``env`` block commits a credential to version control —
        CRITICAL, not MINOR. Only ``${VAR}`` expansion is acceptable.
        """
        payload = {"env": {"GITHUB_TOKEN": "ghp_" + "a" * 40}}
        _commit(project, ".claude/settings.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert report.has_critical
        assert any("GITHUB_TOKEN" in m for m in _messages(report, "CRITICAL"))

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
                        "hooks": [{"type": "command", "command": "/Users/alice/scripts/check.sh"}],
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

    def test_clean_mcp_json_passed_not_suppressed_by_settings_minor(self, project: Path) -> None:
        """m1 regression: a clean .mcp.json must emit its own PASSED line even
        when settings.json (validated earlier into the SAME report) produced a
        MINOR. Before the fix the PASSED gate tested whole-report has_minor, so
        an unrelated settings.json MINOR silently suppressed .mcp.json's PASSED.
        """
        # settings.json: a home-path statusLine → MINOR.
        _commit(
            project,
            ".claude/settings.json",
            json.dumps({"statusLine": {"command": "/Users/alice/bin/status.sh"}}) + "\n",
        )
        # .mcp.json: completely clean (valid mcpServers, no findings).
        _commit(
            project,
            ".mcp.json",
            json.dumps({"mcpServers": {"ok": {"command": "node", "args": ["s.js"]}}}) + "\n",
        )
        report = ValidationReport()
        validate_project_scope(project, report)

        # settings.json MINOR present (the suppressor condition).
        assert any("statusLine" in m for m in _messages(report, "MINOR"))
        # .mcp.json's own PASSED line must still appear.
        assert any(".mcp.json project-scope rules OK" in m for m in _messages(report, "PASSED")), (
            "clean .mcp.json PASSED line was suppressed by an unrelated settings.json MINOR"
        )

    def test_dirty_mcp_json_does_not_emit_passed(self, project: Path) -> None:
        """m1 two-sided: a .mcp.json that DOES have its own MINOR must NOT emit
        the 'rules OK' PASSED line — the slice gate must still react to this
        file's own findings."""
        _commit(
            project,
            ".mcp.json",
            json.dumps({"mcpServers": {"local": {"command": "/Users/alice/bin/mcp"}}}) + "\n",
        )
        report = ValidationReport()
        validate_project_scope(project, report)

        assert any("mcpServers.local.command" in m for m in _messages(report, "MINOR"))
        assert not any(".mcp.json project-scope rules OK" in m for m in _messages(report, "PASSED")), (
            "a .mcp.json with its own MINOR wrongly emitted the 'rules OK' PASSED line"
        )

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
        wouldn't know when it should fire. See cpv-fix-validation refs for
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
                'user: "Validate foo.md"\n'
                'assistant: "I\'ll validate foo.md now."\n'
                "</example>\n\n"
                "<example>\n"
                "Context: User asks Alice to audit a directory.\n"
                'user: "Audit the agents folder"\n'
                'assistant: "Running the audit on the agents folder."\n'
                "</example>\n"
            ),
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        blocking = [
            m for m in _messages(report, "CRITICAL") + _messages(report, "MAJOR") if ".claude/agents/alice.md" in m
        ]
        assert blocking == [], f"Fully-specified agent should have no CRITICAL/MAJOR; got: {blocking}"

    def test_agent_with_missing_name_is_minor(self, project: Path) -> None:
        """Agent missing 'name' in frontmatter → MINOR."""
        _commit(project, ".claude/agents/nameless.md", "---\ndescription: x\n---\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any(".claude/agents/nameless.md" in m and "'name'" in m for m in _messages(report, "MINOR"))

    def test_agent_with_home_path_in_body_is_minor(self, project: Path) -> None:
        """Agent body containing /Users/alice/ is MINOR."""
        _commit(
            project,
            ".claude/agents/homepath.md",
            "---\nname: hp\ndescription: x\n---\nUse /Users/alice/bin/tool\n",
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any(".claude/agents/homepath.md" in m for m in _messages(report, "MINOR"))

    def test_skill_with_no_frontmatter_is_minor(self, project: Path) -> None:
        """Skill SKILL.md without frontmatter → MINOR."""
        _commit(project, ".claude/skills/foo/SKILL.md", "# Just a body\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("SKILL.md" in m and "frontmatter" in m for m in _messages(report, "MINOR"))

    def test_rule_with_home_path_is_minor(self, project: Path) -> None:
        """Rule body with /home/bob/ is MINOR."""
        _commit(project, ".claude/rules/r1.md", "Run /home/bob/tool\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any(".claude/rules/r1.md" in m for m in _messages(report, "MINOR"))

    def test_claude_md_with_home_path_is_minor(self, project: Path) -> None:
        """CLAUDE.md body with /Users/... is MINOR."""
        _commit(project, "CLAUDE.md", "Always use /Users/alice/bin/tool\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        assert any("CLAUDE.md" in m for m in _messages(report, "MINOR"))

    def test_claude_md_with_literal_secret_is_major(self, project: Path) -> None:
        """CLAUDE.md containing a ghp_... token is MAJOR."""
        _commit(project, "CLAUDE.md", f"Token: ghp_{'a' * 40}\n")
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
        assert any("not git-tracked" in r.message for r in report.results if r.level == "INFO")

    def test_ignored_agents_folder_is_not_validated(self, project: Path) -> None:
        """A gitignored .claude/agents/ folder is skipped by the project validator."""
        _commit(project, ".gitignore", ".claude/agents/\n")
        _write_untracked(project, ".claude/agents/broken.md", "not yaml\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        # No MINOR about broken.md because the folder is local-scope
        assert not any("broken.md" in r.message for r in report.results)

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


# =============================================================================
# Gap tests G4-G7: project-scope deep-validator / settings-subtree /
# enabled-plugin enumeration / plugin-cache highest-semver coverage.
#
# These mirror the TestDeepElementValidation / TestSettingsSubtreeValidation /
# TestLocallyEnabledPluginEnumeration classes in test_validate_local_scope.py
# but assert the same guarantees for the TRACKED (git-committed) files that
# validate_project_scope walks.
# =============================================================================


class TestProjectDeepElementValidation:
    """Tracked agents/commands/skills must go through the full per-element
    deep validator (validate_agent, validate_command, validate_skill_comprehensive).
    """

    def test_tracked_agent_with_invalid_tools_is_caught(self, project: Path) -> None:
        """G4: a tracked agent declaring an unknown tool fires a deep-validator
        finding mentioning the tool. Without the deep pipeline, `validate_project_scope`
        would only run the shallow frontmatter scan and silently accept any
        tool name.
        """
        _commit(
            project,
            ".claude/agents/badtool.md",
            (
                "---\n"
                "name: badtool\n"
                "description: A demonstration agent whose tools list contains "
                "an unknown symbol to exercise the deep validator pipeline.\n"
                "tools: [NonExistentTool, Read]\n"
                "---\n"
                "Body of the agent.\n\n"
                "<example>\n"
                "Context: User asks badtool to do something.\n"
                'user: "Run the tool"\n'
                'assistant: "Running now."\n'
                "</example>\n\n"
                "<example>\n"
                "Context: User asks badtool to audit files.\n"
                'user: "Audit the folder"\n'
                'assistant: "Auditing now."\n'
                "</example>\n"
            ),
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        all_msgs = [r.message for r in report.results]
        # The deep validate_agent surfaces an "Unknown tools" finding that
        # includes the offending tool name. The shallow scan would not.
        assert any("badtool.md" in m and "NonExistentTool" in m for m in all_msgs), (
            f"Deep agent validator must flag unknown tool; got: {all_msgs}"
        )


class TestProjectSettingsSubtreeValidation:
    """hooks / mcpServers / lspServers inside a TRACKED settings.json are
    deep-validated by the same pipelines that validate plugin-shipped copies.
    """

    def test_tracked_settings_hooks_with_bad_event_is_caught(self, project: Path) -> None:
        """G5: an unknown event name inside settings.json.hooks is caught by
        the deep `validate_hook` pipeline. A shallow schema check would not
        know which event names are valid.
        """
        settings = {"hooks": {"NotARealEvent": [{"hooks": [{"type": "command", "command": "echo x"}]}]}}
        _commit(project, ".claude/settings.json", json.dumps(settings) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        all_msgs = [r.message for r in report.results]
        assert any("NotARealEvent" in m or "Unknown hook event" in m for m in all_msgs), (
            f"Hook subtree validator must catch bad event; got: {all_msgs}"
        )

    def test_tracked_settings_with_non_dict_hooks_is_major(self, project: Path) -> None:
        """G5: `hooks` set to a non-object must trigger MAJOR without crashing
        the validator. The subtree dispatcher defensively rejects scalars
        instead of passing them to `validate_hook` and blowing up on a type
        error.
        """
        _commit(project, ".claude/settings.json", '{"hooks": "not a dict"}\n')
        report = ValidationReport()
        # Must not raise.
        validate_project_scope(project, report)
        majors = _messages(report, "MAJOR")
        assert any("hooks" in m and ("object" in m or "dict" in m.lower()) for m in majors), (
            f"Non-object hooks value must be MAJOR; got MAJORs: {majors}"
        )


class TestProjectEnabledPluginEnumeration:
    """`enabledPlugins` inside a tracked settings.json triggers per-plugin
    deep validation. Missing installations fire MAJOR.
    """

    def test_enabled_but_uninstalled_plugin_is_major(self, project: Path) -> None:
        """G6: a plugin enabled in the shared settings.json but not present
        in the plugin cache fires a MAJOR — enabling a plugin the team
        doesn't actually have installed is silently a no-op and almost
        always a user mistake.
        """
        settings = {
            "enabledPlugins": {
                "nonexistent-plugin-xyz@fake-marketplace-xyz123": True,
            }
        }
        _commit(project, ".claude/settings.json", json.dumps(settings) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        all_msgs = [r.message for r in report.results]
        assert any(
            "nonexistent-plugin-xyz" in m and ("not installed" in m.lower() or "enabledPlugins" in m) for m in all_msgs
        ), f"Missing-plugin enablement must trigger MAJOR; got: {all_msgs}"


class TestProjectPluginCacheHighestSemver:
    """`resolve_plugin_cache_dir` picks the highest-semver subdirectory out
    of `~/.claude/plugins/cache/<marketplace>/<plugin>/v*/`. Tests here fake
    `Path.home()` to a tmp_path so no real user cache is touched.
    """

    def test_picks_v2_over_v1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """G7: when both `v1.0.0/` and `v2.0.0/` exist, the resolver picks
        v2.0.0. Confirms the semver tuple-compare works for canonical
        `v<MAJOR>.<MINOR>.<PATCH>` layout.
        """
        # Fake HOME so the resolver looks at tmp_path instead of the user's
        # real ~/.claude/ cache. Both `Path.home()` and `Path("~").expanduser()`
        # consult this.
        monkeypatch.setenv("HOME", str(tmp_path))
        cache_base = tmp_path / ".claude" / "plugins" / "cache" / "mkt" / "plg"
        for version in ("v1.0.0", "v2.0.0"):
            (cache_base / version).mkdir(parents=True)
            (cache_base / version / ".claude-plugin").mkdir()
            (cache_base / version / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "plg", "version": version.removeprefix("v")}),
                encoding="utf-8",
            )
        picked = resolve_plugin_cache_dir("plg", "mkt")
        assert picked is not None, "resolver must locate the cache dir"
        assert picked.name == "v2.0.0", f"Resolver must pick highest semver v2.0.0, got: {picked.name}"

    def test_vv_prefix_is_not_doubly_stripped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """G7: a directory literally named `vv1.0.0` must not be collapsed to
        `1.0.0` by over-eager prefix stripping. `str.removeprefix` strips
        exactly ONE leading "v"; `str.lstrip("v")` would strip both and
        produce a wrong sort. With the correct `removeprefix` behaviour,
        `vv1.0.0` parses as a tuple whose first element is the string
        `"v1"` (non-numeric → kept as str), which lexicographically
        outranks the pure-int tuple from `v1.0.0`. Confirms the bugfix
        described in the `_version_key` inline comment.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        cache_base = tmp_path / ".claude" / "plugins" / "cache" / "mkt" / "plg"
        for dirname in ("v1.0.0", "vv1.0.0"):
            (cache_base / dirname).mkdir(parents=True)
            (cache_base / dirname / ".claude-plugin").mkdir()
            (cache_base / dirname / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "plg", "version": "1.0.0"}),
                encoding="utf-8",
            )
        picked = resolve_plugin_cache_dir("plg", "mkt")
        assert picked is not None, "resolver must locate the cache dir"
        # Expect `vv1.0.0` to outrank `v1.0.0` given tuple ordering of
        # `(str, int, int)` vs `(int, int, int)` after a SINGLE removeprefix.
        # If this fails, the strip logic regressed (probably back to lstrip).
        assert picked.name == "vv1.0.0", (
            f"removeprefix must strip exactly one 'v'. If the resolver "
            f"picked 'v1.0.0', the lstrip bug returned; got: {picked.name}"
        )


# =============================================================================
# v2.22.0: .claude/loop.md project-scope coverage
#
# Per scheduled-tasks.md, `.claude/loop.md` replaces the built-in `/loop`
# maintenance prompt. When the file IS git-tracked, it belongs to project
# scope. The validator enforces the 25 KB truncation cap and requires
# UTF-8 decodability. Silent when the file does not exist.
# =============================================================================


class TestLoopMdProjectScope:
    """`.claude/loop.md` recognition under project scope (TRDD-479cde0c §NOW #19)."""

    def test_loop_md_tracked_validated_under_project_scope(self, project: Path) -> None:
        """A git-tracked `.claude/loop.md` produces at least one finding that
        names the file — confirms the project-scope validator is actually
        walking it (not silently skipping)."""
        _commit(project, ".claude/loop.md", "# Loop prompt\n\nRun /review-pr\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        all_msgs = [r.message for r in report.results]
        assert any("loop.md" in m for m in all_msgs), f"Expected a finding mentioning tracked loop.md; got: {all_msgs}"

    def test_loop_md_missing_is_silent(self, project: Path) -> None:
        """If `.claude/loop.md` does not exist, the validator must not emit
        ANY finding that mentions loop.md — silent absence is the correct
        behaviour. (Everything else in the report is fine; only loop.md
        must be silent.)"""
        # No loop.md in the tree — but commit something unrelated so the
        # validator has real work to do (avoids the "no config found" INFO).
        _commit(project, ".claude/settings.json", '{"model": "sonnet"}\n')
        report = ValidationReport()
        validate_project_scope(project, report)
        loop_findings = [r.message for r in report.results if "loop.md" in r.message]
        assert loop_findings == [], f"Missing loop.md must produce NO findings; got: {loop_findings}"

    def test_loop_md_tracked_size_cap_major(self, project: Path) -> None:
        """A tracked `.claude/loop.md` larger than 25,000 bytes fires MAJOR."""
        oversized = "y" * 30_000
        _commit(project, ".claude/loop.md", oversized)
        report = ValidationReport()
        validate_project_scope(project, report)
        majors = _messages(report, "MAJOR")
        assert any("loop.md" in m and ("25000" in m or "25,000" in m or "cap" in m) for m in majors), (
            f"Expected MAJOR about tracked loop.md size cap; got MAJORs: {majors}"
        )


# =============================================================================
# v2.22.0: CLAUDE.md @path import recursion validator (memory.md L95-107)
#
# `@path/to/file.md` in a tracked CLAUDE.md triggers a recursive load (max
# depth 5). Relative paths resolve from the containing file. Absolute paths
# outside the repo are a security surface — `@/etc/passwd` would pull host
# files into Claude's context. The project-scope validator must classify
# each finding category correctly.
# =============================================================================


class TestV221ClaudeMdImports:
    """``@path`` import resolution and recursion enforcement (memory.md L95-107)."""

    def test_at_path_import_into_project_file_accepted(self, project: Path) -> None:
        """A valid relative `@notes.md` import to an existing in-repo file
        does NOT trigger any CRITICAL/MAJOR finding about imports."""
        _commit(project, "notes.md", "# Notes\n\nSome content.\n")
        _commit(project, "CLAUDE.md", "# Main\n\nSee @notes.md for details.\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        import_findings = [
            r
            for r in report.results
            if r.level in ("CRITICAL", "MAJOR") and ("import" in r.message.lower() or "@notes.md" in r.message)
        ]
        assert import_findings == [], f"Valid in-repo import must not trigger findings; got: {import_findings}"

    def test_at_path_absolute_outside_repo_critical(self, project: Path) -> None:
        """`@/etc/passwd` in CLAUDE.md is a security leak → CRITICAL."""
        _commit(project, "CLAUDE.md", "# Main\n\nRead @/etc/passwd for config.\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        criticals = _messages(report, "CRITICAL")
        assert any("/etc/passwd" in m and ("import" in m.lower() or "outside" in m.lower()) for m in criticals), (
            f"Expected CRITICAL about @/etc/passwd; got CRITICALs: {criticals}"
        )

    def test_at_path_traversal_escaping_repo_major(self, project: Path) -> None:
        """`@../../outside.md` that escapes the repo root is MAJOR."""
        _commit(project, "CLAUDE.md", "# Main\n\nAlso @../../outside.md\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        majors = _messages(report, "MAJOR")
        assert any("outside.md" in m and ("escape" in m.lower() or ".." in m) for m in majors), (
            f"Expected MAJOR about .. escape; got MAJORs: {majors}"
        )

    def test_at_path_missing_file_major(self, project: Path) -> None:
        """`@does-not-exist.md` import to a missing file is MAJOR (dead import)."""
        _commit(project, "CLAUDE.md", "# Main\n\nSee @does-not-exist.md\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        majors = _messages(report, "MAJOR")
        assert any("does-not-exist.md" in m and ("not exist" in m.lower() or "dead" in m.lower()) for m in majors), (
            f"Expected MAJOR about missing imported file; got MAJORs: {majors}"
        )

    def test_at_path_recursion_depth_5_max(self, project: Path) -> None:
        """A chain A→B→C→D→E→F (depth 6) must fire a depth-exceeded MAJOR
        on the 6th link (when loading F from E)."""
        # Depth 0 = CLAUDE.md (A). Each import adds one level.
        # CLAUDE.md → b.md → c.md → d.md → e.md → f.md → g.md
        # That's 6 import links; the 6th (f.md → g.md) must trip the cap.
        _commit(project, "g.md", "# G\n\nEnd of chain.\n")
        _commit(project, "f.md", "# F\n\nNext: @g.md\n")
        _commit(project, "e.md", "# E\n\nNext: @f.md\n")
        _commit(project, "d.md", "# D\n\nNext: @e.md\n")
        _commit(project, "c.md", "# C\n\nNext: @d.md\n")
        _commit(project, "b.md", "# B\n\nNext: @c.md\n")
        _commit(project, "CLAUDE.md", "# A\n\nNext: @b.md\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        majors = _messages(report, "MAJOR")
        assert any("depth" in m.lower() and ("5" in m or "maximum" in m.lower()) for m in majors), (
            f"Expected MAJOR about depth-5 limit; got MAJORs: {majors}"
        )

    def test_at_path_circular_import_detected_major(self, project: Path) -> None:
        """A imports B imports A must fire a circular-import MAJOR."""
        _commit(project, "other.md", "# Other\n\nLoops back: @CLAUDE.md\n")
        _commit(project, "CLAUDE.md", "# Main\n\nSee @other.md\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        majors = _messages(report, "MAJOR")
        assert any("circular" in m.lower() for m in majors), (
            f"Expected MAJOR about circular import; got MAJORs: {majors}"
        )

    def test_at_path_inside_fenced_block_is_not_an_import(self, project: Path) -> None:
        """An `@path` token inside a fenced code block must NOT be treated
        as an import — no finding about the fenced token should appear."""
        body = "# Main\n\nExample usage:\n\n```markdown\nSee @/etc/passwd for example only.\n```\n\nEnd of doc.\n"
        _commit(project, "CLAUDE.md", body)
        report = ValidationReport()
        validate_project_scope(project, report)
        # No CRITICAL about /etc/passwd should appear because it's in a
        # fenced code block and thus not an import.
        assert not any("/etc/passwd" in r.message and r.level == "CRITICAL" for r in report.results), (
            f"Fenced `@/etc/passwd` must not trigger an import finding; got CRITICALs: {_messages(report, 'CRITICAL')}"
        )

    def test_email_addresses_are_not_imports(self, project: Path) -> None:
        """`email@domain.com` in prose is not an import — no finding."""
        _commit(
            project,
            "CLAUDE.md",
            "# Main\n\nContact us at support@example.com for help.\n",
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        # No import-related finding should mention example.com.
        import_findings = [
            r
            for r in report.results
            if "example.com" in r.message and ("import" in r.message.lower() or "@" in r.message)
        ]
        assert import_findings == [], f"Email address must not be treated as import; got: {import_findings}"


# =============================================================================
# TRDD-f4e2d385 §3.1: project-scope deep RULES validation.
#
# `validate_rules_folder` runs only the SHALLOW absolute-home-path scan. Per
# the TRDD §3.1 (Phase A), tracked .claude/rules/*.md files must ALSO go
# through `validate_rules_directory` (the same pipeline plugin-shipped rules
# go through). The shallow check stays as a project-scope-specific guardrail
# (no absolute home paths), and the deep check adds the rules-spec rules
# (frontmatter `paths` array shape, absolute glob rejection, `..` segment
# escape, secret/private-path scans on body, token-budget warning on the
# combined corpus, etc.).
#
# Findings from the deep walker are prefixed with `[rules]` and tracked-only
# (the deep walker recurses but the orchestrator filters out untracked
# rules so they remain validate_local_scope's concern).
# =============================================================================


class TestProjectRulesDeepValidation:
    """Tracked rule files must go through `validate_rules_directory`."""

    def test_tracked_rule_with_absolute_glob_paths_is_major(self, project: Path) -> None:
        """A tracked rule whose frontmatter `paths` contains an absolute glob
        (e.g. `/etc/passwd`) is MAJOR via the deep validator. Without the
        deep pipeline this would silently pass project-scope (only
        absolute-home-path scan runs today).
        """
        _commit(
            project,
            ".claude/rules/abs-glob.md",
            "---\npaths:\n  - /etc/passwd\n---\nRule body.\n",
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        # validate_rules emits the file path in `r.file`, not in the message.
        # The deep finding combines both — the message says `paths[0] '...'
        # is absolute` and r.file points at the rule.
        majors = [
            r
            for r in report.results
            if r.level == "MAJOR"
            and r.file is not None
            and "abs-glob.md" in r.file
            and ("absolute" in r.message.lower() or "/etc/passwd" in r.message)
        ]
        assert majors, (
            f"Deep rules validator must flag absolute glob; got results: "
            f"{[(r.level, r.file, r.message) for r in report.results]}"
        )

    def test_tracked_rule_with_invalid_paths_type_is_major(self, project: Path) -> None:
        """A tracked rule with `paths: "not-a-list"` (string instead of array)
        is MAJOR via the deep `_validate_frontmatter` path-shape check.
        Today, project-scope misses this entirely.
        """
        _commit(
            project,
            ".claude/rules/badtype.md",
            "---\npaths: just-a-string\n---\nRule body.\n",
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        majors = [
            r
            for r in report.results
            if r.level == "MAJOR"
            and r.file is not None
            and "badtype.md" in r.file
            and ("array" in r.message.lower() or "list" in r.message.lower())
        ]
        assert majors, (
            f"Deep rules validator must flag non-array paths; got results: "
            f"{[(r.level, r.file, r.message) for r in report.results]}"
        )

    def test_tracked_rule_with_unknown_frontmatter_field_is_minor(self, project: Path) -> None:
        """`path:` (typo of `paths:`) silently disables path-matching. Deep
        validator surfaces this as MINOR via the unknown-field check.
        """
        _commit(
            project,
            ".claude/rules/typo.md",
            "---\npath:\n  - 'src/**/*.py'\n---\nRule body.\n",
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        minors = [
            r
            for r in report.results
            if r.level == "MINOR" and r.file is not None and "typo.md" in r.file and "path" in r.message
        ]
        assert minors, (
            f"Deep rules validator must flag typo-field; got results: "
            f"{[(r.level, r.file, r.message) for r in report.results]}"
        )

    def test_untracked_rule_does_not_appear_in_project_findings(self, project: Path) -> None:
        """Regression guard: an UNTRACKED .claude/rules/*.md must NOT
        produce findings under project scope (mirror of G15 in the local
        scope test). Even though `validate_rules_directory` recurses, the
        project-scope deep wrapper must filter out untracked files.

        We commit a sentinel rule so the deep validator runs at all (the
        folder must classify as `project`), then drop an untracked sibling
        and assert no findings reference it.
        """
        _commit(
            project,
            ".claude/rules/tracked-sentinel.md",
            "Tracked sentinel content.\n",
        )
        _write_untracked(
            project,
            ".claude/rules/untracked-extra.md",
            "---\npaths:\n  - /not-relative-glob\n---\nUntracked content.\n",
        )
        report = ValidationReport()
        validate_project_scope(project, report)
        # No `[rules]` finding should reference the untracked file —
        # otherwise local-scope's findings duplicate into the project
        # report.
        leaks = [r.message for r in report.results if "untracked-extra.md" in r.message]
        assert leaks == [], f"Untracked rules must not leak into project-scope findings; got: {leaks}"


# =============================================================================
# TRDD-f4e2d385 §3.3: project-scope deep .mcp.json validation.
#
# `validate_mcp_json_project_scope` runs project-scope-specific rules
# (literal-secret detection in env, absolute home paths in command/args). Per
# the TRDD §3.3, tracked `.mcp.json` must ALSO go through
# `validate_mcp.validate_mcp_config` — the same deep validator local-scope
# uses for untracked `.mcp.json`. That covers transport schema (stdio
# requires `command`, http/sse require `url`), reserved server names (v2.1.128),
# unknown-field warnings, and package-executor security warnings.
# =============================================================================


class TestProjectMcpJsonDeepValidation:
    """Tracked `.mcp.json` must go through `validate_mcp_config`."""

    def test_tracked_mcp_stdio_missing_command_is_critical(self, project: Path) -> None:
        """A stdio MCP server (default transport) declared without `command`
        is CRITICAL via the deep `validate_mcp_server` schema check. The
        shallow project-scope validator would miss this.
        """
        payload = {"mcpServers": {"broken-stdio": {"args": ["--foo"]}}}
        _commit(project, ".mcp.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        criticals = _messages(report, "CRITICAL")
        assert any("broken-stdio" in m and "command" in m for m in criticals), (
            f"Deep MCP validator must flag missing command; got CRITICALs: {criticals}"
        )

    def test_tracked_mcp_http_missing_url_is_critical(self, project: Path) -> None:
        """An http-transport MCP server without `url` is CRITICAL via the
        deep validator (transport schema). Shallow validator silently
        accepts this.
        """
        payload = {"mcpServers": {"broken-http": {"type": "http"}}}
        _commit(project, ".mcp.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        criticals = _messages(report, "CRITICAL")
        assert any("broken-http" in m and "url" in m for m in criticals), (
            f"Deep MCP validator must flag missing url; got CRITICALs: {criticals}"
        )

    def test_tracked_mcp_invalid_transport_is_major(self, project: Path) -> None:
        """An invalid transport type (`type: foobar`) is MAJOR via the deep
        `VALID_TRANSPORTS` check. Shallow validator silently accepts.
        """
        payload = {"mcpServers": {"weird-transport": {"type": "foobar", "command": "x"}}}
        _commit(project, ".mcp.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        majors = _messages(report, "MAJOR")
        assert any("weird-transport" in m and ("transport" in m.lower() or "foobar" in m) for m in majors), (
            f"Deep MCP validator must flag invalid transport; got MAJORs: {majors}"
        )

    def test_tracked_mcp_unknown_field_is_warning(self, project: Path) -> None:
        """Unknown server field (e.g. `commandz` typo of `command`) is
        WARNING via the deep validator's known-field check.
        """
        payload = {
            "mcpServers": {
                "typo-field": {
                    "command": "echo",
                    "commandz": "oops-typo",
                }
            }
        }
        _commit(project, ".mcp.json", json.dumps(payload) + "\n")
        report = ValidationReport()
        validate_project_scope(project, report)
        all_msgs = [r.message for r in report.results]
        assert any("typo-field" in m and "commandz" in m for m in all_msgs), (
            f"Deep MCP validator must flag unknown field; got: {all_msgs}"
        )


class TestMergeSubreportProjectForwardsMetadata:
    """audit m9 — _merge_subreport_project must preserve category + suggestion.

    Two-sided mirror of the local-scope merge test: present metadata survives,
    absent metadata stays default.
    """

    def test_merge_preserves_category_and_suggestion(self) -> None:
        from validate_project_scope import _merge_subreport_project

        sub = ValidationReport()
        sub.add(
            "MAJOR",
            "deep finding",
            "a.json",
            9,
            phase="security",
            fixable=True,
            fix_id="FX-2",
            category="manifest",
            suggestion="add the required key",
        )
        parent = ValidationReport()
        _merge_subreport_project(sub, parent, "[agent bar]")
        merged = parent.results[-1]
        assert merged.message == "[agent bar] deep finding"
        assert merged.category == "manifest"
        assert merged.suggestion == "add the required key"
        assert (merged.phase, merged.fixable, merged.fix_id) == ("security", True, "FX-2")

    def test_merge_keeps_defaults_when_absent(self) -> None:
        from validate_project_scope import _merge_subreport_project

        sub = ValidationReport()
        sub.add("MINOR", "plain", "b.json", 1)
        parent = ValidationReport()
        _merge_subreport_project(sub, parent, "[skill baz]")
        merged = parent.results[-1]
        assert merged.category == ""
        assert merged.suggestion is None
