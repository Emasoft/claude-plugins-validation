#!/usr/bin/env python3
"""Tests for create_mono_agent.py — the PLUGIN-WIDE ALL-IN-ONE generator.

SUPERSESSION (TRDD-XUNZQ70I): this generator no longer INLINES skill bodies. It
PRELOADS every non-meta skill BY NAME in `skills:` frontmatter and routes to them
from the agent's body. A skill's content is NEVER copied into an agent, because a
skill has to stay INDEPENDENT so it can be shared and fixed ONCE — an inlined copy
is a second source that rots silently.

The old inlining tests are GONE rather than kept beside the new ones: there is
exactly ONE version of the mechanism, and a test asserting the forbidden shape
would be a compatibility path in disguise.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from convert_agent import COMPANION_SKILL_NAME  # noqa: E402
from create_mono_agent import (  # noqa: E402
    _default_agent_name,
    build_mono_agent,
    create,
    select_skills,
)
from validate_agent import validate_agent  # noqa: E402

BLOCKING = ("CRITICAL", "MAJOR", "MINOR", "NIT")

GREET_BODY_LINE = "Greets someone by name, using the salutation they last accepted."
FAREWELL_BODY_LINE = "Bids farewell and records every promise made during the exchange."


def _skill(plugin: Path, name: str, body: str, *, extra_frontmatter: str = "") -> Path:
    d = plugin / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    skill_md = d / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: {name} skill. Use when needed.\nuser-invocable: false\n"
        f"{extra_frontmatter}---\n\n{body}\n",
        encoding="utf-8",
    )
    return skill_md


def _make_plugin(tmp_path: Path) -> Path:
    plugin = tmp_path / "tp"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "tp", "version": "0.1.0", "description": "test"}\n', encoding="utf-8"
    )
    _skill(plugin, "greet", f"# greet\n\n## Overview\n\n{GREET_BODY_LINE}")
    _skill(plugin, "farewell", f"# farewell\n\n## Overview\n\n{FAREWELL_BODY_LINE}")
    _skill(plugin, "tp-the-skills-menu", "# tp-the-skills-menu\n\nRouter menu content lives here.")
    return plugin


class TestSelectSkills:
    """Which skills a plugin-wide ALL-IN-ONE agent may preload."""

    def test_excludes_meta_skills_by_default(self, tmp_path):
        """Routing through a catalog is PLUGIN-OMNI's job, so an ALL-IN-ONE agent
        never lists the menu (nor the skill that generates it)."""
        plugin = _make_plugin(tmp_path)
        included, excluded = select_skills(plugin, include_all=False)
        assert set(included) == {"greet", "farewell"}
        assert [n for n, _r in excluded] == ["tp-the-skills-menu"]
        assert "meta" in dict(excluded)["tp-the-skills-menu"]

    def test_include_all_keeps_meta(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        included, excluded = select_skills(plugin, include_all=True)
        assert "tp-the-skills-menu" in included
        assert excluded == []

    def test_excludes_an_unpreloadable_skill(self, tmp_path):
        """`disable-model-invocation: true` CANNOT be preloaded — the preload is
        silently dropped, so listing it is a MAJOR (AC5) and dead weight."""
        plugin = _make_plugin(tmp_path)
        _skill(
            plugin,
            "user-only",
            "# user-only\n\nOnly the user may run this.",
            extra_frontmatter="disable-model-invocation: true\n",
        )
        included, excluded = select_skills(plugin, include_all=True)
        assert "user-only" not in included
        assert "AC5" in dict(excluded)["user-only"]

    def test_companion_is_never_listed_twice(self, tmp_path):
        """It is appended unconditionally, so a plugin that already ships it must not
        produce a duplicate entry."""
        plugin = _make_plugin(tmp_path)
        _skill(plugin, COMPANION_SKILL_NAME, "# companion\n\nEvidence before claims.")
        included, _ = select_skills(plugin, include_all=True)
        assert COMPANION_SKILL_NAME not in included
        text, _inc, _exc = build_mono_agent(plugin, "tp-all-in-one", include_all=True)
        fm, _body = read_markdown_parts_from_text(text)
        assert fm["skills"].count(COMPANION_SKILL_NAME) == 1


def read_markdown_parts_from_text(text: str):
    """Parse produced TEXT (no file) through the ONE frontmatter parser."""
    from validate_agent import parse_frontmatter

    fm, body, _ = parse_frontmatter(text)
    return fm, body


class TestBuildMonoAgent:
    """The frontmatter model: reference by NAME, never copy."""

    def test_lists_every_non_meta_skill_by_name(self, tmp_path):
        plugin = _make_plugin(tmp_path)
        text, included, _excluded = build_mono_agent(plugin, "tp-all-in-one", include_all=False)
        fm, _body = read_markdown_parts_from_text(text)
        assert fm["skills"] == ["farewell", "greet", COMPANION_SKILL_NAME]
        assert set(included) == {"greet", "farewell"}

    def test_copies_no_skill_content(self, tmp_path):
        """THE acceptance test for the inlining prohibition."""
        plugin = _make_plugin(tmp_path)
        text, _included, _excluded = build_mono_agent(plugin, "tp-all-in-one", include_all=True)
        assert GREET_BODY_LINE not in text
        assert FAREWELL_BODY_LINE not in text
        assert "Router menu content lives here." not in text

    def test_the_guard_is_not_vacuous(self, tmp_path):
        """POSITIVE CONTROL: the skill bodies really are present in the plugin, so the
        assertion above is proving absence in the AGENT, not absence everywhere."""
        plugin = _make_plugin(tmp_path)
        assert GREET_BODY_LINE in (plugin / "skills" / "greet" / "SKILL.md").read_text(encoding="utf-8")

    def test_routes_to_each_skill_from_the_body(self, tmp_path):
        """A preload with no routing is dead weight paid for on every turn (AC4)."""
        from cpv_agent_closure import body_mentions_skill_name

        plugin = _make_plugin(tmp_path)
        text, _included, _excluded = build_mono_agent(plugin, "tp-all-in-one", include_all=False)
        _fm, body = read_markdown_parts_from_text(text)
        for name in ("greet", "farewell", COMPANION_SKILL_NAME):
            assert body_mentions_skill_name(body, name), name

    def test_no_model_pin(self, tmp_path):
        """CA-04 — a generated agent must inherit the session model."""
        plugin = _make_plugin(tmp_path)
        text, _i, _e = build_mono_agent(plugin, "tp-all-in-one", include_all=False)
        fm, _body = read_markdown_parts_from_text(text)
        assert "model" not in fm

    def test_single_h1(self, tmp_path):
        """Exactly one top-level heading (markdownlint MD025)."""
        plugin = _make_plugin(tmp_path)
        text, _i, _e = build_mono_agent(plugin, "tp-all-in-one", include_all=False)
        assert [ln for ln in text.splitlines() if ln.startswith("# ")] == ["# tp-all-in-one"]


class TestCreate:
    """End-to-end create() — writes a validate-clean agent, guards overwrites."""

    def test_creates_valid_agent(self, tmp_path):
        """The generated ALL-IN-ONE agent passes validate_agent — AC1-AC5 included —
        with zero blocking findings."""
        plugin = _make_plugin(tmp_path)
        assert create(plugin, "tp-all-in-one", include_all=False, force=False) == 0
        agent_md = plugin / "agents" / "tp-all-in-one.md"
        assert agent_md.is_file()
        rep = validate_agent(agent_md, skills_roots=[plugin / "skills"])
        blocking = [f"{r.level}: {r.message}" for r in rep.results if r.level in BLOCKING]
        assert blocking == [], blocking

    def test_ensures_the_mandatory_companion_skill(self, tmp_path):
        """Naming a skill that does not resolve is a MAJOR, so the companion must be
        on disk before the agent that preloads it."""
        plugin = _make_plugin(tmp_path)
        assert create(plugin, "tp-all-in-one", include_all=False, force=False) == 0
        companion = plugin / "skills" / COMPANION_SKILL_NAME / "SKILL.md"
        assert companion.is_file()
        assert "NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE" in companion.read_text(
            encoding="utf-8"
        )

    def test_refuses_overwrite_without_force(self, tmp_path):
        """A second run refuses without --force; --force overwrites."""
        plugin = _make_plugin(tmp_path)
        assert create(plugin, "tp-all-in-one", include_all=False, force=False) == 0
        assert create(plugin, "tp-all-in-one", include_all=False, force=False) == 1
        assert create(plugin, "tp-all-in-one", include_all=False, force=True) == 0

    def test_missing_manifest_refused(self, tmp_path):
        """A directory without .claude-plugin/plugin.json is refused."""
        bare = tmp_path / "notaplugin"
        bare.mkdir()
        assert create(bare, "x-all-in-one", include_all=False, force=False) == 1

    def test_no_preloadable_skills_is_refused_not_emitted(self, tmp_path):
        """An agent whose only entry is the companion would be an empty shell."""
        plugin = tmp_path / "bare"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "bare"}\n', encoding="utf-8")
        assert create(plugin, "bare-all-in-one", include_all=False, force=False) == 1
        assert not (plugin / "agents").exists()

    def test_default_name_uses_the_architecture_vocabulary(self, tmp_path):
        """"mono" survives only as this script's historical NAME, never as a
        description of the architecture."""
        plugin = _make_plugin(tmp_path)
        assert _default_agent_name(plugin) == "tp-all-in-one"
