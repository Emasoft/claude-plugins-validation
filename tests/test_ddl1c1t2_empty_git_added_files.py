"""TRDD-DDL1C1T2: added-file detection must not trust a git index found in an install dir.

An attacker who can write into the install dir can plant an empty `.git` there, or
the install can sit inside an unrelated ancestor git work tree. `git ls-files` then
answers with an empty index, `enumerate_shipped_files` returned set(), and a planted
`scripts/evil_added.py` passed the self-integrity check. Pinned here:

- plugin mode refuses an added file when the install dir holds an empty `.git`;
- the same when the install sits inside an unrelated ancestor git repo;
- controls: a clean copy still passes, and manifest GENERATION on CPV's real repo is
  unchanged (still exactly the git-tracked set minus the two manifest files).

Every test runs the real check: skip variables deleted, HOME/cache/audit log sandboxed,
the tag manifest served by a stub (nothing reaches GitHub).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import _plugin_compute_hashes as pch
import _plugin_verify_hashes as pvh
import pytest

VERSION = "1.2.3"
EVIL = "scripts/evil_added.py"
REPO = Path(__file__).resolve().parent.parent


def _sha(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def _plugin(base: Path) -> Path:
    root = base / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "x", "version": VERSION}), encoding="utf-8"
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return root


def _manifest(root: Path) -> dict[str, object]:
    return {
        "files": {
            ".claude-plugin/plugin.json": _sha(root / ".claude-plugin" / "plugin.json"),
            "scripts/a.py": _sha(root / "scripts" / "a.py"),
        }
    }


@pytest.fixture
def armed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Arm the real plugin-mode check with the tag manifest stubbed out of the network."""
    for var in ("PLUGIN_SKIP_GITHUB_INTEGRITY", "CPV_SKIP_GITHUB_INTEGRITY", pvh.RELEASE_CANDIDATE_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(pvh, "_VERIFIED_THIS_PROCESS", False)
    monkeypatch.setattr(pvh, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pvh, "_AUDIT_LOG", tmp_path / "audit.log")


def _verify(root: Path, monkeypatch: pytest.MonkeyPatch) -> bool:
    manifest = _manifest(root)
    monkeypatch.setattr(pvh, "_fetch_github_manifest", lambda _v, prefer_cache=True: manifest)
    return pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True)


def _empty_git(root: Path) -> None:
    """The attacker-made `.git`: a valid, initialised repository whose index is empty."""
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=60)


def _plant(root: Path) -> None:
    (root / EVIL).write_text("print('injected')\n", encoding="utf-8")


def test_empty_git_dir_does_not_hide_an_added_file(armed: None, tmp_path: Path, monkeypatch, capsys) -> None:  # noqa: ANN001
    """An empty `.git` in the install dir must not blind added-file detection."""
    root = _plugin(tmp_path)
    _empty_git(root)
    _plant(root)
    assert pvh._detect_added_files(root, _manifest(root)["files"]) == [EVIL]  # type: ignore[arg-type]
    assert _verify(root, monkeypatch) is False
    assert EVIL in capsys.readouterr().err


def test_ancestor_git_repo_does_not_hide_an_added_file(armed: None, tmp_path: Path, monkeypatch, capsys) -> None:  # noqa: ANN001
    """An install inside an unrelated ancestor git work tree must not blind added-file detection."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    root = _plugin(tmp_path)
    _plant(root)
    assert pvh._detect_added_files(root, _manifest(root)["files"]) == [EVIL]  # type: ignore[arg-type]
    assert _verify(root, monkeypatch) is False
    assert EVIL in capsys.readouterr().err


def test_untracked_added_file_in_a_real_git_clone_is_caught(armed: None, tmp_path: Path) -> None:
    """Even a genuine git root: a file nobody `git add`ed is on disk, so it is an added file."""
    root = _plugin(tmp_path)
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=60)
    subprocess.run(["git", "-C", str(root), "add", ".claude-plugin/plugin.json", "scripts/a.py"], check=True, timeout=60)
    _plant(root)
    assert pvh._detect_added_files(root, _manifest(root)["files"]) == [EVIL]  # type: ignore[arg-type]


@pytest.mark.parametrize("layout", ["plain", "empty-git", "ancestor-git"])
def test_clean_copy_passes(armed: None, tmp_path: Path, monkeypatch, layout: str) -> None:  # noqa: ANN001
    """Control: with no extra file the check passes under every layout (no false positive)."""
    if layout == "ancestor-git":
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=60)
    root = _plugin(tmp_path)
    if layout == "empty-git":
        _empty_git(root)
    assert _verify(root, monkeypatch) is True


def test_release_candidate_enumeration_still_uses_git(tmp_path: Path) -> None:
    """The anchored release-candidate path keeps the git set (clean tree, HEAD pinned): gitignored files stay invisible."""
    root = _plugin(tmp_path)
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=60)
    (root / ".gitignore").write_text("local_only/\n", encoding="utf-8")
    (root / "local_only").mkdir()
    (root / "local_only" / "note.py").write_text("y = 2\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "add", ".gitignore", ".claude-plugin/plugin.json", "scripts/a.py"], check=True, timeout=60
    )
    files = {**_manifest(root)["files"], ".gitignore": _sha(root / ".gitignore")}  # type: ignore[dict-item]
    assert pvh._detect_added_files(root, files, include_disk_walk=False) == []  # type: ignore[arg-type]
    assert pvh._detect_added_files(root, files) == ["local_only/note.py"]  # type: ignore[arg-type]


def test_generation_on_the_real_repo_is_unchanged() -> None:
    """Control: manifest generation still lists exactly the git-tracked set minus the two manifest files."""
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, check=True, timeout=60).stdout
    tracked = {e for e in out.decode("utf-8", errors="surrogateescape").split("\0") if e}
    expected = {e for e in tracked if Path(e).name not in pch._MANIFEST_BASENAMES}
    assert pch.enumerate_shipped_files(REPO) == expected
    assert set(pch.compute_manifest(REPO)["files"]) <= expected  # type: ignore[arg-type]


def test_empty_result_is_impossible_when_files_exist(tmp_path: Path) -> None:
    """The disk walk never yields set() for a root that holds files, whatever `.git` says."""
    root = _plugin(tmp_path)
    _empty_git(root)
    assert pch.walk_shipped_files(root) == {"scripts/a.py", ".claude-plugin/plugin.json"}
    assert pvh._detect_added_files(root, {}) == [".claude-plugin/plugin.json", "scripts/a.py"]
