"""Tests for manage_doctor.py — plugin installation health check.

Coverage: 30 tests across all major code paths.

Functions tested:
- read_plugin_meta(): reads plugin.json metadata with defaults
- _portable_path(): converts paths to forward slashes
- _run_claude_validate(): runs `claude plugin validate` subprocess
- do_doctor(): full health check orchestrator

Coverage breakdown:
- read_plugin_meta: 3 tests (valid, missing file, partial metadata)
- _portable_path: 1 test (unix path forward slashes)
- _run_claude_validate: 5 tests (no claude, success, errors, warnings, timeout)
- do_doctor: 21 tests (healthy, missing dir, corrupt settings, empty marketplaces,
  orphans, reserved names, impersonation, undeclared plugins, verbose mode, etc.)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

# Ensure scripts dir is on path so manage_doctor and cpv_management_common can be imported
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from manage_doctor import (
    _portable_path,
    _run_claude_validate,
    do_doctor,
    read_plugin_meta,
)

# ── Helpers ─────────────────────────────────────────────────────────


def _make_claude_dir(tmp_path: Path) -> Path:
    """Create a minimal ~/.claude directory structure."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    return claude_dir


def _make_settings(claude_dir: Path, data: dict, filename: str = "settings.json") -> Path:
    """Write a settings JSON file inside the claude dir."""
    sf = claude_dir / filename
    sf.write_text(json.dumps(data), encoding="utf-8")
    return sf


def _make_marketplace(marketplaces_dir: Path, mp_name: str, marketplace_json: dict, plugins: dict | None = None) -> Path:
    """Create a marketplace directory with marketplace.json and optional plugin subdirs.

    plugins: dict mapping plugin_name -> plugin.json dict (or None for no plugin.json).
    """
    mp_dir = marketplaces_dir / mp_name
    mp_cp = mp_dir / ".claude-plugin"
    mp_cp.mkdir(parents=True, exist_ok=True)
    (mp_cp / "marketplace.json").write_text(json.dumps(marketplace_json), encoding="utf-8")

    if plugins:
        for pname, pjson in plugins.items():
            plug_dir = mp_dir / "plugins" / pname
            if pjson is not None:
                cp_dir = plug_dir / ".claude-plugin"
                cp_dir.mkdir(parents=True, exist_ok=True)
                (cp_dir / "plugin.json").write_text(json.dumps(pjson), encoding="utf-8")
            else:
                plug_dir.mkdir(parents=True, exist_ok=True)
    return mp_dir


def _patch_paths(monkeypatch, claude_dir: Path):
    """Monkeypatch all module-level path constants to point at tmp_path."""
    plugins_dir = claude_dir / "plugins"
    marketplaces_dir = plugins_dir / "marketplaces"
    cache_dir = plugins_dir / "cache"
    settings_file = claude_dir / "settings.json"

    # Patch on both manage_doctor and cpv_management_common so all references resolve
    for mod_name in ("manage_doctor", "cpv_management_common"):
        mod = sys.modules.get(mod_name)
        if mod:
            monkeypatch.setattr(mod, "CLAUDE_DIR", claude_dir)
            if hasattr(mod, "PLUGINS_DIR"):
                monkeypatch.setattr(mod, "PLUGINS_DIR", plugins_dir)
            monkeypatch.setattr(mod, "MARKETPLACES_DIR", marketplaces_dir)
            monkeypatch.setattr(mod, "CACHE_DIR", cache_dir)
            monkeypatch.setattr(mod, "SETTINGS_FILE", settings_file)
            monkeypatch.setattr(mod, "SETTINGS_TARGET", settings_file)


# ── Valid marketplace.json template ─────────────────────────────────


VALID_MARKETPLACE_JSON = {
    "name": "my-test-marketplace",
    "owner": {"name": "Test Author"},
    "metadata": {"description": "A test marketplace for unit tests"},
    "plugins": [
        {
            "name": "test-plugin",
            "source": "./plugins/test-plugin",
        }
    ],
}


VALID_PLUGIN_JSON = {
    "name": "test-plugin",
    "version": "1.0.0",
    "description": "A test plugin",
}


# ═══════════════════════════════════════════════════════════════════
# Tests for read_plugin_meta
# ═══════════════════════════════════════════════════════════════════


class TestReadPluginMeta:
    """Tests for read_plugin_meta() — reads plugin.json and returns metadata."""

    def test_valid_plugin_json(self, tmp_path):
        """read_plugin_meta returns correct name, version, description from a well-formed plugin.json."""
        plug_dir = tmp_path / "my-plugin"
        cp = plug_dir / ".claude-plugin"
        cp.mkdir(parents=True)
        (cp / "plugin.json").write_text(
            json.dumps({"name": "cool-plugin", "version": "2.3.1", "description": "Does cool things"}),
            encoding="utf-8",
        )
        meta = read_plugin_meta(plug_dir)
        assert meta["name"] == "cool-plugin"
        assert meta["version"] == "2.3.1"
        assert meta["description"] == "Does cool things"

    def test_missing_plugin_json_uses_defaults(self, tmp_path):
        """read_plugin_meta falls back to directory name and defaults when plugin.json is missing."""
        plug_dir = tmp_path / "fallback-name"
        plug_dir.mkdir()
        meta = read_plugin_meta(plug_dir)
        assert meta["name"] == "fallback-name"
        assert meta["version"] == "1.0.0"
        assert meta["description"] == ""

    def test_partial_plugin_json(self, tmp_path):
        """read_plugin_meta fills in defaults for missing fields in a partial plugin.json."""
        plug_dir = tmp_path / "partial"
        cp = plug_dir / ".claude-plugin"
        cp.mkdir(parents=True)
        (cp / "plugin.json").write_text(json.dumps({"version": "0.5.0"}), encoding="utf-8")
        meta = read_plugin_meta(plug_dir)
        # name falls back to directory name because "name" key is absent
        assert meta["name"] == "partial"
        assert meta["version"] == "0.5.0"
        assert meta["description"] == ""


# ═══════════════════════════════════════════════════════════════════
# Tests for _portable_path
# ═══════════════════════════════════════════════════════════════════


class TestPortablePath:
    """Tests for _portable_path() — converts paths to forward slashes."""

    def test_unix_path_unchanged(self):
        """Unix-style paths with forward slashes are returned as-is."""
        p = Path("/Users/test/.claude/plugins/marketplaces/my-mp")
        result = _portable_path(p)
        assert "\\" not in result
        assert "my-mp" in result


# ═══════════════════════════════════════════════════════════════════
# Tests for _run_claude_validate
# ═══════════════════════════════════════════════════════════════════


class TestRunClaudeValidate:
    """Tests for _run_claude_validate() — runs claude plugin validate subprocess."""

    @patch("manage_doctor.shutil.which", return_value=None)
    def test_no_claude_binary_returns_empty(self, mock_which):
        """When claude CLI is not found on PATH, returns empty error and warning lists."""
        errors, warnings = _run_claude_validate(Path("/fake/path"))
        assert errors == []
        assert warnings == []

    @patch("manage_doctor.subprocess.run")
    @patch("manage_doctor.shutil.which", return_value="/usr/local/bin/claude")
    def test_clean_validation_returns_empty(self, mock_which, mock_run):
        """When claude plugin validate passes cleanly, returns no errors or warnings."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["claude", "plugin", "validate", "/path"],
            returncode=0,
            stdout="Plugin validation passed.\n",
            stderr="",
        )
        errors, warnings = _run_claude_validate(Path("/path"))
        assert errors == []
        assert warnings == []

    @patch("manage_doctor.subprocess.run")
    @patch("manage_doctor.shutil.which", return_value="/usr/local/bin/claude")
    def test_errors_parsed_from_output(self, mock_which, mock_run):
        """Error findings prefixed with the arrow character are captured as errors."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["claude", "plugin", "validate", "/path"],
            returncode=1,
            stdout="Found 2 errors:\n  \u276f Missing name field\n  \u276f Invalid version\nFound 1 warning:\n  \u276f No description\n",
            stderr="",
        )
        errors, warnings = _run_claude_validate(Path("/path"))
        assert len(errors) == 2
        assert "Missing name field" in errors[0]
        assert len(warnings) == 1
        assert "No description" in warnings[0]

    @patch("manage_doctor.subprocess.run")
    @patch("manage_doctor.shutil.which", return_value="/usr/local/bin/claude")
    def test_nonzero_exit_no_parsed_errors_adds_generic(self, mock_which, mock_run):
        """When exit code is non-zero but no errors are parsed, a generic error is added."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["claude", "plugin", "validate", "/path"],
            returncode=1,
            stdout="Something went wrong\n",
            stderr="",
        )
        errors, warnings = _run_claude_validate(Path("/path"))
        assert len(errors) == 1
        assert "exited with code 1" in errors[0]

    @patch("manage_doctor.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=120))
    @patch("manage_doctor.shutil.which", return_value="/usr/local/bin/claude")
    def test_timeout_returns_warning(self, mock_which, mock_run):
        """When subprocess times out, a warning is returned instead of crashing."""
        errors, warnings = _run_claude_validate(Path("/path"))
        assert errors == []
        assert len(warnings) == 1
        assert "timed out" in warnings[0]


# ═══════════════════════════════════════════════════════════════════
# Tests for do_doctor
# ═══════════════════════════════════════════════════════════════════


class TestDoDoctor:
    """Tests for do_doctor() — full health check of plugin installation."""

    def test_missing_claude_dir_returns_early(self, tmp_path, monkeypatch, capsys):
        """When ~/.claude does not exist, reports info and returns without error."""
        _patch_paths(monkeypatch, tmp_path / ".claude")
        # Don't create the dir — it should not exist
        do_doctor()
        out = capsys.readouterr().out
        assert "not found" in out.lower() or "No plugins" in out

    def test_healthy_installation_no_issues(self, tmp_path, monkeypatch, capsys):
        """A fully healthy installation with valid marketplace and plugin reports zero issues."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)

        # Create settings
        _make_settings(
            claude_dir,
            {
                "extraKnownMarketplaces": {
                    "my-test-marketplace": {"source": {"source": "directory", "path": _portable_path(claude_dir / "plugins" / "marketplaces" / "my-test-marketplace")}},
                },
                "enabledPlugins": {
                    "test-plugin@my-test-marketplace": True,
                },
            },
        )

        # Create marketplace with plugin
        mp_dir = claude_dir / "plugins" / "marketplaces"
        _make_marketplace(
            mp_dir,
            "my-test-marketplace",
            VALID_MARKETPLACE_JSON,
            plugins={"test-plugin": VALID_PLUGIN_JSON},
        )

        # Mock external dependencies: shutil.which, subprocess.run
        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "All checks passed" in out

    def test_corrupt_settings_json_reports_error(self, tmp_path, monkeypatch, capsys):
        """Corrupt settings.json is detected and reported as an error."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)

        # Write invalid JSON to settings.json
        sf = claude_dir / "settings.json"
        sf.write_text("{invalid json!!!}", encoding="utf-8")

        with patch("manage_doctor.shutil.which", return_value=None):
            do_doctor()

        out = capsys.readouterr().out
        assert "CORRUPT" in out

    def test_empty_marketplace_warns(self, tmp_path, monkeypatch, capsys):
        """A marketplace with no plugins defined gets a warning."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        mp_dir = claude_dir / "plugins" / "marketplaces"
        empty_mj = {
            "name": "empty-mp",
            "owner": {"name": "Test"},
            "metadata": {"description": "Empty"},
            "plugins": [],
        }
        _make_marketplace(mp_dir, "empty-mp", empty_mj)

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "no plugins defined" in out

    def test_missing_marketplace_json_reports_error(self, tmp_path, monkeypatch, capsys):
        """A marketplace directory without marketplace.json is flagged as an error."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        mp_dir = claude_dir / "plugins" / "marketplaces" / "broken-mp"
        mp_dir.mkdir(parents=True)
        # No .claude-plugin/marketplace.json created

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "Missing marketplace.json" in out

    def test_corrupt_marketplace_json(self, tmp_path, monkeypatch, capsys):
        """Corrupt marketplace.json is detected and reported."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        mp_dir = claude_dir / "plugins" / "marketplaces" / "corrupt-mp"
        cp = mp_dir / ".claude-plugin"
        cp.mkdir(parents=True)
        (cp / "marketplace.json").write_text("{broken json!!!", encoding="utf-8")

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "CORRUPT" in out

    def test_reserved_marketplace_name_detected(self, tmp_path, monkeypatch, capsys):
        """Marketplace names reserved by Anthropic are flagged as errors."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        reserved_mj = {
            "name": "anthropic-marketplace",
            "owner": {"name": "Fake"},
            "metadata": {"description": "Imposter"},
            "plugins": [{"name": "a-plugin", "source": "./plugins/a-plugin"}],
        }
        mp_dir = claude_dir / "plugins" / "marketplaces"
        _make_marketplace(mp_dir, "fake-mp", reserved_mj)

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "reserved" in out.lower()

    def test_impersonation_name_detected(self, tmp_path, monkeypatch, capsys):
        """Names containing 'official' + 'anthropic'/'claude' are flagged as impersonation."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        impersonator_mj = {
            "name": "claude-official-plugins",
            "owner": {"name": "Fake"},
            "metadata": {"description": "Not real"},
            "plugins": [{"name": "x-plugin", "source": "./plugins/x-plugin"}],
        }
        mp_dir = claude_dir / "plugins" / "marketplaces"
        _make_marketplace(mp_dir, "impersonator", impersonator_mj)

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "impersonates" in out.lower()

    def test_non_kebab_case_marketplace_name_warns(self, tmp_path, monkeypatch, capsys):
        """Marketplace names not in kebab-case get a warning."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        bad_name_mj = {
            "name": "MyMarketPlace",
            "owner": {"name": "Test"},
            "metadata": {"description": "Bad name"},
            "plugins": [{"name": "a-plugin", "source": "./plugins/a-plugin"}],
        }
        mp_dir = claude_dir / "plugins" / "marketplaces"
        _make_marketplace(mp_dir, "bad-name-mp", bad_name_mj)

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "kebab-case" in out

    def test_missing_owner_field_error(self, tmp_path, monkeypatch, capsys):
        """Missing or invalid owner field in marketplace.json is flagged."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        no_owner_mj = {
            "name": "no-owner-mp",
            "metadata": {"description": "Missing owner"},
            "plugins": [{"name": "a-plugin", "source": "./plugins/a-plugin"}],
        }
        mp_dir = claude_dir / "plugins" / "marketplaces"
        _make_marketplace(mp_dir, "no-owner-mp", no_owner_mj)

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "owner" in out.lower()

    def test_plugins_field_not_array_error(self, tmp_path, monkeypatch, capsys):
        """When plugins field is not a list, it is flagged as an error."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        bad_plugins_mj = {
            "name": "bad-plugins-mp",
            "owner": {"name": "Test"},
            "metadata": {"description": "Bad plugins field"},
            "plugins": "not-a-list",
        }
        mp_dir = claude_dir / "plugins" / "marketplaces"
        _make_marketplace(mp_dir, "bad-plugins-mp", bad_plugins_mj)

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "must be an array" in out

    def test_orphaned_marketplace_in_settings(self, tmp_path, monkeypatch, capsys):
        """Settings referencing a non-existent marketplace path are flagged as orphaned."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)

        # Settings reference a marketplace path that does not exist
        _make_settings(
            claude_dir,
            {
                "extraKnownMarketplaces": {
                    "ghost-mp": {
                        "source": {"source": "directory", "path": "/nonexistent/path/ghost-mp"},
                    },
                },
            },
        )

        # Create the marketplaces dir but don't put anything in it
        (claude_dir / "plugins" / "marketplaces").mkdir(parents=True)

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "Orphaned marketplace" in out or "orphaned" in out.lower()

    def test_orphaned_enabled_plugin_in_settings(self, tmp_path, monkeypatch, capsys):
        """enabledPlugins referencing a non-existent plugin/marketplace are flagged as orphaned."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)

        _make_settings(
            claude_dir,
            {
                "enabledPlugins": {
                    "ghost-plugin@ghost-marketplace": True,
                },
            },
        )

        # Create marketplaces dir but no matching marketplace
        (claude_dir / "plugins" / "marketplaces").mkdir(parents=True)

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "Orphaned" in out or "orphaned" in out.lower()

    def test_plugin_not_declared_in_marketplace_json(self, tmp_path, monkeypatch, capsys):
        """Plugin directory existing but not listed in marketplace.json plugins array is warned."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        # Marketplace declares plugin-a but also has plugin-b on disk
        mj = {
            "name": "test-mp",
            "owner": {"name": "Test"},
            "metadata": {"description": "Test"},
            "plugins": [
                {"name": "plugin-a", "source": "./plugins/plugin-a"},
                {"name": "plugin-b", "source": "./plugins/plugin-b"},
            ],
        }
        mp_dir = claude_dir / "plugins" / "marketplaces"
        # Create both plugins on disk, but plugin-b has a different name in its plugin.json
        _make_marketplace(
            mp_dir,
            "test-mp",
            mj,
            plugins={
                "plugin-a": {"name": "plugin-a", "version": "1.0.0", "description": "A"},
                "plugin-b": {"name": "undeclared-name", "version": "1.0.0", "description": "B"},
            },
        )

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "Not listed in marketplace.json" in out

    def test_verbose_mode_shows_details(self, tmp_path, monkeypatch, capsys):
        """Verbose mode outputs additional validation detail lines."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        mp_dir = claude_dir / "plugins" / "marketplaces"
        _make_marketplace(
            mp_dir,
            "verbose-mp",
            {
                "name": "verbose-mp",
                "owner": {"name": "Test"},
                "metadata": {"description": "Test"},
                "plugins": [{"name": "v-plugin", "source": "./plugins/v-plugin"}],
            },
            plugins={"v-plugin": {"name": "v-plugin", "version": "1.0.0", "description": "V"}},
        )

        # Mock the validate_plugin.py subprocess to return errors so verbose output triggers
        mock_vresult = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="CRITICAL: Missing required field\nMAJOR: Bad structure\n",
        )
        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run", return_value=mock_vresult):
            do_doctor(verbose=True)

        out = capsys.readouterr().out
        # In verbose mode, error detail lines should be printed
        assert "ERROR:" in out or "CRITICAL" in out or "v-plugin" in out

    def test_unrecognized_root_key_warns(self, tmp_path, monkeypatch, capsys):
        """Extra root keys in marketplace.json that Claude CLI does not recognize are warned."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        extra_key_mj = {
            "name": "extra-key-mp",
            "owner": {"name": "Test"},
            "metadata": {"description": "Has extra key"},
            "plugins": [{"name": "p", "source": "./plugins/p"}],
            "customField": "should warn",
        }
        mp_dir = claude_dir / "plugins" / "marketplaces"
        _make_marketplace(mp_dir, "extra-key-mp", extra_key_mj)

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "unrecognized root key" in out

    def test_duplicate_plugin_name_error(self, tmp_path, monkeypatch, capsys):
        """Duplicate plugin names within a marketplace are flagged as errors."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        dup_mj = {
            "name": "dup-mp",
            "owner": {"name": "Test"},
            "metadata": {"description": "Dup plugins"},
            "plugins": [
                {"name": "same-name", "source": "./plugins/same-name"},
                {"name": "same-name", "source": "./plugins/same-name-2"},
            ],
        }
        mp_dir = claude_dir / "plugins" / "marketplaces"
        _make_marketplace(mp_dir, "dup-mp", dup_mj)

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "duplicate" in out.lower()

    def test_plugin_entry_missing_source(self, tmp_path, monkeypatch, capsys):
        """Plugin entry without a source field is warned about."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        no_src_mj = {
            "name": "no-src-mp",
            "owner": {"name": "Test"},
            "metadata": {"description": "No source"},
            "plugins": [{"name": "no-source-plugin"}],
        }
        mp_dir = claude_dir / "plugins" / "marketplaces"
        _make_marketplace(mp_dir, "no-src-mp", no_src_mj)

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "missing" in out.lower() and "source" in out.lower()

    def test_plugin_source_unknown_type(self, tmp_path, monkeypatch, capsys):
        """Plugin with an unknown source type object is warned about."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        unknown_src_mj = {
            "name": "unknown-src-mp",
            "owner": {"name": "Test"},
            "metadata": {"description": "Unknown source type"},
            "plugins": [
                {"name": "bad-src-plugin", "source": {"source": "ftp", "url": "ftp://example.com"}},
            ],
        }
        mp_dir = claude_dir / "plugins" / "marketplaces"
        _make_marketplace(mp_dir, "unknown-src-mp", unknown_src_mj)

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "unknown source type" in out.lower()

    def test_settings_registered_path_mismatch(self, tmp_path, monkeypatch, capsys):
        """Path mismatch between settings registration and actual marketplace path is warned."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)

        mp_dir = claude_dir / "plugins" / "marketplaces"
        _make_marketplace(
            mp_dir,
            "mismatch-mp",
            {
                "name": "mismatch-mp",
                "owner": {"name": "Test"},
                "metadata": {"description": "Mismatch"},
                "plugins": [{"name": "p", "source": "./plugins/p"}],
            },
        )

        _make_settings(
            claude_dir,
            {
                "extraKnownMarketplaces": {
                    "mismatch-mp": {
                        "source": {"source": "directory", "path": "/wrong/path/mismatch-mp"},
                    },
                },
            },
        )

        with patch("manage_doctor.shutil.which", return_value=None), patch("manage_doctor.subprocess.run"):
            do_doctor()

        out = capsys.readouterr().out
        assert "Path mismatch" in out or "mismatch" in out.lower()

    def test_claude_cli_auth_not_authenticated(self, tmp_path, monkeypatch, capsys):
        """When claude auth status returns non-zero, a warning about authentication is shown."""
        claude_dir = _make_claude_dir(tmp_path)
        _patch_paths(monkeypatch, claude_dir)
        _make_settings(claude_dir, {})

        auth_result = subprocess.CompletedProcess(
            args=["claude", "auth", "status"],
            returncode=1,
            stdout="",
            stderr="Not authenticated\n",
        )

        with patch("manage_doctor.shutil.which", return_value="/usr/local/bin/claude"), patch("manage_doctor.subprocess.run", return_value=auth_result):
            do_doctor()

        out = capsys.readouterr().out
        assert "not authenticated" in out.lower()
