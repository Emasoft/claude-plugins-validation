#!/usr/bin/env python3
"""Tests for generate_marketplace_repo.py scaffold generator.

Tests the marketplace repository scaffold generator:
- Marketplace generation (all files created, marketplace.json valid, GitHub sources)
- Plugin sources (all use {source: "github", repo: "..."}, no local paths)
- README catalog (plugin links, install commands)
- Validation helpers (validate_name, validate_plugin_repo)
- Dry run mode (no files written)

Coverage: 25 tests covering all generation functions and validation helpers.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from generate_marketplace_repo import (  # noqa: E402
    _gitignore,
    _marketplace_json,
    _plugin_entry,
    _readme,
    generate_marketplace_repo,
    validate_name,
    validate_plugin_repo,
    write_file,
)

# =============================================================================
# Helper constants
# =============================================================================

DEFAULT_NAME = "my-plugins"
DEFAULT_OWNER_NAME = "Test Organization"
DEFAULT_DESCRIPTION = "A curated collection of Claude Code plugins"
DEFAULT_GITHUB_OWNER = "test-org"
DEFAULT_PLUGINS = ["test-org/plugin-alpha", "test-org/plugin-beta"]


# =============================================================================
# Group 1: Validation helpers (5 tests)
# =============================================================================


class TestValidateName:
    """Tests for validate_name function."""

    def test_valid_kebab_case_name(self):
        """Valid kebab-case name returns None (no error)."""
        assert validate_name("my-cool-plugins") is None

    def test_empty_name_returns_error(self):
        """Empty name returns error message."""
        result = validate_name("")
        assert result is not None
        assert "empty" in result.lower()

    def test_reserved_name_returns_error(self):
        """Reserved name 'official' returns error message."""
        result = validate_name("official")
        assert result is not None
        assert "reserved" in result.lower()

    def test_invalid_case_returns_error(self):
        """Non-kebab-case name 'MyPlugins' returns error message."""
        result = validate_name("MyPlugins")
        assert result is not None
        assert "kebab" in result.lower()


class TestValidatePluginRepo:
    """Tests for validate_plugin_repo function."""

    def test_valid_owner_repo_format(self):
        """Valid 'owner/repo' string returns None (no error)."""
        assert validate_plugin_repo("my-org/my-plugin") is None

    def test_missing_slash_returns_error(self):
        """String without slash returns error message."""
        result = validate_plugin_repo("justarepo")
        assert result is not None
        assert "owner/repo" in result.lower()

    def test_empty_parts_returns_error(self):
        """String with empty parts like '/repo' returns error message."""
        result = validate_plugin_repo("/repo")
        assert result is not None
        assert "owner/repo" in result.lower()


# =============================================================================
# Group 2: Template generation functions (5 tests)
# =============================================================================


class TestMarketplaceJson:
    """Tests for _marketplace_json function."""

    def test_produces_valid_structure(self):
        """_marketplace_json returns dict with required fields."""
        plugins = [_plugin_entry("org/plugin-a")]
        data = _marketplace_json(DEFAULT_NAME, DEFAULT_OWNER_NAME, DEFAULT_DESCRIPTION, plugins)
        assert data["name"] == DEFAULT_NAME
        assert data["version"] == "1.0.0"
        assert data["owner"]["name"] == DEFAULT_OWNER_NAME
        assert data["metadata"]["description"] == DEFAULT_DESCRIPTION
        assert len(data["plugins"]) == 1

    def test_serializable_as_json(self):
        """_marketplace_json output is JSON-serializable."""
        plugins = [_plugin_entry("org/p1"), _plugin_entry("org/p2")]
        data = _marketplace_json("test-mkt", "Owner", "Desc", plugins)
        serialized = json.dumps(data, indent=2)
        roundtrip = json.loads(serialized)
        assert roundtrip["name"] == "test-mkt"
        assert len(roundtrip["plugins"]) == 2


class TestPluginEntry:
    """Tests for _plugin_entry function."""

    def test_entry_uses_github_source(self):
        """_plugin_entry produces entry with source.source='github' and source.repo set."""
        entry = _plugin_entry("myorg/cool-plugin")
        assert entry["source"]["source"] == "github"
        assert entry["source"]["repo"] == "myorg/cool-plugin"

    def test_entry_name_is_repo_name(self):
        """_plugin_entry extracts plugin name from the repo part of owner/repo."""
        entry = _plugin_entry("someuser/awesome-tool")
        assert entry["name"] == "awesome-tool"

    def test_entry_has_placeholder_description(self):
        """_plugin_entry has a placeholder description mentioning the plugin name."""
        entry = _plugin_entry("org/my-plugin")
        assert "my-plugin" in entry["description"]


# =============================================================================
# Group 3: Plugin sources verification (5 tests)
# =============================================================================


class TestPluginSources:
    """Tests that ALL plugin sources use GitHub format, never local paths."""

    def test_all_sources_use_github(self):
        """Every plugin entry from _plugin_entry uses source='github'."""
        repos = ["org/a", "org/b", "other/c", "company/d"]
        entries = [_plugin_entry(r) for r in repos]
        for entry in entries:
            assert entry["source"]["source"] == "github", f"Entry {entry['name']} should use github source"

    def test_no_local_paths_in_sources(self):
        """Plugin entries do not contain local filesystem paths."""
        entry = _plugin_entry("org/plugin-x")
        source = entry["source"]
        # Verify source dict has no path-like values
        for key, value in source.items():
            if isinstance(value, str):
                assert not value.startswith("/"), f"source.{key} should not be a local path"
                assert not value.startswith("./"), f"source.{key} should not be a relative path"

    def test_marketplace_json_plugins_all_github(self):
        """_marketplace_json with multiple plugins all have github sources."""
        plugins = [_plugin_entry(r) for r in DEFAULT_PLUGINS]
        data = _marketplace_json(DEFAULT_NAME, DEFAULT_OWNER_NAME, DEFAULT_DESCRIPTION, plugins)
        for p in data["plugins"]:
            assert p["source"]["source"] == "github"
            assert "/" in p["source"]["repo"]

    def test_generated_marketplace_json_on_disk_uses_github(self, tmp_path):
        """Full generation writes marketplace.json where all plugins use github source."""
        target = tmp_path / "mkt"
        target.mkdir()
        result = generate_marketplace_repo(
            target, DEFAULT_NAME, DEFAULT_OWNER_NAME, DEFAULT_DESCRIPTION,
            DEFAULT_GITHUB_OWNER, DEFAULT_PLUGINS, dry_run=False,
        )
        assert result == 0
        mj_path = target / ".claude-plugin" / "marketplace.json"
        assert mj_path.exists()
        data = json.loads(mj_path.read_text())
        for p in data["plugins"]:
            assert p["source"]["source"] == "github"

    def test_no_path_key_in_sources(self):
        """Plugin source dicts do not have a 'path' key (only 'source' and 'repo')."""
        entry = _plugin_entry("org/plugin")
        assert "path" not in entry["source"]


# =============================================================================
# Group 4: README catalog (3 tests)
# =============================================================================


class TestReadmeCatalog:
    """Tests for _readme function and generated README content."""

    def test_readme_contains_plugin_links(self):
        """Generated README has links to plugin repos."""
        plugins = [_plugin_entry("org/plugin-a"), _plugin_entry("org/plugin-b")]
        content = _readme(DEFAULT_NAME, DEFAULT_DESCRIPTION, DEFAULT_GITHUB_OWNER, plugins)
        assert "plugin-a" in content
        assert "plugin-b" in content
        assert "https://github.com/org/plugin-a" in content

    def test_readme_contains_install_commands(self):
        """Generated README includes 'claude plugin install' commands."""
        plugins = [_plugin_entry("org/my-tool")]
        content = _readme(DEFAULT_NAME, DEFAULT_DESCRIPTION, DEFAULT_GITHUB_OWNER, plugins)
        assert f"claude plugin install my-tool@{DEFAULT_NAME}" in content

    def test_readme_has_table_header(self):
        """Generated README has a markdown table with Plugin, Description, Install columns."""
        plugins = [_plugin_entry("org/x")]
        content = _readme(DEFAULT_NAME, DEFAULT_DESCRIPTION, DEFAULT_GITHUB_OWNER, plugins)
        assert "| Plugin |" in content
        assert "| Description |" in content
        assert "| Install |" in content


# =============================================================================
# Group 5: Full marketplace generation (5 tests)
# =============================================================================


class TestFullMarketplaceGeneration:
    """Tests for generate_marketplace_repo full generation."""

    def test_all_files_created(self, tmp_path):
        """generate_marketplace_repo creates all expected files."""
        target = tmp_path / "marketplace"
        target.mkdir()
        result = generate_marketplace_repo(
            target, DEFAULT_NAME, DEFAULT_OWNER_NAME, DEFAULT_DESCRIPTION,
            DEFAULT_GITHUB_OWNER, DEFAULT_PLUGINS, dry_run=False,
        )
        assert result == 0
        assert (target / ".claude-plugin" / "marketplace.json").exists()
        assert (target / "README.md").exists()
        assert (target / ".gitignore").exists()
        assert (target / ".github" / "workflows" / "validate.yml").exists()
        assert (target / ".github" / "workflows" / "update-catalog.yml").exists()
        assert (target / "scripts" / "update_catalog.py").exists()
        assert (target / "cliff.toml").exists()
        assert (target / ".githooks" / "pre-push").exists()

    def test_marketplace_json_valid(self, tmp_path):
        """Generated marketplace.json is valid JSON with expected structure."""
        target = tmp_path / "mkt"
        target.mkdir()
        generate_marketplace_repo(
            target, DEFAULT_NAME, DEFAULT_OWNER_NAME, DEFAULT_DESCRIPTION,
            DEFAULT_GITHUB_OWNER, DEFAULT_PLUGINS, dry_run=False,
        )
        data = json.loads((target / ".claude-plugin" / "marketplace.json").read_text())
        assert data["name"] == DEFAULT_NAME
        assert data["owner"]["name"] == DEFAULT_OWNER_NAME
        assert len(data["plugins"]) == len(DEFAULT_PLUGINS)

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix file permissions only")
    def test_pre_push_is_executable(self, tmp_path):
        """Generated .githooks/pre-push is executable."""
        target = tmp_path / "mkt"
        target.mkdir()
        generate_marketplace_repo(
            target, DEFAULT_NAME, DEFAULT_OWNER_NAME, DEFAULT_DESCRIPTION,
            DEFAULT_GITHUB_OWNER, DEFAULT_PLUGINS, dry_run=False,
        )
        pre_push = target / ".githooks" / "pre-push"
        assert os.access(pre_push, os.X_OK), ".githooks/pre-push should be executable"

    def test_invalid_name_returns_error(self, tmp_path):
        """generate_marketplace_repo with reserved name returns 1."""
        target = tmp_path / "mkt"
        target.mkdir()
        result = generate_marketplace_repo(
            target, "official", DEFAULT_OWNER_NAME, DEFAULT_DESCRIPTION,
            DEFAULT_GITHUB_OWNER, [], dry_run=False,
        )
        assert result == 1

    def test_invalid_plugin_repo_format_returns_error(self, tmp_path):
        """generate_marketplace_repo with invalid plugin repo format returns 1."""
        target = tmp_path / "mkt"
        target.mkdir()
        result = generate_marketplace_repo(
            target, DEFAULT_NAME, DEFAULT_OWNER_NAME, DEFAULT_DESCRIPTION,
            DEFAULT_GITHUB_OWNER, ["not-a-valid-repo"], dry_run=False,
        )
        assert result == 1


# =============================================================================
# Group 6: Dry run (2 tests)
# =============================================================================


class TestMarketplaceDryRun:
    """Tests for dry run mode."""

    def test_dry_run_creates_no_files(self, tmp_path):
        """generate_marketplace_repo with dry_run=True does not create files."""
        target = tmp_path / "mkt"
        target.mkdir()
        result = generate_marketplace_repo(
            target, DEFAULT_NAME, DEFAULT_OWNER_NAME, DEFAULT_DESCRIPTION,
            DEFAULT_GITHUB_OWNER, DEFAULT_PLUGINS, dry_run=True,
        )
        assert result == 0
        actual_files = [f for f in target.rglob("*") if f.is_file()]
        assert len(actual_files) == 0, f"No files should be created in dry run, found: {actual_files}"

    def test_dry_run_still_validates_inputs(self, tmp_path):
        """Dry run still rejects invalid marketplace name."""
        target = tmp_path / "mkt"
        target.mkdir()
        result = generate_marketplace_repo(
            target, "official", DEFAULT_OWNER_NAME, DEFAULT_DESCRIPTION,
            DEFAULT_GITHUB_OWNER, [], dry_run=True,
        )
        assert result == 1


# =============================================================================
# Group 7: Gitignore and helper functions (2 tests)
# =============================================================================


class TestMarketplaceHelpers:
    """Tests for helper functions in generate_marketplace_repo."""

    def test_gitignore_contains_essentials(self):
        """_gitignore includes Python cache, .env, node_modules, .claude/, .tldr/."""
        content = _gitignore()
        for required in ["__pycache__", ".env", "node_modules", ".claude/", ".tldr/"]:
            assert required in content, f"Missing required gitignore entry: {required}"

    def test_write_file_creates_parent_dirs(self, tmp_path):
        """write_file creates parent directories when they don't exist."""
        target_file = tmp_path / "deep" / "nested" / "dir" / "file.txt"
        write_file(target_file, "hello world", dry_run=False)
        assert target_file.exists()
        assert target_file.read_text() == "hello world"
