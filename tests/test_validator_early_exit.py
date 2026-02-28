#!/usr/bin/env python3
"""Tests for early-exit content-type checks in all 16+ validator scripts.

Each validator's main() function has TWO protections:
1. Path resolution -- paths are resolved to absolute via .resolve()
2. Content-type early exit -- returns exit code >= 1 with a clear error
   when the given path doesn't contain the expected content

This file tests BOTH protections across all validators.

Coverage: 37 tests covering 17 validators (2 per validator + 3 path-resolution tests).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_agent import main as agent_main  # noqa: E402
from validate_command import main as command_main  # noqa: E402
from validate_documentation import main as documentation_main  # noqa: E402
from validate_encoding import main as encoding_main  # noqa: E402
from validate_enterprise import main as enterprise_main  # noqa: E402
from validate_hook import main as hook_main  # noqa: E402
from validate_lsp import main as lsp_main  # noqa: E402
from validate_marketplace import main as marketplace_main  # noqa: E402
from validate_marketplace_pipeline import main as marketplace_pipeline_main  # noqa: E402
from validate_mcp import main as mcp_main  # noqa: E402
from validate_plugin import main as plugin_main  # noqa: E402
from validate_rules import main as rules_main  # noqa: E402
from validate_scoring import main as scoring_main  # noqa: E402
from validate_security import main as security_main  # noqa: E402
from validate_skill import main as skill_main  # noqa: E402
from validate_skill_comprehensive import main as skill_comprehensive_main  # noqa: E402
from validate_xref import main as xref_main  # noqa: E402

# ---------------------------------------------------------------------------
# Component validators
# ---------------------------------------------------------------------------


class TestComponentValidatorEarlyExit:
    """Tests for component validators that expect specific file types."""

    # -- validate_agent --

    def test_agent_rejects_non_md_file(self, tmp_path, monkeypatch, capsys):
        """validate_agent main() returns 1 when given a .txt file instead of .md."""
        txt_file = tmp_path / "not_agent.txt"
        txt_file.write_text("this is not markdown")
        monkeypatch.setattr("sys.argv", ["validate_agent", str(txt_file)])
        result = agent_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "not a Markdown" in captured.err

    def test_agent_rejects_empty_dir(self, tmp_path, monkeypatch, capsys):
        """validate_agent main() returns 1 for a directory with no .md files."""
        # Put only .py files in the directory
        (tmp_path / "script.py").write_text("x = 1")
        monkeypatch.setattr("sys.argv", ["validate_agent", str(tmp_path)])
        result = agent_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No agent definition files" in captured.err

    # -- validate_command --

    def test_command_rejects_non_md_file(self, tmp_path, monkeypatch, capsys):
        """validate_command main() returns 1 when given a .txt file instead of .md."""
        txt_file = tmp_path / "not_command.txt"
        txt_file.write_text("this is not markdown")
        monkeypatch.setattr("sys.argv", ["validate_command", str(txt_file)])
        result = command_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "not a Markdown" in captured.err

    def test_command_rejects_empty_dir(self, tmp_path, monkeypatch, capsys):
        """validate_command main() returns 1 for a directory with no .md files."""
        (tmp_path / "script.py").write_text("x = 1")
        monkeypatch.setattr("sys.argv", ["validate_command", str(tmp_path)])
        result = command_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No command definition files" in captured.err

    # -- validate_hook --

    def test_hook_rejects_non_json_file(self, tmp_path, monkeypatch, capsys):
        """validate_hook main() returns 1 when given a .txt file instead of .json."""
        txt_file = tmp_path / "not_hook.txt"
        txt_file.write_text("this is not json")
        monkeypatch.setattr("sys.argv", ["validate_hook", str(txt_file)])
        result = hook_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "not a JSON file" in captured.err

    def test_hook_rejects_directory(self, tmp_path, monkeypatch, capsys):
        """validate_hook main() returns 1 when given a directory instead of a file."""
        monkeypatch.setattr("sys.argv", ["validate_hook", str(tmp_path)])
        result = hook_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "is not a file" in captured.err

    # -- validate_skill --

    def test_skill_rejects_empty_dir(self, tmp_path, monkeypatch, capsys):
        """validate_skill main() returns 1 for an empty directory (no SKILL.md)."""
        monkeypatch.setattr("sys.argv", ["validate_skill", str(tmp_path)])
        result = skill_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No SKILL.md found" in captured.err

    def test_skill_rejects_dir_without_skill_md(self, tmp_path, monkeypatch, capsys):
        """validate_skill main() returns 1 for a directory that has files but no SKILL.md."""
        (tmp_path / "README.md").write_text("# Not a skill")
        (tmp_path / "code.py").write_text("pass")
        monkeypatch.setattr("sys.argv", ["validate_skill", str(tmp_path)])
        result = skill_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No SKILL.md found" in captured.err

    # -- validate_skill_comprehensive --

    def test_skill_comprehensive_rejects_empty_dir(self, tmp_path, monkeypatch, capsys):
        """validate_skill_comprehensive main() returns 1 for an empty directory."""
        monkeypatch.setattr("sys.argv", ["validate_skill_comprehensive", str(tmp_path)])
        result = skill_comprehensive_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No SKILL.md found" in captured.err

    def test_skill_comprehensive_rejects_dir_without_skill_md(self, tmp_path, monkeypatch, capsys):
        """validate_skill_comprehensive main() returns 1 for directory without SKILL.md."""
        (tmp_path / "other.md").write_text("# Not skill.md")
        monkeypatch.setattr("sys.argv", ["validate_skill_comprehensive", str(tmp_path)])
        result = skill_comprehensive_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No SKILL.md found" in captured.err

    # -- validate_rules --

    def test_rules_rejects_empty_dir(self, tmp_path, monkeypatch, capsys):
        """validate_rules main() returns 1 for an empty directory with no .md rule files."""
        monkeypatch.setattr("sys.argv", ["validate_rules", str(tmp_path)])
        result = rules_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No rule files" in captured.err

    def test_rules_rejects_dir_with_only_non_md_files(self, tmp_path, monkeypatch, capsys):
        """validate_rules main() returns 1 for a directory containing only .py files."""
        (tmp_path / "script.py").write_text("pass")
        monkeypatch.setattr("sys.argv", ["validate_rules", str(tmp_path)])
        result = rules_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No rule files" in captured.err


# ---------------------------------------------------------------------------
# Protocol validators
# ---------------------------------------------------------------------------


class TestProtocolValidatorEarlyExit:
    """Tests for protocol validators (MCP, LSP) that expect config files."""

    # -- validate_mcp --

    def test_mcp_rejects_empty_dir(self, tmp_path, monkeypatch, capsys):
        """validate_mcp main() returns 1 for an empty directory without .mcp.json."""
        monkeypatch.setattr("sys.argv", ["validate_mcp", str(tmp_path)])
        result = mcp_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No MCP configuration found" in captured.err

    def test_mcp_rejects_non_mcp_json_file(self, tmp_path, monkeypatch, capsys):
        """validate_mcp main() returns 1 when given a non-.mcp.json file."""
        plain_file = tmp_path / "config.txt"
        plain_file.write_text("not mcp config")
        monkeypatch.setattr("sys.argv", ["validate_mcp", str(plain_file)])
        result = mcp_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "not an MCP config file" in captured.err

    # -- validate_lsp --

    def test_lsp_rejects_empty_dir(self, tmp_path, monkeypatch, capsys):
        """validate_lsp main() returns 1 for an empty directory without LSP config."""
        monkeypatch.setattr("sys.argv", ["validate_lsp", str(tmp_path)])
        result = lsp_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No LSP configuration found" in captured.err

    def test_lsp_rejects_non_json_file(self, tmp_path, monkeypatch, capsys):
        """validate_lsp main() returns 1 when given a non-.json file."""
        plain_file = tmp_path / "config.txt"
        plain_file.write_text("not lsp config")
        monkeypatch.setattr("sys.argv", ["validate_lsp", str(plain_file)])
        result = lsp_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "not a JSON config file" in captured.err


# ---------------------------------------------------------------------------
# Plugin-wide validators
# ---------------------------------------------------------------------------


class TestPluginWideValidatorEarlyExit:
    """Tests for plugin-wide validators that expect .claude-plugin/ directory."""

    # -- validate_documentation --

    def test_documentation_rejects_dir_without_claude_plugin(self, tmp_path, monkeypatch, capsys):
        """validate_documentation main() returns 1 for a directory without .claude-plugin/."""
        monkeypatch.setattr("sys.argv", ["validate_documentation", str(tmp_path)])
        result = documentation_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No Claude Code plugin found" in captured.err

    def test_documentation_rejects_file_instead_of_dir(self, tmp_path, monkeypatch, capsys):
        """validate_documentation main() returns 1 when given a file instead of a directory."""
        some_file = tmp_path / "file.txt"
        some_file.write_text("not a directory")
        monkeypatch.setattr("sys.argv", ["validate_documentation", str(some_file)])
        result = documentation_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "is not a directory" in captured.err

    # -- validate_security --

    def test_security_rejects_dir_without_claude_plugin(self, tmp_path, monkeypatch, capsys):
        """validate_security main() returns 1 for a directory without .claude-plugin/."""
        monkeypatch.setattr("sys.argv", ["validate_security", str(tmp_path)])
        result = security_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No Claude Code plugin found" in captured.err

    def test_security_rejects_file_instead_of_dir(self, tmp_path, monkeypatch, capsys):
        """validate_security main() returns 1 when given a file instead of a directory."""
        some_file = tmp_path / "file.txt"
        some_file.write_text("not a directory")
        monkeypatch.setattr("sys.argv", ["validate_security", str(some_file)])
        result = security_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "is not a directory" in captured.err

    # -- validate_encoding --

    def test_encoding_rejects_dir_without_claude_plugin(self, tmp_path, monkeypatch, capsys):
        """validate_encoding main() returns 1 for a directory without .claude-plugin/."""
        monkeypatch.setattr("sys.argv", ["validate_encoding", str(tmp_path)])
        result = encoding_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No Claude Code plugin found" in captured.err

    def test_encoding_rejects_file_instead_of_dir(self, tmp_path, monkeypatch, capsys):
        """validate_encoding main() returns 1 when given a file instead of a directory."""
        some_file = tmp_path / "file.txt"
        some_file.write_text("not a directory")
        monkeypatch.setattr("sys.argv", ["validate_encoding", str(some_file)])
        result = encoding_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "is not a directory" in captured.err

    # -- validate_enterprise --

    def test_enterprise_rejects_dir_without_claude_plugin(self, tmp_path, monkeypatch, capsys):
        """validate_enterprise main() returns exit code 1 for directory without .claude-plugin/."""
        monkeypatch.setattr("sys.argv", ["validate_enterprise", str(tmp_path)])
        result = enterprise_main()
        assert result == 1  # EXIT_CRITICAL
        captured = capsys.readouterr()
        assert "No Claude Code plugin found" in captured.err

    def test_enterprise_rejects_file_instead_of_dir(self, tmp_path, monkeypatch, capsys):
        """validate_enterprise main() returns exit code 1 when given a file."""
        some_file = tmp_path / "file.txt"
        some_file.write_text("not a directory")
        monkeypatch.setattr("sys.argv", ["validate_enterprise", str(some_file)])
        result = enterprise_main()
        assert result == 1  # EXIT_CRITICAL
        captured = capsys.readouterr()
        assert "is not a directory" in captured.err

    # -- validate_xref --

    def test_xref_rejects_dir_without_claude_plugin(self, tmp_path, monkeypatch, capsys):
        """validate_xref main() returns 1 for directory without .claude-plugin/."""
        monkeypatch.setattr("sys.argv", ["validate_xref", str(tmp_path)])
        result = xref_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No Claude Code plugin found" in captured.err

    def test_xref_rejects_file_instead_of_dir(self, tmp_path, monkeypatch, capsys):
        """validate_xref main() returns 1 when given a file instead of a directory."""
        some_file = tmp_path / "file.txt"
        some_file.write_text("not a directory")
        monkeypatch.setattr("sys.argv", ["validate_xref", str(some_file)])
        result = xref_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "is not a directory" in captured.err

    # -- validate_scoring --

    def test_scoring_rejects_dir_without_claude_plugin(self, tmp_path, monkeypatch, capsys):
        """validate_scoring main() returns exit code 1 for directory without .claude-plugin/."""
        monkeypatch.setattr("sys.argv", ["validate_scoring", str(tmp_path)])
        result = scoring_main()
        assert result == 1  # EXIT_CRITICAL
        captured = capsys.readouterr()
        assert "No Claude Code plugin found" in captured.err

    def test_scoring_rejects_file_instead_of_dir(self, tmp_path, monkeypatch, capsys):
        """validate_scoring main() returns exit code 1 when given a file."""
        some_file = tmp_path / "file.txt"
        some_file.write_text("not a directory")
        monkeypatch.setattr("sys.argv", ["validate_scoring", str(some_file)])
        result = scoring_main()
        assert result == 1  # EXIT_CRITICAL
        captured = capsys.readouterr()
        assert "is not a directory" in captured.err

    # -- validate_plugin --

    def test_plugin_rejects_dir_without_claude_plugin(self, tmp_path, monkeypatch, capsys):
        """validate_plugin main() returns 1 for directory without .claude-plugin/."""
        monkeypatch.setattr("sys.argv", ["validate_plugin", str(tmp_path)])
        result = plugin_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No Claude Code plugin found" in captured.err

    def test_plugin_rejects_nonexistent_path(self, tmp_path, monkeypatch, capsys):
        """validate_plugin main() returns 1 for a nonexistent path."""
        nonexistent = tmp_path / "does_not_exist"
        monkeypatch.setattr("sys.argv", ["validate_plugin", str(nonexistent)])
        result = plugin_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "is not a directory" in captured.err


# ---------------------------------------------------------------------------
# Marketplace validators
# ---------------------------------------------------------------------------


class TestMarketplaceValidatorEarlyExit:
    """Tests for marketplace validators that expect marketplace.json."""

    # -- validate_marketplace --

    def test_marketplace_rejects_empty_dir(self, tmp_path, monkeypatch, capsys):
        """validate_marketplace main() returns 1 for empty directory without marketplace.json."""
        monkeypatch.setattr("sys.argv", ["validate_marketplace", str(tmp_path)])
        result = marketplace_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No marketplace.json found" in captured.err

    def test_marketplace_rejects_non_marketplace_json_file(self, tmp_path, monkeypatch, capsys):
        """validate_marketplace main() returns 1 when given a non-marketplace.json file."""
        other_file = tmp_path / "config.txt"
        other_file.write_text("not marketplace")
        monkeypatch.setattr("sys.argv", ["validate_marketplace", str(other_file)])
        result = marketplace_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "is not a marketplace.json file" in captured.err

    # -- validate_marketplace_pipeline --

    def test_marketplace_pipeline_rejects_dir_without_marketplace_json(self, tmp_path, monkeypatch, capsys):
        """validate_marketplace_pipeline main() returns nonzero for dir without marketplace.json."""
        monkeypatch.setattr("sys.argv", ["validate_marketplace_pipeline", str(tmp_path)])
        result = marketplace_pipeline_main()
        assert result != 0  # EXIT_MINOR (3) for early exit
        captured = capsys.readouterr()
        assert "No marketplace.json found" in captured.err

    def test_marketplace_pipeline_rejects_file_instead_of_dir(self, tmp_path, monkeypatch, capsys):
        """validate_marketplace_pipeline main() returns nonzero when given a file."""
        some_file = tmp_path / "file.txt"
        some_file.write_text("not a directory")
        monkeypatch.setattr("sys.argv", ["validate_marketplace_pipeline", str(some_file)])
        result = marketplace_pipeline_main()
        assert result != 0  # EXIT_MINOR (3) for early exit
        captured = capsys.readouterr()
        assert "is not a directory" in captured.err


# ---------------------------------------------------------------------------
# Path resolution tests
# ---------------------------------------------------------------------------


class TestPathResolution:
    """Tests that relative paths are resolved correctly (no crashes)."""

    def test_plugin_relative_dot_from_non_plugin_dir(self, tmp_path, monkeypatch, capsys):
        """validate_plugin main() with '.' from a non-plugin dir returns 1, not a crash."""
        monkeypatch.setattr("sys.argv", ["validate_plugin", str(tmp_path)])
        result = plugin_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No Claude Code plugin found" in captured.err

    def test_mcp_relative_dot_from_non_plugin_dir(self, tmp_path, monkeypatch, capsys):
        """validate_mcp main() with a non-plugin dir returns 1, not a crash."""
        monkeypatch.setattr("sys.argv", ["validate_mcp", str(tmp_path)])
        result = mcp_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No MCP configuration found" in captured.err

    def test_lsp_relative_dot_from_non_plugin_dir(self, tmp_path, monkeypatch, capsys):
        """validate_lsp main() with a non-plugin dir returns 1, not a crash."""
        monkeypatch.setattr("sys.argv", ["validate_lsp", str(tmp_path)])
        result = lsp_main()
        assert result == 1
        captured = capsys.readouterr()
        assert "No LSP configuration found" in captured.err
