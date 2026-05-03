"""Tests for scripts/refresh_readme.py + cpv_management_common marker helpers."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import refresh_readme  # noqa: E402
from cpv_management_common import (  # noqa: E402
    detect_components,
    render_components_table,
    replace_marker_block,
)

# ── replace_marker_block ─────────────────────────────────────────────────────


def test_replace_marker_block_appends_when_missing(tmp_path):
    f = tmp_path / "README.md"
    f.write_text("# Hello\n\nSome text.\n", encoding="utf-8")
    changed, status = replace_marker_block(f, "X", "body line", create_if_missing=True)
    assert changed is True
    assert status == "appended"
    text = f.read_text(encoding="utf-8")
    assert "<!-- BEGIN AUTO-X -->" in text
    assert "body line" in text
    assert "<!-- END AUTO-X -->" in text
    assert text.startswith("# Hello\n")  # preserved


def test_replace_marker_block_updates_existing(tmp_path):
    f = tmp_path / "README.md"
    f.write_text(
        "# Hi\n\n<!-- BEGIN AUTO-Y -->\nold body\n<!-- END AUTO-Y -->\n\nfooter\n",
        encoding="utf-8",
    )
    changed, status = replace_marker_block(f, "Y", "new body")
    assert changed is True
    assert status == "updated"
    text = f.read_text(encoding="utf-8")
    assert "new body" in text
    assert "old body" not in text
    assert text.endswith("footer\n")  # surrounding text preserved


def test_replace_marker_block_unchanged_when_identical(tmp_path):
    f = tmp_path / "README.md"
    body = "same body"
    f.write_text(
        f"<!-- BEGIN AUTO-Z -->\n{body}\n<!-- END AUTO-Z -->\n",
        encoding="utf-8",
    )
    changed, status = replace_marker_block(f, "Z", body)
    assert changed is False
    assert status == "unchanged"


def test_replace_marker_block_missing_no_create(tmp_path):
    f = tmp_path / "README.md"
    f.write_text("plain content", encoding="utf-8")
    changed, status = replace_marker_block(f, "X", "body", create_if_missing=False)
    assert changed is False
    assert status == "missing"


def test_replace_marker_block_creates_file(tmp_path):
    f = tmp_path / "fresh.md"
    changed, status = replace_marker_block(f, "X", "hello", create_if_missing=True)
    assert changed is True
    assert status == "created"
    assert f.exists()


def test_replace_marker_block_no_file_no_create_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        replace_marker_block(tmp_path / "nope.md", "X", "body")


# ── detect_components ────────────────────────────────────────────────────────


def test_detect_components_empty_plugin(tmp_path):
    plugin = tmp_path / "p"
    plugin.mkdir()
    assert detect_components(plugin) == {}


def test_detect_components_finds_agents(tmp_path):
    plugin = tmp_path / "p"
    (plugin / "agents").mkdir(parents=True)
    (plugin / "agents" / "a1.md").write_text("---\nname: a1\n---\n")
    (plugin / "agents" / "a2.md").write_text("---\nname: a2\n---\n")
    out = detect_components(plugin)
    assert out["agents"] == ["a1", "a2"]


def test_detect_components_finds_skills(tmp_path):
    plugin = tmp_path / "p"
    (plugin / "skills" / "skill-one").mkdir(parents=True)
    (plugin / "skills" / "skill-one" / "SKILL.md").write_text("# x\n")
    (plugin / "skills" / "no-skill-md").mkdir()  # missing SKILL.md → not counted
    out = detect_components(plugin)
    assert out.get("skills") == ["skill-one"]


def test_detect_components_finds_commands_hooks_mcp(tmp_path):
    plugin = tmp_path / "p"
    (plugin / "commands").mkdir(parents=True)
    (plugin / "commands" / "do-thing.md").write_text("# c\n")
    (plugin / "hooks").mkdir()
    (plugin / "hooks" / "hooks.json").write_text("{}")
    (plugin / ".mcp.json").write_text("{}")
    out = detect_components(plugin)
    assert out["commands"] == ["do-thing"]
    assert out["hooks"] == ["hooks.json"]
    assert out["mcpServers"] == [".mcp.json"]


# ── render_components_table ──────────────────────────────────────────────────


def test_render_components_table_empty():
    out = render_components_table({})
    assert "no components" in out


def test_render_components_table_single_component():
    out = render_components_table({"agents": ["a1", "a2"]})
    assert "| Component | Count | Names |" in out
    assert "`agents/`" in out
    assert "`a1`" in out
    assert "`a2`" in out
    assert "| 2 |" in out


def test_render_components_table_sorted_alphabetically():
    out = render_components_table({"hooks": ["h"], "agents": ["a"], "skills": ["s"]})
    # agents should come before hooks, hooks before skills (alphabetical)
    a_idx = out.index("agents")
    h_idx = out.index("hooks")
    s_idx = out.index("skills")
    assert a_idx < h_idx < s_idx


# ── refresh_readme integration ───────────────────────────────────────────────


def _make_plugin(tmp_path: Path, *, with_readme: bool = True) -> Path:
    plugin = tmp_path / "p"
    plugin.mkdir()
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "p", "version": "0.1.0", "description": "x"}),
    )
    if with_readme:
        (plugin / "README.md").write_text("# p\n\nSome README.\n", encoding="utf-8")
    return plugin


def test_refresh_readme_appends_when_marker_missing(tmp_path):
    plugin = _make_plugin(tmp_path)
    (plugin / "agents").mkdir()
    (plugin / "agents" / "a1.md").write_text("---\nname: a1\n---\n")
    rc = refresh_readme.refresh(plugin, check_only=False)
    assert rc == 0
    text = (plugin / "README.md").read_text(encoding="utf-8")
    assert "<!-- BEGIN AUTO-COMPONENTS -->" in text
    assert "`a1`" in text


def test_refresh_readme_check_returns_1_on_stale(tmp_path):
    plugin = _make_plugin(tmp_path)
    (plugin / "agents").mkdir()
    (plugin / "agents" / "a1.md").write_text("---\nname: a1\n---\n")
    rc = refresh_readme.refresh(plugin, check_only=True)
    assert rc == 1  # markers missing → would change


def test_refresh_readme_check_returns_0_when_fresh(tmp_path):
    plugin = _make_plugin(tmp_path)
    (plugin / "agents").mkdir()
    (plugin / "agents" / "a1.md").write_text("---\nname: a1\n---\n")
    refresh_readme.refresh(plugin, check_only=False)  # do the refresh
    rc = refresh_readme.refresh(plugin, check_only=True)
    assert rc == 0


def test_refresh_readme_bootstraps_missing_readme(tmp_path):
    plugin = _make_plugin(tmp_path, with_readme=False)
    rc = refresh_readme.refresh(plugin, check_only=False)
    assert rc == 0
    assert (plugin / "README.md").is_file()


def test_refresh_readme_cli(tmp_path):
    plugin = _make_plugin(tmp_path)
    (plugin / "agents").mkdir()
    (plugin / "agents" / "a1.md").write_text("---\nname: a1\n---\n")
    res = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "refresh_readme.py"), str(plugin)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert res.returncode == 0
    text = (plugin / "README.md").read_text(encoding="utf-8")
    assert "AUTO-COMPONENTS" in text
