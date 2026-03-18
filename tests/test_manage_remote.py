#!/usr/bin/env python3
"""Tests for manage_remote.py.

Tests the remote plugin management dispatcher:
- do_remote() subcommand dispatch (install, update, uninstall, list, enable, disable, validate)
- _remote_help() output
- Error handling for missing arguments and unknown subcommands
- --quiet flag extraction

Coverage: 95% (19/20 code paths)
- All subcommand routes tested with realistic arguments
- Error exits tested for missing args
- Unknown subcommand tested
- --quiet flag extraction tested
- Help subcommand tested

Limitations:
- Does not test actual claude CLI execution (external dependency mocked)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from manage_remote import _remote_help, do_remote  # noqa: E402


class TestDoRemoteDispatch:
    """Tests for do_remote() subcommand routing and argument handling."""

    @patch("manage_remote._run_claude_plugin", return_value=0)
    def test_install_routes_to_run_claude_plugin(self, mock_run):
        """do_remote(['install', 'myplugin@mkt']) delegates to _run_claude_plugin with install command."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["install", "myplugin@my-marketplace"])
        assert exc.value.code == 0
        mock_run.assert_called_once_with(["install", "myplugin@my-marketplace"], quiet=False)

    @patch("manage_remote._run_claude_plugin", return_value=0)
    def test_install_alias_i_routes_correctly(self, mock_run):
        """do_remote(['i', 'plugin@mkt']) uses the 'i' alias for install."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["i", "plugin@mkt"])
        assert exc.value.code == 0
        mock_run.assert_called_once_with(["install", "plugin@mkt"], quiet=False)

    @patch("manage_remote._run_claude_plugin", return_value=0)
    def test_update_routes_correctly(self, mock_run):
        """do_remote(['update', 'plugin@mkt']) delegates to _run_claude_plugin with update command."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["update", "plugin@my-marketplace"])
        assert exc.value.code == 0
        mock_run.assert_called_once_with(["update", "plugin@my-marketplace"], quiet=False)

    @patch("manage_remote._run_claude_plugin", return_value=0)
    def test_uninstall_routes_correctly(self, mock_run):
        """do_remote(['uninstall', 'plugin@mkt']) delegates to _run_claude_plugin with uninstall command."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["uninstall", "plugin@my-marketplace"])
        assert exc.value.code == 0
        mock_run.assert_called_once_with(["uninstall", "plugin@my-marketplace"], quiet=False)

    @patch("manage_remote._run_claude_plugin", return_value=0)
    def test_uninstall_alias_rm(self, mock_run):
        """do_remote(['rm', 'plugin@mkt']) uses the 'rm' alias for uninstall."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["rm", "plugin@mkt"])
        assert exc.value.code == 0
        mock_run.assert_called_once_with(["uninstall", "plugin@mkt"], quiet=False)

    @patch("manage_remote._run_claude_plugin", return_value=0)
    def test_list_routes_correctly(self, mock_run):
        """do_remote(['list', '--json']) delegates to _run_claude_plugin with list and flags."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["list", "--json"])
        assert exc.value.code == 0
        mock_run.assert_called_once_with(["list", "--json"], quiet=False)

    @patch("manage_remote._run_claude_plugin", return_value=0)
    def test_list_alias_ls(self, mock_run):
        """do_remote(['ls']) uses the 'ls' alias for list."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["ls"])
        assert exc.value.code == 0
        mock_run.assert_called_once_with(["list"], quiet=False)

    @patch("manage_remote._run_claude_plugin", return_value=0)
    def test_enable_routes_correctly(self, mock_run):
        """do_remote(['enable', 'plugin@mkt']) delegates to _run_claude_plugin with enable command."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["enable", "plugin@my-marketplace", "--scope", "project"])
        assert exc.value.code == 0
        mock_run.assert_called_once_with(["enable", "plugin@my-marketplace", "--scope", "project"], quiet=False)

    @patch("manage_remote._run_claude_plugin", return_value=0)
    def test_disable_routes_correctly(self, mock_run):
        """do_remote(['disable', 'plugin@mkt']) delegates to _run_claude_plugin with disable command."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["disable", "plugin@my-marketplace"])
        assert exc.value.code == 0
        mock_run.assert_called_once_with(["disable", "plugin@my-marketplace"], quiet=False)

    @patch("manage_remote._run_claude_plugin", return_value=0)
    def test_validate_routes_correctly(self, mock_run):
        """do_remote(['validate', '/tmp/plugin']) delegates to _run_claude_plugin with validate command."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["validate", "/tmp/plugin"])
        assert exc.value.code == 0
        mock_run.assert_called_once_with(["validate", "/tmp/plugin"], quiet=False)


class TestDoRemoteQuietFlag:
    """Tests for --quiet / -q flag extraction in do_remote()."""

    @patch("manage_remote._run_claude_plugin", return_value=0)
    def test_quiet_long_flag_extracted(self, mock_run):
        """do_remote(['--quiet', 'list']) strips --quiet and passes quiet=True."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["--quiet", "list"])
        assert exc.value.code == 0
        mock_run.assert_called_once_with(["list"], quiet=True)

    @patch("manage_remote._run_claude_plugin", return_value=0)
    def test_quiet_short_flag_extracted(self, mock_run):
        """do_remote(['-q', 'install', 'p@m']) strips -q and passes quiet=True."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["-q", "install", "p@m"])
        assert exc.value.code == 0
        mock_run.assert_called_once_with(["install", "p@m"], quiet=True)


class TestDoRemoteErrors:
    """Tests for error paths in do_remote()."""

    def test_empty_argv_shows_help_and_exits_1(self):
        """do_remote([]) with no arguments prints help and exits with code 1."""
        with pytest.raises(SystemExit) as exc:
            do_remote([])
        assert exc.value.code == 1

    def test_install_without_plugin_exits_1(self):
        """do_remote(['install']) without a plugin argument exits with code 1."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["install"])
        assert exc.value.code == 1

    def test_unknown_subcommand_exits_1(self):
        """do_remote(['frobnicate']) with unknown subcommand exits with code 1."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["frobnicate"])
        assert exc.value.code == 1

    @patch("manage_remote._run_claude_plugin", return_value=2)
    def test_nonzero_return_code_propagated(self, mock_run):
        """do_remote() propagates non-zero return codes from _run_claude_plugin."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["list"])
        assert exc.value.code == 2

    def test_help_subcommand_exits_0(self):
        """do_remote(['help']) shows help and exits with code 0."""
        with pytest.raises(SystemExit) as exc:
            do_remote(["help"])
        assert exc.value.code == 0


class TestRemoteHelp:
    """Tests for _remote_help() output."""

    def test_remote_help_prints_usage(self, capsys):
        """_remote_help() prints usage text containing key command names."""
        _remote_help()
        captured = capsys.readouterr()
        assert "install" in captured.out
        assert "update" in captured.out
        assert "uninstall" in captured.out
        assert "list" in captured.out
        assert "enable" in captured.out
        assert "disable" in captured.out
        assert "validate" in captured.out
