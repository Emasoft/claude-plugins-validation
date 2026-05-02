#!/usr/bin/env python3
"""Tests for command consolidation (v2.8.0).

Validates that:
- 16 commands exist (8 direct-script + 6 agent + 2 v2.12.13 additions).
  Originally 13 after consolidation (8 direct-script + 5 agent).
- Direct-script commands have no agent: field
- Agent commands have the correct agent: field
- Old obsolete commands no longer exist in commands/
- Archived commands are in scripts_dev/commands_archive/
- Canonical-pipeline skill has correct frontmatter
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMMANDS_DIR = PROJECT_ROOT / "commands"
SKILLS_DIR = PROJECT_ROOT / "skills"
ARCHIVE_DIR = PROJECT_ROOT / "scripts_dev" / "commands_archive"


def _parse_frontmatter(path: Path) -> dict | None:
    """Parse YAML frontmatter from a markdown file."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    result: dict = yaml.safe_load(parts[1])
    return result


# --- Direct-script commands (no agent field) ---

DIRECT_SCRIPT_COMMANDS = [
    "cpv-validate-plugin",
    "cpv-validate-skill",
    "cpv-validate-github-plugin",
    "cpv-validate-github-marketplace",
    "cpv-doctor",
    "cpv-list-plugins",
    "cpv-bump-version",
    "cpv-version",
    "cpv-setup-branch-rules",
    "cpv-setup-branch-rules-generic",
    "cpv-validate-project-scope",
    "cpv-validate-local-scope",
]

# --- Agent commands (with agent field) ---

AGENT_COMMANDS = {
    "cpv-validate": "plugin-validator",
    "cpv-manage": "plugin-manager",
    "cpv-create": "plugin-creator",
    "cpv-fix-validation": "plugin-fixer",
    "cpv-fix-marketplace-validation": "marketplace-fixer",
    "cpv-semantic-validation": "semantic-validator",
}


class TestCommandCount:
    """Verify total command count after consolidation."""

    def test_total_command_count_is_24(self):
        """commands/ directory should contain exactly 24 .md files.

        Originally 13 after consolidation (8 direct + 5 agent).
        v2.12.13 added: cpv-link-plugin, cpv-validate-settings-marketplace.
        v2.12.x split: cpv-fix-marketplace-validation (routes to marketplace-fixer).
        v2.12.32 added: cpv-setup-branch-rules (server-side CI enforcement).
        v2.13.1 added: cpv-setup-branch-rules-generic (project-agnostic variant).
        v2.14.x added: cpv-validate-project-scope, cpv-validate-local-scope
          (TRDD-2be75e88 phases 2 and 3).
        v2.46.0 added: cpv-validate-cache (audit-only) and cpv-cache-optimize
          (cache-optimizer agent — audit + fix CA-01..CA-06 invalidation rules).
        v2.48.0 added: cpv-main-menu (single-entry menu routing all CPV
          commands via numbered Unicode tables — refactored away from AskUserQuestion).
        v2.50.0 added: cpv-codemod (deterministic mechanical-fix CLI — issue #17).
        """
        md_files = list(COMMANDS_DIR.glob("*.md"))
        assert len(md_files) == 24, f"Expected 24 commands, found {len(md_files)}: {sorted(f.name for f in md_files)}"


class TestDirectScriptCommands:
    """Verify direct-script commands exist and have no agent: field."""

    def test_all_direct_script_commands_exist(self):
        """All 8 direct-script commands must exist."""
        for name in DIRECT_SCRIPT_COMMANDS:
            assert (COMMANDS_DIR / f"{name}.md").is_file(), f"{name}.md missing"

    def test_direct_script_commands_have_no_agent(self):
        """Direct-script commands must not have an agent: field."""
        for name in DIRECT_SCRIPT_COMMANDS:
            fm = _parse_frontmatter(COMMANDS_DIR / f"{name}.md")
            assert fm is not None, f"{name}.md has no frontmatter"
            assert "agent" not in fm, f"{name}.md should not have agent: field, got '{fm.get('agent')}'"


class TestAgentCommands:
    """Verify agent commands exist and delegate to the correct agent."""

    def test_all_agent_commands_exist(self):
        """All 6 agent commands must exist."""
        for name in AGENT_COMMANDS:
            assert (COMMANDS_DIR / f"{name}.md").is_file(), f"{name}.md missing"

    def test_agent_commands_have_correct_agent(self):
        """Agent commands must have the correct agent: field."""
        for name, expected_agent in AGENT_COMMANDS.items():
            fm = _parse_frontmatter(COMMANDS_DIR / f"{name}.md")
            assert fm is not None, f"{name}.md has no frontmatter"
            assert fm.get("agent") == expected_agent, (
                f"{name}.md: expected agent: {expected_agent}, got {fm.get('agent')}"
            )


class TestObsoleteCommandsRemoved:
    """Verify obsolete commands no longer exist in commands/."""

    def test_old_individual_validators_removed(self):
        """Individual validator commands should be consolidated into cpv-validate."""
        for name in [
            "cpv-validate-hooks",
            "cpv-validate-agents",
            "cpv-validate-command",
            "cpv-validate-security",
            "cpv-validate-scoring",
            "cpv-validate-marketplace",
            "cpv-validate-enterprise",
            "cpv-validate-mcp",
            "cpv-validate-lsp",
            "cpv-validate-documentation",
            "cpv-validate-encoding",
            "cpv-validate-rules",
            "cpv-validate-xref",
        ]:
            assert not (COMMANDS_DIR / f"{name}.md").exists(), f"{name}.md should be archived"

    def test_old_management_commands_removed(self):
        """Individual management commands should be consolidated into cpv-manage."""
        for name in [
            "cpv-install-plugin-from-local-mp",
            "cpv-uninstall-plugin-from-local-mp",
            "cpv-update-plugin",
            "cpv-manage-remote-plugins",
            "cpv-enable-plugin",
            "cpv-disable-plugin",
            "cpv-list-mp-plugins",
            "cpv-search-plugins",
            "cpv-manage-marketplaces",
        ]:
            assert not (COMMANDS_DIR / f"{name}.md").exists(), f"{name}.md should be archived"

    def test_old_creation_commands_removed(self):
        """Individual creation commands should be consolidated into cpv-create."""
        for name in [
            "cpv-create-local-plugin",
            "cpv-create-local-marketplace",
            "cpv-publish-a-plugin-as-github-repo",
            "cpv-create-a-github-marketplace",
            "cpv-publish-a-plugin-to-a-github-marketplace",
            "cpv-standardize",
        ]:
            assert not (COMMANDS_DIR / f"{name}.md").exists(), f"{name}.md should be archived"


class TestArchivedCommands:
    """Verify archived commands are in scripts_dev/commands_archive/ (local only).

    scripts_dev/ is gitignored — these tests only run locally where the
    archive exists. In CI (clean clone), they are skipped automatically.
    """

    def test_archive_directory_exists(self):
        """scripts_dev/commands_archive/ must exist locally (skipped in CI)."""
        if not ARCHIVE_DIR.is_dir():
            pytest.skip("scripts_dev/commands_archive/ not present (gitignored, CI environment)")
        assert ARCHIVE_DIR.is_dir()

    def test_archived_commands_count(self):
        """Archive should contain the 28 moved commands (skipped in CI)."""
        if not ARCHIVE_DIR.is_dir():
            pytest.skip("scripts_dev/commands_archive/ not present (gitignored, CI environment)")
        archived = list(ARCHIVE_DIR.glob("*.md"))
        assert len(archived) >= 25, f"Expected 25+ archived commands, found {len(archived)}"


class TestCanonicalPipelineSkill:
    """Verify the canonical-pipeline skill."""

    def test_skill_directory_exists(self):
        """skills/canonical-pipeline/ directory must exist."""
        assert (SKILLS_DIR / "canonical-pipeline").is_dir()

    def test_skill_md_has_name_field(self):
        """canonical-pipeline/SKILL.md frontmatter must have name: canonical-pipeline."""
        fm = _parse_frontmatter(SKILLS_DIR / "canonical-pipeline" / "SKILL.md")
        assert fm is not None
        assert fm.get("name") == "canonical-pipeline"


AGENTS_DIR = PROJECT_ROOT / "agents"


class TestSkillAgentArchitecture:
    """Enforce the agent-first architecture invariants.

    All skills must be non-user-invocable and loaded only by agents.
    Every "Loaded by X agent" claim in a skill description must be
    backed by an actual agent that lists the skill in its frontmatter.
    """

    def test_all_skills_are_non_user_invocable(self):
        """Every SKILL.md must have user-invocable: false."""
        for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
            fm = _parse_frontmatter(skill_md)
            assert fm is not None, f"{skill_md} has no frontmatter"
            assert fm.get("user-invocable") is False, (
                f"{skill_md.parent.name}: user-invocable must be false, got {fm.get('user-invocable')!r}"
            )

    def test_all_agents_declare_skills_list(self):
        """Every agent must have a skills: list in its frontmatter."""
        for agent_md in AGENTS_DIR.glob("*.md"):
            fm = _parse_frontmatter(agent_md)
            assert fm is not None, f"{agent_md} has no frontmatter"
            skills = fm.get("skills")
            assert isinstance(skills, list) and skills, (
                f"{agent_md.name}: skills must be a non-empty list, got {skills!r}"
            )

    def test_every_skill_is_loaded_by_at_least_one_agent(self):
        """Each skill must be referenced by at least one agent's skills: list."""
        # Gather all skill names declared by agents
        loaded = set()
        for agent_md in AGENTS_DIR.glob("*.md"):
            fm = _parse_frontmatter(agent_md) or {}
            for s in fm.get("skills", []) or []:
                loaded.add(s)
        # Every skill directory must appear in some agent's skills list
        for skill_dir in SKILLS_DIR.iterdir():
            if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").exists():
                continue
            assert skill_dir.name in loaded, (
                f"Skill {skill_dir.name} is not loaded by any agent — orphaned skill"
            )

    def test_loaded_by_claims_match_actual_agents(self):
        """Each 'Loaded by X agent' claim in a skill must map to an agent that loads it."""
        import re

        loaded_by_pattern = re.compile(r"Loaded by ([a-z0-9, -]+?) agents?\b", re.IGNORECASE)
        # Build reverse index: skill -> set(agents that load it)
        skill_to_agents: dict[str, set[str]] = {}
        for agent_md in AGENTS_DIR.glob("*.md"):
            fm = _parse_frontmatter(agent_md) or {}
            agent_name = fm.get("name") or agent_md.stem
            for s in fm.get("skills", []) or []:
                skill_to_agents.setdefault(s, set()).add(agent_name)
        # For each skill, check its description claim
        for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
            fm = _parse_frontmatter(skill_md)
            if not fm:
                continue
            desc = fm.get("description", "")
            if isinstance(desc, list):
                desc = " ".join(desc)
            match = loaded_by_pattern.search(desc or "")
            if not match:
                continue
            claimed_raw = match.group(1)
            claimed = {
                c.strip().removesuffix(" and").strip()
                for c in claimed_raw.replace(",", " ").replace(" and ", " ").split()
                if c.strip()
            }
            skill_name = skill_md.parent.name
            actual = skill_to_agents.get(skill_name, set())
            missing = claimed - actual
            assert not missing, (
                f"{skill_name}: description claims 'Loaded by {claimed_raw}' "
                f"but these agents do not list the skill: {sorted(missing)}. "
                f"Actual loaders: {sorted(actual)}"
            )
