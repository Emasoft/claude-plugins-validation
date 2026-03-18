#!/usr/bin/env python3
"""Tests for manage_registry.py.

Tests the plugin registry listing and searching functions:
- _detect_components: detects commands, agents, skills, rules, hooks, mcp, lsp, output-styles
- _format_components: formats component dict as display string
- do_list: lists installed plugins with fake marketplace structures
- do_search: searches by component type or free text

Coverage: 25 tests covering all code paths across 4 functions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add scripts directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import manage_registry  # noqa: E402
from manage_registry import (  # noqa: E402
    _detect_components,
    _format_components,
    do_list,
    do_search,
)

# ── Helpers ──────────────────────────────────────────────


def _make_plugin(
    base_dir: Path,
    marketplace: str,
    name: str,
    *,
    version: str = "1.0.0",
    description: str = "",
    commands: int = 0,
    agents: int = 0,
    skills: bool = False,
    rules: int = 0,
    hooks: bool = False,
    mcp: bool = False,
    lsp: bool = False,
    output_styles: bool = False,
) -> Path:
    """Create a fake plugin directory structure under base_dir/marketplaces/<marketplace>/plugins/<name>."""
    plug_dir = base_dir / "marketplaces" / marketplace / "plugins" / name
    meta_dir = plug_dir / ".claude-plugin"
    meta_dir.mkdir(parents=True, exist_ok=True)
    plugin_json = {
        "name": name,
        "version": version,
        "description": description,
    }
    (meta_dir / "plugin.json").write_text(json.dumps(plugin_json), encoding="utf-8")

    for i in range(commands):
        cmd_dir = plug_dir / "commands"
        cmd_dir.mkdir(exist_ok=True)
        (cmd_dir / f"cmd-{i}.md").write_text(f"# Command {i}", encoding="utf-8")

    for i in range(agents):
        agent_dir = plug_dir / "agents"
        agent_dir.mkdir(exist_ok=True)
        (agent_dir / f"agent-{i}.md").write_text(f"# Agent {i}", encoding="utf-8")

    if skills:
        skills_dir = plug_dir / "skills"
        skills_dir.mkdir(exist_ok=True)
        (skills_dir / "SKILL.md").write_text("# Skill", encoding="utf-8")

    for i in range(rules):
        rules_dir = plug_dir / "rules"
        rules_dir.mkdir(exist_ok=True)
        (rules_dir / f"rule-{i}.md").write_text(f"# Rule {i}", encoding="utf-8")

    if hooks:
        hooks_dir = plug_dir / "hooks"
        hooks_dir.mkdir(exist_ok=True)

    if mcp:
        (plug_dir / ".mcp.json").write_text("{}", encoding="utf-8")

    if lsp:
        (plug_dir / ".lsp.json").write_text("{}", encoding="utf-8")

    if output_styles:
        styles_dir = plug_dir / "output-styles"
        styles_dir.mkdir(exist_ok=True)

    return plug_dir


def _setup_settings(base_dir: Path, enabled_plugins: dict | None = None) -> Path:
    """Create a fake settings.local.json at base_dir/settings.local.json."""
    settings_path = base_dir / "settings.local.json"
    data = {}
    if enabled_plugins is not None:
        data["enabledPlugins"] = enabled_plugins
    settings_path.write_text(json.dumps(data), encoding="utf-8")
    return settings_path


# ── _detect_components tests ─────────────────────────────


class TestDetectComponents:
    """Tests for _detect_components function."""

    def test_empty_plugin_dir_returns_empty(self, tmp_path):
        """_detect_components returns empty dict when plugin dir has no component directories."""
        plug_dir = tmp_path / "my-plugin"
        plug_dir.mkdir()
        result = _detect_components(plug_dir)
        assert result == {}

    def test_detects_commands_with_correct_count(self, tmp_path):
        """_detect_components detects .md files in commands/ subdirectory and counts them."""
        plug_dir = _make_plugin(tmp_path, "mp", "p", commands=3)
        result = _detect_components(plug_dir)
        assert result["commands"] == 3

    def test_detects_agents_skills_rules(self, tmp_path):
        """_detect_components detects agents, skills (SKILL.md), and rules subdirectories."""
        plug_dir = _make_plugin(tmp_path, "mp", "p", agents=2, skills=True, rules=4)
        result = _detect_components(plug_dir)
        assert result["agents"] == 2
        assert result["skills"] == 1
        assert result["rules"] == 4

    def test_detects_hooks_directory(self, tmp_path):
        """_detect_components detects hooks when hooks/ directory exists."""
        plug_dir = _make_plugin(tmp_path, "mp", "p", hooks=True)
        result = _detect_components(plug_dir)
        assert result["hooks"] == 1

    def test_hooks_file_and_mcp_dir_not_detected(self, tmp_path):
        """_detect_components rejects hooks-as-file (needs dir) and .mcp.json-as-dir (needs file)."""
        plug_dir = tmp_path / "plugin"
        plug_dir.mkdir()
        # hooks must be a directory, not a file
        (plug_dir / "hooks").write_text("not a dir", encoding="utf-8")
        # .mcp.json must be a file, not a directory
        (plug_dir / ".mcp.json").mkdir()
        result = _detect_components(plug_dir)
        assert "hooks" not in result
        assert "mcp" not in result

    def test_detects_mcp_and_lsp_files(self, tmp_path):
        """_detect_components detects .mcp.json and .lsp.json files at plugin root."""
        plug_dir = _make_plugin(tmp_path, "mp", "p", mcp=True, lsp=True)
        result = _detect_components(plug_dir)
        assert result["mcp"] == 1
        assert result["lsp"] == 1

    def test_detects_all_detectable_components_together(self, tmp_path):
        """_detect_components detects all component types in a fully populated plugin."""
        plug_dir = _make_plugin(
            tmp_path,
            "mp",
            "full-plugin",
            commands=2,
            agents=1,
            skills=True,
            rules=3,
            hooks=True,
            mcp=True,
            lsp=True,
        )
        result = _detect_components(plug_dir)
        assert result == {
            "commands": 2,
            "agents": 1,
            "skills": 1,
            "rules": 3,
            "hooks": 1,
            "mcp": 1,
            "lsp": 1,
        }

    def test_empty_commands_dir_not_counted(self, tmp_path):
        """_detect_components does not count a commands/ directory with no .md files."""
        plug_dir = tmp_path / "plugin"
        plug_dir.mkdir()
        (plug_dir / "commands").mkdir()
        (plug_dir / "commands" / "readme.txt").write_text("not md", encoding="utf-8")
        result = _detect_components(plug_dir)
        assert "commands" not in result


# ── _format_components tests ─────────────────────────────


class TestFormatComponents:
    """Tests for _format_components function."""

    def test_empty_components(self):
        """_format_components returns empty string for empty dict."""
        assert _format_components({}) == ""

    def test_singular_and_plural_forms(self):
        """_format_components shows singular for count==1, plural for count>1."""
        assert _format_components({"commands": 1}) == "  [1 command]"
        assert _format_components({"commands": 3}) == "  [3 commands]"

    def test_special_components_uppercased_or_kept(self):
        """_format_components uppercases MCP/LSP but keeps hooks/output-styles lowercase."""
        result = _format_components({"mcp": 1, "lsp": 1, "hooks": 1, "output-styles": 1})
        assert "MCP" in result
        assert "LSP" in result
        assert "hooks" in result
        assert "output-styles" in result

    def test_mixed_components_formatting(self):
        """_format_components formats a mixed set of regular and special components."""
        result = _format_components({"commands": 2, "hooks": 1, "mcp": 1})
        assert result == "  [2 commands, hooks, MCP]"


# ── do_list tests ────────────────────────────────────────


class TestDoList:
    """Tests for do_list function."""

    def test_no_marketplaces_dir(self, tmp_path, monkeypatch, capsys):
        """do_list prints info message when MARKETPLACES_DIR does not exist."""
        monkeypatch.setattr(manage_registry, "MARKETPLACES_DIR", tmp_path / "nonexistent")
        do_list()
        captured = capsys.readouterr()
        assert "No local marketplaces found" in captured.out

    def test_lists_single_plugin(self, tmp_path, monkeypatch, capsys):
        """do_list prints plugin name, version, description, and components for a single plugin."""
        _make_plugin(tmp_path, "my-market", "cool-plugin", version="2.3.1", description="A cool plugin", commands=2)
        settings_path = _setup_settings(tmp_path)
        monkeypatch.setattr(manage_registry, "MARKETPLACES_DIR", tmp_path / "marketplaces")
        monkeypatch.setattr(manage_registry, "SETTINGS_TARGET", settings_path)
        do_list()
        captured = capsys.readouterr()
        assert "cool-plugin" in captured.out
        assert "v2.3.1" in captured.out
        assert "A cool plugin" in captured.out
        assert "2 commands" in captured.out

    def test_lists_enabled_disabled_status(self, tmp_path, monkeypatch, capsys):
        """do_list shows enabled/disabled status based on settings."""
        _make_plugin(tmp_path, "mkt", "enabled-plug", version="1.0.0")
        _make_plugin(tmp_path, "mkt", "disabled-plug", version="1.0.0")
        settings_path = _setup_settings(
            tmp_path,
            {
                "enabled-plug@mkt": True,
                "disabled-plug@mkt": False,
            },
        )
        monkeypatch.setattr(manage_registry, "MARKETPLACES_DIR", tmp_path / "marketplaces")
        monkeypatch.setattr(manage_registry, "SETTINGS_TARGET", settings_path)
        do_list()
        captured = capsys.readouterr()
        assert "enabled" in captured.out
        assert "disabled" in captured.out

    def test_no_plugins_installed(self, tmp_path, monkeypatch, capsys):
        """do_list prints info message when marketplace dirs exist but contain no plugins."""
        mp_dir = tmp_path / "marketplaces" / "empty-market"
        mp_dir.mkdir(parents=True)
        settings_path = _setup_settings(tmp_path)
        monkeypatch.setattr(manage_registry, "MARKETPLACES_DIR", tmp_path / "marketplaces")
        monkeypatch.setattr(manage_registry, "SETTINGS_TARGET", settings_path)
        do_list()
        captured = capsys.readouterr()
        assert "No plugins installed" in captured.out

    def test_skips_non_dir_in_marketplace(self, tmp_path, monkeypatch, capsys):
        """do_list skips files (non-directories) inside the marketplaces directory."""
        _make_plugin(tmp_path, "mkt", "real-plugin", version="1.0.0", description="Real")
        (tmp_path / "marketplaces" / "stray-file.txt").write_text("noise", encoding="utf-8")
        settings_path = _setup_settings(tmp_path)
        monkeypatch.setattr(manage_registry, "MARKETPLACES_DIR", tmp_path / "marketplaces")
        monkeypatch.setattr(manage_registry, "SETTINGS_TARGET", settings_path)
        do_list()
        captured = capsys.readouterr()
        assert "real-plugin" in captured.out
        assert "stray-file" not in captured.out


# ── do_search tests ──────────────────────────────────────


class TestDoSearch:
    """Tests for do_search function."""

    def test_no_marketplaces_dir(self, tmp_path, monkeypatch, capsys):
        """do_search prints info message when MARKETPLACES_DIR does not exist."""
        monkeypatch.setattr(manage_registry, "MARKETPLACES_DIR", tmp_path / "nonexistent")
        do_search("commands")
        captured = capsys.readouterr()
        assert "No local marketplaces found" in captured.out

    def test_search_by_component_type(self, tmp_path, monkeypatch, capsys):
        """do_search filters plugins by component type keyword, excluding non-matching plugins."""
        _make_plugin(tmp_path, "mkt", "with-cmds", commands=3, description="Has commands")
        _make_plugin(tmp_path, "mkt", "no-cmds", agents=1, description="No commands here")
        settings_path = _setup_settings(tmp_path)
        monkeypatch.setattr(manage_registry, "MARKETPLACES_DIR", tmp_path / "marketplaces")
        monkeypatch.setattr(manage_registry, "SETTINGS_TARGET", settings_path)
        do_search("commands")
        captured = capsys.readouterr()
        assert "with-cmds" in captured.out
        assert "no-cmds" not in captured.out

    def test_search_by_type_alias(self, tmp_path, monkeypatch, capsys):
        """do_search resolves singular aliases like 'command' to 'commands'."""
        _make_plugin(tmp_path, "mkt", "cmd-plug", commands=1)
        settings_path = _setup_settings(tmp_path)
        monkeypatch.setattr(manage_registry, "MARKETPLACES_DIR", tmp_path / "marketplaces")
        monkeypatch.setattr(manage_registry, "SETTINGS_TARGET", settings_path)
        do_search("command")
        captured = capsys.readouterr()
        assert "cmd-plug" in captured.out
        assert "1 found" in captured.out

    def test_search_by_name_text(self, tmp_path, monkeypatch, capsys):
        """do_search matches free text against plugin names, case-insensitive."""
        _make_plugin(tmp_path, "mkt", "MyAwesomeTool", description="Does stuff")
        _make_plugin(tmp_path, "mkt", "other-plugin", description="Other stuff")
        settings_path = _setup_settings(tmp_path)
        monkeypatch.setattr(manage_registry, "MARKETPLACES_DIR", tmp_path / "marketplaces")
        monkeypatch.setattr(manage_registry, "SETTINGS_TARGET", settings_path)
        do_search("myawesome")
        captured = capsys.readouterr()
        assert "MyAwesomeTool" in captured.out
        assert "other-plugin" not in captured.out

    def test_search_by_description_text(self, tmp_path, monkeypatch, capsys):
        """do_search matches free text against plugin descriptions."""
        _make_plugin(tmp_path, "mkt", "plug-a", description="Validates JSON schemas")
        _make_plugin(tmp_path, "mkt", "plug-b", description="Formats YAML files")
        settings_path = _setup_settings(tmp_path)
        monkeypatch.setattr(manage_registry, "MARKETPLACES_DIR", tmp_path / "marketplaces")
        monkeypatch.setattr(manage_registry, "SETTINGS_TARGET", settings_path)
        do_search("JSON")
        captured = capsys.readouterr()
        assert "plug-a" in captured.out
        assert "plug-b" not in captured.out

    def test_search_no_results_type_filter(self, tmp_path, monkeypatch, capsys):
        """do_search prints type-specific no-results message when filtering by type."""
        _make_plugin(tmp_path, "mkt", "no-hooks", commands=1)
        settings_path = _setup_settings(tmp_path)
        monkeypatch.setattr(manage_registry, "MARKETPLACES_DIR", tmp_path / "marketplaces")
        monkeypatch.setattr(manage_registry, "SETTINGS_TARGET", settings_path)
        do_search("hooks")
        captured = capsys.readouterr()
        assert "No plugins found with component type: hooks" in captured.out

    def test_search_no_results_text(self, tmp_path, monkeypatch, capsys):
        """do_search prints text-specific no-results message for free text queries."""
        _make_plugin(tmp_path, "mkt", "generic", description="Nothing special")
        settings_path = _setup_settings(tmp_path)
        monkeypatch.setattr(manage_registry, "MARKETPLACES_DIR", tmp_path / "marketplaces")
        monkeypatch.setattr(manage_registry, "SETTINGS_TARGET", settings_path)
        do_search("nonexistent-query-xyz")
        captured = capsys.readouterr()
        assert "No plugins matching: nonexistent-query-xyz" in captured.out

    def test_search_shows_components_in_results(self, tmp_path, monkeypatch, capsys):
        """do_search result output includes formatted component info for matched plugins."""
        _make_plugin(tmp_path, "mkt", "full-plug", commands=2, hooks=True, mcp=True, description="Full featured")
        settings_path = _setup_settings(tmp_path)
        monkeypatch.setattr(manage_registry, "MARKETPLACES_DIR", tmp_path / "marketplaces")
        monkeypatch.setattr(manage_registry, "SETTINGS_TARGET", settings_path)
        do_search("full")
        captured = capsys.readouterr()
        assert "2 commands" in captured.out
        assert "hooks" in captured.out
        assert "MCP" in captured.out
