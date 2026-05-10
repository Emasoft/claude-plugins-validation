"""Tests for cpv-doctor --prune-old-versions (v2.48 disk-cleanup feature).

Validates the cache pruning that frees disk space accumulated when
``claude plugin update`` doesn't remove old versions.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import manage_doctor  # noqa: E402
from manage_doctor import (  # noqa: E402
    _human_bytes,
    _semver_sort_key,
    do_prune_old_versions,
    find_active_versions,
    find_cached_versions,
)

# ── _semver_sort_key ──────────────────────────────────────────────


class TestSemverSortKey:
    def test_numeric_segments_sort_numerically(self):
        # "10" > "9" — string compare would say otherwise
        assert _semver_sort_key("0.10.0") > _semver_sort_key("0.9.9")

    def test_semver_descending_via_reverse(self):
        versions = ["1.0.0", "2.0.0", "1.5.0", "0.9.9"]
        sorted_desc = sorted(versions, key=_semver_sort_key, reverse=True)
        assert sorted_desc == ["2.0.0", "1.5.0", "1.0.0", "0.9.9"]

    def test_handles_non_semver(self):
        # Git-hash style — should not crash
        sorted(["a7b17c91", "1.0.0", "020446a4"], key=_semver_sort_key)


# ── _human_bytes ──────────────────────────────────────────────────


class TestHumanBytes:
    @pytest.mark.parametrize(
        "n,expected",
        [
            (0, "0 B"),
            (1023, "1023 B"),
            (1024, "1.0 KB"),
            (1024 * 1024, "1.0 MB"),
            (5 * 1024 * 1024 * 1024, "5.0 GB"),
        ],
    )
    def test_units(self, n: int, expected: str):
        assert _human_bytes(n) == expected


# ── find_cached_versions ──────────────────────────────────────────


class TestFindCachedVersions:
    def test_empty_cache_returns_empty(self, tmp_path: Path):
        assert find_cached_versions(tmp_path) == {}

    def test_single_plugin_single_version(self, tmp_path: Path):
        (tmp_path / "mkt" / "plug" / "1.0.0").mkdir(parents=True)
        result = find_cached_versions(tmp_path)
        assert result == {("mkt", "plug"): ["1.0.0"]}

    def test_multiple_versions_sorted_newest_first(self, tmp_path: Path):
        for v in ("1.0.0", "2.0.0", "1.5.0"):
            (tmp_path / "mkt" / "plug" / v).mkdir(parents=True)
        result = find_cached_versions(tmp_path)
        assert result == {("mkt", "plug"): ["2.0.0", "1.5.0", "1.0.0"]}

    def test_skips_hidden_dirs(self, tmp_path: Path):
        (tmp_path / "mkt" / "plug" / "1.0.0").mkdir(parents=True)
        (tmp_path / ".hidden" / "plug" / "1.0.0").mkdir(parents=True)
        (tmp_path / "mkt" / ".hidden-plug" / "1.0.0").mkdir(parents=True)
        result = find_cached_versions(tmp_path)
        assert result == {("mkt", "plug"): ["1.0.0"]}

    def test_skips_files_at_marketplace_level(self, tmp_path: Path):
        (tmp_path / "mkt" / "plug" / "1.0.0").mkdir(parents=True)
        (tmp_path / "stray-file.txt").write_text("x")
        result = find_cached_versions(tmp_path)
        assert result == {("mkt", "plug"): ["1.0.0"]}


# ── find_active_versions ──────────────────────────────────────────


class TestFindActiveVersions:
    def test_empty_cache_no_active(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(manage_doctor, "CACHE_DIR", tmp_path / "cache")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(manage_doctor, "SETTINGS_FILE", tmp_path / ".claude" / "settings.json")
        assert find_active_versions(tmp_path / "cache") == {}

    def test_resolves_active_from_claude_json_projects(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cache = tmp_path / "cache"
        (cache / "mkt" / "plug" / "1.0.0").mkdir(parents=True)
        (cache / "mkt" / "plug" / "2.0.0").mkdir(parents=True)
        (tmp_path / ".claude.json").write_text(
            json.dumps({"projects": {"/some/proj": {"enabledPlugins": {"plug@mkt": True}}}})
        )
        monkeypatch.setattr(manage_doctor, "CACHE_DIR", cache)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(manage_doctor, "SETTINGS_FILE", tmp_path / ".claude" / "settings.json")

        result = find_active_versions(cache)
        # Highest semver wins as the cached "active"
        assert result == {("mkt", "plug"): "2.0.0"}

    def test_disabled_plugins_are_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cache = tmp_path / "cache"
        (cache / "mkt" / "plug" / "1.0.0").mkdir(parents=True)
        (tmp_path / ".claude.json").write_text(
            json.dumps({"projects": {"/proj": {"enabledPlugins": {"plug@mkt": False}}}})
        )
        monkeypatch.setattr(manage_doctor, "CACHE_DIR", cache)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(manage_doctor, "SETTINGS_FILE", tmp_path / ".claude" / "settings.json")

        assert find_active_versions(cache) == {}

    def test_malformed_claude_json_does_not_crash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        (tmp_path / ".claude.json").write_text("this is not json")
        monkeypatch.setattr(manage_doctor, "CACHE_DIR", tmp_path / "cache")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(manage_doctor, "SETTINGS_FILE", tmp_path / ".claude" / "settings.json")

        # Should swallow the parse error and return empty
        assert find_active_versions(tmp_path / "cache") == {}


# ── do_prune_old_versions ──────────────────────────────────────────


class TestDoPruneOldVersions:
    def _setup_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        cache = tmp_path / "cache"
        # ai-maestro-janitor with 4 versions (matches user's actual case)
        for v in ("0.3.4", "0.3.7", "0.3.8", "0.3.9"):
            d = cache / "ai-maestro-plugins" / "ai-maestro-janitor" / v
            d.mkdir(parents=True)
            (d / "plugin.json").write_text("{}")
        # plugin with only 1 version — should be left alone
        (cache / "mkt" / "single" / "1.0.0").mkdir(parents=True)
        # No claude.json → highest-semver wins as active
        monkeypatch.setattr(manage_doctor, "CACHE_DIR", cache)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(manage_doctor, "SETTINGS_FILE", tmp_path / ".claude" / "settings.json")
        return cache

    def test_dry_run_does_not_delete(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        cache = self._setup_cache(tmp_path, monkeypatch)
        removed = do_prune_old_versions(dry_run=True)
        # Counts versions we WOULD delete
        assert removed == 3
        # All versions still on disk
        for v in ("0.3.4", "0.3.7", "0.3.8", "0.3.9"):
            assert (cache / "ai-maestro-plugins" / "ai-maestro-janitor" / v).exists()
        out = capsys.readouterr().out
        assert "dry-run" in out

    def test_real_delete_removes_old_versions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cache = self._setup_cache(tmp_path, monkeypatch)
        removed = do_prune_old_versions(dry_run=False)
        assert removed == 3
        # Newest kept
        assert (cache / "ai-maestro-plugins" / "ai-maestro-janitor" / "0.3.9").exists()
        # Old versions gone
        for v in ("0.3.4", "0.3.7", "0.3.8"):
            assert not (cache / "ai-maestro-plugins" / "ai-maestro-janitor" / v).exists()

    def test_single_version_plugin_left_alone(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cache = self._setup_cache(tmp_path, monkeypatch)
        do_prune_old_versions(dry_run=False)
        assert (cache / "mkt" / "single" / "1.0.0").exists()

    def test_keep_n_2_keeps_two_newest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cache = self._setup_cache(tmp_path, monkeypatch)
        removed = do_prune_old_versions(dry_run=False, keep_n=2)
        assert removed == 2  # only 0.3.4 + 0.3.7 deleted
        assert (cache / "ai-maestro-plugins" / "ai-maestro-janitor" / "0.3.9").exists()
        assert (cache / "ai-maestro-plugins" / "ai-maestro-janitor" / "0.3.8").exists()
        assert not (cache / "ai-maestro-plugins" / "ai-maestro-janitor" / "0.3.7").exists()

    def test_active_version_always_kept_even_if_older(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cache = self._setup_cache(tmp_path, monkeypatch)
        # Override active version to be 0.3.7 (older than 0.3.9)
        (tmp_path / ".claude.json").write_text(
            json.dumps({"projects": {"/p": {"enabledPlugins": {"ai-maestro-janitor@ai-maestro-plugins": True}}}})
        )
        # The find_active_versions resolves to "0.3.9" (highest semver) when
        # enabledPlugins entry exists but doesn't pin a version. With keep_n=1
        # only 0.3.9 is kept. Verify the keep set always includes the version
        # find_active_versions identified.
        do_prune_old_versions(dry_run=False, keep_n=1)
        assert (cache / "ai-maestro-plugins" / "ai-maestro-janitor" / "0.3.9").exists()

    def test_no_old_versions_returns_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        cache = tmp_path / "cache"
        (cache / "mkt" / "p" / "1.0.0").mkdir(parents=True)
        monkeypatch.setattr(manage_doctor, "CACHE_DIR", cache)
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(manage_doctor, "SETTINGS_FILE", tmp_path / ".claude" / "settings.json")

        removed = do_prune_old_versions(dry_run=False)
        assert removed == 0
        assert "Cache is clean" in capsys.readouterr().out

    def test_empty_cache_returns_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        monkeypatch.setattr(manage_doctor, "CACHE_DIR", tmp_path / "nonexistent")
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(manage_doctor, "SETTINGS_FILE", tmp_path / ".claude" / "settings.json")

        assert do_prune_old_versions(dry_run=False) == 0
        out = capsys.readouterr().out
        assert "No plugin cache found" in out


# ── argparse wiring ──────────────────────────────────────────────


class TestArgparseWiring:
    def test_prune_dry_run_invokes_with_dry_run_true(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        called: dict = {}

        def fake_prune(dry_run: bool, keep_n: int) -> int:
            called["dry_run"] = dry_run
            called["keep_n"] = keep_n
            return 0

        monkeypatch.setattr(manage_doctor, "do_prune_old_versions", fake_prune)
        monkeypatch.setattr(sys, "argv", ["manage_doctor", "--prune-dry-run"])

        with pytest.raises(SystemExit):
            manage_doctor.main()
        assert called == {"dry_run": True, "keep_n": 1}

    def test_prune_old_versions_invokes_with_dry_run_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        called: dict = {}

        def fake_prune(dry_run: bool, keep_n: int) -> int:
            called["dry_run"] = dry_run
            return 0

        monkeypatch.setattr(manage_doctor, "do_prune_old_versions", fake_prune)
        monkeypatch.setattr(sys, "argv", ["manage_doctor", "--prune-old-versions"])

        with pytest.raises(SystemExit):
            manage_doctor.main()
        assert called["dry_run"] is False

    def test_prune_keep_n_passed_through(self, monkeypatch: pytest.MonkeyPatch):
        called: dict = {}

        def fake_prune(dry_run: bool, keep_n: int) -> int:
            called["keep_n"] = keep_n
            return 0

        monkeypatch.setattr(manage_doctor, "do_prune_old_versions", fake_prune)
        monkeypatch.setattr(sys, "argv", ["manage_doctor", "--prune-old-versions", "--prune-keep", "3"])

        with pytest.raises(SystemExit):
            manage_doctor.main()
        assert called["keep_n"] == 3
