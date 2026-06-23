"""Issue #150 — `standardize --force-templates` the-skills-menu migration.

The v2.143.0 migration reported SUCCESS but produced a BROKEN agent:
  1. it wrote an EMPTY-stub `skills/the-skills-menu/SKILL.md` ("no operational
     skills yet") even on a plugin with many real skills under `skills/`, and
  2. it stripped the agent's `skills:` frontmatter down to `[the-skills-menu]`
     anyway — so the agent lost its core skills AND could not discover them via
     the (empty) menu.

The fix (scripts/standardize_plugin.py):
  - the catalog's `## Plugin Skills` table is POPULATED from the real
    `skills/<name>/SKILL.md` inventory;
  - the migration is GATED on a populatable catalog — with ZERO real skills and
    no pre-existing catalog, NO agent is stripped and NO success is reported (a
    WARNING is emitted instead);
  - the generated catalog drops the `allowed-tools` frontmatter (skills carry no
    tool frontmatter — the tool surface is dynamic).

Every test below is two-sided.
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
    scan_plugin_skills_inventory,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MAIN_AGENT = """\
---
name: main-agent
description: The main agent for this plugin.
skills:
  - alpha-skill
  - beta-skill
  - gamma-skill
model: sonnet
---

# Main Agent

You orchestrate the plugin.

## Workflow

Do the work.
"""


def _make_manifest(root: Path, name: str) -> None:
    cp = root / ".claude-plugin"
    cp.mkdir(parents=True)
    (cp / "plugin.json").write_text(
        json.dumps({"name": name, "version": "0.1.0", "description": "t", "author": "X"}, indent=2),
        encoding="utf-8",
    )


def _write_skill(root: Path, name: str, description: str) -> None:
    sk = root / "skills" / name
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nuser-invocable: false\n---\n\n# {name}\n\nBody.\n",
        encoding="utf-8",
    )


def _plugin_with_skills(tmp_path: Path, skill_names: list[str]) -> Path:
    root = tmp_path / "plug-with-skills"
    _make_manifest(root, "withskills")
    (root / "agents").mkdir()
    (root / "skills").mkdir()
    for n in skill_names:
        _write_skill(root, n, f"Does {n} things.")
    (root / "agents" / "main-agent.md").write_text(_MAIN_AGENT, encoding="utf-8")
    return root


def _plugin_without_skills(tmp_path: Path) -> Path:
    root = tmp_path / "plug-no-skills"
    _make_manifest(root, "noskills")
    (root / "agents").mkdir()
    (root / "skills").mkdir()  # empty — genuinely no operational skills
    (root / "agents" / "main-agent.md").write_text(_MAIN_AGENT, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Inventory scanner (the population source)
# ---------------------------------------------------------------------------


def test_inventory_lists_every_real_skill_excluding_the_menu(tmp_path: Path) -> None:
    root = _plugin_with_skills(tmp_path, ["alpha-skill", "beta-skill", "gamma-skill"])
    # a the-skills-menu dir present alongside must NOT be listed by the inventory
    _write_skill(root, "the-skills-menu", "the catalog itself")

    names = [n for n, _desc in scan_plugin_skills_inventory(root)]

    assert names == ["alpha-skill", "beta-skill", "gamma-skill"]
    assert "the-skills-menu" not in names


def test_inventory_empty_when_no_skills(tmp_path: Path) -> None:
    root = _plugin_without_skills(tmp_path)
    assert scan_plugin_skills_inventory(root) == []


def test_inventory_falls_back_to_dir_name_when_frontmatter_name_missing(tmp_path: Path) -> None:
    """A skill with no parseable `name:` is still discovered by its dir name."""
    root = _plugin_with_skills(tmp_path, ["alpha-skill"])
    nameless = root / "skills" / "nameless-skill"
    nameless.mkdir(parents=True)
    (nameless / "SKILL.md").write_text("# nameless\n\nNo frontmatter here.\n", encoding="utf-8")

    names = [n for n, _desc in scan_plugin_skills_inventory(root)]
    assert "nameless-skill" in names  # never silently dropped


# ---------------------------------------------------------------------------
# (a) plugin WITH N skills — catalog lists all N, agent NOT stripped-to-empty
# ---------------------------------------------------------------------------


def test_catalog_lists_all_real_skills(tmp_path: Path) -> None:
    skills = ["alpha-skill", "beta-skill", "gamma-skill"]
    root = _plugin_with_skills(tmp_path, skills)

    n = migrate_agents_to_skills_menu(root, dry_run=False)

    assert n == 1  # the one agent migrated
    catalog = (root / "skills" / "the-skills-menu" / "SKILL.md").read_text(encoding="utf-8")
    # EVERY real skill is listed (not the empty placeholder)
    for s in skills:
        assert f"`{s}`" in catalog
    # the empty-stub text is gone
    assert "no operational skills yet" not in catalog
    # the descriptions made it into the table too
    assert "Does alpha-skill things" in catalog


def test_agent_migrated_to_populated_menu_not_stripped_to_empty(tmp_path: Path) -> None:
    """With a populated catalog the agent IS switched to rely on the menu.

    The frontmatter `skills:` becomes exactly `[the-skills-menu]` (the canonical
    relies-on-the-populated-menu form) and the body gains the dynamic-load
    instruction — the agent is NOT left stripped-to-empty-with-no-menu (the #150
    broken state); the menu it now relies on actually lists its skills.
    """
    root = _plugin_with_skills(tmp_path, ["alpha-skill", "beta-skill", "gamma-skill"])
    agent = root / "agents" / "main-agent.md"

    migrate_agents_to_skills_menu(root, dry_run=False)

    text = agent.read_text(encoding="utf-8")
    fm = text.split("---", 2)[1]
    # switched to the menu...
    assert "skills:\n  - the-skills-menu" in text
    # ...and the body teaches dynamic loading
    assert _SKILLS_MENU_BODY_INSTRUCTION in text
    # the old skills are no longer PRE-loaded in frontmatter (they live in the menu now)
    assert "alpha-skill" not in fm
    # but the catalog it now points to DOES list them — so they are still reachable
    catalog = (root / "skills" / "the-skills-menu" / "SKILL.md").read_text(encoding="utf-8")
    assert "`alpha-skill`" in catalog
    assert "`beta-skill`" in catalog
    assert "`gamma-skill`" in catalog


# ---------------------------------------------------------------------------
# (b) plugin with ZERO skills — NO strip, NO success (warns instead)
# ---------------------------------------------------------------------------


def test_zero_skills_does_not_strip_agent(tmp_path: Path) -> None:
    root = _plugin_without_skills(tmp_path)
    agent = root / "agents" / "main-agent.md"
    before = agent.read_text(encoding="utf-8")

    n = migrate_agents_to_skills_menu(root, dry_run=False)

    assert n == 0  # nothing migrated
    after = agent.read_text(encoding="utf-8")
    # the agent is UNCHANGED — its core skills are preserved
    assert after == before
    assert "alpha-skill" in after
    assert "beta-skill" in after
    assert "gamma-skill" in after
    # no body instruction was injected
    assert _SKILLS_MENU_BODY_INSTRUCTION not in after
    # and no empty stub catalog was written
    assert not (root / "skills" / "the-skills-menu" / "SKILL.md").exists()


def test_zero_skills_warns_and_does_not_report_success(tmp_path: Path, capsys) -> None:
    root = _plugin_without_skills(tmp_path)

    migrate_agents_to_skills_menu(root, dry_run=False)

    out = capsys.readouterr().out
    # a clear WARNING is emitted...
    assert "SKIPPED" in out
    assert "the-skills-menu-create" in out  # tells the user how to fix it
    # ...and NO success line ("created … catalog" / "migrated … →") is printed
    assert "the-skills-menu catalog" not in out
    assert "→ the-skills-menu" not in out


def test_zero_skills_main_does_not_report_all_already_migrated(tmp_path: Path, capsys) -> None:
    """At main() level, the empty-catalog skip must NOT print the success line."""
    import standardize_plugin

    root = _plugin_without_skills(tmp_path)
    agent = root / "agents" / "main-agent.md"
    before = agent.read_text(encoding="utf-8")

    argv = ["standardize_plugin.py", str(root), "--force-templates"]
    old_argv = sys.argv
    try:
        sys.argv = argv
        standardize_plugin.main()
    finally:
        sys.argv = old_argv

    out = capsys.readouterr().out
    # the misleading "All agents already on the-skills-menu" success is NOT printed
    assert "All agents already on the-skills-menu" not in out
    # the skip warning IS printed
    assert "SKIPPED" in out
    # and the agent is still untouched
    assert agent.read_text(encoding="utf-8") == before


def test_with_skills_main_does_report_progress(tmp_path: Path, capsys) -> None:
    """Complement of the skip case: a real-skill plugin migrates + reports it."""
    import standardize_plugin

    root = _plugin_with_skills(tmp_path, ["alpha-skill"])

    argv = ["standardize_plugin.py", str(root), "--force-templates"]
    old_argv = sys.argv
    try:
        sys.argv = argv
        standardize_plugin.main()
    finally:
        sys.argv = old_argv

    out = capsys.readouterr().out
    assert "SKIPPED" not in out
    assert "the-skills-menu catalog" in out
    assert (root / "skills" / "the-skills-menu" / "SKILL.md").exists()


# ---------------------------------------------------------------------------
# (c) generated menu skill has NO allowed-tools frontmatter
# ---------------------------------------------------------------------------


def test_generated_menu_has_no_allowed_tools_frontmatter(tmp_path: Path) -> None:
    root = _plugin_with_skills(tmp_path, ["alpha-skill"])

    migrate_agents_to_skills_menu(root, dry_run=False)

    catalog = (root / "skills" / "the-skills-menu" / "SKILL.md").read_text(encoding="utf-8")
    fm = catalog.split("---", 2)[1]
    assert "allowed-tools" not in fm
    # sanity: the rest of the standard frontmatter survives
    assert "name: the-skills-menu" in fm
    assert "user-invocable: false" in fm


# ---------------------------------------------------------------------------
# pre-existing hand-curated catalog: migration still proceeds (its contents
# are the author's business), and is NOT clobbered
# ---------------------------------------------------------------------------


def test_existing_catalog_lets_migration_proceed_even_with_no_skills(tmp_path: Path) -> None:
    """A hand-curated catalog is a valid menu — migrate even if skills/ is bare."""
    root = _plugin_without_skills(tmp_path)
    catalog = root / "skills" / "the-skills-menu" / "SKILL.md"
    catalog.parent.mkdir(parents=True)
    sentinel = "---\nname: the-skills-menu\nuser-invocable: false\n---\n\n# hand curated\n"
    catalog.write_text(sentinel, encoding="utf-8")
    agent = root / "agents" / "main-agent.md"

    n = migrate_agents_to_skills_menu(root, dry_run=False)

    assert n == 1  # migration proceeded — a usable catalog exists
    assert "skills:\n  - the-skills-menu" in agent.read_text(encoding="utf-8")
    # the hand-curated catalog is left untouched
    assert catalog.read_text(encoding="utf-8") == sentinel


# ---------------------------------------------------------------------------
# dry-run with real skills: would migrate, writes nothing
# ---------------------------------------------------------------------------


def test_dry_run_with_skills_would_migrate_writes_nothing(tmp_path: Path) -> None:
    root = _plugin_with_skills(tmp_path, ["alpha-skill"])
    agent = root / "agents" / "main-agent.md"
    before = agent.read_text(encoding="utf-8")

    n = migrate_agents_to_skills_menu(root, dry_run=True)

    assert n == 1  # would migrate one
    assert agent.read_text(encoding="utf-8") == before  # but wrote nothing
    assert not (root / "skills" / "the-skills-menu" / "SKILL.md").exists()
