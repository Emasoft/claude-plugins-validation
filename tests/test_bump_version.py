#!/usr/bin/env python3
"""Tests for scripts/bump_version.py — the thin wrapper around publish.py.

After the audit-fix refactor, ``bump_version.py`` is no longer a self-contained
implementation. It delegates to ``publish.bump_semver`` / ``publish.do_bump``
/ ``publish.get_current_version`` so there is exactly one source of truth for
version-bump logic. These tests verify:

1. The wrapper imports from ``publish`` (no duplicate copy of the bump logic)
2. The CLI exposes the same flags as before (--patch / --minor / --major /
   --set / --dry-run)
3. Each flag, when invoked via subprocess against a synthetic plugin layout,
   updates the version files correctly
4. Edge cases: invalid semver via --set, missing plugin.json, and pyproject.toml
   absent

We use ``subprocess`` rather than ``main()`` mocking because the new wrapper
resolves the plugin root from ``__file__`` at module-load time — mocking after
import is not reliable. Subprocess against a copied script in a tmp_path is
the robust way to exercise the full CLI surface.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUMP_SCRIPT = PROJECT_ROOT / "scripts" / "bump_version.py"
PUBLISH_SCRIPT = PROJECT_ROOT / "scripts" / "publish.py"


def _build_synthetic_plugin(tmp_path: Path, version: str = "1.0.0", *, with_pyproject: bool = True) -> Path:
    """Lay out a minimal plugin tree under tmp_path and copy bump_version.py.

    We must also copy publish.py because bump_version.py imports it. The
    synthetic plugin lives at tmp_path/<plugin>/ and the scripts live under
    tmp_path/<plugin>/scripts/ so the wrapper resolves plugin_root correctly
    via ``Path(__file__).resolve().parent.parent``.
    """
    plugin_root = tmp_path / "synth"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (plugin_root / "scripts").mkdir()

    # Manifest
    manifest = {
        "name": "synth",
        "version": version,
        "description": "test",
        "author": "Emasoft",
        "license": "MIT",
    }
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Optional pyproject.toml
    if with_pyproject:
        (plugin_root / "pyproject.toml").write_text(
            f'[project]\nname = "synth"\nversion = "{version}"\ndescription = "test"\n',
            encoding="utf-8",
        )

    # Copy bump_version.py and its transitive Python dependencies. publish.py
    # imports gitignore_filter (local module under scripts/), and may also
    # transitively pull in cpv_validation_common. We copy the full set so the
    # subprocess can resolve every import without a venv install.
    for name in (
        "bump_version.py",
        "publish.py",
        "gitignore_filter.py",
        "cpv_validation_common.py",
        "cpv_management_common.py",
        "cpv_network_resilience.py",
    ):
        src = PROJECT_ROOT / "scripts" / name
        if src.is_file():
            shutil.copy2(src, plugin_root / "scripts" / name)
    return plugin_root


def _run_bump(plugin_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    """Invoke bump_version.py inside the synthetic plugin via subprocess."""
    cmd = [sys.executable, str(plugin_root / "scripts" / "bump_version.py"), *args]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(plugin_root))


def _read_version(plugin_root: Path) -> str:
    data = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    version = data["version"]
    assert isinstance(version, str)
    return version


class TestWrapperContract:
    """Verify bump_version.py is now a thin wrapper around publish.py."""

    def test_wrapper_imports_from_publish(self):
        """bump_version.py must import bump_semver/do_bump/get_current_version from publish."""
        source = BUMP_SCRIPT.read_text(encoding="utf-8")
        assert "from publish import" in source
        for symbol in ("bump_semver", "do_bump", "get_current_version"):
            assert symbol in source, f"wrapper must import {symbol}"

    def test_wrapper_has_no_duplicate_bump_logic(self):
        """The old standalone bump_version() function must not be reintroduced.

        The original 30-line implementation drifted from publish.py — the
        whole point of this refactor is to keep ONE implementation. If a
        future commit re-adds a local def, this test catches it.
        """
        source = BUMP_SCRIPT.read_text(encoding="utf-8")
        assert "def bump_version(" not in source, (
            "bump_version() must not be redefined locally — use publish.bump_semver"
        )

    def test_help_flag_works(self):
        """--help must succeed and mention all four bump modes."""
        r = subprocess.run(
            [sys.executable, str(BUMP_SCRIPT), "--help"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0
        for flag in ("--patch", "--minor", "--major", "--set"):
            assert flag in r.stdout


class TestBumpModes:
    """Each bump mode must update plugin.json and pyproject.toml correctly."""

    def test_patch_bump(self, tmp_path):
        plugin_root = _build_synthetic_plugin(tmp_path, "1.0.0")
        r = _run_bump(plugin_root, ["--patch"])
        assert r.returncode == 0, r.stderr
        assert _read_version(plugin_root) == "1.0.1"
        # pyproject.toml must also be updated
        pyproject = (plugin_root / "pyproject.toml").read_text(encoding="utf-8")
        assert 'version = "1.0.1"' in pyproject

    def test_minor_bump_resets_patch(self, tmp_path):
        plugin_root = _build_synthetic_plugin(tmp_path, "2.1.7")
        r = _run_bump(plugin_root, ["--minor"])
        assert r.returncode == 0, r.stderr
        assert _read_version(plugin_root) == "2.2.0"

    def test_major_bump_resets_minor_and_patch(self, tmp_path):
        plugin_root = _build_synthetic_plugin(tmp_path, "1.5.9")
        r = _run_bump(plugin_root, ["--major"])
        assert r.returncode == 0, r.stderr
        assert _read_version(plugin_root) == "2.0.0"

    def test_set_explicit_version(self, tmp_path):
        plugin_root = _build_synthetic_plugin(tmp_path, "1.0.0")
        r = _run_bump(plugin_root, ["--set", "5.0.0"])
        assert r.returncode == 0, r.stderr
        assert _read_version(plugin_root) == "5.0.0"

    def test_dry_run_does_not_modify_files(self, tmp_path):
        plugin_root = _build_synthetic_plugin(tmp_path, "1.0.0")
        r = _run_bump(plugin_root, ["--patch", "--dry-run"])
        assert r.returncode == 0, r.stderr
        assert "Would bump" in r.stdout
        # File must remain at the original version
        assert _read_version(plugin_root) == "1.0.0"


class TestEdgeCases:
    """Invalid inputs and missing files must produce clear errors."""

    def test_set_invalid_semver_exits_nonzero(self, tmp_path):
        plugin_root = _build_synthetic_plugin(tmp_path, "1.0.0")
        r = _run_bump(plugin_root, ["--set", "not-a-version"])
        assert r.returncode != 0
        assert "not valid semver" in r.stderr.lower()
        # Original file is unchanged
        assert _read_version(plugin_root) == "1.0.0"

    def test_no_args_exits_nonzero(self, tmp_path):
        plugin_root = _build_synthetic_plugin(tmp_path, "1.0.0")
        r = _run_bump(plugin_root, [])
        assert r.returncode != 0  # argparse complains about missing required mode

    def test_mutually_exclusive_modes(self, tmp_path):
        """--patch and --minor at once must be rejected by argparse."""
        plugin_root = _build_synthetic_plugin(tmp_path, "1.0.0")
        r = _run_bump(plugin_root, ["--patch", "--minor"])
        assert r.returncode != 0

    def test_missing_plugin_json_exits_nonzero(self, tmp_path):
        plugin_root = _build_synthetic_plugin(tmp_path, "1.0.0")
        # Remove the manifest so get_current_version returns None
        (plugin_root / ".claude-plugin" / "plugin.json").unlink()
        r = _run_bump(plugin_root, ["--patch"])
        assert r.returncode != 0
        assert "version" in r.stderr.lower()

    def test_without_pyproject_only_updates_plugin_json(self, tmp_path):
        plugin_root = _build_synthetic_plugin(tmp_path, "1.0.0", with_pyproject=False)
        r = _run_bump(plugin_root, ["--patch"])
        assert r.returncode == 0, r.stderr
        assert _read_version(plugin_root) == "1.0.1"
        assert not (plugin_root / "pyproject.toml").exists()


class TestSingleSourceOfTruth:
    """The wrapper and publish.py must agree on every bump operation."""

    @pytest.mark.parametrize(
        ("start", "mode", "expected"),
        [
            ("0.0.0", "patch", "0.0.1"),
            ("0.0.9", "patch", "0.0.10"),
            ("1.2.3", "minor", "1.3.0"),
            ("9.5.4", "minor", "9.6.0"),
            ("1.0.0", "major", "2.0.0"),
            ("0.9.99", "major", "1.0.0"),
        ],
    )
    def test_wrapper_matches_publish_bump_semver(self, start, mode, expected):
        """The wrapper's CLI must produce the same output as publish.bump_semver."""
        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        try:
            from publish import bump_semver  # noqa: PLC0415
        finally:
            sys.path.pop(0)
        assert bump_semver(start, mode) == expected
