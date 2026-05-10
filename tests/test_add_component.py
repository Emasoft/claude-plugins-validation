"""Tests for scripts/add_component.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import add_component as ac  # noqa: E402


@pytest.fixture
def plugin(tmp_path):
    p = tmp_path / "p"
    (p / ".claude-plugin").mkdir(parents=True)
    (p / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "p", "version": "0.1.0", "description": "x"}),
    )
    return p


# ── add_skill ────────────────────────────────────────────────────────────────


def test_add_skill_creates_dir_and_skill_md(plugin):
    rc = ac.add_skill(plugin, "my-skill", "What it does", force=False)
    assert rc == 0
    skill_md = plugin / "skills" / "my-skill" / "SKILL.md"
    assert skill_md.is_file()
    text = skill_md.read_text(encoding="utf-8")
    assert "name: my-skill" in text
    assert "description: What it does" in text


def test_add_skill_refuses_overwrite_without_force(plugin):
    ac.add_skill(plugin, "x", "first", force=False)
    rc = ac.add_skill(plugin, "x", "second", force=False)
    assert rc == 1


def test_add_skill_overwrites_with_force(plugin):
    ac.add_skill(plugin, "x", "first", force=False)
    rc = ac.add_skill(plugin, "x", "second", force=True)
    assert rc == 0
    text = (plugin / "skills" / "x" / "SKILL.md").read_text()
    assert "second" in text


# ── add_agent ────────────────────────────────────────────────────────────────


def test_add_agent_creates_md_with_tools(plugin):
    rc = ac.add_agent(plugin, "ag", "agent desc", "Read, Bash", force=False)
    assert rc == 0
    text = (plugin / "agents" / "ag.md").read_text()
    assert "name: ag" in text
    assert "tools: Read, Bash" in text


def test_add_agent_no_tools_omits_field(plugin):
    rc = ac.add_agent(plugin, "ag", "desc", "", force=False)
    assert rc == 0
    text = (plugin / "agents" / "ag.md").read_text()
    assert "tools:" not in text


# ── add_command ──────────────────────────────────────────────────────────────


def test_add_command_creates_md(plugin):
    rc = ac.add_command(plugin, "do", "command desc", "Bash(uv:*)", force=False)
    assert rc == 0
    text = (plugin / "commands" / "do.md").read_text()
    assert "name: do" in text
    assert "allowed-tools: Bash(uv:*)" in text
    assert "user-invocable: true" in text


def test_add_command_default_allowed_tools(plugin):
    rc = ac.add_command(plugin, "do", "x", "", force=False)
    assert rc == 0
    text = (plugin / "commands" / "do.md").read_text()
    assert "allowed-tools: Bash" in text


# ── add_hook ─────────────────────────────────────────────────────────────────


def test_add_hook_creates_hooks_json(plugin):
    rc = ac.add_hook(plugin, "PreToolUse", "echo hi")
    assert rc == 0
    data = json.loads((plugin / "hooks" / "hooks.json").read_text())
    assert "hooks" in data
    assert "PreToolUse" in data["hooks"]
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "echo hi"


def test_add_hook_appends_to_existing(plugin):
    ac.add_hook(plugin, "PreToolUse", "first")
    ac.add_hook(plugin, "PreToolUse", "second")
    data = json.loads((plugin / "hooks" / "hooks.json").read_text())
    assert len(data["hooks"]["PreToolUse"]) == 2


def test_add_hook_idempotent_dup_skipped(plugin):
    ac.add_hook(plugin, "Stop", "echo same")
    ac.add_hook(plugin, "Stop", "echo same")
    data = json.loads((plugin / "hooks" / "hooks.json").read_text())
    assert len(data["hooks"]["Stop"]) == 1


# ── add_mcp ──────────────────────────────────────────────────────────────────


def test_add_mcp_stdio(plugin):
    rc = ac.add_mcp(plugin, "my-server", "node /path/to/server.js", "")
    assert rc == 0
    data = json.loads((plugin / ".mcp.json").read_text())
    assert "my-server" in data["mcpServers"]
    assert data["mcpServers"]["my-server"]["command"] == "node /path/to/server.js"


def test_add_mcp_http(plugin):
    rc = ac.add_mcp(plugin, "remote", "", "https://api.example.com/mcp")
    assert rc == 0
    data = json.loads((plugin / ".mcp.json").read_text())
    assert data["mcpServers"]["remote"]["type"] == "http"
    assert data["mcpServers"]["remote"]["url"] == "https://api.example.com/mcp"


def test_add_mcp_dup_skipped(plugin):
    ac.add_mcp(plugin, "srv", "cmd", "")
    ac.add_mcp(plugin, "srv", "different-cmd", "")
    data = json.loads((plugin / ".mcp.json").read_text())
    # First-write wins; second is skipped without complaint.
    assert data["mcpServers"]["srv"]["command"] == "cmd"
