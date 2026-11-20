"""Gate 12 push: release-candidate anchor and both output streams (TRDD-8OI25ZJ4).

The 5.23.2 release was refused at Gate 12: the tracked pre-push hook runs
validate_plugin.py, whose integrity check refuses an unreleased version unless
the run carries PLUGIN_INTEGRITY_RELEASE_CANDIDATE, and the push ran with a
plain copy of os.environ. These tests pin the two halves of the fix.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import publish  # noqa: E402


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _stub_gates_10_11(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make Gates 10-11 no-ops so the test reaches the Gate 12 push."""
    monkeypatch.setattr(publish, "run", lambda cmd, *_a, **_k: _completed())
    monkeypatch.setattr(publish, "_head_commit_message", lambda _root: "feat: unrelated")
    monkeypatch.setattr(publish, "_git_porcelain_clean", lambda _root: False)
    monkeypatch.setattr(publish, "_local_tag_exists", lambda _root, _tag: False)
    monkeypatch.setattr(publish, "_remote_tag_exists", lambda _root, _tag: False)
    monkeypatch.setattr(publish, "_resolve_owner_repo", lambda _root: ("Alice", "repo"))
    monkeypatch.setattr(publish, "_ensure_gh_auth", lambda _o, _r: None)
    monkeypatch.setattr(publish, "_ensure_submodules_pushed", lambda _root: None)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def test_push_env_carries_anchor_of_the_release_commit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The push env carries the anchor of the CURRENT HEAD and no skip variable."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    manifest = tmp_path / publish._SELF_HASH_MANIFEST_NAMES[0]
    manifest.write_text('{"files": {}}\n', encoding="utf-8")
    _git(tmp_path, "add", manifest.name)
    _git(tmp_path, "commit", "-q", "-m", "chore(release): v1.0.1")
    expected = f"{hashlib.sha256(manifest.read_bytes()).hexdigest()}:{_git(tmp_path, 'rev-parse', 'HEAD')}"

    # A stale Gate 1 anchor (the pre-bump HEAD) and an inherited skip variable:
    # the push must carry neither.
    monkeypatch.setattr(publish, "_release_anchor", "0" * 64 + ":" + "f" * 40)
    monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
    _stub_gates_10_11(monkeypatch)
    push_envs: list[dict[str, str] | None] = []

    def fake_git_with_retry(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["git", "push"]:
            push_envs.append(kwargs.get("env"))  # type: ignore[arg-type]
        return _completed()

    monkeypatch.setattr(publish, "git_with_retry", fake_git_with_retry)

    assert publish.stage_commit_tag_push(tmp_path, "v1.0.1") == 0
    assert len(push_envs) == 1
    env = push_envs[0]
    assert env is not None
    assert env.get(publish._RELEASE_CANDIDATE_ENV) == expected
    assert "PLUGIN_SKIP_GITHUB_INTEGRITY" not in env
    assert "CPV_SKIP_GITHUB_INTEGRITY" not in env


def test_failed_push_prints_stdout_and_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refused push prints both streams of git push, so a hook refusal on stdout is visible."""
    _stub_gates_10_11(monkeypatch)

    def fake_git_with_retry(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["git", "push"]:
            raise subprocess.CalledProcessError(
                1, cmd, output="HOOK-STDOUT-REFUSAL-MARKER\n", stderr="GIT-STDERR-MARKER\n"
            )
        return _completed()

    monkeypatch.setattr(publish, "git_with_retry", fake_git_with_retry)

    with pytest.raises(subprocess.CalledProcessError):
        publish.stage_commit_tag_push(tmp_path, "v1.0.1")
    captured = capsys.readouterr()
    shown = captured.out + captured.err
    assert "HOOK-STDOUT-REFUSAL-MARKER" in shown
    assert "GIT-STDERR-MARKER" in shown
