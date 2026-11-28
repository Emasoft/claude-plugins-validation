"""TRDD-Z2GGOPPX: the disk walk's skip list must match a dir name only at the plugin root.

`walk_shipped_files` skipped a file when ANY path component was a skip-list name, so
`scripts/build/evil.py`, `skills/x/dist/evil.py` or `scripts/reports/evil.py` were invisible
to added-file detection (the safety net when `git ls-files` lists nothing, TRDD-DDL1C1T2).
Pinned here: build/dist/reports-style dirs are skipped only as the FIRST component; the
tool-regenerated caches (`__pycache__` ...) stay skipped at any depth; the end-to-end
self-integrity check fails on a planted `scripts/build/evil_added.py` under an empty `.git`.

Every test uses real files in tmp_path; the end-to-end one runs the real check with the
tag manifest served by a stub (nothing reaches GitHub).
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
EVIL = "scripts/build/evil_added.py"


def _sha(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def _plugin(base: Path) -> Path:
    root = base / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "x", "version": VERSION}), encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return root


def _touch(root: Path, rel: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"data\n")


@pytest.mark.parametrize("rel", ["scripts/build/evil.py", "skills/x/dist/evil.py", "scripts/reports/evil.py"])
def test_nested_skip_named_dir_is_walked(tmp_path: Path, rel: str) -> None:
    """A file under a nested build/dist/reports dir is returned by the walk (not hidden)."""
    root = _plugin(tmp_path)
    _touch(root, rel)
    assert rel in pch.walk_shipped_files(root)


@pytest.mark.parametrize("rel", ["build/x.py", "dist/x.py", "reports/x.md", ".venv/x.py"])
def test_top_level_skip_named_dir_is_skipped(tmp_path: Path, rel: str) -> None:
    """The same names as the FIRST path component stay skipped (never-shipped dirs)."""
    root = _plugin(tmp_path)
    _touch(root, rel)
    assert rel not in pch.walk_shipped_files(root)


@pytest.mark.parametrize("rel", ["scripts/__pycache__/x.cpython-312.pyc", "__pycache__/y.cpython-312.pyc", "a/b/.pytest_cache/z"])
def test_tool_cache_dirs_are_skipped_at_any_depth(tmp_path: Path, rel: str) -> None:
    """Tool-regenerated caches (__pycache__, .pytest_cache ...) are never returned, at any depth."""
    root = _plugin(tmp_path)
    _touch(root, rel)
    assert rel not in pch.walk_shipped_files(root)


def test_planted_file_in_nested_build_dir_fails_integrity_check(tmp_path: Path, monkeypatch, capsys) -> None:  # noqa: ANN001
    """End to end: an empty `.git` plus scripts/build/evil_added.py must fail the self-integrity check."""
    for var in ("PLUGIN_SKIP_GITHUB_INTEGRITY", "CPV_SKIP_GITHUB_INTEGRITY", pvh.RELEASE_CANDIDATE_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(pvh, "_VERIFIED_THIS_PROCESS", False)
    monkeypatch.setattr(pvh, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(pvh, "_AUDIT_LOG", tmp_path / "audit.log")
    root = _plugin(tmp_path)
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=60)
    _touch(root, EVIL)
    manifest = {
        "files": {
            ".claude-plugin/plugin.json": _sha(root / ".claude-plugin" / "plugin.json"),
            "scripts/a.py": _sha(root / "scripts" / "a.py"),
        }
    }
    monkeypatch.setattr(pvh, "_fetch_github_manifest", lambda _v, prefer_cache=True: manifest)
    assert pvh._detect_added_files(root, manifest["files"]) == [EVIL]
    assert pvh.verify_self_integrity(root, fail_on_mismatch=False, quiet=True) is False
    assert EVIL in capsys.readouterr().err
