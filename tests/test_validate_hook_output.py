#!/usr/bin/env python3
"""Tests for validate_hook_output.py.

Exercises the per-event hook output payload validation module against
hooks.md decision-control table, permission update types, universal fields
and per-event HSO schemas. All fixtures are real dicts — no mocks.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# Add scripts directory to path for imports
_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from validate_hook_output import (  # noqa: E402
    ELICITATION_ACTIONS,
    HOOK_OUTPUT_EVENT_FIELDS,
    PERMISSION_BEHAVIORS,
    PERMISSION_DESTINATIONS,
    PERMISSION_MODES,
    PERMISSION_UPDATE_TYPES,
    PRETOOLUSE_DECISIONS,
    UNIVERSAL_OUTPUT_FIELDS,
    validate_output_payload,
)

SCRIPT_PATH = _SCRIPTS_DIR / "validate_hook_output.py"


# ---------------------------------------------------------------------------
# Fixture builders — real payload dicts matching hooks.md examples
# ---------------------------------------------------------------------------


def _pretooluse_payload(decision: str, reason: str = "") -> dict[str, Any]:
    """Build a PreToolUse payload with the given permissionDecision."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }


def _permission_request_payload(update_type: str) -> dict[str, Any]:
    """Build a PermissionRequest payload exercising an updatedPermissions type."""
    entry: dict[str, Any] = {"type": update_type, "destination": "session"}
    if update_type in {"addRules", "replaceRules", "removeRules"}:
        entry["rules"] = ["Read"]
        entry["behavior"] = "allow"
    elif update_type == "setMode":
        entry["mode"] = "acceptEdits"
    elif update_type in {"addDirectories", "removeDirectories"}:
        entry["directories"] = ["/tmp/work"]
    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {
                "behavior": "allow",
                "updatedPermissions": [entry],
            },
        }
    }


# ---------------------------------------------------------------------------
# Constants integrity — guardrails so edits don't drift from hooks.md
# ---------------------------------------------------------------------------


class TestConstants:
    """Sanity-check the declared enums match hooks.md verbatim."""

    def test_pretooluse_decisions_exhaustive(self):
        """PRETOOLUSE_DECISIONS contains exactly {allow, deny, ask, defer}."""
        assert PRETOOLUSE_DECISIONS == frozenset({"allow", "deny", "ask", "defer"})

    def test_permission_update_types_exhaustive(self):
        """PERMISSION_UPDATE_TYPES has the 6 hooks.md L1119-1126 types."""
        assert PERMISSION_UPDATE_TYPES == frozenset(
            {
                "addRules",
                "replaceRules",
                "removeRules",
                "setMode",
                "addDirectories",
                "removeDirectories",
            }
        )

    def test_permission_behaviors_exhaustive(self):
        """PERMISSION_BEHAVIORS == {allow, deny, ask} per hooks.md L1121."""
        assert PERMISSION_BEHAVIORS == frozenset({"allow", "deny", "ask"})

    def test_permission_destinations_exhaustive(self):
        """PERMISSION_DESTINATIONS matches hooks.md L1134-1139."""
        assert PERMISSION_DESTINATIONS == frozenset({"session", "localSettings", "projectSettings", "userSettings"})

    def test_permission_modes_exhaustive(self):
        """PERMISSION_MODES has the 5 values from hooks.md L1124."""
        assert PERMISSION_MODES == frozenset({"default", "acceptEdits", "dontAsk", "bypassPermissions", "plan"})

    def test_universal_output_fields_exhaustive(self):
        """UNIVERSAL_OUTPUT_FIELDS covers hooks.md L601-606 + decision/reason + v2.1.141 terminalSequence."""
        expected = {
            "continue",
            "stopReason",
            "suppressOutput",
            "hookSpecificOutput",
            "decision",
            "reason",
            "systemMessage",
            "terminalSequence",  # v2.1.141 — desktop notifications, window titles, bells
        }
        assert UNIVERSAL_OUTPUT_FIELDS == frozenset(expected)

    def test_elicitation_actions_exhaustive(self):
        """ELICITATION_ACTIONS = {accept, decline, cancel}."""
        assert ELICITATION_ACTIONS == frozenset({"accept", "decline", "cancel"})

    def test_hook_output_event_fields_covers_all_events(self):
        """Every VALID_HOOK_EVENTS entry has a HOOK_OUTPUT_EVENT_FIELDS row."""
        from cpv_validation_common import VALID_HOOK_EVENTS

        for event in VALID_HOOK_EVENTS:
            assert event in HOOK_OUTPUT_EVENT_FIELDS, f"Missing output schema for event {event!r}"


# ---------------------------------------------------------------------------
# PreToolUse — 4 accepted decisions, 1 unknown decision
# ---------------------------------------------------------------------------


class TestPreToolUseDecisions:
    """PreToolUse permissionDecision enum coverage (hooks.md L984)."""

    def test_decision_allow_passes(self):
        """PreToolUse permissionDecision='allow' is accepted."""
        report = validate_output_payload("PreToolUse", _pretooluse_payload("allow"))
        assert not report.has_major
        assert not report.has_critical

    def test_decision_deny_passes(self):
        """PreToolUse permissionDecision='deny' is accepted."""
        report = validate_output_payload("PreToolUse", _pretooluse_payload("deny"))
        assert not report.has_major
        assert not report.has_critical

    def test_decision_ask_passes(self):
        """PreToolUse permissionDecision='ask' is accepted."""
        report = validate_output_payload("PreToolUse", _pretooluse_payload("ask"))
        assert not report.has_major
        assert not report.has_critical

    def test_decision_defer_passes(self):
        """PreToolUse permissionDecision='defer' is accepted (hooks.md L984)."""
        report = validate_output_payload("PreToolUse", _pretooluse_payload("defer"))
        assert not report.has_major
        assert not report.has_critical

    def test_unknown_decision_yolo_is_major(self):
        """PreToolUse permissionDecision='yolo' produces MAJOR."""
        report = validate_output_payload("PreToolUse", _pretooluse_payload("yolo"))
        assert report.has_major
        majors = report.get_errors_by_level("MAJOR")
        assert any("yolo" in m.message for m in majors)

    def test_pretooluse_decision_non_string_is_major(self):
        """Non-string permissionDecision value is MAJOR."""
        payload: dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": 42,
            }
        }
        report = validate_output_payload("PreToolUse", payload)
        assert report.has_major


# ---------------------------------------------------------------------------
# hookSpecificOutput.hookEventName — required
# ---------------------------------------------------------------------------


class TestHookEventName:
    """Required 'hookEventName' field on hookSpecificOutput."""

    def test_missing_hook_event_name_is_major(self):
        """Missing hookSpecificOutput.hookEventName is MAJOR."""
        payload = {"hookSpecificOutput": {"permissionDecision": "allow"}}
        report = validate_output_payload("PreToolUse", payload)
        assert report.has_major
        assert any("hookEventName" in r.message for r in report.get_all_errors())

    def test_mismatched_hook_event_name_is_major(self):
        """Mismatched hookEventName vs --event is MAJOR."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "permissionDecision": "allow",
            }
        }
        report = validate_output_payload("PreToolUse", payload)
        assert report.has_major


# ---------------------------------------------------------------------------
# Permission update types — all 6 pass, unknown rejected
# ---------------------------------------------------------------------------


class TestPermissionUpdateTypes:
    """All 6 permission update types (hooks.md L1119-1126)."""

    def test_add_rules_accepted(self):
        """addRules permission update type is accepted."""
        report = validate_output_payload("PermissionRequest", _permission_request_payload("addRules"))
        assert not report.has_major
        assert not report.has_critical

    def test_replace_rules_accepted(self):
        """replaceRules permission update type is accepted."""
        report = validate_output_payload("PermissionRequest", _permission_request_payload("replaceRules"))
        assert not report.has_major

    def test_remove_rules_accepted(self):
        """removeRules permission update type is accepted."""
        report = validate_output_payload("PermissionRequest", _permission_request_payload("removeRules"))
        assert not report.has_major

    def test_set_mode_accepted(self):
        """setMode permission update type is accepted."""
        report = validate_output_payload("PermissionRequest", _permission_request_payload("setMode"))
        assert not report.has_major

    def test_add_directories_accepted(self):
        """addDirectories permission update type is accepted."""
        report = validate_output_payload("PermissionRequest", _permission_request_payload("addDirectories"))
        assert not report.has_major

    def test_remove_directories_accepted(self):
        """removeDirectories permission update type is accepted."""
        report = validate_output_payload(
            "PermissionRequest",
            _permission_request_payload("removeDirectories"),
        )
        assert not report.has_major

    def test_unknown_update_type_is_major(self):
        """Unknown permission update type produces MAJOR."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "allow",
                    "updatedPermissions": [{"type": "nukeEverything", "destination": "session"}],
                },
            }
        }
        report = validate_output_payload("PermissionRequest", payload)
        assert report.has_major
        assert any("nukeEverything" in r.message for r in report.get_all_errors())


# ---------------------------------------------------------------------------
# Universal fields — accepted values and unknown keys
# ---------------------------------------------------------------------------


class TestUniversalFields:
    """Universal output field handling (hooks.md L601-606)."""

    def test_continue_true_accepted(self):
        """Top-level 'continue': true is accepted."""
        payload = {"continue": True}
        report = validate_output_payload("Notification", payload)
        assert not report.has_major
        assert not report.has_critical

    def test_continue_non_bool_is_major(self):
        """Top-level 'continue' non-bool is MAJOR."""
        payload = {"continue": "yes"}
        report = validate_output_payload("Notification", payload)
        assert report.has_major

    def test_unknown_top_level_field_is_minor(self):
        """Unknown top-level field produces MINOR."""
        payload = {"mysteryField": "xxx"}
        report = validate_output_payload("Notification", payload)
        assert report.has_minor
        assert any("mysteryField" in r.message for r in report.get_errors_by_level("MINOR"))

    def test_stop_reason_accepted(self):
        """stopReason string is accepted."""
        payload = {"continue": False, "stopReason": "policy violation"}
        report = validate_output_payload("Stop", payload)
        assert not report.has_major

    def test_system_message_non_string_is_major(self):
        """Non-string systemMessage produces MAJOR."""
        payload = {"systemMessage": 7}
        report = validate_output_payload("Notification", payload)
        assert report.has_major


# ---------------------------------------------------------------------------
# Ill-formed payload types
# ---------------------------------------------------------------------------


class TestIllFormedPayloads:
    """Top-level shape / JSON correctness (hooks.md L601 universal schema)."""

    def test_payload_is_list_critical(self):
        """A list at the top level is CRITICAL."""
        report = validate_output_payload("Notification", ["not", "an", "object"])
        assert report.has_critical

    def test_payload_is_string_critical(self):
        """A string at the top level is CRITICAL."""
        report = validate_output_payload("Notification", "just-a-string")
        assert report.has_critical

    def test_payload_is_null_critical(self):
        """null payload is CRITICAL."""
        report = validate_output_payload("Notification", None)
        assert report.has_critical

    def test_hook_specific_output_not_object_is_critical(self):
        """hookSpecificOutput must be an object — list → CRITICAL."""
        payload = {"hookSpecificOutput": ["wrong"]}
        report = validate_output_payload("PreToolUse", payload)
        assert report.has_critical

    def test_unknown_event_is_major(self):
        """Unknown event name passed to validate_output_payload → MAJOR."""
        report = validate_output_payload("NotAnEvent", {})
        assert report.has_major


# ---------------------------------------------------------------------------
# Per-event output accepted
# ---------------------------------------------------------------------------


class TestPerEventAccepted:
    """Per-event output shapes that must pass."""

    def test_session_start_additional_context(self):
        """SessionStart with additionalContext string is accepted."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "Welcome back!",
            }
        }
        report = validate_output_payload("SessionStart", payload)
        assert not report.has_major
        assert not report.has_critical

    def test_post_tool_use_failure_additional_context(self):
        """PostToolUseFailure with additionalContext string is accepted."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUseFailure",
                "additionalContext": "Retry guidance: ...",
            }
        }
        report = validate_output_payload("PostToolUseFailure", payload)
        assert not report.has_major
        assert not report.has_critical

    def test_stop_top_level_block_accepted(self):
        """Stop with top-level decision='block' + reason accepted."""
        payload = {"decision": "block", "reason": "safety"}
        report = validate_output_payload("Stop", payload)
        assert not report.has_major
        assert not report.has_critical

    def test_elicitation_action_accept(self):
        """Elicitation with action='accept' is accepted."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "Elicitation",
                "action": "accept",
                "content": {"ok": True},
            }
        }
        report = validate_output_payload("Elicitation", payload)
        assert not report.has_major
        assert not report.has_critical

    def test_elicitation_action_unknown_is_major(self):
        """Elicitation with unknown action value is MAJOR."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "Elicitation",
                "action": "reboot",
            }
        }
        report = validate_output_payload("Elicitation", payload)
        assert report.has_major

    def test_worktree_create_worktree_path_string(self):
        """WorktreeCreate with worktreePath string is accepted."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "WorktreeCreate",
                "worktreePath": "/tmp/wt",
            }
        }
        report = validate_output_payload("WorktreeCreate", payload)
        assert not report.has_major
        assert not report.has_critical

    def test_worktree_create_worktree_path_non_string_is_major(self):
        """WorktreeCreate worktreePath non-string is MAJOR."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "WorktreeCreate",
                "worktreePath": 42,
            }
        }
        report = validate_output_payload("WorktreeCreate", payload)
        assert report.has_major

    def test_session_start_additional_context_non_string_is_major(self):
        """SessionStart additionalContext non-string is MAJOR."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": 12,
            }
        }
        report = validate_output_payload("SessionStart", payload)
        assert report.has_major

    def test_file_changed_watch_paths_accepted(self):
        """FileChanged with watchPaths list of strings is accepted."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "FileChanged",
                "watchPaths": ["src/**/*.py"],
            }
        }
        report = validate_output_payload("FileChanged", payload)
        assert not report.has_major
        assert not report.has_critical

    def test_permission_denied_retry_accepted(self):
        """PermissionDenied with retry=true is accepted."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionDenied",
                "retry": True,
            }
        }
        report = validate_output_payload("PermissionDenied", payload)
        assert not report.has_major

    def test_unknown_hso_field_is_nit(self):
        """Unknown per-event hookSpecificOutput field is NIT."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "ok",
                "hypotheticalExtra": "nope",
            }
        }
        report = validate_output_payload("SessionStart", payload)
        assert report.has_nit


# ---------------------------------------------------------------------------
# CLI smoke test — spawn the script with --stdin to exercise main()
# ---------------------------------------------------------------------------


class TestCLIEntrypoint:
    """Spawn the script to cover the argparse path end-to-end."""

    def test_cli_stdin_pretooluse_allow_exit_0(self):
        """CLI with --stdin and a PASSED payload exits 0."""
        payload = _pretooluse_payload("allow")
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--event",
                "PreToolUse",
                "--stdin",
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr

    def test_cli_stdin_unknown_decision_exit_nonzero(self):
        """CLI with --stdin and unknown decision exits with MAJOR code."""
        payload = _pretooluse_payload("yolo")
        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--event",
                "PreToolUse",
                "--stdin",
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 2, proc.stderr


class TestPostToolUseUpdatedToolOutput:
    """v2.1.121: hookSpecificOutput.updatedToolOutput generalized from MCP-only
    to all tools. Both `updatedMCPToolOutput` (legacy) and `updatedToolOutput`
    (new) must be accepted on PostToolUse.
    """

    def test_updated_tool_output_accepted(self):
        """PostToolUse with updatedToolOutput (new, all-tools) is accepted."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedToolOutput": "(tool output replacement)",
            }
        }
        report = validate_output_payload("PostToolUse", payload)
        assert not report.has_major
        assert not report.has_critical

    def test_updated_mcp_tool_output_still_accepted(self):
        """Regression guard: legacy updatedMCPToolOutput still accepted."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "updatedMCPToolOutput": "(mcp output replacement)",
            }
        }
        report = validate_output_payload("PostToolUse", payload)
        assert not report.has_major
        assert not report.has_critical

    def test_unknown_post_tool_use_key_still_flagged(self):
        """Genuinely unknown PostToolUse key still produces a finding."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "totallyMadeUpKey": "...",
            }
        }
        report = validate_output_payload("PostToolUse", payload)
        # Either major or critical depending on validator severity policy.
        assert report.has_major or report.has_critical or any("totallyMadeUpKey" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# Top-level reason / decision type-checks (CPV-P2-M1 follow-ups)
# ---------------------------------------------------------------------------


class TestTopLevelReasonTypeCheck:
    """Top-level 'reason' must be a string when present (hooks.md L601-606)."""

    def test_top_level_reason_string_accepted(self):
        """Top-level 'reason' as string is accepted on Stop event."""
        payload = {"decision": "block", "reason": "policy violation"}
        report = validate_output_payload("Stop", payload)
        assert not report.has_major
        assert not report.has_critical

    def test_top_level_reason_int_is_major(self):
        """Top-level 'reason' as int produces MAJOR (must be string)."""
        payload = {"decision": "block", "reason": 42}
        report = validate_output_payload("Stop", payload)
        assert report.has_major
        assert any("reason" in r.message.lower() for r in report.get_errors_by_level("MAJOR"))

    def test_top_level_reason_list_is_major(self):
        """Top-level 'reason' as list produces MAJOR."""
        payload = {"decision": "block", "reason": ["a", "b"]}
        report = validate_output_payload("Stop", payload)
        assert report.has_major

    def test_top_level_reason_null_accepted(self):
        """Top-level 'reason' as null is treated as absent (no error)."""
        payload = {"decision": "block", "reason": None}
        report = validate_output_payload("Stop", payload)
        # null is conventionally "no reason" — should not be flagged as MAJOR
        # because spec L601-606 docs the field as optional.
        assert not report.has_major


class TestTopLevelDecisionScope:
    """Events that DO NOT support top-level 'decision: block' must reject it.

    hooks.md per-event decision-control table (L583-628) enumerates which
    events can emit a top-level decision. Events outside that list (e.g.
    TaskCreated L1440-1443, SessionStart, Notification) only honor
    `continue:false` plus exit-code 2; emitting `decision: block` from
    them is silently ignored at runtime — CPV must surface this as MAJOR
    so hook authors don't ship broken decision logic.
    """

    def test_task_created_decision_block_is_major(self):
        """TaskCreated does not honor top-level 'decision' — MAJOR."""
        payload = {"decision": "block", "reason": "stop"}
        report = validate_output_payload("TaskCreated", payload)
        assert report.has_major
        assert any("decision" in r.message.lower() for r in report.get_errors_by_level("MAJOR"))

    def test_task_completed_decision_block_is_major(self):
        """TaskCompleted does not honor top-level 'decision' — MAJOR."""
        payload = {"decision": "block"}
        report = validate_output_payload("TaskCompleted", payload)
        assert report.has_major

    def test_session_start_decision_block_is_major(self):
        """SessionStart does not honor top-level 'decision' — MAJOR."""
        payload = {"decision": "block"}
        report = validate_output_payload("SessionStart", payload)
        assert report.has_major

    def test_session_end_decision_block_is_major(self):
        """SessionEnd does not honor top-level 'decision' — MAJOR."""
        payload = {"decision": "block"}
        report = validate_output_payload("SessionEnd", payload)
        assert report.has_major

    def test_notification_decision_block_is_major(self):
        """Notification does not honor top-level 'decision' — MAJOR."""
        payload = {"decision": "block"}
        report = validate_output_payload("Notification", payload)
        assert report.has_major

    def test_post_tool_use_decision_allow_is_major(self):
        """PostToolUse top-level 'decision' must be 'block' — 'allow' is MAJOR."""
        payload = {"decision": "allow"}
        report = validate_output_payload("PostToolUse", payload)
        assert report.has_major
        assert any("block" in r.message for r in report.get_errors_by_level("MAJOR"))

    def test_user_prompt_submit_decision_block_accepted(self):
        """UserPromptSubmit top-level 'decision: block' is accepted."""
        payload = {"decision": "block", "reason": "blocked"}
        report = validate_output_payload("UserPromptSubmit", payload)
        assert not report.has_major
        assert not report.has_critical


class TestPreToolUseLegacyDecision:
    """PreToolUse top-level 'decision' is deprecated — hooks.md L1010.

    Legacy values "approve" and "block" (mapped to "allow" / "deny") still
    work at runtime but emit a WARNING. Other values (e.g. "yolo") on
    top-level decision should be MAJOR even with the deprecation warning.
    """

    def test_pretooluse_top_level_approve_warns(self):
        """PreToolUse top-level decision='approve' emits WARNING (deprecated)."""
        payload = {"decision": "approve"}
        report = validate_output_payload("PreToolUse", payload)
        assert any("deprecated" in r.message.lower() for r in report.results)

    def test_pretooluse_top_level_block_warns(self):
        """PreToolUse top-level decision='block' emits WARNING (deprecated)."""
        payload = {"decision": "block"}
        report = validate_output_payload("PreToolUse", payload)
        assert any("deprecated" in r.message.lower() for r in report.results)

    def test_pretooluse_top_level_yolo_is_major(self):
        """PreToolUse top-level decision='yolo' is MAJOR (unknown legacy value)."""
        payload = {"decision": "yolo"}
        report = validate_output_payload("PreToolUse", payload)
        assert report.has_major


class TestStopReasonScopeWithPassed:
    """Verify the report is marked PASSED when only WARNING is present.

    A WARNING about deprecated PreToolUse top-level decision should not
    block the report — `report.passed(...)` should still be recorded if
    no critical/major/minor flags were set.
    """

    def test_pretooluse_top_level_approve_still_passes(self):
        """PreToolUse top-level decision='approve' — only WARNING, marked passed."""
        payload = {"decision": "approve"}
        report = validate_output_payload("PreToolUse", payload)
        # Has the deprecation warning but no major/critical/minor.
        assert not report.has_major
        assert not report.has_critical
        assert not report.has_minor
