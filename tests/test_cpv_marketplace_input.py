#!/usr/bin/env python3
"""Unit tests for ``scripts/cpv_marketplace_input.py`` (TRDD-3dcbb37c §1).

The resolver is the shared input gate for every ``cpv-batch-*`` skill,
so its classification rules need to be pinned hard: every accepted
shape produces the documented ResolvedInput, every ambiguous shape
raises ``InputResolutionError`` with a useful remediation hint, and
URL clones go through a single chokepoint that tests can monkeypatch
to avoid network use.

The tests are split into seven classes:

1. ``TestIsUrlShape`` — boolean URL/owner-repo detector.
2. ``TestParseGithubUrl`` — owner/repo extraction from every accepted shape.
3. ``TestResolveSingleLocal`` — file / skill / plugin / marketplace
   classification of LOCAL paths against tmp fixtures.
4. ``TestResolveAmbiguity`` — explicit-error contract for ambiguous
   or unresolvable inputs.
5. ``TestResolveListForms`` — Python list / ``@listfile`` /
   comma-separated multi-spec forms all funnel through resolve().
6. ``TestResolveUrl`` — URL form, with ``_shallow_clone`` monkeypatched
   so no network is required and the reference-counted cleanup
   callback contract is observable.
7. ``TestMarketplaceExpansion`` — marketplace local + URL expansion
   produces one ResolvedInput per plugin, and the cleanup callback
   only fires after the last per-plugin consumer has called it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

import pytest

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cpv_marketplace_input as cmi  # noqa: E402
from cpv_marketplace_input import (  # noqa: E402
    InputResolutionError,
    ResolvedInput,
    is_url_shape,
    parse_github_url,
    resolve,
)

# ----------------------- helpers -----------------------------------------


def _make_plugin(root: Path, name: str = "demo-plugin") -> Path:
    """Create a minimal valid plugin directory tree under ``root``."""
    plugin_dir = root / name
    (plugin_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "0.1.0"}), encoding="utf-8"
    )
    return plugin_dir


def _make_skill(root: Path, name: str = "demo-skill") -> Path:
    """Create a minimal valid skill folder containing SKILL.md."""
    skill_dir = root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: stub\n---\nbody\n", encoding="utf-8"
    )
    return skill_dir


def _make_marketplace(
    root: Path,
    plugin_specs: list[dict],
    *,
    name: str = "demo-market",
) -> Path:
    """Create a marketplace directory with the given plugins list."""
    market_dir = root / name
    (market_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (market_dir / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {"name": name, "owner": {"name": "Test"}, "plugins": plugin_specs}
        ),
        encoding="utf-8",
    )
    return market_dir


# ----------------------- 1. is_url_shape ---------------------------------


class TestIsUrlShape:
    def test_https_github_url(self) -> None:
        assert is_url_shape("https://github.com/owner/repo") is True

    def test_http_github_url(self) -> None:
        assert is_url_shape("http://github.com/owner/repo") is True

    def test_bare_github_dot_com_prefix(self) -> None:
        assert is_url_shape("github.com/owner/repo") is True

    def test_git_ssh_form(self) -> None:
        assert is_url_shape("git@github.com:owner/repo") is True

    def test_owner_repo_shorthand_no_local(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert is_url_shape("Emasoft/emasoft-plugins") is True

    def test_owner_repo_shorthand_with_local_collision_is_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If `owner/repo` matches a real local path, prefer LOCAL."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "cli.py").write_text("# stub\n")
        assert is_url_shape("scripts/cli.py") is False

    def test_absolute_path_is_not_url(self) -> None:
        assert is_url_shape("/abs/path/foo") is False

    def test_relative_dot_path_is_not_url(self) -> None:
        assert is_url_shape("./foo/bar") is False

    def test_home_tilde_path_is_not_url(self) -> None:
        assert is_url_shape("~/code/foo") is False

    def test_three_segments_is_not_url(self) -> None:
        assert is_url_shape("owner/repo/extra") is False

    def test_single_segment_is_not_url(self) -> None:
        assert is_url_shape("just-a-folder") is False

    def test_empty_is_not_url(self) -> None:
        assert is_url_shape("") is False

    def test_whitespace_only_is_not_url(self) -> None:
        assert is_url_shape("   ") is False


# ----------------------- 2. parse_github_url -----------------------------


class TestParseGithubUrl:
    def test_https_url(self) -> None:
        assert parse_github_url("https://github.com/Emasoft/cpv") == ("Emasoft", "cpv")

    def test_https_url_with_dot_git(self) -> None:
        assert parse_github_url("https://github.com/owner/repo.git") == ("owner", "repo")

    def test_https_url_with_subpath(self) -> None:
        assert parse_github_url("https://github.com/owner/repo/tree/main") == ("owner", "repo")

    def test_github_dot_com_prefix(self) -> None:
        assert parse_github_url("github.com/owner/repo") == ("owner", "repo")

    def test_git_ssh_form(self) -> None:
        assert parse_github_url("git@github.com:owner/repo") == ("owner", "repo")

    def test_owner_repo_shorthand(self) -> None:
        assert parse_github_url("owner/repo") == ("owner", "repo")

    def test_invalid_shape_raises(self) -> None:
        with pytest.raises(InputResolutionError, match="github URL"):
            parse_github_url("not a url at all")


# ----------------------- 3. resolve LOCAL --------------------------------


class TestResolveSingleLocal:
    def test_single_file_resolves_to_file_kind(self, tmp_path: Path) -> None:
        f = tmp_path / "thing.py"
        f.write_text("print('hi')\n")
        result = resolve(str(f))
        assert len(result) == 1
        assert result[0].kind == "file"
        assert result[0].abs_path == f.resolve()
        assert result[0].display_name == "thing.py"
        assert result[0].source_url is None
        assert result[0].cleanup_callback is None

    def test_skill_md_resolves_to_skill_with_parent_dir(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path)
        result = resolve(str(skill_dir / "SKILL.md"))
        assert len(result) == 1
        assert result[0].kind == "skill"
        assert result[0].abs_path == skill_dir.resolve()
        assert result[0].display_name == skill_dir.name

    def test_skill_folder_resolves_to_skill(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path, name="another-skill")
        result = resolve(str(skill_dir))
        assert len(result) == 1
        assert result[0].kind == "skill"
        assert result[0].abs_path == skill_dir.resolve()

    def test_plugin_folder_resolves_to_plugin(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        result = resolve(str(plugin))
        assert len(result) == 1
        assert result[0].kind == "plugin"
        assert result[0].abs_path == plugin.resolve()
        assert result[0].display_name == "demo-plugin"

    def test_marketplace_layout_c_is_marketplace(self, tmp_path: Path) -> None:
        """A folder containing BOTH plugin.json AND marketplace.json
        (Layout C — marketplace-in-plugin self-referential) classifies
        as marketplace and expands."""
        plugin_path = _make_plugin(tmp_path, name="self-ref-plugin")
        # Add marketplace.json that references the plugin itself.
        (plugin_path / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "self-ref-plugin",
                    "owner": {"name": "Test"},
                    "plugins": [
                        {
                            "name": "self-ref-plugin",
                            "version": "0.1.0",
                            "source": {"source": "local", "path": "."},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = resolve(str(plugin_path))
        # Layout C is classified as marketplace; expansion finds 1 plugin
        # (the self-reference).
        assert len(result) == 1
        assert result[0].kind == "plugin"
        assert result[0].display_name == "self-ref-plugin"


# ----------------------- 4. resolve AMBIGUITY ----------------------------


class TestResolveAmbiguity:
    def test_nonexistent_path_raises(self, tmp_path: Path) -> None:
        ghost = tmp_path / "does" / "not" / "exist"
        with pytest.raises(InputResolutionError, match="does not exist"):
            resolve(str(ghost))

    def test_empty_string_raises(self) -> None:
        with pytest.raises(InputResolutionError, match="empty"):
            resolve("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(InputResolutionError, match="empty"):
            resolve("   ")

    def test_directory_without_recognised_markers_raises(
        self, tmp_path: Path
    ) -> None:
        odd = tmp_path / "mystery"
        odd.mkdir()
        (odd / "random.txt").write_text("not a known shape\n")
        with pytest.raises(InputResolutionError, match="doesn't contain"):
            resolve(str(odd))


# ----------------------- 5. resolve LIST forms ---------------------------


class TestResolveListForms:
    def test_python_list_is_flattened(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.py"
        f1.write_text("# a\n")
        f2 = tmp_path / "b.py"
        f2.write_text("# b\n")
        result = resolve([str(f1), str(f2)])
        assert [r.abs_path for r in result] == [f1.resolve(), f2.resolve()]

    def test_at_listfile_resolves_each_line(self, tmp_path: Path) -> None:
        f1 = tmp_path / "x.py"
        f1.write_text("# x\n")
        f2 = tmp_path / "y.py"
        f2.write_text("# y\n")
        listfile = tmp_path / "inputs.txt"
        listfile.write_text(
            f"{f1}\n"
            "# this is a comment, should be ignored\n"
            "\n"
            f"{f2}\n",
            encoding="utf-8",
        )
        result = resolve(f"@{listfile}")
        assert [r.abs_path for r in result] == [f1.resolve(), f2.resolve()]

    def test_comma_separated_is_flattened(self, tmp_path: Path) -> None:
        f1 = tmp_path / "p.py"
        f1.write_text("# p\n")
        f2 = tmp_path / "q.py"
        f2.write_text("# q\n")
        result = resolve(f"{f1},{f2}")
        assert {r.abs_path for r in result} == {f1.resolve(), f2.resolve()}

    def test_listfile_missing_raises_with_remediation(self, tmp_path: Path) -> None:
        with pytest.raises(InputResolutionError, match="could not read list file"):
            resolve(f"@{tmp_path / 'nope.txt'}")

    def test_empty_listfile_returns_empty_list(self, tmp_path: Path) -> None:
        listfile = tmp_path / "empty.txt"
        listfile.write_text("# only comments here\n\n", encoding="utf-8")
        result = resolve(f"@{listfile}")
        assert result == []


# ----------------------- 6. resolve URL (clone-mocked) -------------------


class _CloneFaker:
    """Replacement for ``_shallow_clone`` that copies a prebuilt
    fixture tree into the destination instead of running git."""

    def __init__(self, fixtures_for_repo: dict[tuple[str, str], Callable[[Path], Path]]):
        self._mapping = fixtures_for_repo
        self.calls: list[tuple[str, str, Path]] = []

    def __call__(self, owner: str, repo: str, dest: Path, branch: str | None = None) -> Path:
        self.calls.append((owner, repo, dest))
        builder = self._mapping.get((owner, repo))
        if builder is None:
            raise InputResolutionError(f"unknown fixture for {owner}/{repo}")
        target = dest / repo
        target.mkdir(parents=True, exist_ok=False)
        builder(target)
        return target


class TestResolveUrl:
    def test_url_input_rejected_when_allow_url_false(self) -> None:
        with pytest.raises(InputResolutionError, match="not allowed"):
            resolve("https://github.com/owner/plugin-repo", allow_url=False)

    def test_owner_repo_rejected_when_allow_url_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)  # no local collision
        with pytest.raises(InputResolutionError, match="not allowed"):
            resolve("owner/repo", allow_url=False)

    def test_plugin_url_clones_and_returns_one_plugin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def build_plugin(target: Path) -> Path:
            (target / ".claude-plugin").mkdir(parents=True)
            (target / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "cloned-plugin", "version": "1.0.0"}),
                encoding="utf-8",
            )
            return target

        faker = _CloneFaker({("Emasoft", "cloned-plugin"): build_plugin})
        monkeypatch.setattr(cmi, "_shallow_clone", faker)

        result = resolve("https://github.com/Emasoft/cloned-plugin")
        try:
            assert len(result) == 1
            assert result[0].kind == "plugin"
            assert result[0].source_url == "https://github.com/Emasoft/cloned-plugin"
            assert result[0].display_name == "cloned-plugin"
            assert result[0].cleanup_callback is not None
            assert (result[0].abs_path / ".claude-plugin" / "plugin.json").is_file()
        finally:
            for r in result:
                if r.cleanup_callback is not None:
                    r.cleanup_callback()

    def test_url_cleanup_callback_removes_temp_dir(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def build_plugin(target: Path) -> Path:
            (target / ".claude-plugin").mkdir(parents=True)
            (target / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "p", "version": "0.0.1"}), encoding="utf-8"
            )
            return target

        monkeypatch.setattr(
            cmi,
            "_shallow_clone",
            _CloneFaker({("o", "p"): build_plugin}),
        )

        result = resolve("o/p")
        assert len(result) == 1
        # `_mk_tmp_clone_dir` returns ${TMPDIR}/cpv-batch-input-<uuid>/
        # and `_shallow_clone` puts the cloned repo as ${that}/<repo>/,
        # so the per-clone temp dir is the .parent of abs_path.
        per_clone_tmp = result[0].abs_path.parent
        assert per_clone_tmp.is_dir()
        assert per_clone_tmp.name.startswith("cpv-batch-input-")
        assert result[0].cleanup_callback is not None
        result[0].cleanup_callback()
        # Cleanup removes the per-clone temp dir, NOT the system $TMPDIR.
        assert not per_clone_tmp.exists()

    def test_url_pointing_at_bare_file_root_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def build_bare(target: Path) -> Path:
            (target / "README.md").write_text("# bare repo\n", encoding="utf-8")
            return target

        monkeypatch.setattr(
            cmi,
            "_shallow_clone",
            _CloneFaker({("bare", "stuff"): build_bare}),
        )
        with pytest.raises(InputResolutionError):
            resolve("bare/stuff")


# ----------------------- 7. marketplace expansion ------------------------


class TestMarketplaceExpansion:
    def test_local_marketplace_expands_to_one_per_plugin(
        self, tmp_path: Path
    ) -> None:
        # Set up three sibling plugins next to the marketplace root.
        p_a = _make_plugin(tmp_path, name="plug-a")
        p_b = _make_plugin(tmp_path, name="plug-b")
        p_c = _make_plugin(tmp_path, name="plug-c")
        market = _make_marketplace(
            tmp_path,
            [
                {"name": "plug-a", "version": "0.1.0", "source": {"source": "local", "path": "../plug-a"}},
                {"name": "plug-b", "version": "0.2.0", "source": {"source": "local", "path": "../plug-b"}},
                {"name": "plug-c", "version": "0.3.0", "source": {"source": "local", "path": "../plug-c"}},
            ],
        )
        result = resolve(str(market))
        assert {r.display_name for r in result} == {"plug-a", "plug-b", "plug-c"}
        assert all(r.kind == "plugin" for r in result)
        assert {r.abs_path for r in result} == {
            p_a.resolve(),
            p_b.resolve(),
            p_c.resolve(),
        }
        # Versions surface as metadata.
        versions = {r.display_name: r.metadata.get("plugin_version") for r in result}
        assert versions == {"plug-a": "0.1.0", "plug-b": "0.2.0", "plug-c": "0.3.0"}

    def test_marketplace_plugins_field_not_list_raises(
        self, tmp_path: Path
    ) -> None:
        bad = tmp_path / "bad-market"
        (bad / ".claude-plugin").mkdir(parents=True)
        (bad / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"name": "bad", "owner": {"name": "x"}, "plugins": "not-a-list"}),
            encoding="utf-8",
        )
        with pytest.raises(InputResolutionError, match="not a list"):
            resolve(str(bad))

    def test_marketplace_with_empty_plugins_returns_empty_list(
        self, tmp_path: Path
    ) -> None:
        market = _make_marketplace(tmp_path, plugin_specs=[])
        assert resolve(str(market)) == []

    def test_marketplace_url_expands_and_shares_cleanup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A URL marketplace clones, enumerates its plugins, and the
        per-plugin cleanup callbacks all share a reference-counted
        closure that only removes the temp dir after the LAST consumer
        calls cleanup."""

        def build_market(target: Path) -> Path:
            (target / ".claude-plugin").mkdir(parents=True)
            (target / ".claude-plugin" / "marketplace.json").write_text(
                json.dumps(
                    {
                        "name": "Emasoft/emasoft-plugins",
                        "owner": {"name": "Emasoft"},
                        "plugins": [
                            {
                                "name": "plug-x",
                                "version": "0.1.0",
                                "source": {"source": "github", "repo": "Emasoft/plug-x"},
                            },
                            {
                                "name": "plug-y",
                                "version": "0.2.0",
                                "source": {"source": "github", "repo": "Emasoft/plug-y"},
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return target

        def build_plugin_x(target: Path) -> Path:
            (target / ".claude-plugin").mkdir(parents=True)
            (target / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "plug-x", "version": "0.1.0"}), encoding="utf-8"
            )
            return target

        def build_plugin_y(target: Path) -> Path:
            (target / ".claude-plugin").mkdir(parents=True)
            (target / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "plug-y", "version": "0.2.0"}), encoding="utf-8"
            )
            return target

        monkeypatch.setattr(
            cmi,
            "_shallow_clone",
            _CloneFaker(
                {
                    ("Emasoft", "emasoft-plugins"): build_market,
                    ("Emasoft", "plug-x"): build_plugin_x,
                    ("Emasoft", "plug-y"): build_plugin_y,
                }
            ),
        )

        result = resolve("https://github.com/Emasoft/emasoft-plugins")
        try:
            assert len(result) == 2
            names = {r.display_name for r in result}
            assert names == {"plug-x", "plug-y"}
            # Marketplace-root metadata is set so consumers know where the
            # plugin came from.
            market_root = result[0].metadata["marketplace_root"]
            assert market_root == result[1].metadata["marketplace_root"]
            market_root_path = Path(str(market_root))
            assert market_root_path.is_dir()
            # Calling ONE cleanup must NOT remove the shared market root
            # while the other plugin still needs it.
            cb_a = result[0].cleanup_callback
            assert cb_a is not None
            cb_a()
            assert market_root_path.is_dir(), (
                "shared market root must persist until LAST consumer cleans up"
            )
        finally:
            for r in result:
                if r.cleanup_callback is not None:
                    r.cleanup_callback()


# ----------------------- 8. ResolvedInput dataclass ----------------------


class TestResolvedInputDataclass:
    def test_minimal_construction_has_no_cleanup(self) -> None:
        ri = ResolvedInput(kind="file", abs_path=Path("/tmp/x.py"))
        assert ri.cleanup_callback is None
        assert ri.source_url is None
        assert ri.display_name == ""
        assert ri.metadata == {}

    def test_metadata_field_is_independent_per_instance(self) -> None:
        """Regression: dataclass default_factory MUST give each instance
        its own dict (NOT a shared mutable default)."""
        ri1 = ResolvedInput(kind="file", abs_path=Path("/a"))
        ri2 = ResolvedInput(kind="file", abs_path=Path("/b"))
        ri1.metadata["k"] = "v"
        assert "k" not in ri2.metadata


# ----------------------- 9. skill-pack expansion (Phase 5.5) -------------


def _make_skill_in(root: Path, name: str) -> Path:
    """Create a flat ``<root>/<name>/SKILL.md`` skill folder."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: stub\n---\nbody\n", encoding="utf-8"
    )
    return skill_dir


class TestSkillPackExpansion:
    def test_anthropic_style_pack_expands(self, tmp_path: Path) -> None:
        """A repo with ``./skills/<name>/SKILL.md`` is a skill_pack."""
        _make_skill(tmp_path, name="alpha")
        _make_skill(tmp_path, name="beta")
        _make_skill(tmp_path, name="gamma")
        result = resolve(str(tmp_path))
        assert {r.display_name for r in result} == {"alpha", "beta", "gamma"}
        assert all(r.kind == "skill" for r in result)
        # The pack-root metadata is preserved.
        assert all(r.metadata.get("skill_pack_root") == str(tmp_path.resolve()) for r in result)

    def test_flat_pack_expands(self, tmp_path: Path) -> None:
        """A repo with ``./<name>/SKILL.md`` at depth-1 is also a pack."""
        _make_skill_in(tmp_path, "one")
        _make_skill_in(tmp_path, "two")
        _make_skill_in(tmp_path, "three")
        result = resolve(str(tmp_path))
        assert {r.display_name for r in result} == {"one", "two", "three"}
        assert all(r.kind == "skill" for r in result)

    def test_single_skill_root_is_kind_skill_not_pack(self, tmp_path: Path) -> None:
        """A repo with SKILL.md at the ROOT is a single ``skill``, not a pack."""
        (tmp_path / "SKILL.md").write_text(
            "---\nname: root-skill\ndescription: stub\n---\nbody\n",
            encoding="utf-8",
        )
        result = resolve(str(tmp_path))
        assert len(result) == 1
        assert result[0].kind == "skill"
        assert result[0].abs_path == tmp_path.resolve()

    def test_pack_skips_dot_git_and_node_modules(self, tmp_path: Path) -> None:
        """The pack walker MUST skip noise dirs even when they contain SKILL.md."""
        _make_skill_in(tmp_path, "real-skill")
        # Place a SKILL.md inside .git/ — must be ignored.
        bad = tmp_path / ".git" / "fake"
        bad.mkdir(parents=True)
        (bad / "SKILL.md").write_text("noise", encoding="utf-8")
        result = resolve(str(tmp_path))
        assert len(result) == 1
        assert result[0].display_name == "real-skill"

    def test_mixed_anthropic_and_flat_dedups(self, tmp_path: Path) -> None:
        """When BOTH layouts coexist, every skill surface exactly once."""
        _make_skill(tmp_path, name="anthropic-alpha")
        _make_skill_in(tmp_path, "flat-beta")
        result = resolve(str(tmp_path))
        assert {r.display_name for r in result} == {"anthropic-alpha", "flat-beta"}

    def test_pack_via_url_clone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """URL pointing at a skill-pack repo clones + expands."""
        def build_pack(target: Path) -> Path:
            (target / "skills" / "one").mkdir(parents=True)
            (target / "skills" / "one" / "SKILL.md").write_text(
                "---\nname: one\ndescription: stub\n---\n", encoding="utf-8"
            )
            (target / "skills" / "two").mkdir(parents=True)
            (target / "skills" / "two" / "SKILL.md").write_text(
                "---\nname: two\ndescription: stub\n---\n", encoding="utf-8"
            )
            return target

        monkeypatch.setattr(
            cmi,
            "_shallow_clone",
            _CloneFaker({("Emasoft", "pack-repo"): build_pack}),
        )
        result = resolve("https://github.com/Emasoft/pack-repo")
        try:
            assert len(result) == 2
            assert {r.display_name for r in result} == {"one", "two"}
            assert all(r.source_url == "https://github.com/Emasoft/pack-repo" for r in result)
        finally:
            for r in result:
                if r.cleanup_callback is not None:
                    r.cleanup_callback()

    def test_single_skill_repo_via_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """URL pointing at a one-skill repo (SKILL.md at root) clones as kind=skill."""
        def build_single_skill(target: Path) -> Path:
            (target / "SKILL.md").write_text(
                "---\nname: single-skill\ndescription: stub\n---\n",
                encoding="utf-8",
            )
            return target

        monkeypatch.setattr(
            cmi,
            "_shallow_clone",
            _CloneFaker({("Emasoft", "single-skill"): build_single_skill}),
        )
        result = resolve("https://github.com/Emasoft/single-skill")
        try:
            assert len(result) == 1
            assert result[0].kind == "skill"
            assert result[0].source_url == "https://github.com/Emasoft/single-skill"
        finally:
            for r in result:
                if r.cleanup_callback is not None:
                    r.cleanup_callback()

    def test_mixed_list_local_skill_and_local_plugin(self, tmp_path: Path) -> None:
        """A list containing both a skill folder AND a plugin folder
        resolves each independently (different kinds in one result list)."""
        skill_dir = _make_skill_in(tmp_path, "lonely-skill")
        plugin_dir = _make_plugin(tmp_path, name="lonely-plugin")
        # Wrap the skill in its own root so it's a "single skill" not a pack
        # (the pack heuristic fires when ≥1 child has SKILL.md AT depth-1).
        result = resolve([str(skill_dir), str(plugin_dir)])
        kinds_by_name = {r.display_name: r.kind for r in result}
        assert kinds_by_name == {"lonely-skill": "skill", "lonely-plugin": "plugin"}

    def test_pack_expansion_truncates_at_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pathological case: cap protects against runaway expansion."""
        # Lower the cap to 3 for the test so we don't have to create 10k skills.
        monkeypatch.setattr(cmi, "_SKILL_PACK_EXPAND_CAP", 3)
        for n in ("a", "b", "c", "d", "e"):
            _make_skill_in(tmp_path, n)
        result = resolve(str(tmp_path))
        assert len(result) == 3
        # Truncation is surfaced in metadata so the orchestrator can warn.
        assert any(r.metadata.get("expansion_truncated") for r in result)


class TestMixedMarketplaceEntries:
    """Phase 5.5: marketplace.json may list PLUGINS, SKILLS, or
    SKILL-PACKS interchangeably. The resolver expands each entry by
    its actual on-disk shape, not by trusting an entry-level type
    declaration."""

    def test_marketplace_with_mixed_plugin_and_skill_entries(
        self, tmp_path: Path
    ) -> None:
        # Plugin entry next to a bare skill entry.
        _make_plugin(tmp_path, name="real-plugin")
        _make_skill_in(tmp_path, "real-skill")  # ./real-skill/SKILL.md (no plugin.json)
        _make_marketplace(
            tmp_path,
            [
                {
                    "name": "real-plugin",
                    "version": "0.1.0",
                    "source": {"source": "local", "path": "../real-plugin"},
                },
                {
                    "name": "real-skill",
                    "version": "0.2.0",
                    "source": {"source": "local", "path": "../real-skill"},
                },
            ],
        )
        result = resolve(str(tmp_path / "demo-market"))
        kinds_by_name = {r.display_name: r.kind for r in result}
        assert kinds_by_name == {"real-plugin": "plugin", "real-skill": "skill"}

    def test_marketplace_entry_pointing_at_skill_pack_expands_inline(
        self, tmp_path: Path
    ) -> None:
        """A marketplace entry that resolves to a skill-pack folder
        expands inline into per-skill entries, each carrying the
        marketplace metadata."""
        pack_root = tmp_path / "shared-pack"
        pack_root.mkdir()
        _make_skill_in(pack_root, "one")
        _make_skill_in(pack_root, "two")
        _make_marketplace(
            tmp_path,
            [
                {
                    "name": "shared-pack",
                    "version": "0.1.0",
                    "source": {"source": "local", "path": "../shared-pack"},
                }
            ],
        )
        result = resolve(str(tmp_path / "demo-market"))
        assert {r.display_name for r in result} == {"one", "two"}
        assert all(r.kind == "skill" for r in result)
        # Each emitted entry carries the marketplace_root metadata.
        for r in result:
            assert "marketplace_root" in r.metadata
