#!/usr/bin/env python3
"""Tests for issue #226: nested `.gitignore` files must be honoured git-style.

`GitignoreFilter` used to load only `root/.gitignore`, so a nested
`.gitignore` (e.g. `scripts/memgrep/.gitignore` with `/target`) was never
consulted — `rglob` walked a 98k-file cargo `target/` tree and blew the
1800s validate budget. This pins the fix: a nested `.gitignore`'s patterns
apply relative to ITS OWN directory, exactly like real git.
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from gitignore_filter import GitignoreFilter  # noqa: E402


def _make_tree(root: Path) -> None:
    """Build root/sub/{src/x.py, target/y.txt} plus root/target/y.txt."""
    (root / "sub" / "src").mkdir(parents=True)
    (root / "sub" / "src" / "x.py").write_text("x = 1\n")
    (root / "sub" / "target").mkdir(parents=True)
    (root / "sub" / "target" / "y.txt").write_text("built\n")
    (root / "target").mkdir(parents=True)
    (root / "target" / "y.txt").write_text("not built\n")


def test_nested_gitignore_prunes_rglob(tmp_path: Path) -> None:
    """A nested .gitignore's /target pattern prunes rglob under that subdir only."""
    _make_tree(tmp_path)
    (tmp_path / "sub" / ".gitignore").write_text("/target\n")

    gi = GitignoreFilter(tmp_path)
    results = {p.relative_to(tmp_path).as_posix() for p in gi.rglob("*")}

    assert "sub/src/x.py" in results
    assert not any(r.startswith("sub/target") for r in results)
    assert gi.is_dir_ignored(tmp_path / "sub" / "target") is True


def test_nested_gitignore_is_anchored_to_its_own_directory(tmp_path: Path) -> None:
    """A nested /target pattern must not leak upward to a sibling top-level target/."""
    _make_tree(tmp_path)
    (tmp_path / "sub" / ".gitignore").write_text("/target\n")

    gi = GitignoreFilter(tmp_path)
    results = {p.relative_to(tmp_path).as_posix() for p in gi.rglob("*")}

    assert "target/y.txt" in results
    assert gi.is_dir_ignored(tmp_path / "target") is False


def test_walk_never_descends_into_nested_ignored_dir(tmp_path: Path) -> None:
    """walk() never yields a dirpath located under the nested-ignored sub/target."""
    _make_tree(tmp_path)
    (tmp_path / "sub" / ".gitignore").write_text("/target\n")

    gi = GitignoreFilter(tmp_path)
    dirpaths = {Path(d).relative_to(tmp_path).as_posix() for d, _, _ in gi.walk()}

    assert not any(d == "sub/target" or d.startswith("sub/target/") for d in dirpaths)


def test_iterdir_omits_nested_ignored_entry(tmp_path: Path) -> None:
    """iterdir() on the parent of a nested .gitignore omits the ignored entry."""
    _make_tree(tmp_path)
    (tmp_path / "sub" / ".gitignore").write_text("/target\n")

    gi = GitignoreFilter(tmp_path)
    names = {p.name for p in gi.iterdir(tmp_path / "sub")}

    assert "target" not in names
    assert "src" in names


def test_no_root_gitignore_nested_still_prunes(tmp_path: Path) -> None:
    """With NO root .gitignore at all, a nested one still prunes (the early-return regression)."""
    _make_tree(tmp_path)
    (tmp_path / "sub" / ".gitignore").write_text("/target\n")
    assert not (tmp_path / ".gitignore").exists()

    gi = GitignoreFilter(tmp_path)
    results = {p.relative_to(tmp_path).as_posix() for p in gi.rglob("*")}

    assert not any(r.startswith("sub/target") for r in results)
    assert "sub/src/x.py" in results


def test_root_only_gitignore_unchanged_behaviour(tmp_path: Path) -> None:
    """Root-only .gitignore still ignores build/ and does not ignore src/ (control)."""
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.o").write_text("obj\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("pass\n")
    (tmp_path / ".gitignore").write_text("build/\n")

    gi = GitignoreFilter(tmp_path)

    assert gi.is_dir_ignored(tmp_path / "build") is True
    assert gi.is_dir_ignored(tmp_path / "src") is False
    results = {p.relative_to(tmp_path).as_posix() for p in gi.rglob("*")}
    assert "src/main.py" in results
    assert not any(r.startswith("build") for r in results)
