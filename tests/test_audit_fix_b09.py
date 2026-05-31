"""Audit batch B09 — regression tests for scripts/publish.py fixes.

Covers three audit findings against ``scripts/publish.py``:

* Finding 16 — ``stage_check_working_tree`` used to auto-commit a uv.lock
  *deletion* under the message ``chore: update uv.lock``. A deleted lockfile
  is a destructive change, not the benign ``uv run`` re-resolve the gate is
  designed around, so it must now fall through to the hard-stop branch and
  return 1. These tests drive a real temporary git repo (no mocking — the
  function only shells out to ``git status/add/commit``).

* Finding 80 — ``stage_github_release`` used to ``return 0`` even when
  ``gh release create`` failed with a genuine error, making the publish
  falsely report success. It must now return the gh exit code on a real
  failure, while still treating "release already exists" (idempotent re-run)
  and "gh not installed" as benign (return 0). Driven via stdlib
  monkeypatching of ``publish.gh_with_retry`` and the auth helpers.

* Finding 155 — the ``_PrefetchResults`` / ``_start_prefetch`` docstrings used
  to claim the prefetch ThreadPoolExecutor workers are ``daemon=True``. The
  stdlib executor exposes no ``daemon`` kwarg and its workers are non-daemon.
  This is a guard that pins the real runtime fact so the corrected docstring
  cannot silently drift back to the false claim.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Defensive: tests/conftest.py adds scripts/ to sys.path; this duplicate
# guard makes the file work when collected in isolation.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import publish  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    """Run a git command in ``repo`` and raise on failure (test setup helper)."""
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _init_repo_with_uv_lock(repo: Path) -> None:
    """Create a committed git repo whose HEAD contains uv.lock."""
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "uv.lock").write_text("# lock v1\n", encoding="utf-8")
    (repo / "keep.txt").write_text("keep\n", encoding="utf-8")
    _git(repo, "add", "uv.lock", "keep.txt")
    _git(repo, "commit", "-qm", "init")


def _head_committed_paths(repo: Path) -> set[str]:
    """Return the set of paths changed by the HEAD commit (name-status)."""
    out = subprocess.run(
        ["git", "show", "--name-status", "--format=", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    paths: set[str] = set()
    for line in out.splitlines():
        line = line.rstrip()
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) == 2:
            paths.add(parts[1])
    return paths


def _head_subject(repo: Path) -> str:
    """Return the subject line of the HEAD commit."""
    return subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()


class _FakeProc:
    """Minimal CompletedProcess stand-in for gh_with_retry monkeypatching."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ===========================================================================
# Finding 16 — uv.lock deletion must NOT be auto-committed.
# ===========================================================================


def test_uv_lock_modification_is_auto_committed(tmp_path: Path) -> None:
    """A sole uv.lock MODIFICATION is auto-committed and Gate 1 passes (rc 0).

    This is the benign case the gate is designed around (``uv run`` rewrote
    the lock in place). It is the positive control for the deletion guard.
    """
    repo = tmp_path / "modrepo"
    repo.mkdir()
    _init_repo_with_uv_lock(repo)
    # Simulate `uv run` re-resolving the lock in place (unstaged modification).
    (repo / "uv.lock").write_text("# lock v2 (re-resolved)\n", encoding="utf-8")

    rc = publish.stage_check_working_tree(repo)

    assert rc == 0, "a sole uv.lock modification must auto-commit and pass Gate 1"
    # The auto-commit happened with the canonical message.
    assert _head_subject(repo) == "chore: update uv.lock"
    assert _head_committed_paths(repo) == {"uv.lock"}
    # Working tree is now clean.
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert porcelain.strip() == ""


def test_uv_lock_deletion_is_not_auto_committed(tmp_path: Path) -> None:
    """A sole uv.lock DELETION must NOT be auto-committed — Gate 1 returns 1.

    This is the bug finding 16 reports: a deleted lockfile produced " D uv.lock"
    in porcelain, whose ``line[3:]`` slice equals "uv.lock", so the old equality
    check (``dirty_files == {"uv.lock"}``) matched and silently committed the
    deletion as "chore: update uv.lock". The fix inspects the XY status field and
    rejects a "D" in either column.
    """
    repo = tmp_path / "delrepo"
    repo.mkdir()
    _init_repo_with_uv_lock(repo)
    # Delete the lockfile (unstaged deletion → porcelain " D uv.lock").
    (repo / "uv.lock").unlink()
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout
        == " D uv.lock\n"
    ), "precondition: porcelain reports an unstaged deletion"

    head_before = _head_subject(repo)
    rc = publish.stage_check_working_tree(repo)

    assert rc == 1, "a uv.lock deletion must be rejected, not auto-committed"
    # No new commit was created — HEAD is unchanged.
    assert _head_subject(repo) == head_before
    assert _head_subject(repo) != "chore: update uv.lock"
    # The deletion is still pending (not staged/committed by the gate).
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert "uv.lock" in porcelain


def test_uv_lock_staged_deletion_is_not_auto_committed(tmp_path: Path) -> None:
    """A STAGED uv.lock deletion ("D  uv.lock") is also rejected (X-column D)."""
    repo = tmp_path / "stagedeldelrepo"
    repo.mkdir()
    _init_repo_with_uv_lock(repo)
    # Stage the deletion → porcelain "D  uv.lock" (D in the X/staged column).
    _git(repo, "rm", "-q", "uv.lock")
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout
        == "D  uv.lock\n"
    ), "precondition: porcelain reports a staged deletion"

    head_before = _head_subject(repo)
    rc = publish.stage_check_working_tree(repo)

    assert rc == 1, "a staged uv.lock deletion must be rejected too"
    assert _head_subject(repo) == head_before


def test_clean_tree_passes(tmp_path: Path) -> None:
    """A clean working tree passes Gate 1 with rc 0 and no commit."""
    repo = tmp_path / "cleanrepo"
    repo.mkdir()
    _init_repo_with_uv_lock(repo)

    head_before = _head_subject(repo)
    rc = publish.stage_check_working_tree(repo)

    assert rc == 0
    assert _head_subject(repo) == head_before  # nothing committed


def test_uv_lock_plus_other_change_is_rejected(tmp_path: Path) -> None:
    """uv.lock modified alongside another file is rejected (not sole change)."""
    repo = tmp_path / "mixedrepo"
    repo.mkdir()
    _init_repo_with_uv_lock(repo)
    (repo / "uv.lock").write_text("# lock v2\n", encoding="utf-8")
    (repo / "keep.txt").write_text("changed\n", encoding="utf-8")

    rc = publish.stage_check_working_tree(repo)

    assert rc == 1, "uv.lock + another change is not the benign sole-uv.lock case"


# ===========================================================================
# Finding 80 — stage_github_release must surface genuine gh failures.
# ===========================================================================


def _patch_release_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the auth precheck + owner/repo resolution so only the gh call matters."""
    monkeypatch.setattr(publish.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(publish, "_resolve_owner_repo", lambda _root: ("owner", "repo"))
    monkeypatch.setattr(publish, "_ensure_gh_auth", lambda _o, _r: None)


def test_release_success_returns_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful gh release create returns 0."""
    _patch_release_env(monkeypatch)
    monkeypatch.setattr(
        publish,
        "gh_with_retry",
        lambda *a, **k: _FakeProc(0, stdout="https://github.com/owner/repo/releases/tag/v1"),
    )
    notes = tmp_path / "notes.md"
    notes.write_text("notes\n", encoding="utf-8")

    rc = publish.stage_github_release(tmp_path, "v1.0.0", notes)
    assert rc == 0


def test_release_already_exists_returns_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An 'already exists' failure is idempotent success → return 0.

    Re-running publish.py on a tag whose release was already created (or an
    interrupted-publish recovery) must NOT fail the gate, because the release
    IS present. Both spellings gh emits are accepted.
    """
    _patch_release_env(monkeypatch)
    for stderr in (
        "HTTP 422: Validation Failed (https://api.github.com/...)\nrelease already exists",
        'failed to create release: Release.tag_name "v1" already_exists',
    ):
        monkeypatch.setattr(publish, "gh_with_retry", lambda *a, _e=stderr, **k: _FakeProc(1, stderr=_e))
        notes = tmp_path / "notes.md"
        notes.write_text("notes\n", encoding="utf-8")

        rc = publish.stage_github_release(tmp_path, "v1.0.0", notes)
        assert rc == 0, f"already-exists must be treated as success (stderr={stderr!r})"


def test_release_genuine_failure_returns_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuine gh release create failure returns the non-zero gh exit code.

    This is the core of finding 80: the previous code returned 0 here, so the
    orchestrator (``if rc != 0: return rc``) printed a false ``✓ Published``.
    A real failure (auth revoked, malformed notes, network exhausted) must now
    propagate so the publish reports failure.
    """
    _patch_release_env(monkeypatch)
    monkeypatch.setattr(
        publish,
        "gh_with_retry",
        lambda *a, **k: _FakeProc(1, stderr="HTTP 401: Bad credentials"),
    )
    notes = tmp_path / "notes.md"
    notes.write_text("notes\n", encoding="utf-8")

    rc = publish.stage_github_release(tmp_path, "v1.0.0", notes)
    assert rc != 0, "a genuine gh release failure must NOT be swallowed as success"


def test_release_failure_exitcode_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The gh exit code is preserved verbatim on a genuine failure."""
    _patch_release_env(monkeypatch)
    monkeypatch.setattr(
        publish,
        "gh_with_retry",
        lambda *a, **k: _FakeProc(7, stderr="some other gh error"),
    )
    notes = tmp_path / "notes.md"
    notes.write_text("notes\n", encoding="utf-8")

    rc = publish.stage_github_release(tmp_path, "v1.0.0", notes)
    assert rc == 7


def test_release_gh_missing_returns_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """gh not installed is a documented graceful degradation → return 0."""
    monkeypatch.setattr(publish.shutil, "which", lambda _name: None)
    notes = tmp_path / "notes.md"
    notes.write_text("notes\n", encoding="utf-8")

    rc = publish.stage_github_release(tmp_path, "v1.0.0", notes)
    assert rc == 0


# ===========================================================================
# Finding 155 — ThreadPoolExecutor workers are non-daemon (docstring guard).
# ===========================================================================


def test_threadpool_workers_are_non_daemon() -> None:
    """Pin the runtime fact the corrected docstrings now state: the prefetch
    ThreadPoolExecutor workers are NON-daemon (stdlib exposes no daemon kwarg).

    Guards against the docstring drifting back to the false "daemon=True" claim
    that finding 155 reported. If a future change actually made the workers
    daemon (impossible via the public constructor today), this test fails and
    forces the docstring + this guard to be revisited together.
    """
    import inspect
    import threading
    from concurrent.futures import ThreadPoolExecutor

    # The constructor genuinely has no `daemon` parameter, so the docstring
    # claim "set daemon=True in start_prefetch()" was unimplementable.
    params = list(inspect.signature(ThreadPoolExecutor.__init__).parameters)[1:]
    assert "daemon" not in params, "ThreadPoolExecutor has no daemon kwarg — claim was false"

    captured: dict[str, bool] = {}

    def _probe() -> None:
        captured["daemon"] = threading.current_thread().daemon

    ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cpv-prefetch")
    try:
        ex.submit(_probe).result(timeout=10)
    finally:
        ex.shutdown(wait=True)

    assert captured["daemon"] is False, "prefetch pool workers are non-daemon at runtime"


def test_prefetch_docstrings_do_not_claim_daemon_true() -> None:
    """The corrected docstrings must not claim the workers are daemon=True."""
    shutdown_doc = publish._PrefetchResults.shutdown.__doc__ or ""
    start_doc = publish._start_prefetch.__doc__ or ""
    for doc, where in ((shutdown_doc, "shutdown"), (start_doc, "_start_prefetch")):
        lowered = doc.lower()
        assert "daemon=true" not in lowered, f"{where} docstring still claims daemon=True"
        # It should affirmatively describe the workers as non-daemon.
        assert "non-daemon" in lowered, f"{where} docstring should state workers are non-daemon"
