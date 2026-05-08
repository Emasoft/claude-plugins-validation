#!/usr/bin/env python3
"""Tests for the new validator rule that flags hook commands writing
runtime state to ``${CLAUDE_PLUGIN_ROOT}`` (which is REPLACED on every
plugin update — state is silently destroyed).

This is the persistent-data-folder footgun documented at
https://code.claude.com/docs/en/plugins-reference#persistent-data-directory:

> ${CLAUDE_PLUGIN_ROOT} ... This path changes when the plugin updates.
> The previous version's directory remains on disk for about seven days
> after an update before cleanup, but treat it as ephemeral and do not
> write state here.

The fix the rule teaches: use ``${CLAUDE_PLUGIN_DATA}`` instead — that
resolves to ``~/.claude/plugins/data/<plugin-id>/`` and persists across
updates.

These tests verify:
1. Shell redirect (``>`` / ``>>``) targeting CLAUDE_PLUGIN_ROOT → CRITICAL
2. ``tee`` / ``sqlite3`` direct write → CRITICAL
3. Pipe to ``tee`` / ``sqlite3`` → CRITICAL
4. ``npm install --prefix ${CLAUDE_PLUGIN_ROOT}`` → CRITICAL
5. ``pip install --target ${CLAUDE_PLUGIN_ROOT}/x`` → CRITICAL
6. Same patterns targeting CLAUDE_PLUGIN_DATA → no finding (correct usage)
7. Reading from CLAUDE_PLUGIN_ROOT → no finding (read-only is fine)
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_hook import validate_command_hook  # noqa: E402


def _has_critical_pd_root(report: ValidationReport) -> bool:
    return any(
        r.level == "CRITICAL" and "CLAUDE_PLUGIN_ROOT" in r.message and "REPLACED" in r.message
        for r in report.results
    )


class TestHookWritesToPluginRoot:
    def test_shell_redirect_to_plugin_root_critical(self) -> None:
        report = ValidationReport()
        validate_command_hook(
            {"command": 'date > "${CLAUDE_PLUGIN_ROOT}/last-run.txt"'},
            "PostToolUse",
            None,
            report,
        )
        assert _has_critical_pd_root(report)

    def test_shell_append_redirect_to_plugin_root_critical(self) -> None:
        report = ValidationReport()
        validate_command_hook(
            {"command": 'echo "$STATE" >> "$CLAUDE_PLUGIN_ROOT/log.txt"'},
            "Stop",
            None,
            report,
        )
        assert _has_critical_pd_root(report)

    def test_tee_to_plugin_root_critical(self) -> None:
        report = ValidationReport()
        validate_command_hook(
            {"command": 'echo data | tee "${CLAUDE_PLUGIN_ROOT}/state.json"'},
            "PostToolUse",
            None,
            report,
        )
        assert _has_critical_pd_root(report)

    def test_sqlite3_to_plugin_root_critical(self) -> None:
        report = ValidationReport()
        validate_command_hook(
            {"command": 'sqlite3 "${CLAUDE_PLUGIN_ROOT}/db.sqlite" "INSERT INTO ..."'},
            "PostToolUse",
            None,
            report,
        )
        assert _has_critical_pd_root(report)

    def test_npm_install_prefix_plugin_root_critical(self) -> None:
        report = ValidationReport()
        validate_command_hook(
            {"command": 'npm install --prefix ${CLAUDE_PLUGIN_ROOT} express'},
            "SessionStart",
            None,
            report,
        )
        assert _has_critical_pd_root(report)

    def test_pip_install_target_plugin_root_critical(self) -> None:
        report = ValidationReport()
        validate_command_hook(
            {"command": 'pip install --target=${CLAUDE_PLUGIN_ROOT}/.venv requests'},
            "SessionStart",
            None,
            report,
        )
        assert _has_critical_pd_root(report)

    def test_uv_pip_install_target_plugin_root_critical(self) -> None:
        report = ValidationReport()
        validate_command_hook(
            {"command": 'uv pip install --target "${CLAUDE_PLUGIN_ROOT}/lib" requests'},
            "SessionStart",
            None,
            report,
        )
        assert _has_critical_pd_root(report)


class TestHookWritesToPluginDataAreFine:
    """The same patterns targeting CLAUDE_PLUGIN_DATA must NOT trigger the rule."""

    def test_redirect_to_plugin_data_no_finding(self) -> None:
        report = ValidationReport()
        validate_command_hook(
            {"command": 'date > "${CLAUDE_PLUGIN_DATA}/last-run.txt"'},
            "PostToolUse",
            None,
            report,
        )
        assert not _has_critical_pd_root(report)

    def test_npm_install_prefix_plugin_data_no_finding(self) -> None:
        report = ValidationReport()
        validate_command_hook(
            {"command": 'cd "${CLAUDE_PLUGIN_DATA}" && npm install --prefix . express'},
            "SessionStart",
            None,
            report,
        )
        assert not _has_critical_pd_root(report)

    def test_uv_venv_in_plugin_data_no_finding(self) -> None:
        report = ValidationReport()
        validate_command_hook(
            {"command": 'cd "${CLAUDE_PLUGIN_DATA}" && uv venv && uv pip install -e "${CLAUDE_PLUGIN_ROOT}"'},
            "SessionStart",
            None,
            report,
        )
        assert not _has_critical_pd_root(report)


class TestReadsFromPluginRootAreFine:
    """Reading from CLAUDE_PLUGIN_ROOT is correct usage (scripts, configs are
    bundled there). Only WRITES are flagged."""

    def test_read_python_script_no_finding(self) -> None:
        report = ValidationReport()
        validate_command_hook(
            {"command": 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/process.py"'},
            "PostToolUse",
            None,
            report,
        )
        assert not _has_critical_pd_root(report)

    def test_diff_command_no_finding(self) -> None:
        """The canonical SessionStart bootstrap pattern from Anthropic docs reads
        a manifest from PLUGIN_ROOT and writes a copy to PLUGIN_DATA — no flag."""
        report = ValidationReport()
        validate_command_hook(
            {
                "command": (
                    'diff -q "${CLAUDE_PLUGIN_ROOT}/package.json" '
                    '"${CLAUDE_PLUGIN_DATA}/package.json" >/dev/null 2>&1 || '
                    '(cd "${CLAUDE_PLUGIN_DATA}" && '
                    'cp "${CLAUDE_PLUGIN_ROOT}/package.json" . && npm install)'
                )
            },
            "SessionStart",
            None,
            report,
        )
        assert not _has_critical_pd_root(report)
