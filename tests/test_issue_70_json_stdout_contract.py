#!/usr/bin/env python3
"""Tests for GitHub issue #70-A — the ``--json`` stdout-purity contract.

Issue #70-A: ``cpv-pre-install-scan`` could never reach a verdict because
``validate_plugin.py --strict --json`` wrote the human-readable
``═══ [REPO LINT] ═══`` banner (and per-language lint headers) to STDOUT
ahead of the JSON object, so ``json.loads(stdout)`` raised JSONDecodeError.

The ``--json`` contract is: **stdout = the machine JSON object ONLY**;
everything human-readable goes to stderr. These tests pin both halves of
the fix:

1. ``validate_plugin.py --json`` emits pure JSON on stdout (no preamble),
   with the REPO-LINT banner routed to stderr — while the NON-json run
   keeps the banner on stdout (no regression).
2. ``cpv_pre_install_scan._run_validate_plugin`` parses robustly even when
   fed a leading non-JSON preamble (defence-in-depth), still parses clean
   JSON, and still errors gracefully on genuinely-empty / garbage output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import cpv_pre_install_scan  # noqa: E402

VALIDATE_PLUGIN = scripts_dir / "validate_plugin.py"

BANNER_MARKER = "REPO LINT"


def _run_validate_plugin_subprocess(plugin_dir: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    """Invoke validate_plugin.py as a real subprocess, capturing both streams.

    ``PLUGIN_SKIP_GITHUB_INTEGRITY=1`` is forced into the child env so the
    test never flakes on the dev-time CPV self-integrity gate (editing CPV's
    own scripts drifts their hashes from the published manifest, which would
    otherwise abort validate_plugin BEFORE it reaches the JSON output —
    unrelated to the stdout-purity contract under test).
    """
    cmd = [sys.executable, str(VALIDATE_PLUGIN), str(plugin_dir), "--strict", *extra_args]
    env = {**os.environ, "PLUGIN_SKIP_GITHUB_INTEGRITY": "1"}
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False, env=env)


class TestValidatePluginJsonStdoutPurity:
    """validate_plugin.py --json must keep stdout JSON-only."""

    def test_json_stdout_is_pure_json_no_preamble(self, valid_plugin_dir):
        """--json: stdout parses as JSON directly, with no preamble stripping."""
        result = _run_validate_plugin_subprocess(valid_plugin_dir, "--json")
        # The whole stdout buffer must parse as JSON with NO slicing — this is
        # exactly the json.loads(result.stdout) call that issue #70 broke.
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, dict)
        assert "counts" in parsed
        assert "results" in parsed

    def test_json_stdout_first_nonblank_char_is_brace(self, valid_plugin_dir):
        """--json: the first non-whitespace character on stdout opens the JSON object."""
        result = _run_validate_plugin_subprocess(valid_plugin_dir, "--json")
        assert result.stdout.lstrip().startswith("{")

    def test_json_banner_not_in_stdout(self, valid_plugin_dir):
        """--json: the human-readable REPO-LINT banner must NOT appear on stdout."""
        result = _run_validate_plugin_subprocess(valid_plugin_dir, "--json")
        assert BANNER_MARKER not in result.stdout

    def test_json_banner_is_in_stderr(self, valid_plugin_dir):
        """--json: the REPO-LINT banner is routed to stderr instead."""
        result = _run_validate_plugin_subprocess(valid_plugin_dir, "--json")
        assert BANNER_MARKER in result.stderr

    def test_json_lint_preamble_not_in_stdout(self, valid_plugin_dir):
        """--json: lint engine 'Detected languages' preamble must not leak to stdout.

        Add a real .py source file so the lint engine actually runs and would
        otherwise print its 'Detected languages' / per-language header lines.
        """
        (valid_plugin_dir / "hooks").mkdir(exist_ok=True)
        (valid_plugin_dir / "hooks" / "sample.py").write_text("x = 1\n")
        result = _run_validate_plugin_subprocess(valid_plugin_dir, "--json")
        # Whole stdout still parses as pure JSON, and the lint preamble is absent.
        json.loads(result.stdout)
        assert "Detected languages" not in result.stdout


class TestValidatePluginNonJsonRegression:
    """The NON-json path must keep its human banner on stdout (no regression)."""

    def test_non_json_banner_is_in_stdout(self, valid_plugin_dir):
        """No --json: the REPO-LINT banner stays on stdout exactly as before."""
        result = _run_validate_plugin_subprocess(valid_plugin_dir)
        assert BANNER_MARKER in result.stdout


class TestPreInstallScanParseHardening:
    """cpv_pre_install_scan._run_validate_plugin robustly extracts the JSON."""

    _VALID_JSON_BODY = json.dumps(
        {
            "exit_code": 0,
            "counts": {"critical": 0, "major": 0, "minor": 0, "nit": 0, "warning": 0, "info": 0, "passed": 1},
            "results": [],
        },
        indent=2,
    )

    @staticmethod
    def _fake_completed(stdout: str, stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["validate_plugin.py"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def _invoke(self, fake_stdout: str, fake_stderr: str = "") -> tuple[int, dict[str, Any]]:
        with mock.patch.object(
            cpv_pre_install_scan.subprocess,
            "run",
            return_value=self._fake_completed(fake_stdout, fake_stderr),
        ):
            return cpv_pre_install_scan._run_validate_plugin(Path("/tmp/fake-plugin"))

    def test_clean_json_parses(self):
        """Pristine JSON-only stdout parses and yields a clean (exit 0) verdict."""
        exit_code, summary = self._invoke(self._VALID_JSON_BODY)
        assert exit_code == 0
        assert summary["summary"]["passed"] == 1

    def test_preamble_then_json_parses(self):
        """A leading REPO-LINT banner + lint preamble is stripped and the JSON parses."""
        polluted = (
            "\n"
            "═══ [REPO LINT] (15 languages, gitignore-filtered) ═══\n"
            "  Detected languages: python\n"
            "  [Python] 1 file(s)\n"
            f"{self._VALID_JSON_BODY}\n"
        )
        exit_code, summary = self._invoke(polluted)
        assert exit_code == 0
        assert summary["summary"]["passed"] == 1
        assert summary["findings"] == []

    def test_empty_stdout_errors_gracefully(self):
        """Genuinely-empty stdout still maps to exit 2 with the documented error."""
        exit_code, summary = self._invoke("   \n  ", fake_stderr="boom: path not found")
        assert exit_code == 2
        assert "no JSON output" in summary["error"]
        assert summary["raw_stderr"] == "boom: path not found"

    def test_garbage_stdout_errors_gracefully(self):
        """Non-JSON garbage (no object at all) still maps to exit 2, unparseable."""
        exit_code, summary = self._invoke("this is not json at all\njust noise\n")
        assert exit_code == 2
        assert "unparseable JSON" in summary["error"]
        assert "raw_stdout" in summary

    def test_preamble_with_broken_json_errors_gracefully(self):
        """A preamble followed by a malformed object still errors (no masking)."""
        polluted = "═══ [REPO LINT] ═══\n  Detected languages: python\n{ not valid json ,,, }\n"
        exit_code, summary = self._invoke(polluted)
        assert exit_code == 2
        assert "unparseable JSON" in summary["error"]


class TestExtractJsonObjectHelper:
    """Unit tests for the _extract_json_object preamble-stripper."""

    def test_extracts_from_first_brace_line(self):
        """Returns everything from the first '{'-opening line to the end."""
        text = "banner\nmore preamble\n{\n  \"k\": 1\n}\n"
        out = cpv_pre_install_scan._extract_json_object(text)
        assert out is not None
        assert json.loads(out) == {"k": 1}

    def test_pure_json_passes_through(self):
        """Pure JSON (first line opens the object) is returned unchanged in content."""
        text = '{"a": 2}'
        out = cpv_pre_install_scan._extract_json_object(text)
        assert out is not None
        assert json.loads(out) == {"a": 2}

    def test_no_object_returns_none(self):
        """No line opening an object → None (caller reports a real failure)."""
        assert cpv_pre_install_scan._extract_json_object("no braces here\nat all\n") is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
