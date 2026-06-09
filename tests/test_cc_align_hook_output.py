#!/usr/bin/env python3
"""CC 2.1.163 alignment: Stop/SubagentStop hookSpecificOutput.additionalContext.

CC 2.1.163 lets Stop and SubagentStop hooks return
``hookSpecificOutput.additionalContext`` (feedback to Claude that keeps the
turn going). Before the fix, ``validate_hook_output.py`` omitted that key from
the Stop and SubagentStop allowed-key sets, so the unknown-field loop wrongly
flagged a valid latest-spec payload with a NIT.

These tests are two-sided: a Stop AND a SubagentStop payload carrying
``additionalContext`` must now be ACCEPTED (no unknown-field NIT, no
MAJOR/CRITICAL), while a genuinely-unknown HSO key must STILL be rejected as a
NIT — proving the allowlist was widened by exactly one key, not disabled. All
fixtures are real dicts — no mocks.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Add scripts directory to path for imports.
_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from validate_hook_output import (  # noqa: E402
    HOOK_OUTPUT_EVENT_FIELDS,
    validate_output_payload,
)


def _stop_payload(event_name: str, extra: dict[str, Any]) -> dict[str, Any]:
    """Build a Stop/SubagentStop payload whose hookSpecificOutput carries extra."""
    hso: dict[str, Any] = {"hookEventName": event_name}
    hso.update(extra)
    return {"hookSpecificOutput": hso}


class TestStopAdditionalContextAccepted:
    """CC 2.1.163: additionalContext is now a valid Stop/SubagentStop HSO key."""

    def test_constants_include_additional_context(self):
        """Both Stop and SubagentStop output field sets now contain additionalContext."""
        assert "additionalContext" in HOOK_OUTPUT_EVENT_FIELDS["Stop"]
        assert "additionalContext" in HOOK_OUTPUT_EVENT_FIELDS["SubagentStop"]
        # The fix widened the set by exactly one key — decision/reason stay.
        assert HOOK_OUTPUT_EVENT_FIELDS["Stop"] == frozenset({"decision", "reason", "additionalContext"})
        assert HOOK_OUTPUT_EVENT_FIELDS["SubagentStop"] == frozenset({"decision", "reason", "additionalContext"})

    def test_stop_additional_context_accepted(self):
        """Stop hookSpecificOutput.additionalContext produces no finding (CC 2.1.163)."""
        payload = _stop_payload("Stop", {"additionalContext": "keep going: re-run the failing test"})
        report = validate_output_payload("Stop", payload)
        assert not report.has_critical
        assert not report.has_major
        assert not report.has_minor
        # No unknown-field NIT about additionalContext.
        nits = report.get_errors_by_level("NIT")
        assert not any("additionalContext" in n.message for n in nits)

    def test_subagentstop_additional_context_accepted(self):
        """SubagentStop hookSpecificOutput.additionalContext produces no finding (CC 2.1.163)."""
        payload = _stop_payload("SubagentStop", {"additionalContext": "subagent feedback for the parent"})
        report = validate_output_payload("SubagentStop", payload)
        assert not report.has_critical
        assert not report.has_major
        assert not report.has_minor
        nits = report.get_errors_by_level("NIT")
        assert not any("additionalContext" in n.message for n in nits)


class TestStopUnknownKeyStillRejected:
    """The allowlist still rejects a genuinely-unknown HSO key (fix is scoped)."""

    def test_stop_unknown_key_still_nit(self):
        """An unrecognized Stop HSO key still emits an unknown-field NIT."""
        payload = _stop_payload("Stop", {"bogusKey": "not in the spec"})
        report = validate_output_payload("Stop", payload)
        assert report.has_nit
        nits = report.get_errors_by_level("NIT")
        assert any("bogusKey" in n.message for n in nits)

    def test_subagentstop_unknown_key_still_nit(self):
        """An unrecognized SubagentStop HSO key still emits an unknown-field NIT."""
        payload = _stop_payload("SubagentStop", {"bogusKey": "not in the spec"})
        report = validate_output_payload("SubagentStop", payload)
        assert report.has_nit
        nits = report.get_errors_by_level("NIT")
        assert any("bogusKey" in n.message for n in nits)
