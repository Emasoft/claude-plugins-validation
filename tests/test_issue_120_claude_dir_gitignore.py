#!/usr/bin/env python3
"""Issue #120 — `.claude/` cache-dir coverage check is unsatisfiable when a
plugin deliberately tracks content under `.claude/`.

`validate_gitignore` flags `[MINOR] .gitignore missing coverage for: Claude
Code cache directory (.claude/)` using `git check-ignore -q -- .claude`, which
exits 0 only when the bare `.claude` directory entry is ignored. But a plugin
that tracks e.g. `.claude/project/memory/**` CANNOT ignore the `.claude` dir
(git can't re-include a path under an excluded parent), so the finding is
impossible to clear — it blocks `--strict` publishes for a legitimate use case.

Fix: treat the `.claude/` category as satisfied when the plugin git-tracks any
content under `.claude/` (`git ls-files .claude/` non-empty) — tracked content
is git-authoritative proof of intent, not a cache leak.

Two-sided coverage (real git repo fixtures so `git ls-files`/`git check-ignore`
are exercised for real):
  * FP side — a plugin tracking `.claude/project/memory/MEMORY.md` with the deep
    `.claude/**` + `!…` gitignore → NO `.claude/` finding.
  * Genuine side — a plugin with an un-ignored, un-tracked `.claude/` (only a
    cache file present, nothing tracked) → STILL flags the MINOR.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_plugin import (  # noqa: E402
    _claude_dir_has_tracked_content,
    validate_gitignore,
)

_FINDING_FRAGMENT = "Claude Code cache directory (.claude/)"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _init_repo(repo: Path) -> None:
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (repo / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"fixture","version":"0.1.0",'
        '"description":"issue-120 gitignore fixture for the claude dir coverage check"}\n',
        encoding="utf-8",
    )


def _claude_findings(repo: Path) -> list[str]:
    rep = ValidationReport()
    validate_gitignore(repo, rep)
    return [r.message for r in rep.results if _FINDING_FRAGMENT in r.message]


@pytest.fixture
def tracked_claude_repo(tmp_path: Path) -> Path:
    """A plugin that deliberately tracks `.claude/project/memory/MEMORY.md`."""
    repo = tmp_path / "tracked"
    repo.mkdir()
    _init_repo(repo)
    # Deep gitignore: ignore the .claude cache contents, re-include the memory subtree.
    (repo / ".gitignore").write_text(
        ".claude/**\n"
        "!.claude/project/\n"
        "!.claude/project/memory/\n"
        "!.claude/project/memory/**\n",
        encoding="utf-8",
    )
    mem_dir = repo / ".claude" / "project" / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text("# project memory\n", encoding="utf-8")
    # A genuine cache file that the `.claude/**` rule correctly ignores.
    (repo / ".claude" / "settings.local.json").write_text("{}\n", encoding="utf-8")
    _git(repo, "add", ".claude-plugin/plugin.json", ".gitignore",
         ".claude/project/memory/MEMORY.md")
    _git(repo, "commit", "-m", "init")
    return repo


@pytest.fixture
def untracked_claude_repo(tmp_path: Path) -> Path:
    """A plugin with an un-ignored, un-tracked `.claude/` holding only a cache file."""
    repo = tmp_path / "untracked"
    repo.mkdir()
    _init_repo(repo)
    # .gitignore does NOT cover .claude at all.
    (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (repo / ".claude").mkdir()
    (repo / ".claude" / ".last_cost").write_text("0.0\n", encoding="utf-8")
    _git(repo, "add", ".claude-plugin/plugin.json", ".gitignore")
    _git(repo, "commit", "-m", "init")
    return repo


class TestClaudeDirTrackedContentHelper:
    """`_claude_dir_has_tracked_content` is git-authoritative."""

    def test_true_when_content_tracked(self, tracked_claude_repo: Path):
        """Returns True when a file under `.claude/` is git-tracked."""
        assert _claude_dir_has_tracked_content(tracked_claude_repo) is True

    def test_false_when_nothing_tracked(self, untracked_claude_repo: Path):
        """Returns False when no `.claude/` content is tracked."""
        assert _claude_dir_has_tracked_content(untracked_claude_repo) is False

    def test_false_when_not_a_git_repo(self, tmp_path: Path):
        """Returns False (keep the check live) outside a git repo — graceful."""
        plain = tmp_path / "plain"
        (plain / ".claude").mkdir(parents=True)
        (plain / ".claude" / ".last_cost").write_text("0\n", encoding="utf-8")
        assert _claude_dir_has_tracked_content(plain) is False


class TestIssue120GitignoreClaudeDir:
    """The `.claude/` coverage finding is two-sided after the fix."""

    def test_tracked_claude_content_clears_finding(self, tracked_claude_repo: Path):
        """FP side: tracking `.claude/project/memory/**` → NO `.claude/` finding."""
        findings = _claude_findings(tracked_claude_repo)
        assert findings == [], findings

    def test_untracked_claude_cache_still_flags(self, untracked_claude_repo: Path):
        """Genuine side: an un-ignored, un-tracked `.claude/` cache dir STILL flags."""
        findings = _claude_findings(untracked_claude_repo)
        assert len(findings) == 1, findings
        assert _FINDING_FRAGMENT in findings[0]
