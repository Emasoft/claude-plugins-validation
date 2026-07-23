#!/usr/bin/env python3
"""Tests for create_micro_agents_workflow.py — the EXPERIMENTAL RLM generator.

Verifies the skill palette excludes meta skills, the workflow .ts is fully
substituted (no leftover placeholders) with the palette embedded, the launcher
agent passes validate_agent, and both artifacts are written / overwrite-guarded.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from create_micro_agents_workflow import (  # noqa: E402
    build_skill_palette,
    create,
    render_workflow_ts,
)
from validate_agent import validate_agent  # noqa: E402


def _skill(plugin: Path, name: str, desc: str) -> None:
    d = plugin / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\nuser-invocable: false\n---\n\n# {name}\n\nbody\n",
        encoding="utf-8",
    )


def _make_plugin(tmp_path: Path) -> Path:
    plugin = tmp_path / "tp"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "tp", "version": "0.1.0", "description": "test"}\n', encoding="utf-8"
    )
    _skill(plugin, "greet", "Greet a person. Use when greeting.")
    _skill(plugin, "farewell", "Bid farewell. Use when parting.")
    _skill(plugin, "tp-the-skills-menu", "Router. Use when routing.")
    return plugin


class TestPaletteAndRender:
    """Palette selection + template substitution."""

    def test_palette_excludes_meta(self, tmp_path):
        """The palette carries only non-meta skills, each with its description as purpose."""
        plugin = _make_plugin(tmp_path)
        palette = build_skill_palette(plugin, include_all=False)
        assert {p["name"] for p in palette} == {"greet", "farewell"}
        assert any("Greet a person" in p["purpose"] for p in palette)

    def test_ts_fully_substituted(self, tmp_path):
        """The rendered .ts has no leftover placeholders and embeds the palette."""
        plugin = _make_plugin(tmp_path)
        ts, launcher, workflow_name, count = render_workflow_ts(plugin, "tp", include_all=False)
        assert workflow_name == "tp-micro-agents"
        assert count == 2
        for tok in ("__WORKFLOW_NAME__", "__PLUGIN_SLUG__", "__SKILL_PALETTE__", "__LAUNCHER_NAME__"):
            assert tok not in ts, f"unsubstituted placeholder {tok}"
        assert "export const meta" in ts
        assert '"name": "greet"' in ts
        assert "tp-workflow-launcher" in launcher


class TestCreate:
    """End-to-end create() — writes a valid launcher + a workflow .ts, guards overwrites."""

    def test_creates_launcher_and_workflow(self, tmp_path):
        """Both artifacts land; the launcher passes validate_agent with no blocking findings."""
        plugin = _make_plugin(tmp_path)
        assert create(plugin, "tp", include_all=False, force=False) == 0
        launcher = plugin / "agents" / "tp-workflow-launcher.md"
        ts = plugin / "workflows" / "tp-micro-agents.ts"
        assert launcher.is_file()
        assert ts.is_file()
        rep = validate_agent(launcher)
        blocking = [r.message for r in rep.results if r.level in ("CRITICAL", "MAJOR", "MINOR", "NIT")]
        assert blocking == [], blocking

    def test_refuses_overwrite_without_force(self, tmp_path):
        """A second run refuses without --force; --force overwrites both artifacts."""
        plugin = _make_plugin(tmp_path)
        assert create(plugin, "tp", include_all=False, force=False) == 0
        assert create(plugin, "tp", include_all=False, force=False) == 1
        assert create(plugin, "tp", include_all=False, force=True) == 0

    def test_missing_manifest_refused(self, tmp_path):
        """A directory without .claude-plugin/plugin.json is refused."""
        bare = tmp_path / "notaplugin"
        bare.mkdir()
        assert create(bare, "tp", include_all=False, force=False) == 1
