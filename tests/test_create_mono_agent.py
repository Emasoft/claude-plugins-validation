#!/usr/bin/env python3
"""Tests for create_mono_agent.py — the EXPERIMENTAL prefill-everything generator.

Verifies the mono-agent inlines every NON-meta skill, excludes meta/router
skills, keeps exactly one H1 (skill H1s demoted, fence-aware), produces an agent
that passes validate_agent with zero blocking findings, and refuses unsafe
overwrites / non-plugin targets.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from create_mono_agent import (  # noqa: E402
    _demote_h1,
    build_mono_agent,
    create,
)
from validate_agent import validate_agent  # noqa: E402


def _skill(plugin: Path, name: str, body: str) -> None:
    d = plugin / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill. Use when needed.\nuser-invocable: false\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _make_plugin(tmp_path: Path) -> Path:
    plugin = tmp_path / "tp"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "tp", "version": "0.1.0", "description": "test"}\n', encoding="utf-8"
    )
    _skill(plugin, "greet", "# greet\n\n## Overview\n\nGreets someone by name.")
    _skill(plugin, "farewell", "# farewell\n\n## Overview\n\nBids farewell.")
    _skill(plugin, "tp-the-skills-menu", "# tp-the-skills-menu\n\nRouter menu content.")
    return plugin


class TestBuildMonoAgent:
    """build_mono_agent selection + concatenation semantics."""

    def test_excludes_meta_skills_by_default(self, tmp_path):
        """Meta/router skills are dropped from the mono body unless --include-all."""
        plugin = _make_plugin(tmp_path)
        text, included, excluded = build_mono_agent(plugin, "tp-mono-agent", include_all=False)
        assert set(included) == {"greet", "farewell"}
        assert excluded == ["tp-the-skills-menu"]
        assert "Greets someone by name" in text
        assert "Bids farewell" in text
        assert "Router menu content" not in text

    def test_include_all_keeps_meta(self, tmp_path):
        """--include-all inlines literally every skill, including meta."""
        plugin = _make_plugin(tmp_path)
        text, included, excluded = build_mono_agent(plugin, "tp-mono-agent", include_all=True)
        assert "tp-the-skills-menu" in included
        assert excluded == []
        assert "Router menu content" in text

    def test_single_h1_after_demotion(self, tmp_path):
        """The mono-agent keeps exactly one H1 (its own); inlined skill H1s become H2."""
        plugin = _make_plugin(tmp_path)
        text, _, _ = build_mono_agent(plugin, "tp-mono-agent", include_all=False)
        h1s = [ln for ln in text.splitlines() if ln.startswith("# ")]
        assert h1s == ["# tp-mono-agent"]
        assert "## greet" in text  # demoted from '# greet'


class TestDemoteH1:
    """The fence-aware H1 demotion that prevents markdownlint MD025."""

    def test_demotes_top_level_only(self):
        """'# X' -> '## X'; deeper headings keep their level."""
        assert _demote_h1("# Title\n## Sub\ntext") == "## Title\n## Sub\ntext"

    def test_skips_fenced_code(self):
        """A '# comment' inside a code fence is NOT treated as a heading."""
        md = "# Real\n```bash\n# not a heading\n```\ntail"
        out = _demote_h1(md)
        assert out.startswith("## Real")
        assert "\n# not a heading\n" in out  # unchanged inside the fence
        assert "## not a heading" not in out


class TestCreate:
    """End-to-end create() — writes a validate-clean agent, guards overwrites."""

    def test_creates_valid_agent(self, tmp_path):
        """The generated mono-agent passes validate_agent with zero blocking findings."""
        plugin = _make_plugin(tmp_path)
        assert create(plugin, "tp-mono-agent", include_all=False, force=False) == 0
        agent_md = plugin / "agents" / "tp-mono-agent.md"
        assert agent_md.is_file()
        rep = validate_agent(agent_md)
        blocking = [r.message for r in rep.results if r.level in ("CRITICAL", "MAJOR", "MINOR", "NIT")]
        assert blocking == [], blocking

    def test_refuses_overwrite_without_force(self, tmp_path):
        """A second run refuses without --force; --force overwrites."""
        plugin = _make_plugin(tmp_path)
        assert create(plugin, "tp-mono-agent", include_all=False, force=False) == 0
        assert create(plugin, "tp-mono-agent", include_all=False, force=False) == 1
        assert create(plugin, "tp-mono-agent", include_all=False, force=True) == 0

    def test_missing_manifest_refused(self, tmp_path):
        """A directory without .claude-plugin/plugin.json is refused."""
        bare = tmp_path / "notaplugin"
        bare.mkdir()
        assert create(bare, "x-mono-agent", include_all=False, force=False) == 1
