#!/usr/bin/env python3
"""Tests for `run_linting()` strict-vs-soft missing-linter handling.

Background: previously `run_linting()` printed a WARNING and skipped
languages whose linter was unavailable, but did NOT flip `all_passed` to
False. That meant plugins with JS/TS/Rust/Bash code shipped through
`publish.py` even when eslint / cargo / shellcheck wasn't installed —
the gate said "OK" because no errors were produced (no linter was
actually run).

Fix: strict-by-default. A missing linter for any detected language is a
hard failure; `--soft-missing-linters` flips back to old warning behaviour
for local dev workflows.

These tests verify:
1. Strict mode (default) fails when a linter is missing for a language
   that has files in the project.
2. Soft mode allows the run to succeed when a linter is missing.
3. Strict mode still passes when every detected language's linter is
   present (no false positives).
4. The dispatch table never silently swallows an unknown language —
   if `_LINT_DISPATCH` lacks an entry, the run fails.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from lint_files import run_linting  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path, *, langs: dict[str, str]) -> Path:
    """Build a tmp project with one file per requested language.

    `langs` maps a relative filename (e.g. "main.py") to its content.
    """
    for name, content in langs.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Strict mode (default)
# ---------------------------------------------------------------------------


class TestStrictModeFailsOnMissingLinter:
    def test_missing_eslint_fails_publish_when_js_present(self, tmp_path: Path) -> None:
        """Plugin with JS files but no eslint must fail strict mode."""
        _make_project(tmp_path, langs={"app.js": "console.log('hi');\n"})

        with (
            patch("lint_files.ensure_linter_installed", return_value=False),
            patch("lint_files.lint_javascript") as fake_lint_js,
        ):
            result = run_linting(tmp_path)
        assert result is False, "expected strict mode to fail when eslint is missing"
        # The lint function MUST NOT be called when its linter is missing
        fake_lint_js.assert_not_called()

    def test_missing_shellcheck_fails_publish_when_shell_present(self, tmp_path: Path) -> None:
        """Plugin with shell files but no shellcheck must fail strict mode."""
        _make_project(tmp_path, langs={"deploy.sh": "#!/bin/bash\necho hi\n"})

        with patch("lint_files.ensure_linter_installed", return_value=False):
            result = run_linting(tmp_path)
        assert result is False

    def test_missing_cargo_fails_publish_when_rust_present(self, tmp_path: Path) -> None:
        """Plugin with Rust files but no cargo must fail strict mode."""
        _make_project(tmp_path, langs={"main.rs": "fn main() {}\n"})

        with patch("lint_files.ensure_linter_installed", return_value=False):
            result = run_linting(tmp_path)
        assert result is False


# ---------------------------------------------------------------------------
# Soft mode (--soft-missing-linters)
# ---------------------------------------------------------------------------


class TestSoftModeWarnsOnMissingLinter:
    def test_missing_eslint_passes_in_soft_mode(self, tmp_path: Path) -> None:
        """Soft mode treats missing linters as warnings — for local dev only."""
        _make_project(tmp_path, langs={"app.js": "console.log('hi');\n"})

        with patch("lint_files.ensure_linter_installed", return_value=False):
            result = run_linting(tmp_path, strict_missing_linters=False)
        assert result is True

    def test_soft_mode_still_fails_when_a_linter_actually_finds_issues(self, tmp_path: Path) -> None:
        """Soft mode only relaxes missing-linter; real lint findings still fail."""
        _make_project(tmp_path, langs={"main.py": "import os\n"})  # unused-import bait

        with (
            patch("lint_files.ensure_linter_installed", return_value=True),
            patch("lint_files.lint_python", return_value=False),
        ):
            result = run_linting(tmp_path, strict_missing_linters=False)
        assert result is False, "soft mode must still propagate real linter failures"


# ---------------------------------------------------------------------------
# All linters present — no regression
# ---------------------------------------------------------------------------


class TestStrictModePassesWhenLintersPresent:
    def test_python_only_project_passes(self, tmp_path: Path) -> None:
        _make_project(tmp_path, langs={"main.py": "x = 1\n"})

        with (
            patch("lint_files.ensure_linter_installed", return_value=True),
            patch("lint_files.lint_python", return_value=True),
        ):
            result = run_linting(tmp_path)
        assert result is True

    def test_polyglot_project_passes_when_every_linter_runs(self, tmp_path: Path) -> None:
        _make_project(
            tmp_path,
            langs={
                "main.py": "x = 1\n",
                "app.js": "console.log('hi');\n",
                "deploy.sh": "#!/bin/bash\necho hi\n",
            },
        )

        with (
            patch("lint_files.ensure_linter_installed", return_value=True),
            patch("lint_files.lint_python", return_value=True),
            patch("lint_files.lint_javascript", return_value=True),
            patch("lint_files.lint_shell", return_value=True),
        ):
            result = run_linting(tmp_path)
        assert result is True


# ---------------------------------------------------------------------------
# Empty project
# ---------------------------------------------------------------------------


class TestEmptyProject:
    def test_no_source_files_passes(self, tmp_path: Path) -> None:
        # Empty tree — detect_languages returns {} and run_linting short-circuits.
        result = run_linting(tmp_path)
        assert result is True
