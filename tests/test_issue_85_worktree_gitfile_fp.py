"""Regression tests for issue #85 — linked-worktree ``.git`` FILE false positive.

When a plugin is validated from inside a git LINKED WORKTREE, its top-level
``.git`` is a FILE (a ``gitdir: <abs path>`` pointer), not a directory. The
``.git`` entry in ``PRIVATE_INFO_SKIP_DIRS`` only prunes a ``.git`` DIRECTORY
during the walk — the gitignore-filter's file branch has no basename skip — so
the pointer FILE fell through to the private-path / absolute-path scans. Its
``gitdir:`` value embeds the validating machine's ``$HOME`` + username, which
was flagged twice as a CRITICAL ``Private path leaked`` even though ``.git`` is
pure git plumbing, never shipped plugin content.

Fix: both scan loops (``scan_directory_for_private_info`` and
``validate_no_absolute_paths``) skip any entry whose basename is ``.git``,
immediately after ``rel_path`` is computed.

This is TWO-SIDED: the FP clears (the ``.git`` pointer is skipped) AND the
real-threat sibling (a ``/Users/<user>/`` leak in a TRACKED, shipped
non-``.git`` file) still fires.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_validation_common import (  # noqa: E402
    ValidationReport,
    validate_no_absolute_paths,
    validate_no_private_info,
)

# The linked-worktree pointer file content — embeds a private $HOME + username.
_GITDIR_POINTER = "gitdir: /Users/leakuser/Code/somerepo/.git/worktrees/wt\n"


def _make_tree(tmp_path: Path) -> Path:
    """Build a plugin with a linked-worktree ``.git`` FILE pointer AND a
    genuine private-path leak in a tracked, shipped SKILL.md."""
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo", "version": "0.1.0", "description": "A demo fixture."}\n',
        encoding="utf-8",
    )
    # The FP: a linked-worktree `.git` FILE whose gitdir embeds /Users/<user>/.
    (root / ".git").write_text(_GITDIR_POINTER, encoding="utf-8")
    # The real threat: a TRACKED, shipped file that genuinely leaks a path.
    skill = root / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: A demo skill for the test.\n---\n"
        "Config lives at /Users/leakuser/secret/config.toml\n",
        encoding="utf-8",
    )
    return root


# ============================================================================
# Private-info scan — scan_directory_for_private_info
# ============================================================================


class TestPrivateInfoGitFileSkip:
    """The private-info scan must skip a ``.git`` pointer FILE while still
    flagging a leaked path in a tracked, shipped file."""

    def test_worktree_git_pointer_file_not_flagged(self, tmp_path: Path) -> None:
        """A linked-worktree ``.git`` FILE (gitdir pointer) is skipped — the
        2× CRITICAL private-path FP is gone."""
        root = _make_tree(tmp_path)
        report = ValidationReport()
        validate_no_private_info(root, report, additional_usernames={"leakuser"})
        git_hits = [r for r in report.results if (r.file or "").replace("\\", "/").split("/")[-1] == ".git"]
        assert not git_hits, f".git pointer file should be skipped, got: {[r.message for r in git_hits]}"

    def test_tracked_file_private_path_still_flagged(self, tmp_path: Path) -> None:
        """A ``/Users/`` path in a tracked, shipped SKILL.md STILL fires (FN-safe)."""
        root = _make_tree(tmp_path)
        report = ValidationReport()
        validate_no_private_info(root, report, additional_usernames={"leakuser"})
        leak_hits = [r for r in report.results if "skills/demo/SKILL.md" in (r.file or "").replace("\\", "/")]
        assert leak_hits, "a leaked private path in a tracked file must still fire"


# ============================================================================
# Absolute-path scan — validate_no_absolute_paths
# ============================================================================


class TestAbsolutePathGitFileSkip:
    """validate_no_absolute_paths must skip a ``.git`` pointer FILE while
    still flagging the tracked file. The username is made "private" so the
    pointer path WOULD fire if it were walked — proving the skip is
    meaningful, not vacuous (mirrors test_issue_71 line 175)."""

    def test_worktree_git_pointer_file_not_flagged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The ``.git`` pointer's absolute gitdir path is skipped."""
        import cpv_validation_common as cvc

        monkeypatch.setattr(cvc, "PRIVATE_USERNAMES", {"leakuser"})
        root = _make_tree(tmp_path)
        report = ValidationReport()
        validate_no_absolute_paths(root, report)
        files = {(r.file or "").replace("\\", "/").split("/")[-1] for r in report.results}
        assert ".git" not in files, f".git absolute-path pointer must be skipped: {files}"

    def test_tracked_file_absolute_path_still_flagged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A ``/Users/`` path in a tracked SKILL.md STILL fires (FN-safe)."""
        import cpv_validation_common as cvc

        monkeypatch.setattr(cvc, "PRIVATE_USERNAMES", {"leakuser"})
        root = _make_tree(tmp_path)
        report = ValidationReport()
        validate_no_absolute_paths(root, report)
        files = {(r.file or "").replace("\\", "/") for r in report.results}
        assert any("skills/demo/SKILL.md" in f for f in files), "tracked absolute path must still fire"
