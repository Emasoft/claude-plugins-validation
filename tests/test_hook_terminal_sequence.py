#!/usr/bin/env python3
"""Tests for the v2.1.141 ``terminalSequence`` hook-output field.

Per the v2.1.141 changelog:
    Added ``terminalSequence`` field to hook JSON output so hooks can emit
    desktop notifications, window titles, and bells without a controlling
    terminal

Coverage:
* ``terminalSequence`` accepted as a top-level universal output field on
  every hook event with no spurious "unknown universal field" minor.
* String type accepted (BEL, OSC escape sequences, plain strings — anything
  the hook wants Claude Code to write to the terminal).
* Non-string type flagged as MAJOR.
* Empty string is a no-op and must not trigger any finding.
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_hook_output import UNIVERSAL_OUTPUT_FIELDS, validate_output_payload  # noqa: E402


def _no_unknown_universal_field(report, key: str) -> bool:
    for finding in report.results:
        if finding.level in {"MINOR", "MAJOR", "CRITICAL"} and key in finding.message and "Unknown" in finding.message:
            return False
    return True


def _has_major_about(report, needle: str) -> bool:
    return any(f.level == "MAJOR" and needle in f.message for f in report.results)


def test_terminalSequence_is_a_universal_field():
    """terminalSequence is in the canonical UNIVERSAL_OUTPUT_FIELDS set."""
    assert "terminalSequence" in UNIVERSAL_OUTPUT_FIELDS


def test_terminalSequence_string_accepted_on_PreToolUse():
    """terminalSequence: '\\a' on PreToolUse → no 'unknown universal field' minor."""
    payload = {"terminalSequence": "\\a"}
    report = validate_output_payload("PreToolUse", payload)
    assert _no_unknown_universal_field(report, "terminalSequence")
    assert not _has_major_about(report, "terminalSequence")


def test_terminalSequence_OSC_escape_accepted_on_Notification():
    """Real OSC notification sequence accepted on Notification event."""
    payload = {"terminalSequence": "\x1b]9;Hook fired\x1b\\"}
    report = validate_output_payload("Notification", payload)
    assert _no_unknown_universal_field(report, "terminalSequence")
    assert not _has_major_about(report, "terminalSequence")


def test_terminalSequence_empty_string_is_noop():
    """terminalSequence: '' is a legitimate no-op (don't reject it)."""
    payload = {"terminalSequence": ""}
    report = validate_output_payload("Stop", payload)
    assert _no_unknown_universal_field(report, "terminalSequence")
    assert not _has_major_about(report, "terminalSequence")


def test_terminalSequence_non_string_emits_major():
    """terminalSequence: 42 → MAJOR (must be a string)."""
    payload = {"terminalSequence": 42}
    report = validate_output_payload("PostToolUse", payload)
    assert _has_major_about(report, "terminalSequence")


def test_terminalSequence_boolean_emits_major():
    """terminalSequence: true → MAJOR (must be a string, not bool)."""
    payload = {"terminalSequence": True}
    report = validate_output_payload("Stop", payload)
    assert _has_major_about(report, "terminalSequence")


def test_terminalSequence_dict_emits_major():
    """terminalSequence: {} → MAJOR (must be a string, not object)."""
    payload = {"terminalSequence": {"bell": True}}
    report = validate_output_payload("SessionStart", payload)
    assert _has_major_about(report, "terminalSequence")


def test_terminalSequence_works_with_other_universal_fields():
    """terminalSequence coexists with continue/decision/etc. — no cross-contamination."""
    payload = {
        "continue": True,
        "systemMessage": "build finished",
        "terminalSequence": "\\a",
    }
    report = validate_output_payload("PostToolUse", payload)
    assert _no_unknown_universal_field(report, "terminalSequence")
    assert _no_unknown_universal_field(report, "systemMessage")
