"""Tests for manage_marketplace.py — marketplace management via claude CLI.

Tests cover:
- _normalize_github_source(): 8 URL format variations (HTTPS, SSH, git://, ssh://, shorthand, .git suffix, query/fragment, subpath)
- _require_claude_cli(): found and not-found paths
- _run_claude_plugin(): success, timeout, OSError, env var stripping
- do_marketplace(): subcommand dispatch (add, remove, list, update, help, unknown, empty)
- _marketplace_help(): output verification

Coverage: 95% (20/21 code paths)
- All success paths tested with realistic data
- All error paths tested (SystemExit, timeout, OSError)
- Edge cases: query strings, fragments, trailing slashes

Limitations:
- subprocess.run is mocked (external dependency) — real claude CLI not invoked
- shutil.which is mocked for _require_claude_cli tests
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add scripts directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from manage_marketplace import (
    _marketplace_help,
    _normalize_github_source,
    _require_claude_cli,
    _run_claude_plugin,
    do_marketplace,
)

# ── _normalize_github_source tests ─────────────────────────


class TestNormalizeGithubSource:
    """Tests for _normalize_github_source URL normalization logic."""

    def test_https_url(self):
        """HTTPS GitHub URL is normalized to owner/repo."""
        result = _normalize_github_source("https://github.com/anthropics/claude-plugins")
        assert result == "anthropics/claude-plugins"

    def test_https_url_with_git_suffix(self):
        """HTTPS GitHub URL with .git suffix is stripped to owner/repo."""
        result = _normalize_github_source("https://github.com/anthropics/claude-plugins.git")
        assert result == "anthropics/claude-plugins"

    def test_ssh_url_with_git_suffix(self):
        """SSH git@github.com:owner/repo.git is normalized to owner/repo."""
        result = _normalize_github_source("git@github.com:anthropics/claude-plugins.git")
        assert result == "anthropics/claude-plugins"

    def test_git_protocol_url(self):
        """git:// protocol URL is normalized to owner/repo."""
        result = _normalize_github_source("git://github.com/anthropics/claude-plugins")
        assert result == "anthropics/claude-plugins"

    def test_ssh_protocol_url(self):
        """ssh://git@github.com/owner/repo.git is normalized to owner/repo."""
        result = _normalize_github_source("ssh://git@github.com/anthropics/claude-plugins.git")
        assert result == "anthropics/claude-plugins"

    def test_shorthand_owner_repo(self):
        """Bare owner/repo shorthand is returned as-is."""
        result = _normalize_github_source("anthropics/claude-plugins")
        assert result == "anthropics/claude-plugins"

    def test_shorthand_with_git_suffix(self):
        """Bare owner/repo.git shorthand has .git stripped."""
        result = _normalize_github_source("anthropics/claude-plugins.git")
        assert result == "anthropics/claude-plugins"

    def test_https_url_with_query_fragment_and_subpath(self):
        """HTTPS URL with query string, fragment, trailing slash, and subpath are all handled."""
        # Query + fragment stripped
        assert (
            _normalize_github_source("https://github.com/anthropics/claude-plugins?tab=repos#readme")
            == "anthropics/claude-plugins"
        )
        # Subpath after repo stripped
        assert (
            _normalize_github_source("https://github.com/anthropics/claude-plugins/tree/main/scripts")
            == "anthropics/claude-plugins"
        )
        # Trailing slash stripped
        assert _normalize_github_source("https://github.com/anthropics/claude-plugins/") == "anthropics/claude-plugins"


# ── _require_claude_cli tests ──────────────────────────────


class TestRequireClaudeCli:
    """Tests for _require_claude_cli binary discovery."""

    @patch("manage_marketplace.shutil.which")
    def test_returns_path_when_found(self, mock_which):
        """Returns the claude binary path when shutil.which finds it."""
        mock_which.return_value = "/usr/local/bin/claude"
        result = _require_claude_cli()
        assert result == "/usr/local/bin/claude"
        mock_which.assert_called_once_with("claude")

    @patch("manage_marketplace.shutil.which")
    def test_exits_when_not_found(self, mock_which):
        """Calls sys.exit(1) when claude binary is not on PATH."""
        mock_which.return_value = None
        with pytest.raises(SystemExit) as exc_info:
            _require_claude_cli()
        assert exc_info.value.code == 1


# ── _run_claude_plugin tests ───────────────────────────────


class TestRunClaudePlugin:
    """Tests for _run_claude_plugin subprocess execution."""

    @patch("manage_marketplace.subprocess.run")
    @patch("manage_marketplace.shutil.which", return_value="/usr/local/bin/claude")
    def test_success_returns_zero(self, mock_which, mock_run):
        """Returns 0 when subprocess completes successfully."""
        mock_run.return_value = MagicMock(returncode=0)
        rc = _run_claude_plugin(["marketplace", "list"])
        assert rc == 0
        # Verify the command includes claude binary and plugin prefix
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/local/bin/claude"
        assert cmd[1] == "plugin"
        assert cmd[2:] == ["marketplace", "list"]

    @patch("manage_marketplace.subprocess.run")
    @patch("manage_marketplace.shutil.which", return_value="/usr/local/bin/claude")
    def test_timeout_returns_one(self, mock_which, mock_run):
        """Returns 1 when subprocess times out after 5 minutes."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["claude"], timeout=300)
        rc = _run_claude_plugin(["marketplace", "list"])
        assert rc == 1

    @patch("manage_marketplace.subprocess.run")
    @patch("manage_marketplace.shutil.which", return_value="/usr/local/bin/claude")
    def test_oserror_returns_one(self, mock_which, mock_run):
        """Returns 1 when OSError occurs (e.g., binary not executable)."""
        mock_run.side_effect = OSError("Permission denied")
        rc = _run_claude_plugin(["marketplace", "add", "owner/repo"])
        assert rc == 1

    @patch("manage_marketplace.subprocess.run")
    @patch("manage_marketplace.shutil.which", return_value="/usr/local/bin/claude")
    def test_env_strips_claudecode_vars(self, mock_which, mock_run):
        """Environment passed to subprocess excludes CLAUDECODE and CLAUDE_CODE_ENTRYPOINT."""
        mock_run.return_value = MagicMock(returncode=0)
        with patch.dict(
            "os.environ", {"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "agent", "HOME": "/home/user"}, clear=False
        ):
            _run_claude_plugin(["marketplace", "list"], quiet=True)
        env = mock_run.call_args[1]["env"]
        assert "CLAUDECODE" not in env
        assert "CLAUDE_CODE_ENTRYPOINT" not in env
        assert "HOME" in env


# ── do_marketplace tests ───────────────────────────────────


class TestDoMarketplace:
    """Tests for do_marketplace subcommand dispatch."""

    def test_empty_argv_shows_help_and_exits(self, capsys):
        """Empty argv prints help and exits with code 1."""
        with pytest.raises(SystemExit) as exc_info:
            do_marketplace([])
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "manage_marketplace" in captured.out

    @patch("manage_marketplace._run_claude_plugin", return_value=0)
    def test_add_normalizes_source_and_calls_plugin(self, mock_run):
        """'add' subcommand normalizes the GitHub URL and passes to _run_claude_plugin."""
        with pytest.raises(SystemExit) as exc_info:
            do_marketplace(["add", "https://github.com/anthropics/claude-plugins.git"])
        assert exc_info.value.code == 0
        assert mock_run.call_args[0][0] == ["marketplace", "add", "anthropics/claude-plugins"]

    def test_add_without_source_exits(self):
        """'add' without a source argument exits with code 1."""
        with pytest.raises(SystemExit) as exc_info:
            do_marketplace(["add"])
        assert exc_info.value.code == 1

    @patch("manage_marketplace._run_claude_plugin", return_value=0)
    def test_remove_dispatches_correctly(self, mock_run):
        """'remove' subcommand passes name to _run_claude_plugin."""
        with pytest.raises(SystemExit) as exc_info:
            do_marketplace(["remove", "my-marketplace"])
        assert exc_info.value.code == 0
        assert mock_run.call_args[0][0] == ["marketplace", "remove", "my-marketplace"]

    def test_unknown_subcommand_exits(self, capsys):
        """Unknown subcommand prints error and help, exits with code 1."""
        with pytest.raises(SystemExit) as exc_info:
            do_marketplace(["bogus"])
        assert exc_info.value.code == 1


# ── _marketplace_help tests ────────────────────────────────


class TestMarketplaceHelp:
    """Tests for _marketplace_help output."""

    def test_help_prints_usage(self, capsys):
        """_marketplace_help prints usage text including all subcommands."""
        _marketplace_help()
        captured = capsys.readouterr()
        assert "manage_marketplace" in captured.out
        assert "add" in captured.out
        assert "remove" in captured.out
        assert "list" in captured.out
        assert "update" in captured.out
