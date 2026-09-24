#!/usr/bin/env python3
"""CC v2.1.258–2.1.281 spec sync — hook surface (two-sided).

1. ``setMode`` accepts ``auto`` and ``manual`` (hooks.md); a bogus mode is still MAJOR.
2. StopFailure matcher accepts ``cloud_credential_error`` / ``account_on_hold``;
   a bogus error value still draws the unknown-value finding.
3. Unquoted ``${CLAUDE_PLUGIN_ROOT}`` in a SHELL-FORM hook command → WARNING
   (CC v2.1.281 `claude plugin validate` warns); quoted / exec-form → silent.
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import pytest  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402
from validate_hook import (  # noqa: E402
    STOPFAILURE_ERRORS,
    _check_matcher_values,
    unquoted_plugin_root_in_shell,
    validate_command_hook,
)
from validate_hook_output import validate_output_payload  # noqa: E402


def _set_mode_payload(mode: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {
                "behavior": "allow",
                "updatedPermissions": [{"type": "setMode", "mode": mode, "destination": "session"}],
            },
        }
    }


@pytest.mark.parametrize("mode", ["auto", "manual", "default", "plan"])
def test_set_mode_documented_modes_accepted(mode: str) -> None:
    """Every documented setMode mode (incl. auto + the manual alias) passes."""
    assert not validate_output_payload("PermissionRequest", _set_mode_payload(mode)).has_major


def test_set_mode_bogus_still_major() -> None:
    """Positive control: an undocumented mode still draws a MAJOR."""
    assert validate_output_payload("PermissionRequest", _set_mode_payload("yolo")).has_major


def _matcher_findings(value: str) -> list[str]:
    report = ValidationReport()
    _check_matcher_values(value, STOPFAILURE_ERRORS, "StopFailure", "error", report)
    return [r.message for r in report.results]


@pytest.mark.parametrize("value", ["cloud_credential_error", "account_on_hold"])
def test_stopfailure_new_errors_accepted(value: str) -> None:
    """The hooks.md StopFailure values CPV lacked no longer draw a finding."""
    assert _matcher_findings(value) == []


def test_stopfailure_bogus_error_still_flagged() -> None:
    """Positive control: an undocumented StopFailure error still draws a finding."""
    assert _matcher_findings("totally_made_up_error") != []


def _warnings(hook: dict) -> list[str]:
    report = ValidationReport()
    validate_command_hook(hook, "PreToolUse", None, report)
    return [r.message for r in report.results if r.level == "WARNING" and "unquoted" in r.message]


def test_unquoted_plugin_root_fires() -> None:
    """Shell-form hook with a bare ${CLAUDE_PLUGIN_ROOT} → one WARNING."""
    assert len(_warnings({"command": "bash ${CLAUDE_PLUGIN_ROOT}/x.sh"})) == 1


@pytest.mark.parametrize(
    "hook",
    [
        {"command": 'bash "${CLAUDE_PLUGIN_ROOT}/x.sh"'},
        {"command": "bash '${CLAUDE_PLUGIN_ROOT}/x.sh'"},
        {"command": "${CLAUDE_PLUGIN_ROOT}/x", "args": ["--flag"]},
        {"command": "bash scripts/x.sh"},
    ],
    ids=["double-quoted", "single-quoted", "exec-form", "no-root"],
)
def test_quoted_or_exec_form_silent(hook: dict) -> None:
    """Negative controls: quoted, exec-form, and root-less commands never fire."""
    assert _warnings(hook) == []


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        ('"${CLAUDE_PLUGIN_ROOT}"/x.sh', False),
        ("'${CLAUDE_PLUGIN_ROOT}/x'", False),
        ("bash ${CLAUDE_PLUGIN_ROOT}/x.sh", True),
        ('echo "a" ${CLAUDE_PLUGIN_ROOT}/x', True),
        ("echo \"it's\" '${CLAUDE_PLUGIN_ROOT}'", False),
        ("bash \\${CLAUDE_PLUGIN_ROOT}/x", False),
        ("bash $CLAUDE_PLUGIN_ROOT/x.sh", False),
        (None, False),
    ],
)
def test_helper_edge_cases(cmd: object, expected: bool) -> None:
    """The public predicate on quoting edge cases (bare $VAR form is out of scope)."""
    assert unquoted_plugin_root_in_shell(cmd) is expected
