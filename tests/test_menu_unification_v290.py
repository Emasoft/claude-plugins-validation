#!/usr/bin/env python3
"""Pinning tests for the v2.90.0 menu unification (TRDD-c50531c2).

Three landmark architectural decisions land in v2.90.0 and each one needs
a permanent regression-pin:

1. **Single user-visible slash command.** The ~30 user-facing slash
   commands collapse to ONE: ``/cpv-main-menu``. Every other workflow is
   routed through that menu to its underlying agent.

2. **14 commands convert to ``user-invocable: false`` skills.** Component-
   creation commands (cpv-create-skill, cpv-create-agent, cpv-pack-components,
   etc.) become skills with the same role, loaded by agents — invisible to
   the user-facing skill picker.

3. **5 agents lose their First Contact menu blocks.** Agents that
   previously rendered their own first-contact menu now accept dispatch
   args from /cpv-main-menu instead. The First Contact section is gone
   from plugin-creator, plugin-manager, plugin-validator,
   skill-validation-agent, semantic-validator.

4. **Top-level menu is 8 canonical categories.** Both commands/cpv-main-menu.md
   and skills/cpv-main-menu-skill/references/menu-tree.md MUST contain
   exactly the 8 canonical category labels (Validate / Fix / Optimize
   for Cache / Diagnose / Update / Create / Publish & Migrate / Manage)
   as top-level rows — and MUST NOT carry the pre-v2.90.0 labels
   (Validate from GitHub, GitHub setup, Deep semantic analysis, Doctor
   (deep diagnostic)) as top-level rows.

These tests run against the real on-disk files (no mocks, no stubs).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
COMMANDS_DIR = PLUGIN_ROOT / "commands"
SKILLS_DIR = PLUGIN_ROOT / "skills"
AGENTS_DIR = PLUGIN_ROOT / "agents"

MAIN_MENU_CMD = COMMANDS_DIR / "cpv-main-menu.md"
MAIN_MENU_TREE = SKILLS_DIR / "cpv-main-menu-skill" / "references" / "menu-tree.md"

# The 14 commands converted to `user-invocable: false` skills in v2.90.0
# (TRDD-c50531c2). Each entry maps the new skill folder name → its source
# of truth at skills/<name>/SKILL.md. The OLD cpv-XXX command file is
# pinned absent by tests/test_consolidation_v211.py.
V290_NEW_SKILLS = [
    "add-component-to-plugin",  # ← cpv-add-component
    "add-dependency",            # ← cpv-add-dependency
    "add-hook",                  # ← cpv-create-hook
    "bump-version",              # ← cpv-bump-version
    "deterministic-codemod",     # ← cpv-codemod
    "link-plugin-marketplace",   # ← cpv-link-plugin
    "pack-components",           # ← cpv-pack-components
    "refresh-readme",            # ← cpv-refresh-readme
    "register-mcp",              # ← cpv-create-mcp
    "scaffold-agent",            # ← cpv-create-agent
    "scaffold-command",          # ← cpv-create-command
    "scaffold-skill",            # ← cpv-create-skill
    "show-version",              # ← cpv-version
    "strip-dev-submodules",      # ← cpv-strip-dev-parts
]

# Agents whose ## First Contact section was stripped in v2.90.0. They now
# accept dispatch args from /cpv-main-menu instead of rendering their own
# first-contact menu.
V290_AGENTS_WITHOUT_FIRST_CONTACT = [
    "plugin-creator.md",
    "plugin-manager.md",
    "plugin-validator.md",
    "skill-validation-agent.md",
    "semantic-validator.md",
]

# The 8 canonical top-level category labels — these MUST appear in both
# commands/cpv-main-menu.md AND
# skills/cpv-main-menu-skill/references/menu-tree.md as the top-level
# menu rows.
V290_CANONICAL_CATEGORIES = [
    "Validate",
    "Fix",
    "Optimize for Cache",
    "Diagnose",
    "Update",
    "Create",
    "Publish & Migrate",
    "Manage",
]

# Top-level row labels that existed in pre-v2.90.0 menus and MUST NOT
# re-appear as top-level rows in either of the menu sources. These have
# either been merged into one of the 8 canonical categories or pushed to
# sub-leaves.
V290_FORBIDDEN_TOP_LEVEL_LABELS = [
    "Validate from GitHub",        # → sub-leaf of Validate
    "GitHub setup",                # → folded into Publish & Migrate
    "Deep semantic analysis",      # → leaf of Diagnose
    "Doctor (deep diagnostic)",    # → folded into Diagnose
]


def _parse_frontmatter(path: Path) -> dict | None:
    """Parse YAML frontmatter from a markdown file."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    result = yaml.safe_load(parts[1])
    return result if isinstance(result, dict) else None


# ---------------------------------------------------------------------------
# Invariant 1: only ONE slash command remains
# ---------------------------------------------------------------------------


def test_only_one_slash_command_remains() -> None:
    """commands/ MUST contain only the documented entry points.

    Per TRDD-c50531c2 the 23 user-facing slash commands and the 14
    component-creation commands were either deleted or converted to
    ``user-invocable: false`` skills. ``cpv-main-menu.md`` is the
    discovery surface for routine work.

    Per TRDD-71e68ab5 (v2.91.0) ``cpv-batch-fix.md`` was added as a
    direct-entry power-user command — the doctor recommends it by exact
    name when a plugin has 100+ findings, and forcing the user to
    re-navigate the menu would defeat that recommendation.

    Per TRDD-9dd64dbf (v2.95.0) ``the-skills-menu-create.md`` was added
    as a direct-entry universal migrator — it operates on OTHER plugins
    so menu navigation inside CPV is a UX dead end.

    Any new direct-entry command requires its own TRDD documenting the
    exemption.
    """
    allowed = {"cpv-main-menu.md", "cpv-batch-fix.md", "the-skills-menu-create.md"}
    md_files = list(COMMANDS_DIR.glob("*.md"))
    actual = {f.name for f in md_files}
    unexpected = actual - allowed
    missing = allowed - actual
    assert not unexpected and not missing, (
        f"commands/ must contain exactly {sorted(allowed)} (TRDD-c50531c2 + TRDD-71e68ab5 + TRDD-9dd64dbf). "
        f"Unexpected: {sorted(unexpected)}. Missing: {sorted(missing)}."
    )
    assert MAIN_MENU_CMD.is_file(), (
        f"cpv-main-menu.md MUST exist at {MAIN_MENU_CMD}. Found: {sorted(actual)}"
    )


# ---------------------------------------------------------------------------
# Invariant 2: 14 new skills are user-invocable: false
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skill_name", V290_NEW_SKILLS)
def test_14_new_skills_are_user_invocable_false(skill_name: str) -> None:
    """Each of the 14 v2.90.0 converted-from-commands skills MUST exist
    and MUST declare ``user-invocable: false``.

    Per TRDD-c50531c2 these skills replace the deleted cpv-XXX commands
    of the same role. They are loaded by agents (not directly by users),
    so they must be hidden from the user-facing skill picker.
    """
    skill_md = SKILLS_DIR / skill_name / "SKILL.md"
    assert skill_md.is_file(), (
        f"Skill {skill_name} MUST exist at {skill_md} — it is the v2.90.0 "
        f"replacement for a deleted cpv-XXX command (per TRDD-c50531c2)."
    )
    fm = _parse_frontmatter(skill_md)
    assert fm is not None, f"{skill_md} has no parseable YAML frontmatter"
    assert fm.get("user-invocable") is False, (
        f"{skill_name}/SKILL.md MUST declare `user-invocable: false`. "
        f"Per TRDD-c50531c2 it is a private skill loaded by agents, not "
        f"a user-facing entry point. Current value: "
        f"{fm.get('user-invocable')!r}."
    )


# ---------------------------------------------------------------------------
# Invariant 3: 5 agents have NO ## First Contact section
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent_filename", V290_AGENTS_WITHOUT_FIRST_CONTACT)
def test_5_agents_have_no_first_contact_menu(agent_filename: str) -> None:
    """Each of the 5 v2.90.0 stripped agents MUST NOT contain a
    ``## First Contact`` markdown section in its body.

    Per TRDD-c50531c2 these agents previously rendered their own
    first-contact menu (asking the user "what do you want to do?").
    That responsibility now belongs exclusively to /cpv-main-menu —
    the agents accept dispatch args and execute the chosen workflow
    directly. Leaving a First Contact section in place risks the user
    being re-prompted after dispatch from the main menu.

    Bare prose mentions of the phrase are allowed (they may document
    what was MOVED). The fail is on the SECTION HEADER specifically.
    """
    agent_md = AGENTS_DIR / agent_filename
    assert agent_md.is_file(), f"agent file missing: {agent_md}"
    body = agent_md.read_text(encoding="utf-8")
    has_first_contact_section = any(
        line.startswith("## First Contact") for line in body.splitlines()
    )
    assert not has_first_contact_section, (
        f"{agent_filename} contains a `## First Contact` section. Per "
        f"TRDD-c50531c2 (v2.90.0 menu unification) this section MUST be "
        f"removed — the agent now accepts dispatch args from "
        f"/cpv-main-menu rather than rendering its own first-contact "
        f"menu. Bare prose mentions are allowed; only the section "
        f"header is forbidden."
    )


# ---------------------------------------------------------------------------
# Invariant 4: top-level menu is the 8 canonical categories
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", V290_CANONICAL_CATEGORIES)
def test_top_level_menu_cpv_main_menu_contains_canonical_category(label: str) -> None:
    """commands/cpv-main-menu.md MUST contain each of the 8 canonical
    top-level category labels verbatim."""
    body = MAIN_MENU_CMD.read_text(encoding="utf-8")
    assert label in body, (
        f"cpv-main-menu.md does not contain canonical top-level category "
        f"label '{label}'. Per TRDD-c50531c2 (v2.90.0) all 8 categories "
        f"(Validate / Fix / Optimize for Cache / Diagnose / Update / "
        f"Create / Publish & Migrate / Manage) MUST appear in the "
        f"top-level menu of cpv-main-menu.md."
    )


@pytest.mark.parametrize("label", V290_CANONICAL_CATEGORIES)
def test_top_level_menu_menu_tree_contains_canonical_category(label: str) -> None:
    """skills/cpv-main-menu-skill/references/menu-tree.md MUST contain
    each of the 8 canonical top-level category labels verbatim."""
    body = MAIN_MENU_TREE.read_text(encoding="utf-8")
    assert label in body, (
        f"menu-tree.md does not contain canonical top-level category "
        f"label '{label}'. Per TRDD-c50531c2 (v2.90.0) all 8 categories "
        f"MUST appear in the §3.0 top-level menu of menu-tree.md so the "
        f"menu rendered to the user matches the spec."
    )


@pytest.mark.parametrize("label", V290_FORBIDDEN_TOP_LEVEL_LABELS)
def test_top_level_menu_cpv_main_menu_excludes_forbidden_label_as_row(label: str) -> None:
    """commands/cpv-main-menu.md MUST NOT carry any of the pre-v2.90.0
    top-level row labels AS A TOP-LEVEL ROW.

    These labels may still appear in body prose (e.g. as historical
    notes or sub-menu references), so we check only the top-level
    table — the line shape ``│ N │ <label>`` (with any number).
    """
    body = MAIN_MENU_CMD.read_text(encoding="utf-8")
    # Match any top-level row of the menu: leading "│ " then a single
    # alphanumeric (digit or letter), then " │ <label>".
    import re

    pattern = re.compile(r"^│\s+[0-9A-Za-z]\s+│\s+" + re.escape(label) + r"\b", re.MULTILINE)
    match = pattern.search(body)
    assert match is None, (
        f"cpv-main-menu.md contains forbidden pre-v2.90.0 top-level row "
        f"label '{label}'. Per TRDD-c50531c2 this label MUST NOT appear "
        f"as a top-level row — it has been merged into one of the 8 "
        f"canonical categories or pushed to a sub-leaf. Matched line: "
        f"{match.group(0)!r}."
    )


@pytest.mark.parametrize("label", V290_FORBIDDEN_TOP_LEVEL_LABELS)
def test_top_level_menu_menu_tree_excludes_forbidden_label_as_row(label: str) -> None:
    """skills/cpv-main-menu-skill/references/menu-tree.md MUST NOT carry
    any of the pre-v2.90.0 top-level row labels in its §3.0 top-level
    table.

    These labels may still appear elsewhere in the file (e.g. in §3.7
    sub-menu headings or in historical comments), so we check only the
    §3.0 top-level table specifically.
    """
    body = MAIN_MENU_TREE.read_text(encoding="utf-8")
    # Extract the §3.0 top-level menu block. It starts after the heading
    # `### 3.0 Top-level menu` and ends at the next ``### `` heading.
    lines = body.splitlines()
    in_top_level = False
    top_level_block: list[str] = []
    for line in lines:
        if line.startswith("### 3.0 Top-level menu"):
            in_top_level = True
            continue
        if in_top_level:
            if line.startswith("### "):
                break
            top_level_block.append(line)
    assert top_level_block, (
        "menu-tree.md is missing its §3.0 Top-level menu section — the "
        "anchor `### 3.0 Top-level menu` was not found. Cannot perform "
        "the top-level row label forbidden check."
    )
    block_text = "\n".join(top_level_block)
    # Same pattern as the cpv-main-menu check.
    import re

    pattern = re.compile(r"^│\s+[0-9A-Za-z]\s+│\s+" + re.escape(label) + r"\b", re.MULTILINE)
    match = pattern.search(block_text)
    assert match is None, (
        f"menu-tree.md §3.0 Top-level menu contains forbidden "
        f"pre-v2.90.0 top-level row label '{label}'. Per TRDD-c50531c2 "
        f"this label MUST NOT appear as a §3.0 top-level row. Matched "
        f"line: {match.group(0)!r}."
    )
