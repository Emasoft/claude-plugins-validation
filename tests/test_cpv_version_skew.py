"""Real, non-mocked tests for scripts/cpv_version_skew.py (GitHub issue #212)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cpv_version_skew as vs  # noqa: E402


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_plugin(tmp_path: Path, name: str, version: str) -> Path:
    plugin_dir = tmp_path / "installed-plugin"
    _write_json(plugin_dir / ".claude-plugin" / "plugin.json", {"name": name, "version": version})
    return plugin_dir


def _make_marketplace(tmp_path: Path, plugin_name: str, marketplace_version: str) -> Path:
    mp_dir = tmp_path / "marketplace"
    _write_json(
        mp_dir / ".claude-plugin" / "marketplace.json",
        {
            "name": "test-marketplace",
            "owner": {"name": "Test"},
            "plugins": [{"name": plugin_name, "version": marketplace_version, "source": {"source": "github", "repo": "x/y"}}],
        },
    )
    return mp_dir


def test_in_sync_exits_zero(tmp_path, capsys):
    """Identical installed and marketplace versions report skew=none and exit 0."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.2.3")
    mp_dir = _make_marketplace(tmp_path, "foo", "1.2.3")
    rc = vs.main([str(plugin_dir), str(mp_dir), "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skew"] == "none"
    assert out["installed"] == "1.2.3"
    assert out["marketplace"] == "1.2.3"


def test_major_skew_exits_one(tmp_path, capsys):
    """A major-version-behind installed plugin reports skew=major and exits 1."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.0.0")
    mp_dir = _make_marketplace(tmp_path, "foo", "2.0.0")
    rc = vs.main([str(plugin_dir), str(mp_dir), "--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["skew"] == "major"


def test_minor_skew_exits_two(tmp_path, capsys):
    """A minor-version-behind installed plugin reports skew=minor and exits 2."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.2.0")
    mp_dir = _make_marketplace(tmp_path, "foo", "1.5.0")
    rc = vs.main([str(plugin_dir), str(mp_dir), "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["skew"] == "minor"


def test_patch_skew_exits_two(tmp_path, capsys):
    """A patch-version-behind installed plugin reports skew=patch and exits 2."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.2.3")
    mp_dir = _make_marketplace(tmp_path, "foo", "1.2.9")
    rc = vs.main([str(plugin_dir), str(mp_dir), "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["skew"] == "patch"


def test_name_not_found_exits_three(tmp_path, capsys):
    """A plugin name absent from the marketplace's plugin list exits 3."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.0.0")
    mp_dir = _make_marketplace(tmp_path, "bar", "1.0.0")
    rc = vs.main([str(plugin_dir), str(mp_dir), "--json"])
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert "not found" in out["reason"]


def test_bad_installed_version_exits_three(tmp_path, capsys):
    """A non-strict-semver installed version (e.g. with a pre-release suffix) exits 3."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.0.0-beta")
    mp_dir = _make_marketplace(tmp_path, "foo", "1.0.0")
    rc = vs.main([str(plugin_dir), str(mp_dir), "--json"])
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert "semver" in out["reason"]


def test_bad_marketplace_version_exits_three(tmp_path, capsys):
    """A non-strict-semver marketplace version exits 3, not a false match."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.0.0")
    mp_dir = _make_marketplace(tmp_path, "foo", "not-a-version")
    rc = vs.main([str(plugin_dir), str(mp_dir), "--json"])
    assert rc == 3


def test_missing_installed_manifest_exits_three(tmp_path, capsys):
    """A plugin dir with no .claude-plugin/plugin.json exits 3."""
    plugin_dir = tmp_path / "empty-plugin"
    plugin_dir.mkdir()
    mp_dir = _make_marketplace(tmp_path, "foo", "1.0.0")
    rc = vs.main([str(plugin_dir), str(mp_dir), "--json"])
    assert rc == 3


def test_missing_marketplace_ref_exits_three(tmp_path, capsys):
    """A marketplace-ref path that resolves to nothing exits 3, not a crash."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.0.0")
    rc = vs.main([str(plugin_dir), str(tmp_path / "does-not-exist"), "--json"])
    assert rc == 3


def test_missing_relative_path_with_slash_reports_no_such_path_not_git_clone(tmp_path, capsys, monkeypatch):
    """A missing relative path shaped like owner/repo (e.g. .claude-plugin/marketplace.json) fails
    as 'no such path', never as a doomed git-clone attempt (coordinator-reported bug)."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.0.0")
    monkeypatch.chdir(tmp_path)
    rc = vs.main([str(plugin_dir), ".claude-plugin/marketplace.json", "--json"])
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert "no such path" in out["reason"]
    assert "clone" not in out["reason"].lower()


def test_marketplace_entry_missing_version_exits_three(tmp_path, capsys):
    """A marketplace plugin entry with no 'version' pin exits 3."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.0.0")
    mp_dir = tmp_path / "marketplace"
    _write_json(
        mp_dir / ".claude-plugin" / "marketplace.json",
        {"name": "mp", "owner": {"name": "T"}, "plugins": [{"name": "foo", "source": {"source": "github", "repo": "x/y"}}]},
    )
    rc = vs.main([str(plugin_dir), str(mp_dir), "--json"])
    assert rc == 3


def test_json_flag_shape(tmp_path, capsys):
    """--json emits exactly the documented keys."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.0.0")
    mp_dir = _make_marketplace(tmp_path, "foo", "1.0.0")
    vs.main([str(plugin_dir), str(mp_dir), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert set(out.keys()) == {"plugin", "installed", "marketplace", "skew", "breaking_hint", "reason"}


def test_human_output_without_json_flag(tmp_path, capsys):
    """Without --json, human-readable text (not JSON) is printed to stdout."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.0.0")
    mp_dir = _make_marketplace(tmp_path, "foo", "1.0.0")
    rc = vs.main([str(plugin_dir), str(mp_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Plugin:" in out
    assert "Skew:        none" in out


def test_marketplace_ref_as_direct_json_file(tmp_path, capsys):
    """A marketplace-ref pointing directly at a marketplace.json file also works."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.0.0")
    mp_file = tmp_path / "marketplace.json"
    _write_json(
        mp_file,
        {"name": "mp", "owner": {"name": "T"}, "plugins": [{"name": "foo", "version": "1.0.0", "source": {"source": "github", "repo": "x/y"}}]},
    )
    rc = vs.main([str(plugin_dir), str(mp_file), "--json"])
    assert rc == 0


def test_relative_marketplace_ref_from_chdired_cwd_resolves_locally(tmp_path, capsys, monkeypatch):
    """A relative marketplace.json path resolves against a chdir'd cwd, not as a bogus owner/repo remote."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.0.0")
    _write_json(
        tmp_path / ".claude-plugin" / "marketplace.json",
        {"name": "mp", "owner": {"name": "T"}, "plugins": [{"name": "foo", "version": "1.0.0", "source": {"source": "github", "repo": "x/y"}}]},
    )
    monkeypatch.chdir(tmp_path)
    rc = vs.main([str(plugin_dir), ".claude-plugin/marketplace.json", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skew"] == "none"


def test_owner_repo_shaped_ref_that_exists_locally_resolves_as_local(tmp_path, capsys, monkeypatch):
    """A ref shaped like 'owner/repo' that also exists as a local dir resolves locally, never as a remote clone."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.0.0")
    owner_repo_dir = tmp_path / "someowner" / "somerepo"
    _write_json(
        owner_repo_dir / ".claude-plugin" / "marketplace.json",
        {"name": "mp", "owner": {"name": "T"}, "plugins": [{"name": "foo", "version": "1.0.0", "source": {"source": "github", "repo": "x/y"}}]},
    )
    monkeypatch.chdir(tmp_path)
    rc = vs.main([str(plugin_dir), "someowner/somerepo", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skew"] == "none"


def test_breaking_hint_true_when_changelog_mentions_breaking(tmp_path, capsys):
    """breaking_hint is True when the marketplace repo's CHANGELOG.md mentions BREAKING between the two versions."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.0.0")
    mp_dir = _make_marketplace(tmp_path, "foo", "2.0.0")
    (mp_dir / "CHANGELOG.md").write_text(
        "## 2.0.0\nBREAKING: removed the old API.\n\n## 1.0.0\ninitial release\n", encoding="utf-8"
    )
    rc = vs.main([str(plugin_dir), str(mp_dir), "--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["breaking_hint"] is True


def test_breaking_hint_false_when_no_changelog(tmp_path, capsys):
    """breaking_hint is False (never an error) when no changelog is present."""
    plugin_dir = _make_plugin(tmp_path, "foo", "1.0.0")
    mp_dir = _make_marketplace(tmp_path, "foo", "2.0.0")
    rc = vs.main([str(plugin_dir), str(mp_dir), "--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["breaking_hint"] is False
