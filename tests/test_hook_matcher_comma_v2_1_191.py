#!/usr/bin/env python3
"""Two-sided tests for comma-separated hook matchers (TRDD-ABFRMED0).

Claude Code v2.1.191 fixed comma-separated hook matchers (e.g. `"Bash,PowerShell"`)
so they fire — they are valid matcher values. `_check_matcher_values` now splits a
matcher on `,` as well as `|`/`(`/`)`, so each comma-separated tool is validated
individually instead of the whole `"a,b"` string being treated as one unknown
token (a spurious INFO false positive). These tests prove the split works and that
a real unknown part inside a comma list still surfaces.
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_hook import _check_matcher_values  # noqa: E402

_KNOWN = {"Bash", "PowerShell", "Edit", "Read"}


def _infos(report: ValidationReport) -> list[str]:
    return [r.message for r in report.results if r.level == "INFO"]


def test_comma_matcher_both_known_no_info() -> None:
    """A `"Bash,PowerShell"` matcher (both known) produces NO unknown-value INFO."""
    report = ValidationReport()
    _check_matcher_values("Bash,PowerShell", _KNOWN, "PreToolUse", "tool_name", report)
    assert _infos(report) == []


def test_comma_matcher_unknown_part_named_alone() -> None:
    """A `"Bash,NotARealTool"` matcher flags ONLY the unknown part, not the whole string."""
    report = ValidationReport()
    _check_matcher_values("Bash,NotARealTool", _KNOWN, "PreToolUse", "tool_name", report)
    infos = _infos(report)
    assert len(infos) == 1, infos
    assert "NotARealTool" in infos[0]
    # The comma split worked: the message must NOT carry the whole "Bash,NotARealTool".
    assert "Bash,NotARealTool" not in infos[0]
    assert "'Bash'" not in infos[0]  # the known half is not flagged


def test_pipe_group_matcher_still_clean() -> None:
    """A regex-style `"(Bash|Edit)"` matcher still produces no INFO (no regression)."""
    report = ValidationReport()
    _check_matcher_values("(Bash|Edit)", _KNOWN, "PreToolUse", "tool_name", report)
    assert _infos(report) == []


def test_single_tool_matcher_still_clean() -> None:
    """A plain single-tool `"Bash"` matcher still produces no INFO."""
    report = ValidationReport()
    _check_matcher_values("Bash", _KNOWN, "PreToolUse", "tool_name", report)
    assert _infos(report) == []


def test_comma_and_pipe_mixed() -> None:
    """A mixed `"Bash,Edit|Read"` matcher splits on both separators, all known → no INFO."""
    report = ValidationReport()
    _check_matcher_values("Bash,Edit|Read", _KNOWN, "PreToolUse", "tool_name", report)
    assert _infos(report) == []


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
