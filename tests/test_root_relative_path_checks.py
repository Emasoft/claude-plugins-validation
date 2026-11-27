"""Root-relative path checks: folders ABOVE the root must not change the result.

Same bug class as the CPV 5.25.0 hidden-ancestor xref bug: a check that tests
ABSOLUTE path components lets an ancestor named ``rules`` / ``scripts`` /
``fixtures`` / ``.git`` flip the outcome. Real files in tmp_path, no mocks.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_lint_engine import _is_own_script  # noqa: E402
from cpv_validation_common import _is_toc_exempt  # noqa: E402
from update_marketplace_metadata import calculate_directory_checksum  # noqa: E402

EMPTY_SHA256 = hashlib.sha256().hexdigest()


def _plugin(base: Path) -> Path:
    """Create a minimal plugin (manifest present) at ``base`` and return it."""
    (base / ".claude-plugin").mkdir(parents=True)
    (base / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    return base


# ── Site 1: _is_toc_exempt ──────────────────────────────────────────────


def test_toc_exempt_ignores_rules_ancestor_of_plugin(tmp_path: Path) -> None:
    """A plugin located under a folder named ``rules`` does not exempt its ordinary files."""
    plugin = _plugin(tmp_path / "rules" / "p")
    (plugin / "references").mkdir()
    f = plugin / "references" / "guide.md"
    f.write_text("# g\n", encoding="utf-8")
    assert _is_toc_exempt(f) is False


def test_toc_exempt_still_exempts_agents_dir_inside_plugin(tmp_path: Path) -> None:
    """A file in the plugin's own ``agents/`` dir is still exempt, even under a ``rules`` ancestor."""
    plugin = _plugin(tmp_path / "rules" / "p")
    (plugin / "agents").mkdir()
    f = plugin / "agents" / "a.md"
    f.write_text("# a\n", encoding="utf-8")
    assert _is_toc_exempt(f) is True


def test_toc_exempt_nested_dir_inside_plugin(tmp_path: Path) -> None:
    """A file nested below ``commands/`` inside the plugin is exempt."""
    plugin = _plugin(tmp_path / "p")
    (plugin / "commands" / "sub").mkdir(parents=True)
    f = plugin / "commands" / "sub" / "c.md"
    f.write_text("# c\n", encoding="utf-8")
    assert _is_toc_exempt(f) is True


# ── Site 2: _is_own_script ──────────────────────────────────────────────


def test_own_script_ignores_fixtures_and_tests_ancestors_of_repo(tmp_path: Path) -> None:
    """A repo checked out under ``tests/fixtures/`` still treats its own ``scripts/x.py`` as own."""
    repo = tmp_path / "tests" / "fixtures" / "repo"
    (repo / "scripts").mkdir(parents=True)
    f = repo / "scripts" / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    assert _is_own_script(f, repo) is True


def test_own_script_requires_scripts_dir_inside_repo(tmp_path: Path) -> None:
    """A ``scripts`` ancestor ABOVE the repo does not make a repo file an own script."""
    repo = tmp_path / "scripts" / "repo"
    (repo / "lib").mkdir(parents=True)
    f = repo / "lib" / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    assert _is_own_script(f, repo) is False


def test_own_script_fixture_inside_repo_still_excluded(tmp_path: Path) -> None:
    """``tests/fixtures/<plugin>/scripts/x.py`` INSIDE the repo is still foreign."""
    repo = tmp_path / "repo"
    d = repo / "tests" / "fixtures" / "plug" / "scripts"
    d.mkdir(parents=True)
    f = d / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    assert _is_own_script(f, repo) is False


def test_own_script_tests_before_scripts_inside_repo_excluded(tmp_path: Path) -> None:
    """``tests/scripts/x.py`` INSIDE the repo is excluded (tests before scripts)."""
    repo = tmp_path / "repo"
    (repo / "tests" / "scripts").mkdir(parents=True)
    f = repo / "tests" / "scripts" / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    assert _is_own_script(f, repo) is False


def test_own_script_outside_repo_is_false(tmp_path: Path) -> None:
    """A file outside ``repo_root`` is never an own script."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "scripts").mkdir()
    f = tmp_path / "scripts" / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    assert _is_own_script(f, repo) is False


# ── Site 3: calculate_directory_checksum ────────────────────────────────


def test_checksum_ignores_git_ancestor_of_dir(tmp_path: Path) -> None:
    """A ``.git`` ancestor ABOVE the hashed dir does not empty the checksum."""
    plain = tmp_path / "plain" / "p"
    under_git = tmp_path / ".git" / "p"
    for d in (plain, under_git):
        d.mkdir(parents=True)
        (d / "a.txt").write_text("hello\n", encoding="utf-8")
    got = calculate_directory_checksum(under_git)
    assert got != EMPTY_SHA256
    assert got == calculate_directory_checksum(plain)


def test_checksum_still_excludes_git_dir_inside_dir(tmp_path: Path) -> None:
    """A ``.git/`` folder INSIDE the hashed dir still does not affect the checksum."""
    d = tmp_path / "p"
    d.mkdir()
    (d / "a.txt").write_text("hello\n", encoding="utf-8")
    before = calculate_directory_checksum(d)
    (d / ".git").mkdir()
    (d / ".git" / "HEAD").write_text("ref\n", encoding="utf-8")
    assert calculate_directory_checksum(d) == before
