"""Tests for the the-skills-menu canon migration in the standardize --fix path.

Gap-closure (spec-sync Unit C): when ``standardize_plugin.py --force-templates``
runs on an EXISTING plugin, every agent is migrated to the-skills-menu method —
frontmatter ``skills:`` rewritten to ``[the-skills-menu]`` and the mandatory
dynamic-loading body instruction inserted — and a per-plugin
``skills/the-skills-menu/SKILL.md`` catalog is created if absent. This is the
canon UPGRADE verb (``--force-templates``), NOT plain ``--fix`` (which only adds
missing files and must leave agents untouched).

Every test is two-sided:
  (a) a static-``skills:`` agent IS migrated + gets the body instruction + a
      catalog file is created, under force_templates;
  (b) re-running is an idempotent no-op (no second body line);
  (c) plain ``--fix`` (force_templates=False) leaves agents UNTOUCHED;
  (d) an agent file lacking frontmatter is skipped + reported, not crashed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from standardize_plugin import (  # noqa: E402
    _SKILLS_MENU_BODY_INSTRUCTION,
    migrate_agents_to_skills_menu,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_skill(root: Path, name: str, description: str) -> Path:
    """Lay down a real skills/<name>/SKILL.md so the catalog can be POPULATED.

    Issue #150: the migration is now gated on a populated catalog — a plugin
    with zero real skills is NOT migrated. These tests assert the HAPPY path
    (an agent IS migrated), so the fixture must ship at least one real skill.
    """
    sk = root / "skills" / name
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nuser-invocable: false\n---\n\n# {name}\n\nBody.\n",
        encoding="utf-8",
    )
    return sk


def _make_plugin(tmp_path: Path) -> Path:
    """Lay down a minimal plugin tree with a manifest, agents/, and a REAL skill.

    The skill makes the the-skills-menu catalog populatable, so the migration
    proceeds (issue #150 gates migration on a non-empty catalog).
    """
    root = tmp_path / "plug"
    cp = root / ".claude-plugin"
    cp.mkdir(parents=True)
    (cp / "plugin.json").write_text(
        json.dumps({"name": "myplug", "version": "0.1.0", "description": "t", "author": "X"}, indent=2),
        encoding="utf-8",
    )
    (root / "agents").mkdir()
    (root / "skills").mkdir()
    _write_skill(root, "validation", "Validate a plugin tree.")
    return root


def _write_agent(root: Path, name: str, content: str) -> Path:
    p = root / "agents" / name
    p.write_text(content, encoding="utf-8")
    return p


_STATIC_AGENT = """\
---
name: fix-agent
description: Fixes plugin issues.
skills:
  - validation
  - security-scan
  - publish-pipeline
model: sonnet
---

# Fix Agent

You fix things in plugins.

## Workflow

Do the work.
"""

_FLOW_AGENT = """\
---
name: flow-agent
description: Inline skills list.
skills: [validation, security-scan]
model: sonnet
---

# Flow Agent

Body text here.
"""

_NO_FRONTMATTER_AGENT = """\
# Not An Agent

This file has no YAML frontmatter, so it is not a valid agent definition.
"""


# ---------------------------------------------------------------------------
# (a) a static-skills agent IS migrated + body instruction + catalog created
# ---------------------------------------------------------------------------


def test_static_skills_agent_is_migrated(tmp_path: Path) -> None:
    root = _make_plugin(tmp_path)
    agent = _write_agent(root, "fix-agent.md", _STATIC_AGENT)

    n = migrate_agents_to_skills_menu(root, dry_run=False)

    assert n == 1
    text = agent.read_text(encoding="utf-8")
    # frontmatter skills: rewritten to exactly [the-skills-menu] (block form)
    assert "skills:\n  - the-skills-menu" in text
    # old operational skills are gone from frontmatter
    fm = text.split("---", 2)[1]
    assert "validation" not in fm
    assert "security-scan" not in fm
    assert "publish-pipeline" not in fm
    # every other field preserved
    assert "name: fix-agent" in fm
    assert "model: sonnet" in fm
    # body instruction inserted (exactly once)
    assert text.count(_SKILLS_MENU_BODY_INSTRUCTION) == 1
    # the existing body content survived
    assert "## Workflow" in text


def test_flow_skills_agent_is_migrated(tmp_path: Path) -> None:
    root = _make_plugin(tmp_path)
    agent = _write_agent(root, "flow-agent.md", _FLOW_AGENT)

    n = migrate_agents_to_skills_menu(root, dry_run=False)

    assert n == 1
    text = agent.read_text(encoding="utf-8")
    assert "skills:\n  - the-skills-menu" in text
    fm = text.split("---", 2)[1]
    assert "validation" not in fm
    assert "security-scan" not in fm
    assert "[validation" not in fm
    assert _SKILLS_MENU_BODY_INSTRUCTION in text


def test_body_instruction_is_a_standalone_paragraph(tmp_path: Path) -> None:
    """The instruction renders as its own paragraph right after the H1."""
    root = _make_plugin(tmp_path)
    agent = _write_agent(root, "fix-agent.md", _STATIC_AGENT)

    migrate_agents_to_skills_menu(root, dry_run=False)

    text = agent.read_text(encoding="utf-8")
    # blank line before and after the instruction
    assert f"\n\n{_SKILLS_MENU_BODY_INSTRUCTION}\n\n" in text
    # placed after the H1, not before it
    h1_idx = text.index("# Fix Agent")
    instr_idx = text.index(_SKILLS_MENU_BODY_INSTRUCTION)
    assert h1_idx < instr_idx


def test_catalog_is_created_when_absent(tmp_path: Path) -> None:
    root = _make_plugin(tmp_path)
    _write_agent(root, "fix-agent.md", _STATIC_AGENT)
    catalog = root / "skills" / "the-skills-menu" / "SKILL.md"
    assert not catalog.exists()

    migrate_agents_to_skills_menu(root, dry_run=False)

    assert catalog.exists()
    body = catalog.read_text(encoding="utf-8")
    assert "name: the-skills-menu" in body
    assert "## Plugin Skills" in body
    # the catalog is namespaced to THIS plugin
    assert "myplug" in body


def test_catalog_is_populated_and_drops_allowed_tools(tmp_path: Path) -> None:
    """The created catalog is POPULATED from the real inventory, not the stub.

    Issue #150: the catalog must list every real skill (here ``validation``,
    seeded by _make_plugin) and must NOT carry the empty-stub placeholder
    "no operational skills yet"; it also drops the ``allowed-tools`` frontmatter
    (skills do not declare tools).
    """
    root = _make_plugin(tmp_path)
    _write_agent(root, "fix-agent.md", _STATIC_AGENT)

    migrate_agents_to_skills_menu(root, dry_run=False)

    catalog = (root / "skills" / "the-skills-menu" / "SKILL.md").read_text(encoding="utf-8")
    # the real skill is listed
    assert "`validation`" in catalog
    # the empty-stub placeholder is gone
    assert "no operational skills yet" not in catalog
    # no tool frontmatter
    fm = catalog.split("---", 2)[1]
    assert "allowed-tools" not in fm
    # still the standard catalog shape, namespaced to this plugin
    assert "## Plugin Skills" in catalog
    assert "myplug" in catalog


def test_existing_catalog_is_not_clobbered(tmp_path: Path) -> None:
    root = _make_plugin(tmp_path)
    _write_agent(root, "fix-agent.md", _STATIC_AGENT)
    catalog = root / "skills" / "the-skills-menu" / "SKILL.md"
    catalog.parent.mkdir(parents=True)
    sentinel = "---\nname: the-skills-menu\n---\n\n# hand curated\n"
    catalog.write_text(sentinel, encoding="utf-8")

    migrate_agents_to_skills_menu(root, dry_run=False)

    # mechanical standardize never refreshes a hand-curated catalog
    assert catalog.read_text(encoding="utf-8") == sentinel


# ---------------------------------------------------------------------------
# (b) re-running is an idempotent no-op (no second body line)
# ---------------------------------------------------------------------------


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    root = _make_plugin(tmp_path)
    agent = _write_agent(root, "fix-agent.md", _STATIC_AGENT)

    n1 = migrate_agents_to_skills_menu(root, dry_run=False)
    after_first = agent.read_text(encoding="utf-8")
    n2 = migrate_agents_to_skills_menu(root, dry_run=False)
    after_second = agent.read_text(encoding="utf-8")

    assert n1 == 1
    assert n2 == 0  # nothing left to migrate
    assert after_first == after_second  # byte-identical
    # exactly one body instruction — no duplicate
    assert after_second.count(_SKILLS_MENU_BODY_INSTRUCTION) == 1


# ---------------------------------------------------------------------------
# (c) plain --fix (force_templates=False) leaves agents UNTOUCHED
# ---------------------------------------------------------------------------


def test_plain_fix_leaves_agents_untouched(tmp_path: Path) -> None:
    """The migration is force-templates-only; plain --fix never invokes it.

    This asserts at the main() level that the agent is unchanged when
    --force-templates is NOT passed (only --fix).
    """
    import standardize_plugin

    root = _make_plugin(tmp_path)
    agent = _write_agent(root, "fix-agent.md", _STATIC_AGENT)
    original = agent.read_text(encoding="utf-8")

    argv = ["standardize_plugin.py", str(root), "--fix"]
    old_argv = sys.argv
    try:
        sys.argv = argv
        standardize_plugin.main()
    finally:
        sys.argv = old_argv

    # plain --fix must NOT migrate the agent
    assert agent.read_text(encoding="utf-8") == original
    assert _SKILLS_MENU_BODY_INSTRUCTION not in agent.read_text(encoding="utf-8")
    # and it must NOT create the the-skills-menu catalog
    assert not (root / "skills" / "the-skills-menu" / "SKILL.md").exists()


def test_force_templates_main_migrates_agents(tmp_path: Path) -> None:
    """The complement of (c): --force-templates at main() DOES migrate."""
    import standardize_plugin

    root = _make_plugin(tmp_path)
    agent = _write_agent(root, "fix-agent.md", _STATIC_AGENT)

    argv = ["standardize_plugin.py", str(root), "--force-templates"]
    old_argv = sys.argv
    try:
        sys.argv = argv
        standardize_plugin.main()
    finally:
        sys.argv = old_argv

    text = agent.read_text(encoding="utf-8")
    assert "skills:\n  - the-skills-menu" in text
    assert _SKILLS_MENU_BODY_INSTRUCTION in text
    assert (root / "skills" / "the-skills-menu" / "SKILL.md").exists()


# ---------------------------------------------------------------------------
# (d) an agent file lacking frontmatter is skipped + reported, not crashed
# ---------------------------------------------------------------------------


def test_agent_without_frontmatter_is_skipped(tmp_path: Path, capsys) -> None:
    root = _make_plugin(tmp_path)
    bad = _write_agent(root, "not-agent.md", _NO_FRONTMATTER_AGENT)
    good = _write_agent(root, "fix-agent.md", _STATIC_AGENT)
    bad_before = bad.read_text(encoding="utf-8")

    n = migrate_agents_to_skills_menu(root, dry_run=False)

    # only the well-formed agent migrated; the frontmatter-less file untouched
    assert n == 1
    assert bad.read_text(encoding="utf-8") == bad_before
    assert "skills:\n  - the-skills-menu" in good.read_text(encoding="utf-8")
    # the skip is reported for manual review (not a crash)
    out = capsys.readouterr().out
    assert "Manual review needed" in out
    assert "not-agent.md" in out


def test_no_agents_dir_is_no_op(tmp_path: Path) -> None:
    """A skill-only plugin (no agents/) WITH real skills still gets the catalog.

    The catalog is created (it has skills to list) and 0 agents migrate.
    """
    root = tmp_path / "skillonly"
    cp = root / ".claude-plugin"
    cp.mkdir(parents=True)
    (cp / "plugin.json").write_text(
        json.dumps({"name": "skillonly", "version": "0.1.0", "description": "t", "author": "X"}, indent=2),
        encoding="utf-8",
    )
    (root / "skills").mkdir()
    _write_skill(root, "do-thing", "Do a thing.")

    n = migrate_agents_to_skills_menu(root, dry_run=False)

    assert n == 0
    catalog = root / "skills" / "the-skills-menu" / "SKILL.md"
    assert catalog.exists()
    assert "`do-thing`" in catalog.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# dry-run does not write
# ---------------------------------------------------------------------------


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    root = _make_plugin(tmp_path)
    agent = _write_agent(root, "fix-agent.md", _STATIC_AGENT)
    original = agent.read_text(encoding="utf-8")

    n = migrate_agents_to_skills_menu(root, dry_run=True)

    assert n == 1  # would migrate one
    assert agent.read_text(encoding="utf-8") == original  # but wrote nothing
    assert not (root / "skills" / "the-skills-menu" / "SKILL.md").exists()
