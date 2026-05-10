"""Tests for plugin.json::skills path-list validation (CC v2.1.136+).

Per the changelog: "a `skills` entry in plugin.json hiding the plugin's
default `skills/` directory, and listing a file path now shows an error
instead of failing silently."

CPV mirrors that behaviour:
- Field absent or non-list → no-op (default `skills/` walk continues).
- Field is a list → every entry is path-validated (folder w/ SKILL.md OR
  direct SKILL.md file). Missing/bogus paths emit MAJOR.
- Field IS declared → the default `skills/` walk is suppressed (auth-
  oritative manifest list — matches CC loader semantics).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_plugin  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402


def _make_plugin(tmp_path: Path, *, skills_field: object = None) -> Path:
    """Create a minimal plugin with optional `skills` manifest field."""
    root = tmp_path / "demo-plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    manifest: dict[str, object] = {
        "name": "demo-plugin",
        "version": "1.0.0",
        "description": "x",
        "author": {"name": "t", "email": "t@e.com"},
    }
    if skills_field is not None:
        manifest["skills"] = skills_field
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


class TestValidateManifestSkillPaths:
    """Cover every documented behaviour of the v2.1.136 path-list validator."""

    def test_no_skills_field_returns_false(self, tmp_path: Path) -> None:
        """Field absent → default `skills/` walk should continue."""
        root = _make_plugin(tmp_path)
        report = ValidationReport()
        result = validate_plugin.validate_manifest_skill_paths(root, report)
        assert result is False
        # No findings emitted when the field is absent.
        assert [r for r in report.results if "skills" in r.message] == []

    def test_non_list_emits_major_and_returns_true(self, tmp_path: Path) -> None:
        """Field present but not a list → MAJOR, but field IS declared so
        the caller must suppress the default walk."""
        root = _make_plugin(tmp_path, skills_field="skills/my-skill/")
        report = ValidationReport()
        result = validate_plugin.validate_manifest_skill_paths(root, report)
        assert result is True
        majors = [r for r in report.results if r.level == "MAJOR" and "must be a list" in r.message]
        assert len(majors) == 1

    def test_missing_path_emits_major(self, tmp_path: Path) -> None:
        """Listed path doesn't exist → MAJOR."""
        root = _make_plugin(tmp_path, skills_field=["skills/ghost/"])
        report = ValidationReport()
        result = validate_plugin.validate_manifest_skill_paths(root, report)
        assert result is True
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("does not exist" in r.message for r in majors)

    def test_folder_without_skill_md_emits_major(self, tmp_path: Path) -> None:
        """Folder exists but lacks SKILL.md → MAJOR."""
        root = _make_plugin(tmp_path, skills_field=["skills/empty/"])
        (root / "skills" / "empty").mkdir(parents=True)
        report = ValidationReport()
        result = validate_plugin.validate_manifest_skill_paths(root, report)
        assert result is True
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("contains no SKILL.md" in r.message for r in majors)

    def test_valid_folder_path_clean(self, tmp_path: Path) -> None:
        """Folder with SKILL.md → no findings."""
        root = _make_plugin(tmp_path, skills_field=["skills/good/"])
        skill_dir = root / "skills" / "good"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: good\ndescription: x\n---\n## Overview\n")
        report = ValidationReport()
        result = validate_plugin.validate_manifest_skill_paths(root, report)
        assert result is True
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert majors == []

    def test_valid_skill_md_file_path_clean(self, tmp_path: Path) -> None:
        """Direct SKILL.md path → no findings."""
        root = _make_plugin(tmp_path, skills_field=["skills/good/SKILL.md"])
        skill_dir = root / "skills" / "good"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: good\ndescription: x\n---\n## Overview\n")
        report = ValidationReport()
        result = validate_plugin.validate_manifest_skill_paths(root, report)
        assert result is True
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert majors == []

    def test_non_skill_md_file_emits_major(self, tmp_path: Path) -> None:
        """File path pointing at a non-SKILL.md file → MAJOR."""
        root = _make_plugin(tmp_path, skills_field=["skills/good/notes.md"])
        skill_dir = root / "skills" / "good"
        skill_dir.mkdir(parents=True)
        (skill_dir / "notes.md").write_text("# notes\n")
        report = ValidationReport()
        result = validate_plugin.validate_manifest_skill_paths(root, report)
        assert result is True
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("not a SKILL.md" in r.message for r in majors)

    def test_path_traversal_rejected(self, tmp_path: Path) -> None:
        """`../escape/SKILL.md` resolves outside the plugin root → MAJOR."""
        root = _make_plugin(tmp_path, skills_field=["../escape/SKILL.md"])
        report = ValidationReport()
        result = validate_plugin.validate_manifest_skill_paths(root, report)
        assert result is True
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("escapes the plugin root" in r.message for r in majors)

    def test_non_string_entry_emits_major(self, tmp_path: Path) -> None:
        """`skills: [42]` → MAJOR for the non-string entry."""
        root = _make_plugin(tmp_path, skills_field=[42])
        report = ValidationReport()
        result = validate_plugin.validate_manifest_skill_paths(root, report)
        assert result is True
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert any("must be a string" in r.message for r in majors)

    def test_validate_skills_skips_default_walk_when_manifest_lists_present(self, tmp_path: Path) -> None:
        """When manifest declares `skills`, the default skills/ walk MUST
        be suppressed even if other skill folders exist on disk. Mirrors
        the CC v2.1.136+ loader: the manifest list is authoritative."""
        # Manifest declares ONE valid skill.
        root = _make_plugin(tmp_path, skills_field=["skills/listed/"])
        listed = root / "skills" / "listed"
        listed.mkdir(parents=True)
        (listed / "SKILL.md").write_text(
            "---\nname: listed\ndescription: Use when ... Trigger with ...\n---\n## Overview\nx\n"
        )
        # An UNLISTED skill folder exists on disk — should be IGNORED.
        unlisted = root / "skills" / "unlisted"
        unlisted.mkdir(parents=True)
        (unlisted / "SKILL.md").write_text(
            "---\nname: unlisted\ndescription: Use when ... Trigger with ...\n---\n## Overview\nx\n"
        )
        report = ValidationReport()
        validate_plugin.validate_skills(root, report)
        # No "Found N skill(s) to validate" info-line, because the
        # manifest's authoritative list short-circuited the default walk.
        info_lines = [r for r in report.results if "skill(s) to validate" in r.message]
        assert info_lines == [], (
            f"default walk should be suppressed when manifest declares skills; "
            f"got info lines: {[r.message for r in info_lines]}"
        )
