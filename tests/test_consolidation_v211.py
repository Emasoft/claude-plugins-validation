#!/usr/bin/env python3
"""Tests for v2.1.1 command consolidation.

Validates that:
- New renamed/created commands have correct YAML frontmatter
- Old obsolete commands no longer exist in commands/
- Canonical-pipeline skill has correct frontmatter
- Total command count is 37 (down from 43)
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMMANDS_DIR = PROJECT_ROOT / "commands"
SKILLS_DIR = PROJECT_ROOT / "skills"


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


class TestNewCommandsExist:
    """Verify new/renamed command files exist."""

    def test_create_local_plugin_exists(self):
        """cpv-create-local-plugin.md exists in commands/."""
        assert (COMMANDS_DIR / "cpv-create-local-plugin.md").is_file()

    def test_create_local_marketplace_exists(self):
        """cpv-create-local-marketplace.md exists in commands/."""
        assert (COMMANDS_DIR / "cpv-create-local-marketplace.md").is_file()

    def test_standardize_exists(self):
        """cpv-standardize.md exists in commands/."""
        assert (COMMANDS_DIR / "cpv-standardize.md").is_file()


class TestObsoleteCommandsRemoved:
    """Verify obsolete commands no longer exist in commands/."""

    def test_setup_plugin_repo_removed(self):
        """cpv-setup-plugin-repo.md should not exist in commands/."""
        assert not (COMMANDS_DIR / "cpv-setup-plugin-repo.md").exists()

    def test_setup_github_marketplace_removed(self):
        """cpv-setup-github-marketplace.md should not exist in commands/."""
        assert not (COMMANDS_DIR / "cpv-setup-github-marketplace.md").exists()

    def test_publish_to_marketplace_removed(self):
        """cpv-publish-to-marketplace.md should not exist in commands/."""
        assert not (COMMANDS_DIR / "cpv-publish-to-marketplace.md").exists()

    def test_audit_github_plugin_removed(self):
        """cpv-audit-github-plugin.md should not exist in commands/."""
        assert not (COMMANDS_DIR / "cpv-audit-github-plugin.md").exists()

    def test_audit_security_removed(self):
        """cpv-audit-security.md should not exist in commands/."""
        assert not (COMMANDS_DIR / "cpv-audit-security.md").exists()

    def test_standardize_marketplace_removed(self):
        """cpv-standardize-marketplace.md should not exist in commands/."""
        assert not (COMMANDS_DIR / "cpv-standardize-marketplace.md").exists()

    def test_standardize_plugin_removed(self):
        """cpv-standardize-plugin.md should not exist in commands/."""
        assert not (COMMANDS_DIR / "cpv-standardize-plugin.md").exists()

    def test_old_publish_as_github_repo_removed(self):
        """cpv-publish-as-github-repo.md replaced by cpv-publish-a-plugin-as-github-repo.md."""
        assert not (COMMANDS_DIR / "cpv-publish-as-github-repo.md").exists()

    def test_old_publish_plugin_to_marketplace_removed(self):
        """cpv-publish-plugin-to-marketplace.md replaced by cpv-publish-a-plugin-to-a-github-marketplace.md."""
        assert not (COMMANDS_DIR / "cpv-publish-plugin-to-marketplace.md").exists()

    def test_old_create_github_marketplace_removed(self):
        """cpv-create-github-marketplace.md replaced by cpv-create-a-github-marketplace.md."""
        assert not (COMMANDS_DIR / "cpv-create-github-marketplace.md").exists()

    def test_old_install_plugin_removed(self):
        """cpv-install-plugin.md replaced by cpv-install-plugin-from-local-mp.md."""
        assert not (COMMANDS_DIR / "cpv-install-plugin.md").exists()

    def test_old_uninstall_plugin_removed(self):
        """cpv-uninstall-plugin.md replaced by cpv-uninstall-plugin-from-local-mp.md."""
        assert not (COMMANDS_DIR / "cpv-uninstall-plugin.md").exists()


class TestNewCommandFrontmatter:
    """Verify new commands have correct YAML frontmatter fields."""

    def test_create_local_plugin_has_name_field(self):
        """cpv-create-local-plugin.md frontmatter must have name: cpv-create-local-plugin."""
        fm = _parse_frontmatter(COMMANDS_DIR / "cpv-create-local-plugin.md")
        assert fm is not None
        assert fm.get("name") == "cpv-create-local-plugin"

    def test_create_local_marketplace_has_name_field(self):
        """cpv-create-local-marketplace.md frontmatter must have name: cpv-create-local-marketplace."""
        fm = _parse_frontmatter(COMMANDS_DIR / "cpv-create-local-marketplace.md")
        assert fm is not None
        assert fm.get("name") == "cpv-create-local-marketplace"

    def test_standardize_has_name_field(self):
        """cpv-standardize.md frontmatter must have name: cpv-standardize."""
        fm = _parse_frontmatter(COMMANDS_DIR / "cpv-standardize.md")
        assert fm is not None
        assert fm.get("name") == "cpv-standardize"

    def test_publish_a_plugin_as_github_repo_has_name_field(self):
        """cpv-publish-a-plugin-as-github-repo.md frontmatter must have correct name."""
        fm = _parse_frontmatter(COMMANDS_DIR / "cpv-publish-a-plugin-as-github-repo.md")
        assert fm is not None
        assert fm.get("name") == "cpv-publish-a-plugin-as-github-repo"

    def test_publish_a_plugin_to_a_github_marketplace_has_name_field(self):
        """cpv-publish-a-plugin-to-a-github-marketplace.md frontmatter must have correct name."""
        fm = _parse_frontmatter(COMMANDS_DIR / "cpv-publish-a-plugin-to-a-github-marketplace.md")
        assert fm is not None
        assert fm.get("name") == "cpv-publish-a-plugin-to-a-github-marketplace"

    def test_create_a_github_marketplace_has_name_field(self):
        """cpv-create-a-github-marketplace.md frontmatter must have correct name."""
        fm = _parse_frontmatter(COMMANDS_DIR / "cpv-create-a-github-marketplace.md")
        assert fm is not None
        assert fm.get("name") == "cpv-create-a-github-marketplace"

    def test_install_plugin_from_local_mp_has_name_field(self):
        """cpv-install-plugin-from-local-mp.md frontmatter must have correct name."""
        fm = _parse_frontmatter(COMMANDS_DIR / "cpv-install-plugin-from-local-mp.md")
        assert fm is not None
        assert fm.get("name") == "cpv-install-plugin-from-local-mp"

    def test_uninstall_plugin_from_local_mp_has_name_field(self):
        """cpv-uninstall-plugin-from-local-mp.md frontmatter must have correct name."""
        fm = _parse_frontmatter(COMMANDS_DIR / "cpv-uninstall-plugin-from-local-mp.md")
        assert fm is not None
        assert fm.get("name") == "cpv-uninstall-plugin-from-local-mp"


class TestCanonicalPipelineSkill:
    """Verify the new canonical-pipeline skill."""

    def test_skill_directory_exists(self):
        """skills/canonical-pipeline/ directory must exist."""
        assert (SKILLS_DIR / "canonical-pipeline").is_dir()

    def test_skill_md_has_name_field(self):
        """canonical-pipeline/SKILL.md frontmatter must have name: canonical-pipeline."""
        fm = _parse_frontmatter(SKILLS_DIR / "canonical-pipeline" / "SKILL.md")
        assert fm is not None
        assert fm.get("name") == "canonical-pipeline"


class TestCommandCount:
    """Verify total command count after consolidation."""

    def test_total_command_count_is_38(self):
        """commands/ directory should contain exactly 38 .md files."""
        md_files = list(COMMANDS_DIR.glob("*.md"))
        assert len(md_files) == 38, f"Expected 38 commands, found {len(md_files)}: {sorted(f.name for f in md_files)}"
