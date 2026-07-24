"""Issue #176 — `--strict` scope: stop grading non-shippable / non-relevant content.

Two of the three reported cases are clean, FN-safe scope corrections (the third,
skillaudit on memory-note frontmatter, is answered by reword-guidance, not a
scanner change — see the issue thread). Covered here:

* Case 3 — ci-preflight shellcheck/shfmt must skip genuinely-unshipped shell
  scripts (gitignored AND untracked), which never appear in a CI checkout of the
  published artifact. A TRACKED shell script (even if also gitignored — it still
  ships) MUST stay linted (FN-safe).
* Case 2 — markdownlint must skip `**/fixtures/**` markdown, which are deliberate
  parser test inputs, not shipped docs. Style-tier only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cpv_ci_preflight import _shell_script_paths  # noqa: E402
from cpv_lint_engine import lint_markdown  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


# ── Case 3: shellcheck/shfmt path discovery respects the shipped surface ──


def test_shell_script_paths_skips_gitignored_untracked(tmp_path: Path) -> None:
    """A gitignored+untracked `downloads_dev/dev.sh` is NOT linted (absent from a
    CI checkout), while a tracked `hook.sh` IS — issue #176 case 3."""
    repo = tmp_path / "plug"
    (repo / "downloads_dev").mkdir(parents=True)
    (repo / "hook.sh").write_text("#!/usr/bin/env bash\necho hi\n")
    (repo / "downloads_dev" / "dev.sh").write_text("#!/usr/bin/env bash\necho dev\n")
    (repo / ".gitignore").write_text("downloads_dev/\n")
    _git(repo, "init")
    _git(repo, "add", "hook.sh", ".gitignore")  # stage the shipped files
    paths = _shell_script_paths(repo)
    assert "hook.sh" in paths
    assert not any("downloads_dev" in p for p in paths)


def test_shell_script_paths_keeps_tracked_gitignored(tmp_path: Path) -> None:
    """FN-safe: a TRACKED file that is also gitignored still SHIPS (git archive
    keeps already-tracked files), so it must stay linted — not skipped."""
    repo = tmp_path / "plug2"
    repo.mkdir()
    (repo / "shipped.sh").write_text("#!/usr/bin/env bash\necho shipped\n")
    (repo / ".gitignore").write_text("shipped.sh\n")  # ignore a file that IS tracked
    _git(repo, "init")
    _git(repo, "add", "-f", "shipped.sh", ".gitignore")  # force-add despite ignore → tracked
    paths = _shell_script_paths(repo)
    assert "shipped.sh" in paths


def test_shell_script_paths_no_git_scans_all(tmp_path: Path) -> None:
    """No git repo → nothing is skipped on gitignore grounds (the present tree IS
    the artifact); every shell script is returned."""
    repo = tmp_path / "plug3"
    (repo / "downloads_dev").mkdir(parents=True)
    (repo / "a.sh").write_text("echo a\n")
    (repo / "downloads_dev" / "b.sh").write_text("echo b\n")
    paths = _shell_script_paths(repo)
    assert "a.sh" in paths
    assert any("b.sh" in p for p in paths)


# ── Case 2: markdownlint skips fixtures (style-tier) ──


def test_lint_markdown_excludes_fixtures(tmp_path: Path) -> None:
    """A files list of ONLY `tests/fixtures/*.md` filters to empty → lint_markdown
    returns True without ever resolving/invoking the linter (issue #176 case 2)."""
    report = ValidationReport()
    fixture = tmp_path / "tests" / "fixtures" / "sample.md"
    resolver = MagicMock(return_value=["/bin/markdownlint-cli2"])
    with patch("cpv_lint_engine._resolve", resolver):
        ok = lint_markdown(tmp_path, [fixture], report)
    assert ok is True
    resolver.assert_not_called()  # the tool was never reached — nothing to lint


def test_lint_markdown_lints_non_fixtures(tmp_path: Path) -> None:
    """Control: a non-fixtures markdown file passes the filter and reaches the
    linter-resolution step (so the fixtures exclude is not over-broad)."""
    report = ValidationReport()
    doc = tmp_path / "README.md"
    resolver = MagicMock(return_value=None)  # tool 'missing' → early, but AFTER resolve
    with patch("cpv_lint_engine._resolve", resolver):
        lint_markdown(tmp_path, [doc], report, strict_missing_tools=False)
    resolver.assert_called()  # the non-fixtures file reached the tool logic
