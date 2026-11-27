"""Issue #284: the generated publish.py publishes to a configurable remote.

Real git repositories (bare repos on disk), no mocks. The template is rendered
for every pipeline profile and checked statically for hardcoded ``origin``
sites; the behavioural tests load the emitted module and drive it against a
working repo that has two remotes: ``origin`` -> ownerA/repoA and ``plugin`` ->
ownerB/repoB.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

gpr = importlib.import_module("scripts.generate_plugin_repo")

_ORIGIN_SITE = re.compile(
    r"""["']origin["'/]|remote\.origin|refs/remotes/origin|--atomic origin|--tags origin"""
)
# The only two lines allowed to spell the remote name: the documented default,
# and the submodule's OWN remote (a submodule is not the plugin repo).
_ORIGIN_ALLOWLIST = (
    'return "origin"',
    '"fetch", "--dry-run", "origin", ref',
)
_PROFILES = sorted(v for k, v in vars(gpr).items() if k.startswith("PROFILE_"))


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _render(profile: str | None = None) -> str:
    from test_canon_143_genrepo import _params

    if profile is None:
        return str(gpr.gen_publish_py(_params()))
    return str(gpr.gen_publish_py(_params(), profile))


def _emitted(tmp_path: Path) -> Any:
    path = tmp_path / "emitted_publish.py"
    path.write_text(_render(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("emitted_publish_284", path)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._publish_remote_override = None
    module._publish_remote_cache = None
    return module


class _World:
    def __init__(self, tmp_path: Path) -> None:
        self.bare_a = tmp_path / "ownerA" / "repoA.git"
        self.bare_b = tmp_path / "ownerB" / "repoB.git"
        for bare in (self.bare_a, self.bare_b):
            bare.mkdir(parents=True)
            _git(bare, "init", "--bare", "-q", "-b", "master")
        self.root = tmp_path / "work"
        self.root.mkdir()
        _git(self.root, "init", "-q", "-b", "master")
        _git(self.root, "config", "user.email", "t@example.invalid")
        _git(self.root, "config", "user.name", "t")
        manifest = self.root / ".claude-plugin" / "plugin.json"
        manifest.parent.mkdir()
        manifest.write_text(json.dumps({"name": "my-plugin", "version": "1.0.0"}), encoding="utf-8")
        _git(self.root, "add", ".claude-plugin/plugin.json")
        # Same subject stage_commit_and_push expects, so it skips its commit step.
        _git(self.root, "commit", "-q", "-m", "chore: bump version to 1.0.1")
        _git(self.root, "remote", "add", "origin", str(self.bare_a))
        _git(self.root, "remote", "add", "plugin", str(self.bare_b))
        _git(self.root, "push", "-q", "origin", "master")

    def refs(self, bare: Path) -> str:
        return _git(bare, "for-each-ref")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("CPV_PUBLISH_REMOTE", raising=False)
    monkeypatch.delenv("CPV_SKIP_GH_AUTH_CHECK", raising=False)


@pytest.fixture
def world(tmp_path):
    return _World(tmp_path)


@pytest.fixture
def mod(tmp_path):
    return _emitted(tmp_path)


# -- static: no hardcoded origin left in any rendered profile -----------------


@pytest.mark.parametrize("profile", _PROFILES)
def test_rendered_publish_py_has_no_hardcoded_origin(profile):
    """Every profile renders compilable source with no hardcoded origin remote site."""
    body = _render(profile)
    compile(body, f"publish_{profile}.py", "exec")
    offenders = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(body.splitlines(), 1)
        if _ORIGIN_SITE.search(line) and not any(a in line for a in _ORIGIN_ALLOWLIST)
    ]
    assert not offenders, "hardcoded origin sites:\n" + "\n".join(offenders)


@pytest.mark.parametrize("profile", _PROFILES)
def test_every_gh_release_and_run_call_is_pinned_with_repo(profile):
    """Every gh argv that creates a release or lists runs carries --repo."""
    body = _render(profile)
    gh_lines = [
        line
        for line in body.splitlines()
        if re.search(r'"gh"', line) and re.search(r'"(release|run)"', line)
    ]
    assert len(gh_lines) >= 3, "expected the release-create and two run-list argv lines"
    unpinned = [line.strip() for line in gh_lines if "--repo" not in line]
    assert not unpinned, "gh calls without --repo:\n" + "\n".join(unpinned)


# -- behaviour: remote selection ----------------------------------------------


def test_override_selects_plugin_remote(mod, world):
    """The --remote override wins and is returned."""
    mod._publish_remote_override = "plugin"
    assert mod._publish_remote(world.root) == "plugin"


def test_env_var_selects_plugin_remote(mod, world, monkeypatch):
    """$CPV_PUBLISH_REMOTE selects the remote when no override is set."""
    monkeypatch.setenv("CPV_PUBLISH_REMOTE", "plugin")
    assert mod._publish_remote(world.root) == "plugin"


def test_override_beats_env_var(mod, world, monkeypatch):
    """--remote takes precedence over $CPV_PUBLISH_REMOTE."""
    monkeypatch.setenv("CPV_PUBLISH_REMOTE", "origin")
    mod._publish_remote_override = "plugin"
    assert mod._publish_remote(world.root) == "plugin"


def test_default_is_origin(mod, world):
    """With nothing explicit the remote is origin."""
    assert mod._publish_remote(world.root) == "origin"


def test_default_origin_returned_unchecked_when_no_remote_exists(mod, tmp_path):
    """The default name is not validated, so a repo with no remote keeps today's behaviour."""
    bare_repo = tmp_path / "noremote"
    bare_repo.mkdir()
    _git(bare_repo, "init", "-q")
    assert mod._publish_remote(bare_repo) == "origin"


@pytest.mark.parametrize("name", ["nope", "-x", "has space", "a;b", ""])
def test_explicit_bad_remote_exits_1(mod, world, capsys, name):
    """An explicit remote that is invalid or not configured exits 1 naming it and the fix."""
    mod._publish_remote_override = name
    with pytest.raises(SystemExit) as exc:
        mod._publish_remote(world.root)
    assert exc.value.code == 1
    out = capsys.readouterr()
    text = out.out + out.err
    assert "git remote" in text
    if name.strip() and name.isalpha():
        assert name in text


def test_explicit_unconfigured_env_remote_exits_1(mod, world, monkeypatch):
    """An unconfigured $CPV_PUBLISH_REMOTE value exits 1 too."""
    monkeypatch.setenv("CPV_PUBLISH_REMOTE", "nope")
    with pytest.raises(SystemExit) as exc:
        mod._publish_remote(world.root)
    assert exc.value.code == 1


def test_resolved_remote_is_cached(mod, world):
    """The first resolution is cached for the rest of the run."""
    mod._publish_remote_override = "plugin"
    assert mod._publish_remote(world.root) == "plugin"
    mod._publish_remote_override = None
    assert mod._publish_remote(world.root) == "plugin"


# -- behaviour: every consumer follows the selected remote --------------------


def test_current_repo_slug_follows_remote(mod, world):
    """The gh --repo slug is the selected remote's owner/repo."""
    mod._publish_remote_override = "plugin"
    assert mod._current_repo_slug(world.root) == "ownerB/repoB"


def test_resolve_owner_repo_follows_remote(mod, world):
    """_resolve_owner_repo parses the selected remote's URL."""
    mod._publish_remote_override = "plugin"
    assert mod._resolve_owner_repo(world.root) == ("ownerB", "repoB")


def test_remote_slug_follows_remote(mod, world):
    """The branch-rules slug helper reads the selected remote (it parses ssh/https URLs only)."""
    _git(world.root, "remote", "set-url", "plugin", "https://github.com/ownerB/repoB.git")
    mod._publish_remote_override = "plugin"
    assert mod._get_remote_slug(world.root) == "ownerB/repoB"


def test_remote_tag_state_is_per_remote(mod, world):
    """A tag pushed only to the plugin remote exists there and not on origin."""
    _git(world.root, "tag", "-a", "v1.0.1", "-m", "r")
    _git(world.root, "push", "-q", "plugin", "v1.0.1")
    mod._publish_remote_override = "plugin"
    assert mod._remote_tag_state(world.root, "v1.0.1") is True
    mod._publish_remote_cache = None
    mod._publish_remote_override = "origin"
    assert mod._remote_tag_state(world.root, "v1.0.1") is False


def test_read_remote_version_is_per_remote(mod, world):
    """The idempotency baseline is read from the selected remote tracking branch."""
    manifest = world.root / ".claude-plugin" / "plugin.json"
    manifest.write_text(json.dumps({"name": "my-plugin", "version": "1.0.5"}), encoding="utf-8")
    _git(world.root, "commit", "-q", "-am", "newer")
    _git(world.root, "push", "-q", "plugin", "master")  # only the plugin remote sees 1.0.5
    mod._publish_remote_override = "plugin"
    assert mod._read_remote_version(world.root) == "1.0.5"
    mod._publish_remote_cache = None
    mod._publish_remote_override = "origin"
    assert mod._read_remote_version(world.root) == "1.0.0"


def test_stage_commit_and_push_pushes_only_to_selected_remote(mod, world, monkeypatch, capsys):
    """The release push (commit/tag/dependency tag) lands on remote B; A's refs are unchanged."""
    monkeypatch.setenv("CPV_SKIP_GH_AUTH_CHECK", "1")  # no network/gh in the test
    a_before = world.refs(world.bare_a)
    mod._publish_remote_override = "plugin"
    mod.stage_commit_and_push(world.root, "1.0.1", False)
    b_refs = world.refs(world.bare_b)
    assert "refs/heads/master" in b_refs
    assert "refs/tags/v1.0.1" in b_refs
    assert "refs/tags/my-plugin--v1.0.1" in b_refs
    assert world.refs(world.bare_a) == a_before
    assert "git push --atomic plugin" in capsys.readouterr().out


def test_pre_push_hook_child_sees_selected_remote_without_mutating_os_environ(mod, world, tmp_path, monkeypatch):
    """The hook (which runs `publish.py --gate`) gets CPV_PUBLISH_REMOTE via the push env only.

    Exporting it with `os.environ[...] =` inside the generated file is an ENV_INJECTION finding
    (#231), so the value must reach the hook child through the push subprocess env instead.
    """
    monkeypatch.setenv("CPV_SKIP_GH_AUTH_CHECK", "1")
    seen = tmp_path / "hook-saw-remote.txt"
    hook = world.root / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\nprintf %s \"$CPV_PUBLISH_REMOTE\" > '{seen}'\n", encoding="utf-8")
    hook.chmod(0o755)
    # A global core.hooksPath would bypass .git/hooks; pin this repo to its own hooks dir.
    _git(world.root, "config", "core.hooksPath", str(hook.parent))
    mod._publish_remote_override = "plugin"
    mod.stage_commit_and_push(world.root, "1.0.1", False)
    assert seen.read_text(encoding="utf-8") == "plugin"
    assert "CPV_PUBLISH_REMOTE" not in os.environ


def test_stage_commit_and_push_dry_run_names_selected_remote(mod, world, capsys):
    """The dry-run preview names the selected remote."""
    mod._publish_remote_override = "plugin"
    mod.stage_commit_and_push(world.root, "1.0.1", True)
    assert "Would push (atomic): plugin HEAD" in capsys.readouterr().out
