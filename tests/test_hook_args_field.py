#!/usr/bin/env python3
"""Tests for the v2.1.139 hook ``args: string[]`` exec-form field.

Per the official CC hooks.md schema, ``command`` and ``args`` are
**complementary** in exec form (canonical example: ``{"command": "node",
"args": ["${CLAUDE_PLUGIN_ROOT}/scripts/x.js"]}``). Issue #24 (v2.83.0
regression) incorrectly flagged them as mutually exclusive — fixed in
v2.87.0.

Covers:
* args valid (args-only legacy form) → passes
* command + args (canonical exec form) → passes, no MAJOR
* command="node script.js" + args (bare-name-with-whitespace) → MINOR
* args empty list → CRITICAL
* args with non-string element → CRITICAL
* Neither command nor args → CRITICAL
* args[0] absolute path without ${CLAUDE_PLUGIN_ROOT} → MAJOR portability
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_hook import validate_command_hook  # noqa: E402


def _new_report() -> ValidationReport:
    return ValidationReport()


def _has_finding(report: ValidationReport, level: str, needle: str) -> bool:
    """True if any finding at `level` contains `needle` in its message."""
    for finding in report.results:
        if finding.level == level and needle in finding.message:
            return True
    return False


def test_args_valid_string_list_passes():
    """args: ['python3', 'hello.py'] with no command → PASSED, no CRITICAL/MAJOR."""
    hook = {"args": ["python3", "hello.py"]}
    report = _new_report()
    ok = validate_command_hook(hook, "PreToolUse", None, report)
    assert ok is True
    # Should NOT produce CRITICAL/MAJOR about missing command or args shape
    for finding in report.results:
        if finding.level in ("CRITICAL", "MAJOR"):
            assert "args" not in finding.message or "exec form" in finding.message, (
                f"unexpected CRITICAL/MAJOR on valid args hook: {finding.message}"
            )
    assert _has_finding(report, "PASSED", "Args (exec form,")


def test_canonical_exec_form_command_plus_args_passes():
    """Issue #24: command + args (canonical exec form per CC docs) → passes, no MAJOR.

    The docs explicitly define this as the exec form — ``command`` is the
    executable, ``args`` is the argv vector. v2.83.0 wrongly emitted MAJOR
    "mutually exclusive"; v2.87.0 (fix) removed the bogus mutex.
    """
    hook = {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/format.js", "--fix"]}
    report = _new_report()
    ok = validate_command_hook(hook, "PreToolUse", None, report)
    assert ok is True
    # No "mutually exclusive" MAJOR may be emitted.
    for finding in report.results:
        assert "mutually exclusive" not in finding.message, (
            f"v2.83.0 mutex regression resurfaced: {finding.level} — {finding.message}"
        )
    # A PASSED line about exec form must be present.
    assert _has_finding(report, "PASSED", "Exec form")


def test_exec_form_bare_command_with_whitespace_emits_minor():
    """Exec form (command + args) with whitespace in command → MINOR.

    The CC docs explicitly warn: if `command` is a bare name with
    whitespace alongside `args`, the spawn fails. Single targeted MINOR
    so the author catches it pre-publish.
    """
    hook = {"command": "node script.js", "args": ["--fix"]}
    report = _new_report()
    validate_command_hook(hook, "PreToolUse", None, report)
    assert _has_finding(report, "MINOR", "bare executable")


def test_exec_form_command_with_path_separator_no_whitespace_warning():
    """`command` containing slashes (path form) is fine — no whitespace warning.

    Only the bare-name + whitespace combination is hazardous. Path forms
    like `/usr/bin/node` are valid exec-form executables.
    """
    hook = {"command": "${CLAUDE_PLUGIN_ROOT}/bin/runner", "args": ["--once"]}
    report = _new_report()
    validate_command_hook(hook, "PreToolUse", None, report)
    for finding in report.results:
        assert "bare executable" not in finding.message


def test_args_empty_list_emits_critical():
    """args: [] → CRITICAL ('cannot be empty')."""
    hook = {"args": []}
    report = _new_report()
    ok = validate_command_hook(hook, "PreToolUse", None, report)
    assert ok is False
    assert _has_finding(report, "CRITICAL", "empty list"), (
        f"expected CRITICAL about empty list, got: {[(f.level, f.message) for f in report.results]}"
    )


def test_args_non_string_element_emits_critical():
    """args: ['python3', 42] → CRITICAL about non-string element."""
    hook = {"args": ["python3", 42, "hello.py"]}
    report = _new_report()
    ok = validate_command_hook(hook, "PreToolUse", None, report)
    assert ok is False
    assert _has_finding(report, "CRITICAL", "args[1]"), (
        f"expected CRITICAL about args[1] type, got: {[(f.level, f.message) for f in report.results]}"
    )


def test_neither_command_nor_args_emits_critical():
    """No command, no args → CRITICAL with updated message mentioning both."""
    hook = {"type": "command"}
    report = _new_report()
    ok = validate_command_hook(hook, "PreToolUse", None, report)
    assert ok is False
    # Updated message should reference both 'command' and 'args' / v2.1.139
    found = False
    for finding in report.results:
        if finding.level == "CRITICAL" and "command" in finding.message and "args" in finding.message:
            found = True
            break
    assert found, (
        f"expected CRITICAL mentioning both 'command' and 'args', got: {[(f.level, f.message) for f in report.results]}"
    )


def test_args_first_token_absolute_path_emits_major_portability():
    """args[0] absolute path without env var → MAJOR portability check."""
    hook = {"args": ["/usr/local/bin/python3", "hello.py"]}
    report = _new_report()
    validate_command_hook(hook, "PreToolUse", None, report)
    # The same portability check that runs on `command` should also run on `args` synthesized command.
    assert _has_finding(report, "MAJOR", "absolute path"), (
        f"expected MAJOR portability finding on absolute path, got: {[(f.level, f.message) for f in report.results]}"
    )


def test_args_not_a_list_emits_critical():
    """args as a string (wrong shape) → CRITICAL about list."""
    hook = {"args": "python3 hello.py"}
    report = _new_report()
    ok = validate_command_hook(hook, "PreToolUse", None, report)
    assert ok is False
    assert _has_finding(report, "CRITICAL", "must be a list"), (
        f"expected CRITICAL about list type, got: {[(f.level, f.message) for f in report.results]}"
    )
