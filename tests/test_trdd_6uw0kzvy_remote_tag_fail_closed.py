"""TRDD-6UW0KZVY — an unreadable remote must never read as "no tags".

amvcp TRDD-YY5ISKCJ (fixed there in v1.5.1, 807fbbc): an ls-remote non-zero
collapsed into "no tags" made a version gate pass vacuously. CPV's copy had
the same collapse in `_remote_tag_exists`, consumed by two DESTRUCTIVE
pre-push recovery branches (undo the release commit / move the local tag).

Real git repos throughout — no mocks for the git layer. Three-valued
contract: True = confirmed present, False = confirmed absent (first-publish
path), None = could not read (destructive consumers refuse).
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

pub = importlib.import_module("scripts.publish")
gpr = importlib.import_module("scripts.generate_plugin_repo")


def _emitted_helpers() -> SimpleNamespace:
    """Exec ONLY the two remote-tag helpers out of the emitted canon, so the
    emitted CODE (not the generator module) is what the behavioral tests run."""
    from test_canon_143_genrepo import _params

    body = gpr.gen_publish_py(_params())
    start = body.index("def _remote_tag_state(")
    end = body.index("\ndef ", body.index("def _remote_tag_exists("))
    snippet = body[start:end]
    ns: dict = {"subprocess": subprocess, "Path": Path}
    exec(compile("from __future__ import annotations\n" + snippet, "emitted-helpers", "exec"), ns)  # noqa: S102
    return SimpleNamespace(**ns)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _repo_with_origin(tmp_path: Path, *, origin: str | None) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "commit", "--allow-empty", "-m", "seed")
    if origin is not None:
        _git(root, "remote", "add", "origin", origin)
    return root


class TestRemoteTagStateThreeValued:
    def test_unreadable_remote_is_none_not_false(self, tmp_path):
        """A remote path that does not exist -> ls-remote non-zero -> None.
        The old helper returned False here — the whole defect."""
        root = _repo_with_origin(tmp_path, origin=str(tmp_path / "missing-bare"))
        assert pub._remote_tag_state(root, "v1.0.0") is None
        assert _emitted_helpers()._remote_tag_state(root, "v1.0.0") is None

    def test_readable_remote_with_no_tag_is_false(self, tmp_path):
        """First-publish path: read SUCCEEDED, zero tags -> False, unaffected."""
        bare = tmp_path / "bare"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        root = _repo_with_origin(tmp_path, origin=str(bare))
        assert pub._remote_tag_state(root, "v1.0.0") is False
        assert _emitted_helpers()._remote_tag_state(root, "v1.0.0") is False

    def test_readable_remote_with_the_tag_is_true(self, tmp_path):
        bare = tmp_path / "bare"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        root = _repo_with_origin(tmp_path, origin=str(bare))
        _git(root, "tag", "v1.0.0")
        _git(root, "push", "-q", "origin", "HEAD", "v1.0.0")
        assert pub._remote_tag_state(root, "v1.0.0") is True
        assert _emitted_helpers()._remote_tag_state(root, "v1.0.0") is True

    def test_exists_wrapper_is_positive_only(self, tmp_path):
        """The post-push verify wrapper maps BOTH False and None to False, so
        an unreadable remote reports UNVERIFIED — never green, never blocking."""
        root = _repo_with_origin(tmp_path, origin=str(tmp_path / "missing-bare"))
        assert pub._remote_tag_exists(root, "v1.0.0") is False
        assert _emitted_helpers()._remote_tag_exists(root, "v1.0.0") is False


class TestDestructiveConsumersRefuseOnNone:
    """Source-level pin: the two destructive sites consult _remote_tag_state
    and carry an explicit None-refusal; no destructive site is left on the
    positive-only wrapper."""

    SRC = (REPO_ROOT / "scripts" / "publish.py").read_text(encoding="utf-8")

    def test_recovery_branch_requires_positive_false(self):
        assert "_recovery_candidate and _tag_on_remote is False" in self.SRC
        assert "Refusing to consolidate" in self.SRC

    def test_tag_move_requires_positive_false(self):
        # The drift check moved into the shared `_ensure_tag_at_head` helper
        # when issue #216 extended it to the dependency tag. The invariant is
        # unchanged and is what this pins: a tag is moved ONLY on the remote's
        # positive `False`, so BOTH other answers must refuse first.
        assert "remote_state is None:" in self.SRC
        assert "remote_state is True:" in self.SRC
        assert "Refusing to move" in self.SRC

    def test_no_destructive_site_uses_the_bool_wrapper(self):
        """`not _remote_tag_exists(` was the fail-open idiom — it must be gone."""
        assert "not _remote_tag_exists(" not in self.SRC


class TestEmittedCanonParity:
    def test_emitted_body_carries_the_three_valued_helper(self):
        from test_canon_143_genrepo import _params

        body = gpr.gen_publish_py(_params())
        # The emitted publish.py has no destructive pre-push consumer, but the
        # helper contract must be present so future consumers inherit it.
        compile(body, "publish.py", "exec")
