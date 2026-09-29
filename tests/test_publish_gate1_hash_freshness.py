"""Tests for publish.py Gate 1's self-hash manifest freshness check (TRDD-L8LIHYPA).

A stale or incomplete self-hash manifest silently disarms Gate 3's self-scan
exemption (per-file SHA mismatch → CPV flags its own rule-prose). Gate 1 now
fails BEFORE that, on BOTH mechanisms:

- staleness: a listed file's sha256 no longer matches the manifest, and
- completeness: a git-tracked file is absent from the manifest.

Fixtures use a REAL tmp git repo with a manifest written in the exact format
`_plugin_compute_hashes.py` emits (`{"files": {"<rel>": "sha256:<hex>"}}`), so
the checks run against real hashing, real git enumeration and a real manifest.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import publish  # noqa: E402

MANIFEST_NEW = ".plugin-self-hashes.json"
MANIFEST_LEGACY = ".cpv-self-hashes.json"


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )
    return result.stdout


def _init_repo(tmp_path: Path) -> Path:
    """A minimal tracked-file repo, committed, with a fresh self-hash manifest."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "scripts").mkdir()
    (root / "scripts" / "tool.py").write_text("print('v1')\n", encoding="utf-8")
    (root / "README.md").write_text("# repo\n", encoding="utf-8")
    _git("init", "-q", cwd=root)
    _git("config", "user.email", "t@e.com", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    return root


def _write_manifest(root: Path, *, legacy: bool = False) -> None:
    """Write a manifest in compute_manifest's format from the real tracked files."""
    tracked = set(_git("ls-files", "-z", cwd=root).split("\0")) - {""}
    files = {rel: _sha256(root / rel) for rel in sorted(tracked) if rel not in (MANIFEST_NEW, MANIFEST_LEGACY)}
    name = MANIFEST_LEGACY if legacy else MANIFEST_NEW
    (root / name).write_text(
        json.dumps({"version": 1, "computed_at": "2026-09-28T00:00:00+00:00", "files": files}, indent=2) + "\n",
        encoding="utf-8",
    )


def _gate(root: Path, monkeypatch, capsys) -> tuple[int, str]:
    """Run stage_check_working_tree with git status stubbed clean (the fixture
    tree IS clean of *status-visible* changes; the freshness check is what
    must catch the manifest drift). Returns (rc, stderr)."""
    import subprocess as _sp

    real_run = _sp.run

    def fake_run(cmd, *a, **k):
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(publish, "run", fake_run)
    rc = publish.stage_check_working_tree(root)
    return rc, capsys.readouterr().err


def test_fresh_manifest_gate1_passes(monkeypatch, tmp_path, capsys):
    """End-to-end: freshly generated manifest over a clean tree → Gate 1 exit 0."""
    root = _init_repo(tmp_path)
    _write_manifest(root)
    rc, _err = _gate(root, monkeypatch, capsys)
    assert rc == 0


def test_edited_tracked_file_stale_manifest_fails_naming_file(monkeypatch, tmp_path, capsys):
    """Staleness half: edit a tracked file after the regen → fail, name the file."""
    root = _init_repo(tmp_path)
    _write_manifest(root)
    (root / "scripts" / "tool.py").write_text("print('EDITED')\n", encoding="utf-8")
    rc, err = _gate(root, monkeypatch, capsys)
    assert rc == 1
    assert "scripts/tool.py" in err


def test_new_tracked_file_absent_from_manifest_fails(monkeypatch, tmp_path, capsys):
    """Completeness half: a NEW tracked file missing from the manifest → fail."""
    root = _init_repo(tmp_path)
    _write_manifest(root)
    (root / "new_component.py").write_text("# new\n", encoding="utf-8")
    _git("add", "new_component.py", cwd=root)
    _git("commit", "-q", "-m", "add file", cwd=root)
    rc, err = _gate(root, monkeypatch, capsys)
    assert rc == 1
    assert "new_component.py" in err


def test_manifest_absent_check_skipped(monkeypatch, tmp_path, capsys):
    """No manifest (non-CPV repo) → freshness check skipped, Gate 1 passes."""
    root = _init_repo(tmp_path)
    assert not (root / MANIFEST_NEW).exists()
    rc, _err = _gate(root, monkeypatch, capsys)
    assert rc == 0


def test_failure_message_includes_regen_and_commit_steps(monkeypatch, tmp_path, capsys):
    """Review note 1: the remediation must include BOTH the regen command AND
    the commit step, or the regen dirties the tracked manifest and the next
    run is refused by this very gate's dirty-tree check."""
    root = _init_repo(tmp_path)
    _write_manifest(root)
    (root / "README.md").write_text("# edited\n", encoding="utf-8")
    rc, err = _gate(root, monkeypatch, capsys)
    assert rc == 1
    assert "_plugin_compute_hashes.py" in err
    assert "git commit" in err


def test_legacy_manifest_name_supported(monkeypatch, tmp_path, capsys):
    """Legacy `.cpv-self-hashes.json` alone is picked up and verified too."""
    root = _init_repo(tmp_path)
    _write_manifest(root, legacy=True)
    rc, _err = _gate(root, monkeypatch, capsys)
    assert rc == 0
    (root / "scripts" / "tool.py").write_text("print('EDITED')\n", encoding="utf-8")
    rc, _err = _gate(root, monkeypatch, capsys)
    assert rc == 1
