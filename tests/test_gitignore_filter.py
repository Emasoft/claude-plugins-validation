#!/usr/bin/env python3
"""Tests for GitignoreFilter class in scripts/gitignore_filter.py.

Coverage: 15 tests covering __init__, is_ignored, is_dir_ignored, walk, rglob, iterdir.
All tests use tmp_path for real filesystem operations — no mocking.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from gitignore_filter import GitignoreFilter  # noqa: E402


def test_init_without_gitignore(tmp_path: Path) -> None:
    """GitignoreFilter on a directory with no .gitignore has empty patterns list."""
    gi = GitignoreFilter(tmp_path)
    assert gi.patterns == []
    assert gi.root == tmp_path.resolve()


def test_init_with_gitignore(tmp_path: Path) -> None:
    """GitignoreFilter loads all non-comment, non-blank lines from .gitignore."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.pyc\n__pycache__/\n# comment\n\ndist/\n", encoding="utf-8")
    gi = GitignoreFilter(tmp_path)
    assert gi.patterns == ["*.pyc", "__pycache__/", "dist/"]


def test_is_ignored_basic_pattern(tmp_path: Path) -> None:
    """Basic glob pattern *.pyc matches .pyc files but not .py files."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.pyc\n", encoding="utf-8")
    gi = GitignoreFilter(tmp_path)

    pyc_file = tmp_path / "foo.pyc"
    pyc_file.touch()
    py_file = tmp_path / "foo.py"
    py_file.touch()

    assert gi.is_ignored(pyc_file) is True
    assert gi.is_ignored(py_file) is False


def test_is_ignored_directory_pattern(tmp_path: Path) -> None:
    """Directory pattern __pycache__/ matches files under __pycache__."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("__pycache__/\n", encoding="utf-8")
    gi = GitignoreFilter(tmp_path)

    pycache_dir = tmp_path / "__pycache__"
    pycache_dir.mkdir()
    cached_file = pycache_dir / "foo.pyc"
    cached_file.touch()

    # The file inside __pycache__ should be ignored because its path contains __pycache__
    assert gi.is_ignored(cached_file) is True


def test_is_ignored_doublestar(tmp_path: Path) -> None:
    """Pattern **/dist matches 'dist' at any depth.

    NOTE: The current is_path_gitignored implementation uses substring matching
    (f'/{suffix}' in f'/{rel_path}') for **/X patterns, which means 'distfile'
    also matches because '/dist' is a substring of '/a/b/distfile'. This is a
    known limitation — real git would NOT match 'distfile'. The test documents
    the actual behavior.
    """
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("**/dist\n", encoding="utf-8")
    gi = GitignoreFilter(tmp_path)

    # Create nested dist directory
    nested_dist = tmp_path / "a" / "b" / "dist"
    nested_dist.mkdir(parents=True)

    # Create a file that should NOT be confused with 'dist'
    other_file = tmp_path / "a" / "b" / "README.md"
    other_file.touch()

    # a/b/dist should be matched by **/dist
    assert gi.is_ignored(nested_dist) is True
    # A file with a completely different name should not match
    assert gi.is_ignored(other_file) is False


def test_is_ignored_negation(tmp_path: Path) -> None:
    """Negation pattern !important.log un-ignores a file when placed BEFORE the glob.

    NOTE: The current is_path_gitignored implementation processes patterns in
    order and returns True on the first positive match. For negation to work,
    the negation pattern must appear BEFORE the positive pattern in the list,
    so that the negation check (which returns False) fires first. This differs
    from real git where later negation patterns override earlier positive ones.
    The test documents the actual behavior by placing the negation first.
    """
    gitignore = tmp_path / ".gitignore"
    # Negation MUST come before the glob for the current implementation to work
    gitignore.write_text("!important.log\n*.log\n", encoding="utf-8")
    gi = GitignoreFilter(tmp_path)

    debug_log = tmp_path / "debug.log"
    debug_log.touch()
    important_log = tmp_path / "important.log"
    important_log.touch()

    assert gi.is_ignored(debug_log) is True
    assert gi.is_ignored(important_log) is False


def test_is_dir_ignored(tmp_path: Path) -> None:
    """is_dir_ignored returns True for a directory matching a dir-only pattern."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")
    gi = GitignoreFilter(tmp_path)

    build_dir = tmp_path / "build"
    build_dir.mkdir()

    assert gi.is_dir_ignored(build_dir) is True


def test_is_dir_ignored_not_matching(tmp_path: Path) -> None:
    """is_dir_ignored returns False for a directory not matching any pattern."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")
    gi = GitignoreFilter(tmp_path)

    src_dir = tmp_path / "src"
    src_dir.mkdir()

    assert gi.is_dir_ignored(src_dir) is False


def test_walk_skips_gitignored_dirs(tmp_path: Path) -> None:
    """walk() does not descend into gitignored directories like node_modules/."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\n", encoding="utf-8")
    gi = GitignoreFilter(tmp_path)

    # Create directory structure
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").touch()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lodash.js").touch()

    walked_files = []
    walked_dirs = []
    for dirpath, dirnames, filenames in gi.walk():
        walked_dirs.append(dirpath)
        for f in filenames:
            walked_files.append(f)

    # node_modules directory should not appear in walked results
    assert not any("node_modules" in d for d in walked_dirs)
    assert "lodash.js" not in walked_files
    assert "main.py" in walked_files


def test_walk_skips_gitignored_files(tmp_path: Path) -> None:
    """walk() excludes files matching gitignore patterns like *.log."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.log\n", encoding="utf-8")
    gi = GitignoreFilter(tmp_path)

    (tmp_path / "app.py").touch()
    (tmp_path / "debug.log").touch()
    (tmp_path / "error.log").touch()

    walked_files = []
    for _dirpath, _dirnames, filenames in gi.walk():
        walked_files.extend(filenames)

    assert "app.py" in walked_files
    assert "debug.log" not in walked_files
    assert "error.log" not in walked_files


def test_walk_with_skip_dirs(tmp_path: Path) -> None:
    """walk(skip_dirs={'vendor'}) skips vendor/ even if not in .gitignore."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.tmp\n", encoding="utf-8")
    gi = GitignoreFilter(tmp_path)

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").touch()
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.py").touch()

    walked_files = []
    walked_dirs = []
    for dirpath, dirnames, filenames in gi.walk(skip_dirs={"vendor"}):
        walked_dirs.append(dirpath)
        walked_files.extend(filenames)

    assert not any("vendor" in d for d in walked_dirs)
    assert "lib.py" not in walked_files
    assert "main.py" in walked_files


def test_walk_skip_hidden_default(tmp_path: Path) -> None:
    """walk() skips hidden directories (starting with .) by default."""
    (tmp_path / ".gitignore").write_text("", encoding="utf-8")
    gi = GitignoreFilter(tmp_path)

    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "secret.txt").touch()
    (tmp_path / "visible").mkdir()
    (tmp_path / "visible" / "readme.txt").touch()

    walked_files = []
    walked_dirs = []
    for dirpath, dirnames, filenames in gi.walk():
        walked_dirs.append(dirpath)
        walked_files.extend(filenames)

    assert not any(".hidden" in d for d in walked_dirs)
    assert "secret.txt" not in walked_files
    assert "readme.txt" in walked_files


def test_rglob_filters_gitignored(tmp_path: Path) -> None:
    """rglob('*.py') does not return .py files in gitignored directories."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("build/\n", encoding="utf-8")
    gi = GitignoreFilter(tmp_path)

    # Create structure with .py files in both normal and gitignored dirs
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").touch()
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "generated.py").touch()

    results = list(gi.rglob("*.py"))
    result_names = [p.name for p in results]

    assert "main.py" in result_names
    assert "generated.py" not in result_names


def test_iterdir_filters_gitignored(tmp_path: Path) -> None:
    """iterdir() does not yield items matching gitignore patterns."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.pyc\nnode_modules/\n", encoding="utf-8")
    gi = GitignoreFilter(tmp_path)

    (tmp_path / "app.py").touch()
    (tmp_path / "app.pyc").touch()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "readme.md").touch()

    results = list(gi.iterdir())
    result_names = [p.name for p in results]

    assert "app.py" in result_names
    assert "readme.md" in result_names
    # .gitignore itself is not gitignored
    assert ".gitignore" in result_names
    assert "app.pyc" not in result_names
    # node_modules as a directory entry — is_ignored checks the path against patterns
    assert "node_modules" not in result_names


def test_walk_cross_platform_paths(tmp_path: Path) -> None:
    """Patterns with forward slashes work on all platforms via Path.as_posix()."""
    gitignore = tmp_path / ".gitignore"
    # Pattern uses forward slashes — should work even on Windows via as_posix()
    gitignore.write_text("output/logs/\n", encoding="utf-8")
    gi = GitignoreFilter(tmp_path)

    # Create nested structure matching the pattern
    logs_dir = tmp_path / "output" / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "run.log").touch()
    (tmp_path / "output" / "data.txt").touch()

    # Verify is_dir_ignored works with the nested path pattern
    assert gi.is_dir_ignored(logs_dir) is True

    # Verify walk skips the gitignored nested dir
    walked_files = []
    for _dirpath, _dirnames, filenames in gi.walk():
        walked_files.extend(filenames)

    assert "run.log" not in walked_files
    assert "data.txt" in walked_files
