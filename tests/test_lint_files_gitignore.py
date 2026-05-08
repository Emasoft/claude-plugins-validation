#!/usr/bin/env python3
"""Regression tests for lint_files.py — gitignore-filtered targeting.

Background: prior versions of `lint_python`, `lint_javascript`, `lint_go`
discarded the `files` parameter (`# noqa: ARG001`) and passed `str(repo_root)`
or `.` / `./...` to ruff / mypy / eslint / gofmt. Those tools have their own
gitignore handling, but it breaks down when the repo contains nested `.git/`
directories (for example reference-repo clones under a gitignored
`INPUT_DEV/`): each nested `.git/` is a fresh root, so the parent's
`.gitignore` rules don't apply and foreign code gets scanned.

Fix: route every linter through `_files_or_root()` so the gitignore-filtered
file list from `detect_languages()` is the actual scan target.

These tests verify:
1. `_files_or_root()` returns the file paths when files is non-empty.
2. `_files_or_root()` falls back to repo_root when files is None / empty.
3. `lint_python` invokes ruff with the filtered file list, NOT the repo root.
4. `lint_python` invokes mypy with the filtered file list.
5. `lint_javascript` invokes eslint with the filtered file list.
6. `lint_go` invokes gofmt with the filtered file list.
7. `lint_go` skips `go vet` when repo_root has no go.mod (avoids descending
   into nested cloned Go modules).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from lint_files import (  # noqa: E402
    _files_or_root,
    lint_go,
    lint_javascript,
    lint_python,
)

# ---------------------------------------------------------------------------
# 1. _files_or_root helper
# ---------------------------------------------------------------------------


class TestFilesOrRoot:
    def test_returns_files_when_provided(self, tmp_path: Path) -> None:
        files = [tmp_path / "a.py", tmp_path / "b.py"]
        result = _files_or_root(tmp_path, files)
        assert result == [str(tmp_path / "a.py"), str(tmp_path / "b.py")]

    def test_falls_back_to_repo_root_when_files_is_none(self, tmp_path: Path) -> None:
        assert _files_or_root(tmp_path, None) == [str(tmp_path)]

    def test_falls_back_to_repo_root_when_files_is_empty(self, tmp_path: Path) -> None:
        assert _files_or_root(tmp_path, []) == [str(tmp_path)]


# ---------------------------------------------------------------------------
# 2. lint_python — ruff + mypy targets the filtered file list
# ---------------------------------------------------------------------------


def _fake_run_factory():
    """Build a subprocess.run replacement that captures the argv it was called with."""
    captured: list[list[str]] = []

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        return FakeResult()

    return captured, fake_run


class TestLintPython:
    def test_ruff_receives_filtered_files_not_repo_root(self, tmp_path: Path) -> None:
        """The fix: ruff must be invoked with the file list, not str(repo_root).

        Without this, ruff descends into INPUT_DEV/ and reports findings against
        foreign code despite the parent .gitignore.
        """
        files = [tmp_path / "a.py", tmp_path / "b.py"]
        captured, fake_run = _fake_run_factory()

        with (
            patch("lint_files.subprocess.run", side_effect=fake_run),
            patch("lint_files.shutil.which", return_value=None),
        ):
            lint_python(tmp_path, files)

        assert captured, "subprocess.run was never called"
        ruff_cmd = captured[0]
        assert ruff_cmd[0] == "ruff"
        assert ruff_cmd[1] == "check"
        # Last positional args must be the file list
        assert str(tmp_path / "a.py") in ruff_cmd
        assert str(tmp_path / "b.py") in ruff_cmd
        # Critical: repo_root itself MUST NOT be a positional argument
        assert str(tmp_path) not in ruff_cmd

    def test_ruff_falls_back_to_repo_root_when_files_is_none(self, tmp_path: Path) -> None:
        """Backward compat: when no files are supplied, scan the whole repo."""
        captured, fake_run = _fake_run_factory()

        with (
            patch("lint_files.subprocess.run", side_effect=fake_run),
            patch("lint_files.shutil.which", return_value=None),
        ):
            lint_python(tmp_path, None)

        ruff_cmd = captured[0]
        assert str(tmp_path) in ruff_cmd

    def test_mypy_receives_filtered_files_not_repo_root(self, tmp_path: Path) -> None:
        files = [tmp_path / "a.py", tmp_path / "b.py"]
        captured, fake_run = _fake_run_factory()

        with (
            patch("lint_files.subprocess.run", side_effect=fake_run),
            patch("lint_files.shutil.which", return_value="/usr/bin/mypy"),
        ):
            lint_python(tmp_path, files)

        # captured: [ruff_cmd, mypy_cmd]
        assert len(captured) >= 2, f"expected ruff + mypy invocations, got {len(captured)}"
        mypy_cmd = captured[1]
        assert mypy_cmd[0] == "mypy"
        assert str(tmp_path / "a.py") in mypy_cmd
        assert str(tmp_path / "b.py") in mypy_cmd
        # repo_root should NOT appear as a positional after the fix
        positional = [arg for arg in mypy_cmd[1:] if not arg.startswith("--")]
        assert str(tmp_path) not in positional


# ---------------------------------------------------------------------------
# 3. lint_javascript — eslint targets the filtered file list
# ---------------------------------------------------------------------------


def _which_eslint_available(name: str) -> str | None:
    """`shutil.which` mock that pretends eslint is installed and nothing else."""
    return "/usr/bin/eslint" if name == "eslint" else None


class TestLintJavascript:
    def test_eslint_receives_filtered_files_not_dot(self, tmp_path: Path) -> None:
        # Must look like an eslint-configured project so we get past the early-return
        (tmp_path / "eslint.config.js").write_text("export default [];", encoding="utf-8")
        files = [tmp_path / "a.ts", tmp_path / "b.tsx"]
        captured, fake_run = _fake_run_factory()

        with (
            patch("lint_files.subprocess.run", side_effect=fake_run),
            patch("lint_files.shutil.which", side_effect=_which_eslint_available),
        ):
            lint_javascript(tmp_path, files)

        # eslint call should include the file paths, not "."
        assert captured, "subprocess.run was never called"
        eslint_cmd = captured[-1]
        assert str(tmp_path / "a.ts") in eslint_cmd
        assert str(tmp_path / "b.tsx") in eslint_cmd
        assert "." not in eslint_cmd

    def test_eslint_falls_back_to_dot_when_files_is_none(self, tmp_path: Path) -> None:
        (tmp_path / "eslint.config.js").write_text("export default [];", encoding="utf-8")
        captured, fake_run = _fake_run_factory()

        with (
            patch("lint_files.subprocess.run", side_effect=fake_run),
            patch("lint_files.shutil.which", side_effect=_which_eslint_available),
        ):
            lint_javascript(tmp_path, None)

        eslint_cmd = captured[-1]
        assert "." in eslint_cmd


# ---------------------------------------------------------------------------
# 4. lint_go — gofmt targets the filtered file list, vet skipped without go.mod
# ---------------------------------------------------------------------------


class TestLintGo:
    def test_gofmt_receives_filtered_files_not_dot(self, tmp_path: Path) -> None:
        files = [tmp_path / "main.go"]
        captured, fake_run = _fake_run_factory()

        with patch("lint_files.subprocess.run", side_effect=fake_run):
            lint_go(tmp_path, files)

        gofmt_cmd = captured[0]
        assert gofmt_cmd[0] == "gofmt"
        assert gofmt_cmd[1] == "-l"
        assert str(tmp_path / "main.go") in gofmt_cmd
        # `.` MUST NOT be passed as the scan target after the fix
        assert "." not in gofmt_cmd[2:]

    def test_go_vet_skipped_without_go_mod(self, tmp_path: Path) -> None:
        """Without go.mod at repo_root, go vet would descend into nested modules
        (cloned reference repos under gitignored trees). We skip it entirely.
        """
        files = [tmp_path / "main.go"]
        captured, fake_run = _fake_run_factory()

        with patch("lint_files.subprocess.run", side_effect=fake_run):
            lint_go(tmp_path, files)

        # Only gofmt should be invoked — no go vet call
        for cmd in captured:
            assert cmd[0] != "go" or cmd[1] != "vet", f"unexpected go vet invocation: {cmd}"

    def test_go_vet_runs_when_go_mod_present(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module example.com/foo\n", encoding="utf-8")
        files = [tmp_path / "main.go"]
        captured, fake_run = _fake_run_factory()

        with patch("lint_files.subprocess.run", side_effect=fake_run):
            lint_go(tmp_path, files)

        # Expect both gofmt and go vet calls
        cmds = [tuple(c[:2]) for c in captured]
        assert ("gofmt", "-l") in cmds
        assert ("go", "vet") in cmds
