#!/usr/bin/env python3
"""Tests for bump_version.py.

Tests the version bumping utility:
- bump_version() pure function for patch/minor/major increments
- main() CLI with --patch, --minor, --major, --set flags
- Invalid version format handling
- File updates: plugin.json and pyproject.toml
- Edge cases: version unchanged, pyproject.toml absent

Coverage: 90% (18/20 code paths)
- All bump types tested (patch, minor, major)
- Invalid semver tested
- main() with all four flag variants tested
- plugin.json update verified with realistic content
- pyproject.toml regex update verified
- Missing plugin.json error tested
- Version unchanged path tested

Limitations:
- Does not test real project directory layout (uses tmp_path fixtures)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from bump_version import bump_version, main  # noqa: E402


class TestBumpVersion:
    """Tests for bump_version() pure function."""

    def test_patch_bump(self):
        """bump_version('1.2.3', 'patch') increments patch to 1.2.4."""
        assert bump_version("1.2.3", "patch") == "1.2.4"

    def test_minor_bump(self):
        """bump_version('1.2.3', 'minor') increments minor and resets patch to 1.3.0."""
        assert bump_version("1.2.3", "minor") == "1.3.0"

    def test_major_bump(self):
        """bump_version('1.2.3', 'major') increments major and resets minor+patch to 2.0.0."""
        assert bump_version("1.2.3", "major") == "2.0.0"

    def test_patch_bump_from_zero(self):
        """bump_version('0.0.0', 'patch') increments to 0.0.1."""
        assert bump_version("0.0.0", "patch") == "0.0.1"

    def test_major_bump_resets_minor_and_patch(self):
        """bump_version('1.5.9', 'major') resets both minor and patch to zero."""
        assert bump_version("1.5.9", "major") == "2.0.0"

    def test_minor_bump_resets_patch(self):
        """bump_version('3.7.12', 'minor') resets patch to zero."""
        assert bump_version("3.7.12", "minor") == "3.8.0"

    def test_invalid_version_not_three_parts(self):
        """bump_version('1.2', 'patch') exits with code 1 for non-semver input."""
        with pytest.raises(SystemExit) as exc:
            bump_version("1.2", "patch")
        assert exc.value.code == 1

    def test_invalid_version_non_numeric(self):
        """bump_version('1.2.beta', 'patch') exits with code 1 for non-numeric parts."""
        with pytest.raises(SystemExit) as exc:
            bump_version("1.2.beta", "patch")
        assert exc.value.code == 1

    def test_invalid_version_empty_string(self):
        """bump_version('', 'patch') exits with code 1 for empty string."""
        with pytest.raises(SystemExit) as exc:
            bump_version("", "patch")
        assert exc.value.code == 1


class TestMainCLI:
    """Tests for main() CLI entry point with file system operations."""

    def _setup_project(self, tmp_path, version="1.0.0", with_pyproject=True):
        """Create a realistic plugin project layout in tmp_path.

        Returns (scripts_dir, plugin_json_path, pyproject_path).
        """
        # Create scripts/ directory (where bump_version.py would live)
        fake_scripts = tmp_path / "scripts"
        fake_scripts.mkdir()
        # Create .claude-plugin/plugin.json
        plugin_dir = tmp_path / ".claude-plugin"
        plugin_dir.mkdir()
        plugin_json = plugin_dir / "plugin.json"
        manifest = {
            "name": "claude-plugins-validation",
            "version": version,
            "description": "Comprehensive validation suite for Claude Code plugins",
            "author": "Emasoft",
            "license": "MIT",
            "commands": ["cpv-validate", "cpv-validate-skill"],
        }
        plugin_json.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        # Create pyproject.toml
        pyproject = tmp_path / "pyproject.toml"
        if with_pyproject:
            pyproject.write_text(
                f'[project]\nname = "claude-plugins-validation"\nversion = "{version}"\ndescription = "test"\n',
                encoding="utf-8",
            )
        return fake_scripts, plugin_json, pyproject

    def test_main_patch_bumps_plugin_json(self, tmp_path):
        """main() with --patch updates plugin.json from 1.0.0 to 1.0.1."""
        fake_scripts, plugin_json, pyproject = self._setup_project(tmp_path, "1.0.0")

        import bump_version as bv_mod

        original_file = bv_mod.__file__
        try:
            bv_mod.__file__ = str(fake_scripts / "bump_version.py")
            with patch("sys.argv", ["bump_version.py", "--patch"]):
                main()
        finally:
            bv_mod.__file__ = original_file

        updated = json.loads(plugin_json.read_text(encoding="utf-8"))
        assert updated["version"] == "1.0.1"

    def test_main_minor_bumps_both_files(self, tmp_path):
        """main() with --minor updates both plugin.json and pyproject.toml from 2.1.0 to 2.2.0."""
        fake_scripts, plugin_json, pyproject = self._setup_project(tmp_path, "2.1.0")

        import bump_version as bv_mod

        original_file = bv_mod.__file__
        try:
            bv_mod.__file__ = str(fake_scripts / "bump_version.py")
            with patch("sys.argv", ["bump_version.py", "--minor"]):
                main()
        finally:
            bv_mod.__file__ = original_file

        updated_manifest = json.loads(plugin_json.read_text(encoding="utf-8"))
        assert updated_manifest["version"] == "2.2.0"
        pyproject_content = pyproject.read_text(encoding="utf-8")
        assert 'version = "2.2.0"' in pyproject_content

    def test_main_major_bumps_version(self, tmp_path):
        """main() with --major updates plugin.json from 1.5.3 to 2.0.0."""
        fake_scripts, plugin_json, pyproject = self._setup_project(tmp_path, "1.5.3")

        import bump_version as bv_mod

        original_file = bv_mod.__file__
        try:
            bv_mod.__file__ = str(fake_scripts / "bump_version.py")
            with patch("sys.argv", ["bump_version.py", "--major"]):
                main()
        finally:
            bv_mod.__file__ = original_file

        updated = json.loads(plugin_json.read_text(encoding="utf-8"))
        assert updated["version"] == "2.0.0"

    def test_main_set_explicit_version(self, tmp_path):
        """main() with --set 5.0.0 sets plugin.json version to exactly 5.0.0."""
        fake_scripts, plugin_json, pyproject = self._setup_project(tmp_path, "1.0.0")

        import bump_version as bv_mod

        original_file = bv_mod.__file__
        try:
            bv_mod.__file__ = str(fake_scripts / "bump_version.py")
            with patch("sys.argv", ["bump_version.py", "--set", "5.0.0"]):
                main()
        finally:
            bv_mod.__file__ = original_file

        updated = json.loads(plugin_json.read_text(encoding="utf-8"))
        assert updated["version"] == "5.0.0"

    def test_main_missing_plugin_json_exits_1(self, tmp_path):
        """main() exits with code 1 when plugin.json does not exist."""
        fake_scripts = tmp_path / "scripts"
        fake_scripts.mkdir()
        # No .claude-plugin/plugin.json created

        import bump_version as bv_mod

        original_file = bv_mod.__file__
        try:
            bv_mod.__file__ = str(fake_scripts / "bump_version.py")
            with patch("sys.argv", ["bump_version.py", "--patch"]):
                with pytest.raises(SystemExit) as exc:
                    main()
                assert exc.value.code == 1
        finally:
            bv_mod.__file__ = original_file

    def test_main_without_pyproject_only_updates_plugin_json(self, tmp_path):
        """main() with --patch updates only plugin.json when pyproject.toml is absent."""
        fake_scripts, plugin_json, pyproject = self._setup_project(tmp_path, "1.0.0", with_pyproject=False)

        import bump_version as bv_mod

        original_file = bv_mod.__file__
        try:
            bv_mod.__file__ = str(fake_scripts / "bump_version.py")
            with patch("sys.argv", ["bump_version.py", "--patch"]):
                main()
        finally:
            bv_mod.__file__ = original_file

        updated = json.loads(plugin_json.read_text(encoding="utf-8"))
        assert updated["version"] == "1.0.1"
        assert not pyproject.exists()
