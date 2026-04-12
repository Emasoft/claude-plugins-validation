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

    def test_total_command_count_is_16(self):
        """commands/ directory should contain exactly 16 .md files.

        Originally 13 after consolidation (8 direct + 5 agent).
        v2.12.13 added: cpv-link-plugin, cpv-validate-settings-marketplace.
        v2.12.x split: cpv-fix-marketplace-validation (routes to marketplace-fixer).
        """
        md_files = list(COMMANDS_DIR.glob("*.md"))
        assert len(md_files) == 16, f"Expected 16 commands, found {len(md_files)}: {sorted(f.name for f in md_files)}"


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
            "cpv-validate-hooks", "cpv-validate-agents", "cpv-validate-command",
            "cpv-validate-security", "cpv-validate-scoring", "cpv-validate-marketplace",
            "cpv-validate-enterprise", "cpv-validate-mcp", "cpv-validate-lsp",
            "cpv-validate-documentation", "cpv-validate-encoding", "cpv-validate-rules",
            "cpv-validate-xref",
        ]:
            assert not (COMMANDS_DIR / f"{name}.md").exists(), f"{name}.md should be archived"

    def test_old_management_commands_removed(self):
        """Individual management commands should be consolidated into cpv-manage."""
        for name in [
            "cpv-install-plugin-from-local-mp", "cpv-uninstall-plugin-from-local-mp",
            "cpv-update-plugin", "cpv-manage-remote-plugins", "cpv-enable-plugin",
            "cpv-disable-plugin", "cpv-list-mp-plugins", "cpv-search-plugins",
            "cpv-manage-marketplaces",
        ]:
            assert not (COMMANDS_DIR / f"{name}.md").exists(), f"{name}.md should be archived"

    def test_old_creation_commands_removed(self):
        """Individual creation commands should be consolidated into cpv-create."""
        for name in [
            "cpv-create-local-plugin", "cpv-create-local-marketplace",
            "cpv-publish-a-plugin-as-github-repo", "cpv-create-a-github-marketplace",
            "cpv-publish-a-plugin-to-a-github-marketplace", "cpv-standardize",
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
