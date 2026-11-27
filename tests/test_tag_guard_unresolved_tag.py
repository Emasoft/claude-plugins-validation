"""TRDD-4DC84M2T: an existing tag that cannot be resolved to a commit is refused.

Real git repositories, no mocks. Both copies of ``_ensure_tag_at_head`` are
exercised: CPV's own scripts/publish.py and the publish.py that
generate_plugin_repo.py emits for other plugins.
"""

from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

pub = importlib.import_module("scripts.publish")
gpr = importlib.import_module("scripts.generate_plugin_repo")


def _git(cwd: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, input=stdin, capture_output=True, text=True, check=True
    ).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "commit", "--allow-empty", "-q", "-m", "seed")
    _git(root, "tag", "-a", "v1.0.0", "-m", "release")
    blob = _git(root, "hash-object", "-w", "--stdin", stdin="not a commit")
    _git(root, "tag", "v-blob", blob)
    return root


def _emitted_publish(tmp_path: Path):
    from test_canon_143_genrepo import _params

    path = tmp_path / "emitted_publish.py"
    path.write_text(gpr.gen_publish_py(_params()), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("emitted_publish", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guards(tmp_path):
    return {"cpv": pub._ensure_tag_at_head, "emitted": _emitted_publish(tmp_path)._ensure_tag_at_head}


@pytest.mark.parametrize("copy", ["cpv", "emitted"])
def test_annotated_tag_at_head_is_accepted(tmp_path, guards, copy):
    """An annotated tag that peels to HEAD passes the guard."""
    root = _repo(tmp_path)
    assert guards[copy](root, "v1.0.0", "release") is True


@pytest.mark.parametrize("copy", ["cpv", "emitted"])
def test_tag_that_is_not_a_commit_is_refused(tmp_path, guards, copy, capsys):
    """A tag pointing at a blob cannot be resolved to a commit and is refused."""
    root = _repo(tmp_path)
    assert guards[copy](root, "v-blob", "release") is False
    out = capsys.readouterr()
    assert "Could not resolve local tag v-blob to a commit" in out.out + out.err


@pytest.mark.parametrize("copy", ["cpv", "emitted"])
def test_refused_tag_leaves_refs_unchanged(tmp_path, guards, copy):
    """Refusing never moves, creates or deletes a tag."""
    root = _repo(tmp_path)
    before = _git(root, "show-ref", "--tags")
    guards[copy](root, "v-blob", "release")
    assert _git(root, "show-ref", "--tags") == before
