"""Issue #272: ci-preflight trivy skips gitignored untracked dirs that CI never checks out."""

from __future__ import annotations

import subprocess
from pathlib import Path

from cpv_ci_preflight import _argv_trivy


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _write(root: Path, rel: str, text: str = "x\n") -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _skip_dirs(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--skip-dirs"]


def test_unshipped_dir_is_skipped(tmp_path: Path) -> None:
    """Gitignored untracked dirs get a --skip-dirs pair; tracked dirs do not."""
    _git(tmp_path, "init", "-q")
    _write(tmp_path, "scripts/a.py", "print(1)\n")
    _write(tmp_path, ".gitignore", "docs_dev/\nnode_modules/\n")
    _git(tmp_path, "add", "scripts/a.py", ".gitignore")
    _git(tmp_path, "commit", "-q", "-m", "init")
    _write(tmp_path, "docs_dev/x.md")
    _write(tmp_path, "node_modules/pkg/index.js")
    argv = _argv_trivy("trivy", tmp_path)
    assert ["--skip-dirs", "docs_dev"] == argv[argv.index("docs_dev") - 1 : argv.index("docs_dev") + 1]
    assert ["--skip-dirs", "node_modules"] == argv[argv.index("node_modules") - 1 : argv.index("node_modules") + 1]
    assert argv[-1] == "."
    assert "scripts" not in _skip_dirs(argv)


def test_dir_with_tracked_file_is_not_skipped(tmp_path: Path) -> None:
    """A gitignored dir holding a force-added tracked file is not skipped."""
    _git(tmp_path, "init", "-q")
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "build/keep.txt")
    _git(tmp_path, "add", ".gitignore")
    _git(tmp_path, "add", "-f", "build/keep.txt")
    _git(tmp_path, "commit", "-q", "-m", "init")
    _write(tmp_path, "build/tmp.log")
    assert "build" not in _skip_dirs(_argv_trivy("trivy", tmp_path))


def test_no_git_skips_nothing(tmp_path: Path) -> None:
    """Without git nothing is skipped on gitignore grounds."""
    _write(tmp_path, ".gitignore", "docs_dev/\n")
    _write(tmp_path, "docs_dev/x.md")
    assert "--skip-dirs" not in _argv_trivy("trivy", tmp_path)


def test_glob_metachar_dir_is_not_skipped(tmp_path: Path) -> None:
    """A gitignored dir whose name has glob metacharacters is left scanned, never glob-skipped."""
    _git(tmp_path, "init", "-q")
    _write(tmp_path, "scripts/a.py", "print(1)\n")
    _write(tmp_path, ".gitignore", "[ab]_dev/\nplain_dev/\n")
    _git(tmp_path, "add", "scripts/a.py", ".gitignore")
    _git(tmp_path, "commit", "-q", "-m", "init")
    _write(tmp_path, "[ab]_dev/x.md")
    _write(tmp_path, "plain_dev/x.md")
    assert _skip_dirs(_argv_trivy("trivy", tmp_path)) == ["plain_dev"]


def test_argv_keeps_exit_code_flags(tmp_path: Path) -> None:
    """The fs subcommand, --exit-code 1 and --no-progress are still present."""
    argv = _argv_trivy("trivy", tmp_path)
    assert argv[:5] == ["trivy", "fs", "--exit-code", "1", "--no-progress"]
