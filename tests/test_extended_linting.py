#!/usr/bin/env python3
"""Tests for lint_files.py -- read-only linting module.

Tests verify that lint functions are importable, detect_languages works,
run_linting returns bool, and cross-platform hints are printed.

Coverage: 16 tests
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from lint_files import (  # noqa: E402
    detect_languages,
    ensure_linter_installed,
    lint_css,
    lint_dockerfile,
    lint_html,
    lint_powershell,
    lint_sql,
    lint_toml,
    lint_xml,
    run_linting,
)

# ---------------------------------------------------------------------------
# 1-7: Verify all 7 new lint functions are importable and callable
# ---------------------------------------------------------------------------


class TestLintFunctionsExist:
    """Verify all 7 new lint functions are importable and callable."""

    def test_lint_dockerfile_exists(self):
        """lint_dockerfile is importable from lint_files and callable."""
        assert callable(lint_dockerfile)

    def test_lint_xml_exists(self):
        """lint_xml is importable from lint_files and callable."""
        assert callable(lint_xml)

    def test_lint_css_exists(self):
        """lint_css is importable from lint_files and callable."""
        assert callable(lint_css)

    def test_lint_html_exists(self):
        """lint_html is importable from lint_files and callable."""
        assert callable(lint_html)

    def test_lint_sql_exists(self):
        """lint_sql is importable from lint_files and callable."""
        assert callable(lint_sql)

    def test_lint_toml_exists(self):
        """lint_toml is importable from lint_files and callable."""
        assert callable(lint_toml)

    def test_lint_powershell_exists(self):
        """lint_powershell is importable from lint_files and callable."""
        assert callable(lint_powershell)


# ---------------------------------------------------------------------------
# 8-9: run_linting returns bool and passes on empty directory
# ---------------------------------------------------------------------------


class TestRunLinting:
    """Verify run_linting returns bool and handles empty directories."""

    def test_run_linting_returns_bool(self, tmp_path: Path):
        """run_linting returns a bool (not a tuple) on an empty temp dir."""
        result = run_linting(tmp_path)
        assert isinstance(result, bool), f"Expected bool, got {type(result).__name__}"

    def test_run_linting_empty_dir_passes(self, tmp_path: Path):
        """run_linting returns True on an empty directory (no files = pass)."""
        result = run_linting(tmp_path)
        assert result is True, "Empty directory should pass linting"


# ---------------------------------------------------------------------------
# 10: detect_languages finds all 7 new file types
# ---------------------------------------------------------------------------


class TestDetectLanguages:
    """Verify detect_languages detects all 7 new file categories."""

    def test_detect_languages_finds_new_types(self, tmp_path: Path):
        """detect_languages detects dockerfile, xml, css, html, sql, toml, powershell."""
        # Create one file per new category
        (tmp_path / "Dockerfile").write_text("FROM alpine:3.18\n")
        (tmp_path / "test.xml").write_text('<?xml version="1.0"?><root/>\n')
        (tmp_path / "test.css").write_text("body { margin: 0; }\n")
        (tmp_path / "test.html").write_text("<html><body>hello</body></html>\n")
        (tmp_path / "test.sql").write_text("SELECT 1;\n")
        (tmp_path / "test.toml").write_text('[project]\nname = "demo"\n')
        (tmp_path / "test.ps1").write_text("Write-Host 'hello'\n")

        detected = detect_languages(tmp_path)

        expected = {"dockerfile", "xml", "css", "html", "sql", "toml", "powershell"}
        for lang in expected:
            assert lang in detected, f"detect_languages missed '{lang}'"


# ---------------------------------------------------------------------------
# 11-12: TOML validation with real tomllib (no mocks)
# ---------------------------------------------------------------------------


class TestTomlValidation:
    """Test TOML validation using stdlib tomllib directly."""

    def test_tomllib_catches_invalid_toml(self, tmp_path: Path):
        """tomllib raises TOMLDecodeError on syntactically invalid TOML."""
        bad_toml = tmp_path / "broken.toml"
        bad_toml.write_text('[section\nkey = \n"unterminated\n')
        with pytest.raises(tomllib.TOMLDecodeError):
            with open(bad_toml, "rb") as fp:
                tomllib.load(fp)

    def test_tomllib_passes_valid_toml(self, tmp_path: Path):
        """tomllib successfully parses well-formed TOML content."""
        good_toml = tmp_path / "pyproject.toml"
        good_toml.write_text(
            '[project]\nname = "my-package"\nversion = "1.0.0"\ndescription = "A test package"\n\n[project.optional-dependencies]\ndev = ["pytest==8.0.0"]\n'
        )
        with open(good_toml, "rb") as fp:
            data = tomllib.load(fp)
        assert data["project"]["name"] == "my-package"
        assert data["project"]["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# 13: resolve_tool_command integration test
# ---------------------------------------------------------------------------


class TestResolveToolCommand:
    """Test resolve_tool_command from cpv_validation_common."""

    def test_resolve_tool_command_known_tool(self):
        """resolve_tool_command returns None or a list for a known tool like ruff."""
        from cpv_validation_common import resolve_tool_command

        result = resolve_tool_command("ruff")
        assert result is None or isinstance(result, list)


# ---------------------------------------------------------------------------
# 14: ensure_linter_installed always returns True for toml
# ---------------------------------------------------------------------------


class TestEnsureLinterToml:
    """Verify ensure_linter_installed returns True for toml (stdlib)."""

    def test_ensure_toml_always_available(self, tmp_path: Path):
        """ensure_linter_installed returns True for 'toml' because tomllib is stdlib."""
        assert ensure_linter_installed("toml", tmp_path) is True


# ---------------------------------------------------------------------------
# 15-16: Cross-platform install hints
# ---------------------------------------------------------------------------


class TestCrossPlatformHints:
    """Verify cross-platform installation hints for missing linters."""

    def test_hadolint_hint_printed(self, tmp_path: Path, capsys):
        """ensure_linter_installed for dockerfile prints install hint if hadolint missing."""
        result = ensure_linter_installed("dockerfile", tmp_path)
        if result is False:
            captured = capsys.readouterr().out
            assert any(kw in captured for kw in ("brew", "scoop", "apt", "hadolint")), (
                f"Expected install hint for hadolint, got: {captured!r}"
            )

    def test_xmllint_hint_printed(self, tmp_path: Path, capsys):
        """ensure_linter_installed for xml prints hint mentioning libxml2 if xmllint missing."""
        result = ensure_linter_installed("xml", tmp_path)
        if result is False:
            captured = capsys.readouterr().out
            assert "libxml2" in captured, f"Expected 'libxml2' in install hint, got: {captured!r}"
