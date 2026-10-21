"""Release-candidate anchor for the integrity check (owner decision 2026-10-21).

An unreleased CPV version has no GitHub manifest, so publish.py used to run its
validator gates with the integrity check switched off. It now passes
PLUGIN_INTEGRITY_RELEASE_CANDIDATE="<sha256 of the manifest>:<HEAD sha>", and
the verifier hashes every file against the manifest committed in that exact,
clean commit. These tests use real git repositories: each passing case proves
the anchor verifies, and each refusing case proves one way of tampering is
caught, with the refusal reason asserted and an audit line written.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import _plugin_verify_hashes as pvh
import publish
import pytest
from _plugin_compute_hashes import compute_manifest, write_manifest

ENV = pvh.RELEASE_CANDIDATE_ENV


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit_all(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", message)


def _anchor(root: Path) -> str:
    sha = hashlib.sha256((root / pvh.MANIFEST_FILE).read_bytes()).hexdigest()
    return f"{sha}:{_git(root, 'rev-parse', 'HEAD')}"


def _regen_and_commit(root: Path, message: str) -> None:
    write_manifest(root, compute_manifest(root))
    _commit_all(root, message)


@pytest.fixture
def candidate(tmp_path: Path) -> Path:
    """A clean, committed plugin root whose committed manifest matches every file."""
    root = (tmp_path / "plugin").resolve()
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "demo", "version": "1.0.0"}\n')
    (root / "scripts").mkdir()
    (root / "scripts" / "tool.py").write_text("print('hello')\n")
    (root / "README.md").write_text("# demo\n")
    _git(root, "init", "-q")
    _commit_all(root, "initial")
    _regen_and_commit(root, "manifest")
    return root


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(pvh, "_VERIFIED_THIS_PROCESS", False)
    monkeypatch.delenv("PLUGIN_SKIP_GITHUB_INTEGRITY", raising=False)
    monkeypatch.delenv("CPV_SKIP_GITHUB_INTEGRITY", raising=False)
    log = tmp_path / "audit.log"
    monkeypatch.setattr(pvh, "_AUDIT_LOG", log)
    return log


def _events(log: Path) -> list[dict[str, object]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def _verify(root: Path) -> bool:
    return pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True)


def _refused_with(root: Path, text: str, capsys: pytest.CaptureFixture[str], log: Path) -> None:
    assert _verify(root) is False
    err = capsys.readouterr().err
    assert text in err, err
    assert pvh._VERIFIED_THIS_PROCESS is False
    refusals = [e for e in _events(log) if e["event"] == "refused"]
    assert refusals and refusals[-1]["mode"] == "release-candidate"


def test_clean_candidate_passes(candidate: Path, monkeypatch: pytest.MonkeyPatch, capsys, _fresh_state: Path) -> None:
    """A clean committed candidate with the correct anchor verifies, says so even when quiet, and is audited."""
    monkeypatch.setenv(ENV, _anchor(candidate))
    assert _verify(candidate) is True
    assert "OK (release candidate)" in capsys.readouterr().err
    assert pvh._VERIFIED_THIS_PROCESS is True
    assert [e["event"] for e in _events(_fresh_state)] == ["release-candidate-verified"]


def test_wrong_manifest_hash_refuses(candidate: Path, monkeypatch: pytest.MonkeyPatch, capsys, _fresh_state) -> None:
    """An anchor whose manifest hash is not the on-disk manifest's refuses."""
    head = _git(candidate, "rev-parse", "HEAD")
    monkeypatch.setenv(ENV, "0" * 64 + ":" + head)
    _refused_with(candidate, "does not hash to the anchored value", capsys, _fresh_state)


def test_wrong_commit_refuses(candidate: Path, monkeypatch: pytest.MonkeyPatch, capsys, _fresh_state) -> None:
    """An anchor naming a commit other than HEAD refuses."""
    sha = _anchor(candidate).split(":")[0]
    monkeypatch.setenv(ENV, sha + ":" + "1" * 40)
    _refused_with(candidate, "not the anchored commit", capsys, _fresh_state)


@pytest.mark.parametrize("value", ["", "garbage", "A" * 64 + ":" + "b" * 40, "a" * 64 + "b" * 40])
def test_malformed_anchor_refuses(
    candidate: Path, monkeypatch: pytest.MonkeyPatch, capsys, _fresh_state, value: str
) -> None:
    """Empty, garbage, uppercase or separator-less anchors refuse; setting the variable never skips the check."""
    monkeypatch.setenv(ENV, value)
    _refused_with(candidate, "is malformed", capsys, _fresh_state)


def test_dirty_tracked_file_refuses(candidate: Path, monkeypatch: pytest.MonkeyPatch, capsys, _fresh_state) -> None:
    """An uncommitted edit to a tracked file refuses."""
    monkeypatch.setenv(ENV, _anchor(candidate))
    (candidate / "scripts" / "tool.py").write_text("print('tampered')\n")
    _refused_with(candidate, "the working tree is not clean", capsys, _fresh_state)


def test_untracked_file_refuses(candidate: Path, monkeypatch: pytest.MonkeyPatch, capsys, _fresh_state) -> None:
    """An untracked file dropped into the tree refuses."""
    monkeypatch.setenv(ENV, _anchor(candidate))
    (candidate / "scripts" / "evil.py").write_text("import os\n")
    _refused_with(candidate, "the working tree is not clean", capsys, _fresh_state)


def test_committed_edit_without_regeneration_refuses(
    candidate: Path, monkeypatch: pytest.MonkeyPatch, capsys, _fresh_state
) -> None:
    """A committed edit whose manifest was NOT regenerated refuses on the file's hash."""
    (candidate / "scripts" / "tool.py").write_text("print('tampered')\n")
    _commit_all(candidate, "edit")
    monkeypatch.setenv(ENV, _anchor(candidate))
    _refused_with(candidate, "scripts/tool.py: content differs from the committed manifest", capsys, _fresh_state)


def test_tracked_file_missing_from_manifest_refuses(
    candidate: Path, monkeypatch: pytest.MonkeyPatch, capsys, _fresh_state
) -> None:
    """A committed new file the manifest does not list refuses."""
    (candidate / "scripts" / "extra.py").write_text("x = 1\n")
    _commit_all(candidate, "add file, no regen")
    monkeypatch.setenv(ENV, _anchor(candidate))
    _refused_with(candidate, "scripts/extra.py: tracked by git but absent from the manifest", capsys, _fresh_state)


def test_on_disk_manifest_differs_from_committed_refuses(
    candidate: Path, monkeypatch: pytest.MonkeyPatch, capsys, _fresh_state
) -> None:
    """A manifest edited on disk (anchor computed from the edited bytes) refuses as a dirty tree."""
    manifest = candidate / pvh.MANIFEST_FILE
    manifest.write_text(manifest.read_text().replace("sha256:", "sha256:", 1) + "\n")
    monkeypatch.setenv(ENV, _anchor(candidate))
    _refused_with(candidate, "the working tree is not clean", capsys, _fresh_state)


def test_legacy_manifest_mismatch_refuses(
    candidate: Path, monkeypatch: pytest.MonkeyPatch, capsys, _fresh_state
) -> None:
    """A committed legacy manifest that differs from the canonical one refuses."""
    legacy = candidate / pvh.MANIFEST_FILE_LEGACY
    legacy.write_text('{"files": {}}\n')
    _commit_all(candidate, "diverge legacy")
    monkeypatch.setenv(ENV, _anchor(candidate))
    _refused_with(candidate, f"{pvh.MANIFEST_FILE_LEGACY} differs from {pvh.MANIFEST_FILE}", capsys, _fresh_state)


def test_not_git_top_level_refuses(candidate: Path, monkeypatch: pytest.MonkeyPatch, capsys, _fresh_state) -> None:
    """A plugin root nested below the git top level refuses."""
    outer = candidate.parent
    nested = outer / "nested"
    nested.mkdir()
    for rel in (".claude-plugin/plugin.json",):
        (nested / rel).parent.mkdir(parents=True, exist_ok=True)
        (nested / rel).write_text((candidate / rel).read_text())
    _git(outer, "init", "-q")
    _commit_all(outer, "outer")
    monkeypatch.setenv(ENV, "a" * 64 + ":" + _git(outer, "rev-parse", "HEAD"))
    _refused_with(nested, "is not the top level of its git work tree", capsys, _fresh_state)


def test_skip_variable_cannot_override_a_refusal(
    candidate: Path, monkeypatch: pytest.MonkeyPatch, capsys, _fresh_state
) -> None:
    """With the anchor set, a skip variable is ignored (and said so): a tampered file still refuses."""
    (candidate / "scripts" / "tool.py").write_text("print('tampered')\n")
    _commit_all(candidate, "edit")
    monkeypatch.setenv(ENV, _anchor(candidate))
    monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
    assert _verify(candidate) is False
    err = capsys.readouterr().err
    note = next(line for line in err.splitlines() if "is ignored for a release-candidate check" in line)
    assert "PLUGIN_SKIP_GITHUB_INTEGRITY" in note
    assert "content differs from the committed manifest" in err


def test_regenerated_and_committed_tampering_passes_but_leaves_a_commit(
    candidate: Path, monkeypatch: pytest.MonkeyPatch, _fresh_state: Path
) -> None:
    """The anchor's known limit: tampering that is committed WITH a regenerated manifest passes.

    That is the trade-off the owner accepted: the attacker must leave a commit
    behind (and the run is audited). This test pins the limit so nobody reads
    the anchor as stronger than it is.
    """
    before = _git(candidate, "rev-list", "--count", "HEAD")
    (candidate / "scripts" / "tool.py").write_text("print('tampered')\n")
    _commit_all(candidate, "edit")
    _regen_and_commit(candidate, "regen")
    monkeypatch.setenv(ENV, _anchor(candidate))
    assert _verify(candidate) is True
    assert int(_git(candidate, "rev-list", "--count", "HEAD")) == int(before) + 2
    assert _events(_fresh_state)[-1]["event"] == "release-candidate-verified"


def test_publish_candidate_env_strips_skips_and_sets_the_gate1_anchor(
    candidate: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """publish computes the anchor from the clean tree; the validator env carries it and no skip variable."""
    monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
    monkeypatch.setenv("CPV_SKIP_GITHUB_INTEGRITY", "1")
    monkeypatch.setattr(publish, "_release_anchor", publish._compute_release_anchor(candidate))
    env = publish._candidate_anchor_env()
    assert env[ENV] == _anchor(candidate)
    assert "PLUGIN_SKIP_GITHUB_INTEGRITY" not in env
    assert "CPV_SKIP_GITHUB_INTEGRITY" not in env


def test_publish_without_manifest_sets_no_anchor_and_still_strips_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repo with no self-hash manifest gets no anchor, and the skip variables are still removed."""
    monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
    monkeypatch.setenv(ENV, "stale-value-from-the-shell")
    monkeypatch.setattr(publish, "_release_anchor", None)
    env = publish._candidate_anchor_env()
    assert ENV not in env
    assert "PLUGIN_SKIP_GITHUB_INTEGRITY" not in env


@pytest.mark.parametrize("var", ["PLUGIN_SKIP_GITHUB_INTEGRITY", "CPV_SKIP_GITHUB_INTEGRITY"])
def test_gate0_refuses_integrity_skip_vars(monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    """Gate 0 aborts a publish when either integrity skip variable is set."""
    monkeypatch.setenv(var, "1")
    assert publish.stage_bypass_guard() == 1


def test_skip_variable_outside_pytest_is_warned_and_audited(
    candidate: Path, monkeypatch: pytest.MonkeyPatch, capsys, _fresh_state
) -> None:
    """A honoured skip variable (no anchor, not under pytest) prints a warning and writes an audit line."""
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
    assert _verify(candidate) is True
    assert "integrity check SKIPPED" in capsys.readouterr().err
    assert _events(_fresh_state)[-1]["event"] == "skip-variable-honoured"
