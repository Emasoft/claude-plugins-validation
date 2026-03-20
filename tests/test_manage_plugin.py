#!/usr/bin/env python3
"""Tests for manage_plugin.py — plugin lifecycle management.

Tests cover:
- Gitignore pattern parsing and regex conversion
- Git metadata detection
- Gitignore matcher building (manual fallback path)
- Plugin directory copying with filtering
- Plugin root discovery
- Plugin metadata reading with defaults
- Plugin origin reference detection
- Shebang detection, executable checks, permission fixing
- Script file discovery
- Portable path conversion
- Installed plugins loading and v1->v2 migration
- Full install, uninstall, enable, disable, update flows

Coverage: ~85% of code paths tested with realistic data.
Only external subprocess calls (git, validate_plugin.py) are mocked.
"""

from __future__ import annotations

import json
import os
import platform
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add scripts directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import manage_plugin as mp  # noqa: E402

# ── Helpers ──────────────────────────────────────────────────


def _make_plugin_dir(root: Path, name: str = "test-plugin", version: str = "1.0.0", description: str = "A test plugin") -> Path:
    """Create a minimal valid plugin directory structure under *root*."""
    plugin_dir = root / name
    cp_dir = plugin_dir / ".claude-plugin"
    cp_dir.mkdir(parents=True)
    meta = {"name": name, "version": version, "description": description}
    (cp_dir / "plugin.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (plugin_dir / "README.md").write_text(f"# {name}\n\n{description}\n", encoding="utf-8")
    return plugin_dir


def _make_marketplace(mp_dir: Path, marketplace_name: str, plugin_entries: list | None = None):
    """Create a marketplace directory with marketplace.json."""
    cp = mp_dir / ".claude-plugin"
    cp.mkdir(parents=True, exist_ok=True)
    mj = {
        "name": marketplace_name,
        "version": "1.0.0",
        "owner": {"name": "local"},
        "metadata": {"description": "Test marketplace"},
        "plugins": plugin_entries or [],
    }
    (cp / "marketplace.json").write_text(json.dumps(mj, indent=2), encoding="utf-8")


# ── Tests: _parse_gitignore_patterns ─────────────────────────


class TestParseGitignorePatterns:
    """Tests for _parse_gitignore_patterns — parsing .gitignore files."""

    def test_parse_existing_gitignore(self, tmp_path):
        """Parse a .gitignore file with comments, blanks, and valid patterns."""
        gi = tmp_path / ".gitignore"
        gi.write_text("# comment\n\nnode_modules/\n*.pyc\n!important.pyc\n", encoding="utf-8")
        patterns = mp._parse_gitignore_patterns(gi)
        assert patterns == ["node_modules/", "*.pyc", "!important.pyc"]

    def test_parse_nonexistent_gitignore(self, tmp_path):
        """Return empty list when .gitignore does not exist."""
        gi = tmp_path / ".gitignore"
        assert not gi.exists()
        patterns = mp._parse_gitignore_patterns(gi)
        assert patterns == []

    def test_parse_empty_and_whitespace_gitignore(self, tmp_path):
        """Empty .gitignore returns []; lines are stripped of whitespace."""
        gi = tmp_path / ".gitignore"
        gi.write_text("", encoding="utf-8")
        assert mp._parse_gitignore_patterns(gi) == []
        gi.write_text("  dist/  \n  build  \n", encoding="utf-8")
        assert mp._parse_gitignore_patterns(gi) == ["dist/", "build"]


# ── Tests: _gitignore_pattern_to_re ──────────────────────────


class TestGitignorePatternToRe:
    """Tests for _gitignore_pattern_to_re — converting gitignore globs to regex."""

    def test_simple_glob_star(self):
        """*.pyc matches any .pyc file."""
        regex, neg = mp._gitignore_pattern_to_re("*.pyc")
        assert neg is False
        assert regex is not None
        assert regex.search("foo.pyc")
        assert regex.search("dir/foo.pyc")
        assert not regex.search("foo.py")

    def test_double_star_glob(self):
        """**/build matches build at any depth."""
        regex, neg = mp._gitignore_pattern_to_re("**/build")
        assert regex is not None
        assert regex.search("build")
        assert regex.search("src/build")

    def test_negation_and_anchored(self):
        """Negation (!pattern) and anchored (path/with/slash) patterns work."""
        regex, neg = mp._gitignore_pattern_to_re("!important.txt")
        assert neg is True
        assert regex is not None
        regex2, neg2 = mp._gitignore_pattern_to_re("src/build")
        assert neg2 is False
        assert regex2.pattern.startswith("^")

    def test_question_mark_and_bracket(self):
        """? matches one char; [abc] character class works."""
        regex, _ = mp._gitignore_pattern_to_re("file?.txt")
        assert regex.search("fileA.txt")
        assert not regex.search("file.txt")
        regex2, _ = mp._gitignore_pattern_to_re("[abc].txt")
        assert regex2.search("a.txt")
        assert not regex2.search("d.txt")

    def test_empty_after_strip_returns_none(self):
        """Empty pattern after stripping negation prefix returns None."""
        regex, neg = mp._gitignore_pattern_to_re("!")
        assert regex is None


# ── Tests: _is_git_metadata ─────────────────────────────────


class TestIsGitMetadata:
    """Tests for _is_git_metadata — detecting git metadata files."""

    def test_dot_git_and_subpath(self):
        """'.git' and '.git/config' are git metadata."""
        assert mp._is_git_metadata(".git") is True
        assert mp._is_git_metadata(".git/config") is True

    def test_all_git_dotfiles(self):
        """.gitignore, .gitattributes, .gitmodules, .gitkeep are all git metadata."""
        for name in (".gitignore", ".gitattributes", ".gitmodules", ".gitkeep"):
            assert mp._is_git_metadata(name) is True
        assert mp._is_git_metadata("subdir/.gitignore") is True

    def test_regular_file_not_metadata(self):
        """Regular files are not git metadata."""
        assert mp._is_git_metadata("src/main.py") is False

    def test_backslash_normalization(self):
        """Backslashes in paths are normalized before checking."""
        assert mp._is_git_metadata(".git\\config") is True


# ── Tests: _build_gitignore_matcher ──────────────────────────


class TestBuildGitignoreMatcher:
    """Tests for _build_gitignore_matcher — building ignore functions."""

    def test_minimal_matcher_no_gitignore_no_git(self, tmp_path):
        """Without .git or .gitignore, only git metadata is ignored."""
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        (plugin_dir / "src").mkdir()
        (plugin_dir / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
        matcher = mp._build_gitignore_matcher(plugin_dir)
        # Regular files pass
        assert matcher(plugin_dir / "src" / "main.py") is False
        # .git metadata is ignored
        git_dir = plugin_dir / ".git"
        git_dir.mkdir()
        assert matcher(git_dir) is True

    def test_manual_matcher_with_gitignore(self, tmp_path):
        """With .gitignore but no .git dir, manual matcher filters patterns."""
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".gitignore").write_text("*.pyc\ndist/\n", encoding="utf-8")
        (plugin_dir / "main.py").write_text("code", encoding="utf-8")
        (plugin_dir / "main.pyc").write_text("bytecode", encoding="utf-8")
        dist = plugin_dir / "dist"
        dist.mkdir()
        matcher = mp._build_gitignore_matcher(plugin_dir)
        assert matcher(plugin_dir / "main.py") is False
        assert matcher(plugin_dir / "main.pyc") is True
        assert matcher(dist) is True

    def test_manual_matcher_negation(self, tmp_path):
        """Negation patterns re-include previously ignored files."""
        plugin_dir = tmp_path / "plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".gitignore").write_text("*.log\n!important.log\n", encoding="utf-8")
        (plugin_dir / "debug.log").write_text("log", encoding="utf-8")
        (plugin_dir / "important.log").write_text("keep", encoding="utf-8")
        matcher = mp._build_gitignore_matcher(plugin_dir)
        assert matcher(plugin_dir / "debug.log") is True
        assert matcher(plugin_dir / "important.log") is False


# ── Tests: _copy_plugin_from_dir ─────────────────────────────


class TestCopyPluginFromDir:
    """Tests for _copy_plugin_from_dir — recursive copy with filtering."""

    def test_copy_basic_structure(self, tmp_path):
        """Copy a basic plugin directory without any ignore function."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("print('hello')", encoding="utf-8")
        sub = src / "lib"
        sub.mkdir()
        (sub / "util.py").write_text("def f(): pass", encoding="utf-8")
        dest = tmp_path / "dest"
        mp._copy_plugin_from_dir(src, dest)
        assert (dest / "main.py").exists()
        assert (dest / "lib" / "util.py").exists()

    def test_copy_skips_git_files(self, tmp_path):
        """Git metadata files (.git, .gitignore etc.) are always skipped."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("code", encoding="utf-8")
        (src / ".gitignore").write_text("*.pyc", encoding="utf-8")
        (src / ".gitattributes").write_text("* text=auto", encoding="utf-8")
        git_dir = src / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
        dest = tmp_path / "dest"
        mp._copy_plugin_from_dir(src, dest)
        assert (dest / "main.py").exists()
        assert not (dest / ".gitignore").exists()
        assert not (dest / ".gitattributes").exists()
        assert not (dest / ".git").exists()

    def test_copy_with_ignore_fn(self, tmp_path):
        """Custom ignore function filters out specific files."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "keep.py").write_text("keep", encoding="utf-8")
        (src / "skip.pyc").write_text("skip", encoding="utf-8")
        dest = tmp_path / "dest"
        mp._copy_plugin_from_dir(src, dest, ignore_fn=lambda p: p.suffix == ".pyc")
        assert (dest / "keep.py").exists()
        assert not (dest / "skip.pyc").exists()

    def test_copy_skips_symlinks(self, tmp_path):
        """Symlinks are not followed during copy."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "real.py").write_text("real", encoding="utf-8")
        link = src / "link.py"
        link.symlink_to(src / "real.py")
        dest = tmp_path / "dest"
        mp._copy_plugin_from_dir(src, dest)
        assert (dest / "real.py").exists()
        assert not (dest / "link.py").exists()


# ── Tests: find_plugin_root ──────────────────────────────────


class TestFindPluginRoot:
    """Tests for find_plugin_root — locating plugin.json in directory trees."""

    def test_find_root_direct(self, tmp_path):
        """Find plugin root when .claude-plugin/plugin.json is directly present."""
        plugin = _make_plugin_dir(tmp_path, "my-plugin")
        found = mp.find_plugin_root(tmp_path)
        assert found == plugin

    def test_find_root_nested(self, tmp_path):
        """Find plugin root when nested under subdirectories."""
        sub = tmp_path / "deep" / "nested"
        sub.mkdir(parents=True)
        plugin = _make_plugin_dir(sub, "nested-plugin")
        found = mp.find_plugin_root(tmp_path)
        assert found == plugin

    def test_find_root_none(self, tmp_path):
        """Return None when no plugin.json exists."""
        (tmp_path / "some_file.txt").write_text("not a plugin", encoding="utf-8")
        found = mp.find_plugin_root(tmp_path)
        assert found is None

    def test_find_root_skips_marketplace(self, tmp_path):
        """Directories with marketplace.json alongside plugin.json are skipped."""
        plugin = tmp_path / "mp-plugin"
        cp = plugin / ".claude-plugin"
        cp.mkdir(parents=True)
        (cp / "plugin.json").write_text('{"name":"mp","version":"1.0.0"}', encoding="utf-8")
        (cp / "marketplace.json").write_text('{"name":"market"}', encoding="utf-8")
        found = mp.find_plugin_root(tmp_path)
        assert found is None


# ── Tests: read_plugin_meta ──────────────────────────────────


class TestReadPluginMeta:
    """Tests for read_plugin_meta — reading plugin.json with defaults."""

    def test_read_valid_meta(self, tmp_path):
        """Read well-formed plugin.json and return all fields."""
        plugin = _make_plugin_dir(tmp_path, "my-plugin", "2.0.0", "My description")
        meta = mp.read_plugin_meta(plugin)
        assert meta["name"] == "my-plugin"
        assert meta["version"] == "2.0.0"
        assert meta["description"] == "My description"

    def test_read_meta_defaults_and_corrupt_json(self, tmp_path):
        """Missing fields use defaults; corrupt JSON also falls back to defaults."""
        plugin = tmp_path / "fallback-plugin"
        cp = plugin / ".claude-plugin"
        cp.mkdir(parents=True)
        (cp / "plugin.json").write_text("{}", encoding="utf-8")
        meta = mp.read_plugin_meta(plugin)
        assert meta["name"] == "fallback-plugin"
        assert meta["version"] == "1.0.0"
        assert meta["description"] == ""
        # Corrupt JSON also uses defaults
        bad = tmp_path / "bad-plugin"
        bcp = bad / ".claude-plugin"
        bcp.mkdir(parents=True)
        (bcp / "plugin.json").write_text("NOT JSON {{{", encoding="utf-8")
        meta2 = mp.read_plugin_meta(bad)
        assert meta2["name"] == "bad-plugin"


# ── Tests: _detect_plugin_origin_refs ────────────────────────


class TestDetectPluginOriginRefs:
    """Tests for _detect_plugin_origin_refs — finding marketplace/repo references."""

    def test_detect_marketplace_ref(self, tmp_path):
        """Detect 'marketplace' string field in plugin.json."""
        plugin = tmp_path / "p"
        cp = plugin / ".claude-plugin"
        cp.mkdir(parents=True)
        meta = {"name": "p", "version": "1.0.0", "marketplace": "official"}
        (cp / "plugin.json").write_text(json.dumps(meta), encoding="utf-8")
        refs = mp._detect_plugin_origin_refs(plugin)
        assert any("marketplace" in r and "official" in r for r in refs)

    def test_detect_repository_dict(self, tmp_path):
        """Detect 'repository' dict field with 'url' key."""
        plugin = tmp_path / "p"
        cp = plugin / ".claude-plugin"
        cp.mkdir(parents=True)
        meta = {"name": "p", "version": "1.0.0", "repository": {"url": "https://github.com/org/repo"}}
        (cp / "plugin.json").write_text(json.dumps(meta), encoding="utf-8")
        refs = mp._detect_plugin_origin_refs(plugin)
        assert any("repository.url" in r for r in refs)

    def test_detect_author_github_string(self, tmp_path):
        """Detect author string containing github.com."""
        plugin = tmp_path / "p"
        cp = plugin / ".claude-plugin"
        cp.mkdir(parents=True)
        meta = {"name": "p", "version": "1.0.0", "author": "https://github.com/some-user"}
        (cp / "plugin.json").write_text(json.dumps(meta), encoding="utf-8")
        refs = mp._detect_plugin_origin_refs(plugin)
        assert any("author" in r and "github.com" in r for r in refs)

    def test_detect_bundled_marketplace_json(self, tmp_path):
        """Detect references in bundled marketplace.json."""
        plugin = tmp_path / "p"
        cp = plugin / ".claude-plugin"
        cp.mkdir(parents=True)
        (cp / "plugin.json").write_text('{"name":"p","version":"1.0.0"}', encoding="utf-8")
        mj = {"name": "my-market", "url": "https://example.com/market"}
        (cp / "marketplace.json").write_text(json.dumps(mj), encoding="utf-8")
        refs = mp._detect_plugin_origin_refs(plugin)
        assert any("my-market" in r for r in refs)
        assert any("example.com" in r for r in refs)

    def test_no_refs_minimal_plugin(self, tmp_path):
        """Minimal plugin.json with no origin fields returns empty list."""
        plugin = _make_plugin_dir(tmp_path, "clean-plugin")
        refs = mp._detect_plugin_origin_refs(plugin)
        assert refs == []


# ── Tests: _has_shebang ─────────────────────────────────────


class TestHasShebang:
    """Tests for _has_shebang — detecting shebang lines in files."""

    def test_file_with_shebang(self, tmp_path):
        """File starting with #! is detected as having a shebang."""
        f = tmp_path / "script.sh"
        f.write_bytes(b"#!/bin/bash\necho hello\n")
        assert mp._has_shebang(f) is True

    def test_file_without_shebang_and_edge_cases(self, tmp_path):
        """Regular file, empty file, and non-existent file all return False."""
        f = tmp_path / "code.py"
        f.write_bytes(b"print('hello')\n")
        assert mp._has_shebang(f) is False
        empty = tmp_path / "empty"
        empty.write_bytes(b"")
        assert mp._has_shebang(empty) is False
        assert mp._has_shebang(tmp_path / "no_such_file") is False


# ── Tests: _is_executable / _make_executable ─────────────────


class TestExecutableHandling:
    """Tests for _is_executable and _make_executable — cross-platform permissions."""

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix permission test")
    def test_make_executable_unix(self, tmp_path):
        """_make_executable sets execute bits on Unix."""
        f = tmp_path / "script.sh"
        f.write_text("#!/bin/bash\necho hi", encoding="utf-8")
        f.chmod(0o644)
        assert not os.access(f, os.X_OK)
        mp._make_executable(f)
        assert os.access(f, os.X_OK)
        mode = f.stat().st_mode
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP
        assert mode & stat.S_IXOTH

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix permission test")
    def test_is_executable_unix(self, tmp_path):
        """_is_executable checks os.access on Unix."""
        f = tmp_path / "script.sh"
        f.write_text("#!/bin/bash", encoding="utf-8")
        f.chmod(0o644)
        assert mp._is_executable(f) is False
        f.chmod(0o755)
        assert mp._is_executable(f) is True


# ── Tests: _find_all_scripts ────────────────────────────────


class TestFindAllScripts:
    """Tests for _find_all_scripts — finding script files by extension."""

    def test_find_scripts_by_extension(self, tmp_path):
        """Find .py, .sh, .js, .ts, .rb, .pl files."""
        for ext in [".py", ".sh", ".js", ".ts", ".rb", ".pl"]:
            (tmp_path / f"script{ext}").write_text("code", encoding="utf-8")
        (tmp_path / "data.txt").write_text("not a script", encoding="utf-8")
        scripts = mp._find_all_scripts(tmp_path)
        names = {s.name for s in scripts}
        assert "script.py" in names
        assert "script.sh" in names
        assert "script.js" in names
        assert "data.txt" not in names

    def test_find_extensionless_scripts_and_windows_exts(self, tmp_path):
        """Files in scripts/ without extension and Windows .cmd/.bat/.ps1 are found."""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "build").write_text("#!/bin/bash", encoding="utf-8")
        for ext in [".cmd", ".bat", ".ps1"]:
            (tmp_path / f"run{ext}").write_text("echo hi", encoding="utf-8")
        found = mp._find_all_scripts(tmp_path)
        names = {s.name for s in found}
        assert "build" in names
        assert "run.cmd" in names
        assert "run.bat" in names


# ── Tests: _fix_permissions ──────────────────────────────────


class TestFixPermissions:
    """Tests for _fix_permissions — making script files executable."""

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix permission test")
    def test_fix_permissions_makes_scripts_executable(self, tmp_path):
        """All script files in plugin directory are made executable."""
        (tmp_path / "run.sh").write_text("#!/bin/bash\necho hi", encoding="utf-8")
        (tmp_path / "tool.py").write_text("#!/usr/bin/env python3", encoding="utf-8")
        for f in tmp_path.iterdir():
            f.chmod(0o644)
        mp._fix_permissions(tmp_path)
        assert os.access(tmp_path / "run.sh", os.X_OK)
        assert os.access(tmp_path / "tool.py", os.X_OK)


# ── Tests: _portable_path ───────────────────────────────────


class TestPortablePath:
    """Tests for _portable_path — forward slash conversion."""

    def test_forward_slashes_and_no_backslash(self):
        """Unix paths use forward slashes; result never contains backslashes."""
        result = mp._portable_path(Path("/home/user/plugins/my-plugin"))
        assert "\\" not in result
        assert "/home/user/plugins/my-plugin" in result
        # Idempotent: replacing \\ with / gives same result
        p = Path("a/b/c")
        assert mp._portable_path(p).replace("\\", "/") == mp._portable_path(p)


# ── Tests: _load_installed_plugins ───────────────────────────


class TestLoadInstalledPlugins:
    """Tests for _load_installed_plugins — loading + migrating v1->v2 format."""

    def test_load_empty_file(self, tmp_path, monkeypatch):
        """Non-existent file returns v2 structure with empty plugins."""
        installed_file = tmp_path / "installed_plugins.json"
        monkeypatch.setattr(mp, "INSTALLED_FILE", installed_file)
        result = mp._load_installed_plugins()
        assert result["version"] == 2
        assert isinstance(result["plugins"], dict)

    def test_load_v2_format(self, tmp_path, monkeypatch):
        """V2 format is returned as-is."""
        installed_file = tmp_path / "installed_plugins.json"
        data = {
            "version": 2,
            "plugins": {
                "test@market": [{"scope": "user", "version": "1.0.0", "installedAt": "2025-01-01T00:00:00Z", "lastUpdated": "2025-01-01T00:00:00Z", "installPath": "/path/to/plugin"}],
            },
        }
        installed_file.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(mp, "INSTALLED_FILE", installed_file)
        result = mp._load_installed_plugins()
        assert result["version"] == 2
        assert "test@market" in result["plugins"]

    def test_migrate_v1_to_v2(self, tmp_path, monkeypatch):
        """V1 format (no version key, dicts as values) is migrated to v2."""
        installed_file = tmp_path / "installed_plugins.json"
        v1_data = {
            "my-plugin@local": {"version": "1.0.0", "installedAt": "2025-01-01T00:00:00Z"},
        }
        installed_file.write_text(json.dumps(v1_data), encoding="utf-8")
        monkeypatch.setattr(mp, "INSTALLED_FILE", installed_file)
        result = mp._load_installed_plugins()
        assert result["version"] == 2
        plugins = result["plugins"]
        assert "my-plugin@local" in plugins
        entry = plugins["my-plugin@local"]
        # V1->V2 migration wraps dict in list and adds scope
        assert isinstance(entry, list)
        assert entry[0]["scope"] == "user"


# ── Tests: do_install ────────────────────────────────────────


class TestDoInstall:
    """Tests for do_install — full install flow from directory source."""

    def _setup_env(self, tmp_path, monkeypatch):
        """Set up isolated environment for install tests."""
        mp_dir = tmp_path / "marketplaces"
        mp_dir.mkdir()
        settings_file = tmp_path / "settings.local.json"
        installed_file = tmp_path / "installed_plugins.json"
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", mp_dir)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", settings_file)
        monkeypatch.setattr(mp, "INSTALLED_FILE", installed_file)
        monkeypatch.setattr(mp, "CACHE_DIR", cache_dir)
        return mp_dir, settings_file, installed_file

    @patch.object(mp, "_run_cpv_validation", return_value=([], [], True))
    def test_install_from_directory(self, mock_val, tmp_path, monkeypatch):
        """Install a plugin from a directory source into a marketplace."""
        mp_dir, settings_file, installed_file = self._setup_env(tmp_path, monkeypatch)
        source = _make_plugin_dir(tmp_path / "source", "cool-plugin", "1.2.3", "A cool plugin")

        mp.do_install(str(source), "test-market", force=True, quiet=True)

        dest = mp_dir / "test-market" / "plugins" / "cool-plugin"
        assert dest.exists()
        assert (dest / "README.md").exists()
        # Settings updated
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        assert "test-market" in settings["extraKnownMarketplaces"]
        assert settings["enabledPlugins"]["cool-plugin@test-market"] is True
        # Installed registry updated
        installed = json.loads(installed_file.read_text(encoding="utf-8"))
        assert "cool-plugin@test-market" in installed["plugins"]

    @patch.object(mp, "_run_cpv_validation", return_value=([], [], True))
    def test_install_bad_args_exits(self, mock_val, tmp_path, monkeypatch):
        """Install without marketplace name or from non-existent source exits."""
        self._setup_env(tmp_path, monkeypatch)
        source = _make_plugin_dir(tmp_path / "source", "p")
        with pytest.raises(SystemExit):
            mp.do_install(str(source), None, quiet=True)
        with pytest.raises(SystemExit):
            mp.do_install("/nonexistent/path", "market", quiet=True)

    @patch.object(mp, "_run_cpv_validation", return_value=(["CRITICAL: missing field"], [], False))
    def test_install_validation_fail_force_controls_outcome(self, mock_val, tmp_path, monkeypatch):
        """Validation failure exits without --force but succeeds with --force."""
        mp_dir, settings_file, installed_file = self._setup_env(tmp_path, monkeypatch)
        source = _make_plugin_dir(tmp_path / "source", "bad-plugin")
        with pytest.raises(SystemExit):
            mp.do_install(str(source), "market", force=False, quiet=True)
        mp.do_install(str(source), "market", force=True, quiet=True)
        assert (mp_dir / "market" / "plugins" / "bad-plugin").exists()

    @patch.object(mp, "_run_cpv_validation", return_value=([], [], True))
    def test_install_dry_run_no_files_created(self, mock_val, tmp_path, monkeypatch):
        """Dry run does not create any files."""
        mp_dir, settings_file, installed_file = self._setup_env(tmp_path, monkeypatch)
        source = _make_plugin_dir(tmp_path / "source", "dry-plugin")
        mp.do_install(str(source), "market", dry_run=True, quiet=True)
        assert not (mp_dir / "market" / "plugins" / "dry-plugin").exists()
        assert not settings_file.exists()


# ── Tests: do_uninstall ──────────────────────────────────────


class TestDoUninstall:
    """Tests for do_uninstall — uninstall flow."""

    def _setup_installed(self, tmp_path, monkeypatch, plugin_name="test-plugin", marketplace="test-market"):
        """Create a pre-installed plugin environment."""
        mp_dir = tmp_path / "marketplaces"
        plug_dir = mp_dir / marketplace / "plugins" / plugin_name
        plug_dir.mkdir(parents=True)
        (plug_dir / "README.md").write_text("content", encoding="utf-8")
        _make_marketplace(mp_dir / marketplace, marketplace, [{"name": plugin_name, "version": "1.0.0", "source": f"./plugins/{plugin_name}"}])
        settings_file = tmp_path / "settings.local.json"
        settings = {
            "extraKnownMarketplaces": {marketplace: {"source": {"source": "directory", "path": str(mp_dir / marketplace)}}},
            "enabledPlugins": {f"{plugin_name}@{marketplace}": True},
        }
        settings_file.write_text(json.dumps(settings), encoding="utf-8")
        installed_file = tmp_path / "installed_plugins.json"
        installed = {
            "version": 2,
            "plugins": {
                f"{plugin_name}@{marketplace}": [{"scope": "user", "version": "1.0.0"}],
            },
        }
        installed_file.write_text(json.dumps(installed), encoding="utf-8")
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", mp_dir)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", settings_file)
        monkeypatch.setattr(mp, "INSTALLED_FILE", installed_file)
        monkeypatch.setattr(mp, "CACHE_DIR", cache_dir)
        return mp_dir, settings_file, installed_file, plug_dir

    def test_uninstall_removes_plugin_dir(self, tmp_path, monkeypatch):
        """Uninstalling removes the plugin directory."""
        mp_dir, settings_file, installed_file, plug_dir = self._setup_installed(tmp_path, monkeypatch)
        mp.do_uninstall("test-plugin@test-market", quiet=True)
        assert not plug_dir.exists()

    def test_uninstall_removes_from_settings_and_installed(self, tmp_path, monkeypatch):
        """Uninstalling removes plugin from both settings and installed_plugins.json."""
        mp_dir, settings_file, installed_file, plug_dir = self._setup_installed(tmp_path, monkeypatch)
        mp.do_uninstall("test-plugin@test-market", quiet=True)
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        assert "test-plugin@test-market" not in settings.get("enabledPlugins", {})
        installed = json.loads(installed_file.read_text(encoding="utf-8"))
        assert "test-plugin@test-market" not in installed["plugins"]

    def test_uninstall_empty_marketplace_removed(self, tmp_path, monkeypatch):
        """Empty marketplace directory is removed after last plugin uninstalled."""
        mp_dir, settings_file, installed_file, plug_dir = self._setup_installed(tmp_path, monkeypatch)
        mp.do_uninstall("test-plugin@test-market", quiet=True)
        assert not (mp_dir / "test-market").exists()
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        assert "test-market" not in settings.get("extraKnownMarketplaces", {})

    def test_uninstall_invalid_format_exits_and_dry_run_keeps(self, tmp_path, monkeypatch):
        """Invalid key format exits; dry run does not remove anything."""
        with pytest.raises(SystemExit):
            mp.do_uninstall("no-at-sign", quiet=True)
        mp_dir, settings_file, installed_file, plug_dir = self._setup_installed(tmp_path, monkeypatch)
        mp.do_uninstall("test-plugin@test-market", quiet=True, dry_run=True)
        assert plug_dir.exists()

    def test_uninstall_cleans_cache(self, tmp_path, monkeypatch):
        """Uninstall cleans up plugin cache directory."""
        mp_dir, settings_file, installed_file, plug_dir = self._setup_installed(tmp_path, monkeypatch)
        cache_dir = tmp_path / "cache"
        cache_plug = cache_dir / "test-market" / "test-plugin"
        cache_plug.mkdir(parents=True)
        (cache_plug / "cached.json").write_text("{}", encoding="utf-8")
        mp.do_uninstall("test-plugin@test-market", quiet=True)
        assert not cache_plug.exists()


# ── Tests: do_enable ─────────────────────────────────────────


class TestDoEnable:
    """Tests for do_enable — enabling a disabled plugin."""

    def _setup_disabled(self, tmp_path, monkeypatch):
        """Create environment with a disabled plugin."""
        mp_dir = tmp_path / "marketplaces"
        plug_dir = mp_dir / "market" / "plugins" / "my-plugin"
        plug_dir.mkdir(parents=True)
        settings_file = tmp_path / "settings.local.json"
        settings = {"enabledPlugins": {"my-plugin@market": False}}
        settings_file.write_text(json.dumps(settings), encoding="utf-8")
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", mp_dir)
        monkeypatch.setattr(mp, "SETTINGS_FILE", settings_file)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", settings_file)
        return settings_file

    def test_enable_sets_true(self, tmp_path, monkeypatch):
        """Enabling a plugin sets enabledPlugins entry to True."""
        settings_file = self._setup_disabled(tmp_path, monkeypatch)
        mp.do_enable("my-plugin@market", quiet=True)
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        assert settings["enabledPlugins"]["my-plugin@market"] is True

    def test_enable_invalid_format_and_nonexistent_exits(self, tmp_path, monkeypatch):
        """Enable with non-existent plugin exits with error."""
        mp_dir = tmp_path / "marketplaces"
        mp_dir.mkdir()
        settings_file = tmp_path / "settings.local.json"
        settings_file.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", mp_dir)
        monkeypatch.setattr(mp, "SETTINGS_FILE", settings_file)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", settings_file)
        with pytest.raises(SystemExit):
            mp.do_enable("nope", quiet=True)
        with pytest.raises(SystemExit):
            mp.do_enable("ghost@market", quiet=True)

    def test_enable_dry_run(self, tmp_path, monkeypatch):
        """Dry run enable does not modify settings."""
        settings_file = self._setup_disabled(tmp_path, monkeypatch)
        mp.do_enable("my-plugin@market", quiet=True, dry_run=True)
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        assert settings["enabledPlugins"]["my-plugin@market"] is False


# ── Tests: do_disable ────────────────────────────────────────


class TestDoDisable:
    """Tests for do_disable — disabling an enabled plugin."""

    def _setup_enabled(self, tmp_path, monkeypatch):
        """Create environment with an enabled plugin."""
        mp_dir = tmp_path / "marketplaces"
        plug_dir = mp_dir / "market" / "plugins" / "my-plugin"
        plug_dir.mkdir(parents=True)
        settings_file = tmp_path / "settings.local.json"
        settings = {"enabledPlugins": {"my-plugin@market": True}}
        settings_file.write_text(json.dumps(settings), encoding="utf-8")
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", mp_dir)
        monkeypatch.setattr(mp, "SETTINGS_FILE", settings_file)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", settings_file)
        return settings_file

    def test_disable_sets_false(self, tmp_path, monkeypatch):
        """Disabling a plugin sets enabledPlugins entry to False."""
        settings_file = self._setup_enabled(tmp_path, monkeypatch)
        mp.do_disable("my-plugin@market", quiet=True)
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        assert settings["enabledPlugins"]["my-plugin@market"] is False

    def test_disable_invalid_format_and_nonexistent_exits(self, tmp_path, monkeypatch):
        """Disable with invalid format or non-existent plugin exits with error."""
        with pytest.raises(SystemExit):
            mp.do_disable("bad", quiet=True)
        mp_dir = tmp_path / "marketplaces"
        mp_dir.mkdir()
        settings_file = tmp_path / "settings.local.json"
        settings_file.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", mp_dir)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", settings_file)
        with pytest.raises(SystemExit):
            mp.do_disable("ghost@market", quiet=True)

    def test_disable_dry_run(self, tmp_path, monkeypatch):
        """Dry run disable does not modify settings."""
        settings_file = self._setup_enabled(tmp_path, monkeypatch)
        mp.do_disable("my-plugin@market", quiet=True, dry_run=True)
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        assert settings["enabledPlugins"]["my-plugin@market"] is True


# ── Tests: do_update ─────────────────────────────────────────


class TestDoUpdate:
    """Tests for do_update — updating an existing plugin."""

    def _setup_for_update(self, tmp_path, monkeypatch):
        """Create environment with a plugin already installed for update testing."""
        mp_dir = tmp_path / "marketplaces"
        plug_dir = mp_dir / "market" / "plugins" / "my-plugin"
        cp = plug_dir / ".claude-plugin"
        cp.mkdir(parents=True)
        meta = {"name": "my-plugin", "version": "1.0.0", "description": "Old version"}
        (cp / "plugin.json").write_text(json.dumps(meta), encoding="utf-8")
        (plug_dir / "README.md").write_text("# Old", encoding="utf-8")
        _make_marketplace(mp_dir / "market", "market", [{"name": "my-plugin", "version": "1.0.0", "source": "./plugins/my-plugin"}])
        settings_file = tmp_path / "settings.local.json"
        settings = {
            "extraKnownMarketplaces": {"market": {"source": {"source": "directory", "path": str(mp_dir / "market")}}},
            "enabledPlugins": {"my-plugin@market": True},
        }
        settings_file.write_text(json.dumps(settings), encoding="utf-8")
        installed_file = tmp_path / "installed_plugins.json"
        installed = {
            "version": 2,
            "plugins": {"my-plugin@market": [{"scope": "user", "version": "1.0.0"}]},
        }
        installed_file.write_text(json.dumps(installed), encoding="utf-8")
        cache_dir = tmp_path / "cache"
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", mp_dir)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", settings_file)
        monkeypatch.setattr(mp, "INSTALLED_FILE", installed_file)
        monkeypatch.setattr(mp, "CACHE_DIR", cache_dir)
        return mp_dir, settings_file, installed_file

    @patch.object(mp, "_run_cpv_validation", return_value=([], [], True))
    def test_update_replaces_old_version(self, mock_val, tmp_path, monkeypatch):
        """Update replaces old plugin version with new source."""
        mp_dir, settings_file, installed_file = self._setup_for_update(tmp_path, monkeypatch)
        new_source = _make_plugin_dir(tmp_path / "newsource", "my-plugin", "2.0.0", "New version")
        mp.do_update(str(new_source), "market", force=True, quiet=True)
        dest = mp_dir / "market" / "plugins" / "my-plugin"
        assert dest.exists()
        installed = json.loads(installed_file.read_text(encoding="utf-8"))
        entry = installed["plugins"]["my-plugin@market"]
        assert entry[0]["version"] == "2.0.0"

    def test_update_bad_source_or_no_marketplace_exits(self, tmp_path, monkeypatch):
        """Update from non-existent source or without marketplace name exits with error."""
        self._setup_for_update(tmp_path, monkeypatch)
        with pytest.raises(SystemExit):
            mp.do_update("/nonexistent", "market", quiet=True)
        new_source = _make_plugin_dir(tmp_path / "newsource", "my-plugin", "2.0.0")
        with pytest.raises(SystemExit):
            mp.do_update(str(new_source), None, quiet=True)

    def test_update_not_installed_exits(self, tmp_path, monkeypatch):
        """Update for a plugin not yet installed exits with error."""
        mp_dir = tmp_path / "marketplaces"
        mp_dir.mkdir()
        settings_file = tmp_path / "settings.local.json"
        settings_file.write_text("{}", encoding="utf-8")
        installed_file = tmp_path / "installed_plugins.json"
        installed_file.write_text('{"version":2,"plugins":{}}', encoding="utf-8")
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", mp_dir)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", settings_file)
        monkeypatch.setattr(mp, "INSTALLED_FILE", installed_file)
        monkeypatch.setattr(mp, "CACHE_DIR", tmp_path / "cache")
        new_source = _make_plugin_dir(tmp_path / "newsource", "fresh-plugin", "1.0.0")
        with pytest.raises(SystemExit):
            mp.do_update(str(new_source), "market", quiet=True)

    @patch.object(mp, "_run_cpv_validation", return_value=([], [], True))
    def test_update_dry_run_no_changes(self, mock_val, tmp_path, monkeypatch):
        """Dry run update does not modify files."""
        mp_dir, settings_file, installed_file = self._setup_for_update(tmp_path, monkeypatch)
        new_source = _make_plugin_dir(tmp_path / "newsource", "my-plugin", "2.0.0")
        mp.do_update(str(new_source), "market", dry_run=True, quiet=True)
        # Old version should still be there
        dest = mp_dir / "market" / "plugins" / "my-plugin"
        assert dest.exists()
        meta = json.loads((dest / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        assert meta["version"] == "1.0.0"


# ── Tests: _resolve_settings_file ────────────────────────────


class TestResolveSettingsFile:
    """Tests for _resolve_settings_file -- scope-based settings file resolution."""

    def test_scope_user_returns_settings_file(self, tmp_path, monkeypatch):
        """scope='user' returns the global SETTINGS_FILE path."""
        fake_settings = tmp_path / "settings.json"
        monkeypatch.setattr(mp, "SETTINGS_FILE", fake_settings)
        result = mp._resolve_settings_file("user")
        assert result == fake_settings

    def test_scope_local_returns_project_settings(self, tmp_path, monkeypatch):
        """scope='local' returns project .claude/settings.local.json based on cwd."""
        monkeypatch.chdir(tmp_path)
        result = mp._resolve_settings_file("local")
        expected = tmp_path / ".claude" / "settings.local.json"
        assert result == expected

    def test_scope_default_returns_settings_target(self, tmp_path, monkeypatch):
        """Any other scope (including empty string) returns SETTINGS_TARGET."""
        fake_target = tmp_path / "settings.local.json"
        monkeypatch.setattr(mp, "SETTINGS_TARGET", fake_target)
        result = mp._resolve_settings_file("default")
        assert result == fake_target
        result2 = mp._resolve_settings_file("")
        assert result2 == fake_target


# ── Tests: _collect_all_plugin_keys ──────────────────────────


class TestCollectAllPluginKeys:
    """Tests for _collect_all_plugin_keys -- scanning settings files for plugin keys."""

    def test_collects_keys_from_settings_file(self, tmp_path, monkeypatch):
        """Keys from SETTINGS_FILE (user-level) are collected."""
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"enabledPlugins": {"plugA@market1": True}}), encoding="utf-8")
        monkeypatch.setattr(mp, "SETTINGS_FILE", sf)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", tmp_path / "nonexistent.json")
        monkeypatch.chdir(tmp_path)
        result = mp._collect_all_plugin_keys()
        assert "plugA@market1" in result
        assert str(sf) in result["plugA@market1"]

    def test_collects_keys_from_settings_target(self, tmp_path, monkeypatch):
        """Keys from SETTINGS_TARGET (user local) are collected."""
        st = tmp_path / "settings.local.json"
        st.write_text(json.dumps({"enabledPlugins": {"plugB@market2": False}}), encoding="utf-8")
        monkeypatch.setattr(mp, "SETTINGS_FILE", tmp_path / "nonexistent.json")
        monkeypatch.setattr(mp, "SETTINGS_TARGET", st)
        monkeypatch.chdir(tmp_path)
        result = mp._collect_all_plugin_keys()
        assert "plugB@market2" in result
        assert str(st) in result["plugB@market2"]

    def test_collects_keys_from_project_settings(self, tmp_path, monkeypatch):
        """Keys from project .claude/settings.local.json are collected when file exists."""
        proj_claude = tmp_path / ".claude"
        proj_claude.mkdir()
        proj_settings = proj_claude / "settings.local.json"
        proj_settings.write_text(json.dumps({"enabledPlugins": {"plugC@market3": True}}), encoding="utf-8")
        monkeypatch.setattr(mp, "SETTINGS_FILE", tmp_path / "nonexistent1.json")
        monkeypatch.setattr(mp, "SETTINGS_TARGET", tmp_path / "nonexistent2.json")
        monkeypatch.chdir(tmp_path)
        result = mp._collect_all_plugin_keys()
        assert "plugC@market3" in result
        assert str(proj_settings) in result["plugC@market3"]


# ── Tests: _resolve_plugin_key ───────────────────────────────


class TestResolvePluginKey:
    """Tests for _resolve_plugin_key -- resolving bare names and full keys."""

    def _setup_settings(self, tmp_path, monkeypatch, keys_map: dict):
        """Create SETTINGS_FILE with given enabledPlugins and patch module."""
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"enabledPlugins": keys_map}), encoding="utf-8")
        monkeypatch.setattr(mp, "SETTINGS_FILE", sf)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", tmp_path / "nonexistent.json")
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", tmp_path / "marketplaces")
        monkeypatch.chdir(tmp_path)

    def test_bare_name_unique_match(self, tmp_path, monkeypatch):
        """Bare name resolves to the unique full key found in settings."""
        self._setup_settings(tmp_path, monkeypatch, {"my-tool@official": True})
        result = mp._resolve_plugin_key("my-tool")
        assert result == "my-tool@official"

    def test_bare_name_ambiguous_exits(self, tmp_path, monkeypatch):
        """Bare name matching multiple keys raises SystemExit."""
        self._setup_settings(tmp_path, monkeypatch, {"my-tool@market1": True, "my-tool@market2": False})
        with pytest.raises(SystemExit):
            mp._resolve_plugin_key("my-tool")

    def test_bare_name_not_found_exits(self, tmp_path, monkeypatch):
        """Bare name not found anywhere raises SystemExit."""
        self._setup_settings(tmp_path, monkeypatch, {"other@market": True})
        with pytest.raises(SystemExit):
            mp._resolve_plugin_key("ghost-plugin")

    def test_name_at_marketplace_returned_as_is(self, tmp_path, monkeypatch):
        """name@marketplace format is returned unchanged."""
        result = mp._resolve_plugin_key("my-tool@official")
        assert result == "my-tool@official"

    def test_name_at_owner_marketplace_strips_owner(self, tmp_path, monkeypatch):
        """name@owner/marketplace strips the owner/ prefix."""
        result = mp._resolve_plugin_key("my-tool@acme-corp/official")
        assert result == "my-tool@official"

    def test_bare_name_found_on_disk_not_in_settings(self, tmp_path, monkeypatch):
        """Bare name found in marketplace plugins dir but not in settings resolves correctly."""
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"enabledPlugins": {}}), encoding="utf-8")
        monkeypatch.setattr(mp, "SETTINGS_FILE", sf)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", tmp_path / "nonexistent.json")
        mp_dir = tmp_path / "marketplaces"
        plug_dir = mp_dir / "local-market" / "plugins" / "disk-plugin"
        plug_dir.mkdir(parents=True)
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", mp_dir)
        monkeypatch.chdir(tmp_path)
        result = mp._resolve_plugin_key("disk-plugin")
        assert result == "disk-plugin@local-market"


# ── Tests: _verify_plugin_installed ──────────────────────────


class TestVerifyPluginInstalled:
    """Tests for _verify_plugin_installed -- checking plugin presence."""

    def test_key_in_settings_returns_true(self, tmp_path, monkeypatch):
        """Plugin key found in settings enabledPlugins returns True."""
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"enabledPlugins": {"my-plug@market": True}}), encoding="utf-8")
        monkeypatch.setattr(mp, "SETTINGS_FILE", sf)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", tmp_path / "nonexistent.json")
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", tmp_path / "marketplaces")
        monkeypatch.chdir(tmp_path)
        assert mp._verify_plugin_installed("my-plug@market") is True

    def test_key_on_disk_returns_true(self, tmp_path, monkeypatch):
        """Plugin key found in marketplace plugins dir on disk returns True."""
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({}), encoding="utf-8")
        monkeypatch.setattr(mp, "SETTINGS_FILE", sf)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", tmp_path / "nonexistent.json")
        mp_dir = tmp_path / "marketplaces"
        plug_dir = mp_dir / "market" / "plugins" / "disk-only"
        plug_dir.mkdir(parents=True)
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", mp_dir)
        monkeypatch.chdir(tmp_path)
        assert mp._verify_plugin_installed("disk-only@market") is True

    def test_key_not_found_returns_false(self, tmp_path, monkeypatch):
        """Plugin key not in settings and not on disk returns False."""
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({}), encoding="utf-8")
        monkeypatch.setattr(mp, "SETTINGS_FILE", sf)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", tmp_path / "nonexistent.json")
        mp_dir = tmp_path / "marketplaces"
        mp_dir.mkdir()
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", mp_dir)
        monkeypatch.chdir(tmp_path)
        assert mp._verify_plugin_installed("ghost@nowhere") is False


# ── Tests: do_enable with scope="local" cascading ────────────


class TestDoEnableLocalScope:
    """Tests for do_enable with scope='local' -- project-level enable with cascading."""

    def _setup_local_enable(self, tmp_path, monkeypatch):
        """Set up environment for local-scope enable tests."""
        mp_dir = tmp_path / "marketplaces"
        plug_dir = mp_dir / "market" / "plugins" / "my-plugin"
        plug_dir.mkdir(parents=True)
        # User-level settings with plugin enabled
        user_settings = tmp_path / "settings.json"
        user_settings.write_text(json.dumps({"enabledPlugins": {"my-plugin@market": True}}), encoding="utf-8")
        # User local settings (SETTINGS_TARGET) -- empty
        user_local = tmp_path / "settings.local.json"
        user_local.write_text(json.dumps({}), encoding="utf-8")
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", mp_dir)
        monkeypatch.setattr(mp, "SETTINGS_FILE", user_settings)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", user_local)
        # Project dir
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        monkeypatch.chdir(proj_dir)
        return user_settings, proj_dir

    def test_enable_local_cascades_disables_user_level(self, tmp_path, monkeypatch):
        """Enabling at local scope writes project settings AND disables at user level."""
        user_settings, proj_dir = self._setup_local_enable(tmp_path, monkeypatch)
        mp.do_enable("my-plugin@market", quiet=True, scope="local")
        # Project settings created and plugin enabled
        proj_settings = proj_dir / ".claude" / "settings.local.json"
        assert proj_settings.exists()
        proj_data = json.loads(proj_settings.read_text(encoding="utf-8"))
        assert proj_data["enabledPlugins"]["my-plugin@market"] is True
        # User level cascaded to disabled
        user_data = json.loads(user_settings.read_text(encoding="utf-8"))
        assert user_data["enabledPlugins"]["my-plugin@market"] is False

    def test_enable_local_dry_run_shows_cascading_no_write(self, tmp_path, monkeypatch):
        """Dry run with local scope does not write any files."""
        user_settings, proj_dir = self._setup_local_enable(tmp_path, monkeypatch)
        mp.do_enable("my-plugin@market", quiet=False, dry_run=True, scope="local")
        # Project settings NOT created
        proj_settings = proj_dir / ".claude" / "settings.local.json"
        assert not proj_settings.exists()
        # User level NOT changed
        user_data = json.loads(user_settings.read_text(encoding="utf-8"))
        assert user_data["enabledPlugins"]["my-plugin@market"] is True


# ── Tests: do_disable with scope="local" ─────────────────────


class TestDoDisableLocalScope:
    """Tests for do_disable with scope='local' -- project-level disable."""

    def test_disable_local_writes_project_settings(self, tmp_path, monkeypatch):
        """Disabling at local scope writes False to project settings file."""
        mp_dir = tmp_path / "marketplaces"
        plug_dir = mp_dir / "market" / "plugins" / "my-plugin"
        plug_dir.mkdir(parents=True)
        # User-level settings with plugin enabled
        user_settings = tmp_path / "settings.json"
        user_settings.write_text(json.dumps({"enabledPlugins": {"my-plugin@market": True}}), encoding="utf-8")
        user_local = tmp_path / "settings.local.json"
        user_local.write_text(json.dumps({}), encoding="utf-8")
        monkeypatch.setattr(mp, "MARKETPLACES_DIR", mp_dir)
        monkeypatch.setattr(mp, "SETTINGS_FILE", user_settings)
        monkeypatch.setattr(mp, "SETTINGS_TARGET", user_local)
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        monkeypatch.chdir(proj_dir)
        mp.do_disable("my-plugin@market", quiet=True, scope="local")
        # Project settings created with plugin disabled
        proj_settings = proj_dir / ".claude" / "settings.local.json"
        assert proj_settings.exists()
        proj_data = json.loads(proj_settings.read_text(encoding="utf-8"))
        assert proj_data["enabledPlugins"]["my-plugin@market"] is False
