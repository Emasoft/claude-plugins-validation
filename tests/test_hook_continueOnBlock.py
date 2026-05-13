#!/usr/bin/env python3
"""Tests for the v2.1.139 hook ``continueOnBlock: bool`` field.

Per the changelog (v2.1.139):
    Added hook `continueOnBlock` config option for `PostToolUse` —
    set to `true` to feed the hook's rejection reason back to Claude
    and continue the turn

Covers:
* continueOnBlock: true on PostToolUse → no warning
* continueOnBlock: false on PostToolUse → no warning (explicit-default is fine)
* continueOnBlock on PostToolUseFailure → no warning (same event lineage)
* continueOnBlock on Stop / PreToolUse → MINOR (silently ignored)
* continueOnBlock as a string → CRITICAL (must be boolean)
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_hook import HookValidationReport, validate_single_hook  # noqa: E402


def _new_report() -> HookValidationReport:
    return HookValidationReport(hook_path="test-hook")


def _has_finding(report: HookValidationReport, level: str, needle: str) -> bool:
    for finding in report.results:
        if finding.level == level and needle in finding.message:
            return True
    return False


def _no_finding(report: HookValidationReport, level: str, needle: str) -> bool:
    return not _has_finding(report, level, needle)


def test_continueOnBlock_true_on_PostToolUse_passes_clean():
    """continueOnBlock: true on PostToolUse → no MINOR/CRITICAL about the field."""
    hook = {"type": "command", "command": "echo hi", "continueOnBlock": True}
    report = _new_report()
    validate_single_hook(hook, "PostToolUse", None, report)
    assert _no_finding(report, "MINOR", "continueOnBlock"), (
        f"unexpected MINOR on PostToolUse continueOnBlock: {[(f.level, f.message) for f in report.results]}"
    )
    assert _no_finding(report, "CRITICAL", "continueOnBlock"), (
        f"unexpected CRITICAL on PostToolUse continueOnBlock: {[(f.level, f.message) for f in report.results]}"
    )


def test_continueOnBlock_false_on_PostToolUse_passes_clean():
    """continueOnBlock: false on PostToolUse → no warning (explicit default is fine)."""
    hook = {"type": "command", "command": "echo hi", "continueOnBlock": False}
    report = _new_report()
    validate_single_hook(hook, "PostToolUse", None, report)
    assert _no_finding(report, "MINOR", "continueOnBlock")
    assert _no_finding(report, "CRITICAL", "continueOnBlock")


def test_continueOnBlock_on_PostToolUseFailure_passes_clean():
    """continueOnBlock on PostToolUseFailure → no warning (same event lineage)."""
    hook = {"type": "command", "command": "echo hi", "continueOnBlock": True}
    report = _new_report()
    validate_single_hook(hook, "PostToolUseFailure", None, report)
    assert _no_finding(report, "MINOR", "continueOnBlock")


def test_continueOnBlock_on_Stop_emits_minor():
    """continueOnBlock on Stop → MINOR (silently ignored by CC)."""
    hook = {"type": "command", "command": "echo hi", "continueOnBlock": True}
    report = _new_report()
    validate_single_hook(hook, "Stop", None, report)
    assert _has_finding(report, "MINOR", "continueOnBlock"), (
        f"expected MINOR on continueOnBlock outside PostToolUse, got: {[(f.level, f.message) for f in report.results]}"
    )


def test_continueOnBlock_on_PreToolUse_emits_minor():
    """continueOnBlock on PreToolUse → MINOR (only PostToolUse / PostToolUseFailure are valid)."""
    hook = {"type": "command", "command": "echo hi", "continueOnBlock": True}
    report = _new_report()
    validate_single_hook(hook, "PreToolUse", None, report)
    assert _has_finding(report, "MINOR", "continueOnBlock")


def test_continueOnBlock_non_boolean_emits_critical():
    """continueOnBlock: 'true' (string) → CRITICAL about type."""
    hook = {"type": "command", "command": "echo hi", "continueOnBlock": "true"}
    report = _new_report()
    validate_single_hook(hook, "PostToolUse", None, report)
    assert _has_finding(report, "CRITICAL", "continueOnBlock"), (
        f"expected CRITICAL on non-bool continueOnBlock, got: {[(f.level, f.message) for f in report.results]}"
    )


def test_continueOnBlock_integer_emits_critical():
    """continueOnBlock: 1 (int, not bool) → CRITICAL."""
    hook = {"type": "command", "command": "echo hi", "continueOnBlock": 1}
    report = _new_report()
    validate_single_hook(hook, "PostToolUse", None, report)
    # Python's bool is a subclass of int, so isinstance(True, int) is True but
    # isinstance(1, bool) is False. The check uses isinstance(..., bool) so
    # int 1 should be flagged.
    assert _has_finding(report, "CRITICAL", "continueOnBlock")
