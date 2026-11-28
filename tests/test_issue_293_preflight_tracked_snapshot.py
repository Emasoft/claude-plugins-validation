"""Issue #293: ci-preflight checkov/trivy scan only tracked files, as CI does on a clean checkout."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cpv_ci_preflight import PreflightResult, _gate_megalinter_tool, _tracked_snapshot


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
        timeout=60,
    )


def _write(root: Path, rel: str, text: str = "x\n") -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _dirty_repo(root: Path) -> None:
    """Build a repo with committed, staged, deleted-tracked and gitignored-untracked files."""
    _git(root, "init", "-q")
    _write(root, ".gitignore", "reports_dev/\n.pytest_cache/\n")
    _write(root, "src/a.py", "print(1)\n")
    _write(root, "gone.txt", "bye\n")
    _git(root, "add", ".gitignore", "src/a.py", "gone.txt")
    _git(root, "commit", "-q", "-m", "init")
    _write(root, "src/a.py", "print(2)\n")  # modified in the working tree
    _write(root, "staged.txt", "new\n")
    _git(root, "add", "staged.txt")
    (root / "gone.txt").unlink()
    _write(root, "reports_dev/x.json", "{}\n")
    _write(root, ".pytest_cache/v/cache/nodeids", "[]\n")


def test_snapshot_holds_only_tracked_files(tmp_path: Path) -> None:
    """The snapshot has committed and staged files with working-tree content, not ignored or deleted ones."""
    _dirty_repo(tmp_path)
    with _tracked_snapshot(tmp_path) as snap:
        assert snap is not None
        assert (snap / "src/a.py").read_text(encoding="utf-8") == "print(2)\n"
        assert (snap / "staged.txt").read_text(encoding="utf-8") == "new\n"
        assert (snap / ".gitignore").is_file()
        assert not (snap / "reports_dev").exists()
        assert not (snap / ".pytest_cache").exists()
        assert not (snap / "gone.txt").exists()
    assert not snap.exists()


def test_snapshot_is_none_outside_git(tmp_path: Path) -> None:
    """A directory that is not a git work tree yields None so the caller scans it as before."""
    _write(tmp_path, "a.txt")
    with _tracked_snapshot(tmp_path) as snap:
        assert snap is None


def _probe_gate(root: Path, *, scan_tracked_snapshot: bool) -> PreflightResult:
    """Run the megalinter gate with a real argv that exits 1 when reports_dev exists in its cwd."""
    code = 'import sys; from pathlib import Path; sys.exit(1 if Path("reports_dev").exists() else 0)'
    result = PreflightResult(plugin_path=root)
    _gate_megalinter_tool(
        result,
        gate="probe",
        linter_id="REPOSITORY_PROBE",
        tool_name=Path(sys.executable).name,
        install_hint="n/a",
        build_argv=lambda _tool, _root: [sys.executable, "-c", code],
        enabled={"REPOSITORY_PROBE"},
        scan_tracked_snapshot=scan_tracked_snapshot,
    )
    return result


def test_gate_passes_on_snapshot_and_fails_on_dirty_tree(tmp_path: Path) -> None:
    """With the snapshot the ignored scratch is invisible (PASS); without it the gate FAILs."""
    _dirty_repo(tmp_path)
    assert _probe_gate(tmp_path, scan_tracked_snapshot=True).exit_code == 0
    assert _probe_gate(tmp_path, scan_tracked_snapshot=False).exit_code == 1
