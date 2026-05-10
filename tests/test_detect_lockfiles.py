#!/usr/bin/env python3
"""Tests for detect_lockfiles.py (TRDD-79638eb6 — Part 2).

Covers the lockfile registry — every entry in LOCKFILES gets a positive
test, plus negative cases (no lockfiles, lockfile-only-in-subdir,
non-existent path), and the find_lockfile_path helper.

The validator-side behaviour (orphan / gitignored lockfiles) lives in
test_validate_drift_detection.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make scripts/ importable
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from detect_lockfiles import LOCKFILES, detect_lockfiles, find_lockfile_path  # noqa: E402


class TestLockfileRegistry:
    """Sanity checks on the LOCKFILES registry contents."""

    def test_registry_includes_python_lockfiles(self):
        """uv.lock, poetry.lock, Pipfile.lock all map to 'python'."""
        for name in ("uv.lock", "poetry.lock", "Pipfile.lock"):
            assert LOCKFILES[name] == "python"

    def test_registry_includes_js_lockfiles(self):
        """All four JS lockfiles map to 'js'."""
        for name in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb"):
            assert LOCKFILES[name] == "js"

    def test_registry_includes_deno_rust_go_ruby_elixir(self):
        """One lockfile per the remaining supported languages."""
        assert LOCKFILES["deno.lock"] == "deno"
        assert LOCKFILES["Cargo.lock"] == "rust"
        assert LOCKFILES["go.sum"] == "go"
        assert LOCKFILES["Gemfile.lock"] == "ruby"
        assert LOCKFILES["mix.lock"] == "elixir"

    def test_registry_values_are_known_languages(self):
        """Every language in LOCKFILES is one of the codes detect_language emits."""
        valid_langs = {"python", "js", "ts", "deno", "rust", "go", "ruby", "elixir"}
        for lang in LOCKFILES.values():
            assert lang in valid_langs


class TestDetectLockfilesPositive:
    """Each lockfile registered in LOCKFILES is detected when present."""

    @pytest.mark.parametrize("lockfile_name", sorted(LOCKFILES.keys()))
    def test_every_registered_lockfile_is_detected(self, tmp_path, lockfile_name):
        """Touch the lockfile at root and detect_lockfiles returns it."""
        (tmp_path / lockfile_name).write_text("# lockfile content\n")
        result = detect_lockfiles(tmp_path)
        assert lockfile_name in result
        assert result[lockfile_name] == LOCKFILES[lockfile_name]

    def test_multiple_lockfiles_all_detected(self, tmp_path):
        """A polyglot tree returns every lockfile present at root."""
        (tmp_path / "uv.lock").write_text("")
        (tmp_path / "package-lock.json").write_text("{}")
        (tmp_path / "Cargo.lock").write_text("")
        result = detect_lockfiles(tmp_path)
        assert "uv.lock" in result
        assert "package-lock.json" in result
        assert "Cargo.lock" in result
        assert result["uv.lock"] == "python"
        assert result["package-lock.json"] == "js"
        assert result["Cargo.lock"] == "rust"


class TestDetectLockfilesNegative:
    """Empty trees, subdir-only lockfiles, non-existent paths, non-dirs."""

    def test_empty_directory_returns_empty_dict(self, tmp_path):
        """A directory with no lockfiles returns {}."""
        result = detect_lockfiles(tmp_path)
        assert result == {}

    def test_lockfile_in_subdirectory_is_ignored(self, tmp_path):
        """Lockfiles in subdirs do NOT count — only the plugin root is scanned."""
        sub = tmp_path / "vendor" / "some-pkg"
        sub.mkdir(parents=True)
        (sub / "uv.lock").write_text("")
        result = detect_lockfiles(tmp_path)
        assert result == {}

    def test_nonexistent_path_returns_empty_dict(self, tmp_path):
        """A path that does not exist returns {} (no exception)."""
        missing = tmp_path / "no-such-dir"
        result = detect_lockfiles(missing)
        assert result == {}

    def test_path_to_file_returns_empty_dict(self, tmp_path):
        """A path that points to a file returns {} (not raises)."""
        f = tmp_path / "afile.txt"
        f.write_text("hi")
        result = detect_lockfiles(f)
        assert result == {}

    def test_lockfile_as_directory_is_ignored(self, tmp_path):
        """A directory named 'uv.lock' (not a file) is not detected."""
        (tmp_path / "uv.lock").mkdir()
        result = detect_lockfiles(tmp_path)
        assert result == {}


class TestFindLockfilePath:
    """The find_lockfile_path helper returns the absolute Path or None."""

    def test_existing_lockfile_returns_path(self, tmp_path):
        """When the lockfile exists, return its absolute Path."""
        (tmp_path / "uv.lock").write_text("")
        result = find_lockfile_path(tmp_path, "uv.lock")
        assert result is not None
        assert result.name == "uv.lock"
        assert result.is_file()

    def test_missing_lockfile_returns_none(self, tmp_path):
        """When the lockfile is absent, return None."""
        result = find_lockfile_path(tmp_path, "uv.lock")
        assert result is None

    def test_empty_lockfile_name_returns_none(self, tmp_path):
        """An empty lockfile name returns None (defensive guard)."""
        result = find_lockfile_path(tmp_path, "")
        assert result is None

    def test_lockfile_dir_returns_none(self, tmp_path):
        """A directory with the same name as a lockfile is not returned."""
        (tmp_path / "uv.lock").mkdir()
        result = find_lockfile_path(tmp_path, "uv.lock")
        assert result is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
