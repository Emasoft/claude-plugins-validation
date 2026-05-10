"""Tests for cpv_repo_shape — TRDD-9065109a Phase B (zero-config publish pipeline).

Covers:
  - detect_repo_shape(root) -> RepoShape — classifier returning one of:
      single-plugin, marketplace-hub, nested-monorepo, marketplace-in-plugin,
      workspace-multi-git, submodule-bundle, unknown
  - extract_config_from_tree(root, shape) -> RepoConfig — auto-detection of
    plugin name/version, GitHub remote owner/repo, marketplace owner/repo,
    submodule paths, and bundled binary references — all from project tree
    state (no env-var fallbacks, no per-plugin customization).
  - workspace-multi-git iteration helpers.

TDD-first: write failing tests, then implement cpv_repo_shape.py.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest


# -----------------------------------------------------------------------------
# Module under test (imports defer until tests run, so failing imports show
# up as test errors rather than collection-time crashes).
# -----------------------------------------------------------------------------
def _import_module():
    """Import the module under test. Defer the import so test discovery
    still works when the module hasn't been written yet (TDD-red phase)."""
    import cpv_repo_shape

    return cpv_repo_shape


# -----------------------------------------------------------------------------
# Helpers for building synthetic repo trees.
# -----------------------------------------------------------------------------


def _git_init(path: Path) -> None:
    """Initialize a minimal git repo at path (no commits, no remote)."""
    subprocess.run(
        ["git", "init", "--quiet", "-b", "main"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    # Set local user config so commits work without depending on the
    # developer machine's global git config.
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )


def _write_plugin_json(root: Path, name: str = "test-plugin", version: str = "0.1.0") -> Path:
    cpd = root / ".claude-plugin"
    cpd.mkdir(parents=True, exist_ok=True)
    plugin_json = cpd / "plugin.json"
    plugin_json.write_text(
        json.dumps({"name": name, "version": version}, indent=2),
        encoding="utf-8",
    )
    return plugin_json


def _write_marketplace_json(
    root: Path,
    name: str = "test-marketplace",
    plugins: list[dict] | None = None,
) -> Path:
    cpd = root / ".claude-plugin"
    cpd.mkdir(parents=True, exist_ok=True)
    mp_json = cpd / "marketplace.json"
    mp_json.write_text(
        json.dumps(
            {
                "name": name,
                "metadata": {"version": "0.1.0"},
                "plugins": plugins or [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return mp_json


# -----------------------------------------------------------------------------
# detect_repo_shape() — classifier
# -----------------------------------------------------------------------------


class TestDetectRepoShape:
    """Verify the seven shapes from the TRDD §B detection table."""

    def test_single_plugin_only_plugin_json(self, tmp_path):
        """Repo with only .claude-plugin/plugin.json → single-plugin."""
        _write_plugin_json(tmp_path)
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        assert shape.kind == "single-plugin"
        assert shape.root == tmp_path

    def test_marketplace_hub_only_marketplace_json(self, tmp_path):
        """Repo with only .claude-plugin/marketplace.json → marketplace-hub."""
        _write_marketplace_json(tmp_path)
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        assert shape.kind == "marketplace-hub"

    def test_nested_monorepo_layout_b(self, tmp_path):
        """Layout B: marketplace.json at root + plugins/<name>/.claude-plugin/."""
        _write_marketplace_json(
            tmp_path,
            plugins=[{"source": {"source": "relative-path", "path": "./plugins/p1"}, "name": "p1"}],
        )
        plugin_dir = tmp_path / "plugins" / "p1"
        _write_plugin_json(plugin_dir, name="p1")
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        assert shape.kind == "nested-monorepo"

    def test_marketplace_in_plugin_layout_c(self, tmp_path):
        """Layout C: both plugin.json AND marketplace.json at root, single self-entry."""
        _write_plugin_json(tmp_path, name="solo-plugin")
        _write_marketplace_json(
            tmp_path,
            name="solo-marketplace",
            plugins=[{"source": {"source": "relative-path", "path": "./"}, "name": "solo-plugin"}],
        )
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        assert shape.kind == "marketplace-in-plugin"

    def test_workspace_multi_git(self, tmp_path):
        """Workspace with multiple subfolders, each with its own .git/ + .claude-plugin/."""
        # Two sibling plugin repos
        for name in ("plugin-a", "plugin-b"):
            sub = tmp_path / name
            sub.mkdir()
            _git_init(sub)
            _write_plugin_json(sub, name=name)
        # No top-level .claude-plugin/ — workspace, not a plugin itself.
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        assert shape.kind == "workspace-multi-git"
        # The detected children should include both subfolders.
        children = sorted(shape.children) if shape.children else []
        assert tmp_path / "plugin-a" in children
        assert tmp_path / "plugin-b" in children

    def test_submodule_bundle(self, tmp_path):
        """plugin.json + .gitmodules referencing a binary submodule → submodule-bundle."""
        _write_plugin_json(tmp_path)
        gitmodules = tmp_path / ".gitmodules"
        gitmodules.write_text(
            textwrap.dedent(
                """
                [submodule "external/lib-rust"]
                    path = external/lib-rust
                    url = https://github.com/example/lib-rust.git
                """
            ).strip(),
            encoding="utf-8",
        )
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        assert shape.kind == "submodule-bundle"
        assert shape.submodule_paths == ["external/lib-rust"]

    def test_unknown_when_no_signals(self, tmp_path):
        """Empty dir → unknown (not a CPV-managed repo)."""
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        assert shape.kind == "unknown"

    def test_layout_c_with_submodules_keeps_layout_c_kind(self, tmp_path):
        """A Layout C plugin that ALSO has submodules stays classified as
        marketplace-in-plugin (the layout dominates), but its submodule
        paths are exposed on the shape so callers can layer the extra
        verify gates on top."""
        _write_plugin_json(tmp_path, name="solo")
        _write_marketplace_json(
            tmp_path,
            plugins=[{"source": {"source": "relative-path", "path": "./"}, "name": "solo"}],
        )
        (tmp_path / ".gitmodules").write_text(
            textwrap.dedent(
                """
                [submodule "external/lib"]
                    path = external/lib
                    url = https://github.com/example/lib.git
                """
            ).strip(),
            encoding="utf-8",
        )
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        assert shape.kind == "marketplace-in-plugin"
        # Submodule still reported via the convenience field.
        assert shape.submodule_paths == ["external/lib"]

    def test_nested_monorepo_with_submodules_keeps_layout_b_kind(self, tmp_path):
        """Layout B + submodules → nested-monorepo (NOT submodule-bundle)."""
        _write_marketplace_json(
            tmp_path,
            plugins=[{"source": {"source": "relative-path", "path": "./plugins/p1"}, "name": "p1"}],
        )
        plugin_dir = tmp_path / "plugins" / "p1"
        _write_plugin_json(plugin_dir, name="p1")
        (tmp_path / ".gitmodules").write_text(
            '[submodule "vendor"]\n    path = vendor\n    url = https://example.com/v.git\n',
            encoding="utf-8",
        )
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        assert shape.kind == "nested-monorepo"
        assert shape.submodule_paths == ["vendor"]


# -----------------------------------------------------------------------------
# extract_config_from_tree() — auto-detect every value publish.py needs.
# -----------------------------------------------------------------------------


class TestExtractConfigFromTree:
    def test_extracts_plugin_name_and_version(self, tmp_path):
        _write_plugin_json(tmp_path, name="my-plugin", version="1.2.3")
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        cfg = mod.extract_config_from_tree(tmp_path, shape)
        assert cfg.plugin_name == "my-plugin"
        assert cfg.plugin_version == "1.2.3"

    def test_extracts_github_remote(self, tmp_path):
        """Reads owner/repo from `git remote get-url origin`."""
        _write_plugin_json(tmp_path)
        _git_init(tmp_path)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:owner/test-plugin.git"],
            cwd=str(tmp_path),
            check=True,
            capture_output=True,
        )
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        cfg = mod.extract_config_from_tree(tmp_path, shape)
        assert cfg.github_owner == "owner"
        assert cfg.github_repo == "test-plugin"

    def test_extracts_marketplace_owner_repo_from_notify_workflow(self, tmp_path):
        """Reads MARKETPLACE_OWNER / MARKETPLACE_REPO from the workflow file."""
        _write_plugin_json(tmp_path)
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "notify-marketplace.yml").write_text(
            textwrap.dedent(
                """
                name: notify-marketplace
                on: [push]
                jobs:
                  notify:
                    runs-on: ubuntu-latest
                    env:
                      MARKETPLACE_OWNER: 'mkt-owner'
                      MARKETPLACE_REPO: 'mkt-repo'
                """
            ).strip(),
            encoding="utf-8",
        )
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        cfg = mod.extract_config_from_tree(tmp_path, shape)
        assert cfg.marketplace_owner == "mkt-owner"
        assert cfg.marketplace_repo == "mkt-repo"

    def test_extracts_submodule_paths(self, tmp_path):
        _write_plugin_json(tmp_path)
        (tmp_path / ".gitmodules").write_text(
            textwrap.dedent(
                """
                [submodule "external/lib-rust"]
                    path = external/lib-rust
                    url = https://github.com/example/lib-rust.git
                [submodule "external/lib-go"]
                    path = external/lib-go
                    url = https://github.com/example/lib-go.git
                """
            ).strip(),
            encoding="utf-8",
        )
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        cfg = mod.extract_config_from_tree(tmp_path, shape)
        assert "external/lib-rust" in cfg.submodule_paths
        assert "external/lib-go" in cfg.submodule_paths

    def test_marketplace_hub_extracts_marketplace_name(self, tmp_path):
        """For a marketplace-hub repo, marketplace_name comes from marketplace.json."""
        _write_marketplace_json(tmp_path, name="my-marketplace")
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        cfg = mod.extract_config_from_tree(tmp_path, shape)
        assert cfg.marketplace_name == "my-marketplace"

    def test_no_origin_remote_returns_none(self, tmp_path):
        """If git origin is missing, github_owner/repo are None (not crash)."""
        _write_plugin_json(tmp_path)
        _git_init(tmp_path)  # No remote configured.
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        cfg = mod.extract_config_from_tree(tmp_path, shape)
        assert cfg.github_owner is None
        assert cfg.github_repo is None

    def test_workspace_multi_git_lists_children(self, tmp_path):
        """For a workspace, config.children is the per-child list (ordered)."""
        for name in ("p1", "p2"):
            sub = tmp_path / name
            sub.mkdir()
            _git_init(sub)
            _write_plugin_json(sub, name=name)
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        cfg = mod.extract_config_from_tree(tmp_path, shape)
        assert cfg.children is not None
        # Each child should be a (Path, RepoShape) pair.
        kinds = {c.kind for _, c in cfg.children}
        assert kinds == {"single-plugin"}


# -----------------------------------------------------------------------------
# Backward compatibility: detect_repo_shape() must agree with detect_layout()
# for the existing Layout A / Layout B / Layout C cases.
# -----------------------------------------------------------------------------


class TestBackwardCompatWithDetectLayout:
    def test_layout_a_single_plugin_matches(self, tmp_path):
        """Existing Layout A (notify workflow) maps to single-plugin shape."""
        import publish

        _write_plugin_json(tmp_path)
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "notify-marketplace.yml").write_text(
            "env:\n  MARKETPLACE_OWNER: 'x'\n  MARKETPLACE_REPO: 'y'\n",
            encoding="utf-8",
        )
        legacy_layout, _ = publish.detect_layout(tmp_path)
        assert legacy_layout == "A"
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        # New classifier returns single-plugin (Layout A is a special-case
        # of single-plugin with marketplace dispatch wired up).
        assert shape.kind == "single-plugin"

    def test_layout_b_nested_matches(self, tmp_path):
        """Existing Layout B (nested marketplace) maps to nested-monorepo when
        detection is run from the marketplace root."""
        import publish

        _write_marketplace_json(
            tmp_path,
            plugins=[{"source": {"source": "relative-path", "path": "./plugins/p1"}, "name": "p1"}],
        )
        plugin_dir = tmp_path / "plugins" / "p1"
        _write_plugin_json(plugin_dir, name="p1")
        # publish.detect_layout works from the plugin root (returns "B"
        # because find_parent_marketplace finds the marketplace one up).
        legacy_layout, _ = publish.detect_layout(plugin_dir)
        assert legacy_layout == "B"
        mod = _import_module()
        # New classifier from the marketplace root → nested-monorepo.
        shape = mod.detect_repo_shape(tmp_path)
        assert shape.kind == "nested-monorepo"


# -----------------------------------------------------------------------------
# pick_workspace_child() — interactive picker (non-interactive part: unit test).
# -----------------------------------------------------------------------------


class TestPickWorkspaceChild:
    def test_returns_selected_child(self, tmp_path):
        """User picks index 1 → returns first child path."""
        for name in ("plugin-a", "plugin-b"):
            sub = tmp_path / name
            sub.mkdir()
            _git_init(sub)
            _write_plugin_json(sub, name=name)
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        # Picker accepts a callable for the "user input" so tests don't need
        # to mock stdin. Picking "1" returns the first sorted child.
        choice = mod.pick_workspace_child(shape, input_fn=lambda _prompt: "1")
        assert choice == tmp_path / "plugin-a"

    def test_zero_returns_none(self, tmp_path):
        """User picks 0 → None (cancel). Two children needed because the
        classifier only treats N≥2 sibling plugin repos as a workspace."""
        for name in ("plugin-a", "plugin-b"):
            sub = tmp_path / name
            sub.mkdir()
            _git_init(sub)
            _write_plugin_json(sub, name=name)
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        choice = mod.pick_workspace_child(shape, input_fn=lambda _prompt: "0")
        assert choice is None

    def test_invalid_input_returns_none(self, tmp_path):
        """Garbage input → None (cancel) instead of crashing. Two children
        needed (see test_zero_returns_none for the rationale)."""
        for name in ("plugin-a", "plugin-b"):
            sub = tmp_path / name
            sub.mkdir()
            _git_init(sub)
            _write_plugin_json(sub, name=name)
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        choice = mod.pick_workspace_child(shape, input_fn=lambda _prompt: "not-a-number")
        assert choice is None

    def test_picker_rejects_non_workspace_shape(self, tmp_path):
        """Calling picker on a single-plugin shape is a programming error."""
        _write_plugin_json(tmp_path)
        mod = _import_module()
        shape = mod.detect_repo_shape(tmp_path)
        with pytest.raises(ValueError):
            mod.pick_workspace_child(shape, input_fn=lambda _prompt: "1")


# -----------------------------------------------------------------------------
# parse_owner_repo_from_remote — pure helper, currently lives in publish.py
# but the new module re-exports a stable version for cpv.publish callers.
# -----------------------------------------------------------------------------


class TestParseOwnerRepo:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("git@github.com:owner/repo.git", ("owner", "repo")),
            ("https://github.com/owner/repo.git", ("owner", "repo")),
            ("https://github.com/owner/repo", ("owner", "repo")),
            ("ssh://git@github.com/owner/repo.git", ("owner", "repo")),
        ],
    )
    def test_parses_known_url_styles(self, url, expected):
        mod = _import_module()
        assert mod.parse_owner_repo_from_remote(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "not-a-url",
            "https://gitlab.com/owner/repo.git",  # not GitHub
        ],
    )
    def test_returns_none_for_unparseable(self, url):
        mod = _import_module()
        assert mod.parse_owner_repo_from_remote(url) is None


# -----------------------------------------------------------------------------
# CLI smoke tests
# -----------------------------------------------------------------------------


class TestCliEntryPoint:
    def test_main_returns_0_on_known_shape(self, tmp_path, capsys):
        """`main(['<path>'])` exits 0 and prints a 'shape:' line."""
        _write_plugin_json(tmp_path, name="cli-plugin", version="0.5.0")
        mod = _import_module()
        rc = mod.main([str(tmp_path)])
        assert rc == 0
        captured = capsys.readouterr().out
        assert "shape:" in captured
        assert "single-plugin" in captured
        assert "cli-plugin" in captured

    def test_main_returns_0_on_unknown_shape(self, tmp_path, capsys):
        """An empty dir is `unknown` but the CLI still exits 0 — the
        classification IS the answer."""
        mod = _import_module()
        rc = mod.main([str(tmp_path)])
        assert rc == 0
        captured = capsys.readouterr().out
        assert "unknown" in captured

    def test_main_returns_1_on_nonexistent_path(self, tmp_path, capsys):
        """A path that isn't a directory → exit 1, error to stderr."""
        bogus = tmp_path / "does-not-exist"
        mod = _import_module()
        rc = mod.main([str(bogus)])
        assert rc == 1
        captured = capsys.readouterr()
        assert "not a directory" in captured.err

    def test_main_prints_help(self, capsys):
        """`-h` prints usage to stdout and exits 0."""
        mod = _import_module()
        rc = mod.main(["-h"])
        assert rc == 0
        captured = capsys.readouterr().out
        assert "Usage:" in captured
        # Sanity: every shape kind is mentioned in the help text so the
        # user doesn't have to read the source to know the taxonomy.
        for kind in mod.SHAPE_KINDS:
            assert kind in captured
