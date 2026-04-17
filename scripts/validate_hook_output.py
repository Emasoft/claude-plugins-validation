#!/usr/bin/env python3
"""
Claude Plugins Validation - Hook Output Payload Validator

Validates the JSON payload a hook emits to stdout (or over HTTP) against the
per-event decision-control table documented in hooks.md. This is distinct from
``validate_hook.py`` which validates the ``hooks.json`` configuration shape.

Based on:
  - https://code.claude.com/docs/en/hooks.md (L583-628: decision-control table,
    L880-976: tool_input, L984: PreToolUse decision enum, L989: precedence,
    L1010: deprecation, L1089-1113: PermissionRequest, L1115-1141: permission
    update types)

Spec references (hooks.md line numbers) are embedded alongside each constant.

Usage:
    uv run python scripts/validate_hook_output.py --event PreToolUse payload.json
    echo '{"hookSpecificOutput": ...}' | uv run python scripts/validate_hook_output.py --event PreToolUse --stdin
    uv run python scripts/validate_hook_output.py --event SessionStart payload.json --report out.md

Exit codes:
    0 - All checks passed
    1 - CRITICAL issues found (ill-formed JSON, wrong top-level type)
    2 - MAJOR issues found (unknown decision values, missing required fields)
    3 - MINOR issues found (unknown universal fields)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Allow running as a script: add scripts/ to sys.path so the sibling
# ``cpv_validation_common`` module resolves regardless of cwd.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from cpv_validation_common import (  # noqa: E402
    VALID_HOOK_EVENTS,
    ValidationReport,
    print_report_summary,
    print_results_by_level,
    save_report_and_print_summary,
)

# =============================================================================
# Universal output fields — hooks.md L601-606
# =============================================================================

# Fields that ANY hook event may emit at the top level of its JSON payload.
# "continue" defaults to true; if false, Claude stops processing entirely.
# "stopReason" is a message shown to the user when continue=false.
# "suppressOutput" (default false) omits stdout from the debug log.
# "systemMessage" is a warning shown to the user.
# "hookSpecificOutput" carries per-event extra fields.
# "decision" and "reason" are the legacy top-level decision surface kept by
# several events (PostToolUse, Stop, ConfigChange, etc.).
UNIVERSAL_OUTPUT_FIELDS: frozenset[str] = frozenset(
    {
        "continue",
        "stopReason",
        "suppressOutput",
        "hookSpecificOutput",
        "decision",
        "reason",
        "systemMessage",
    }
)

# =============================================================================
# PreToolUse — hooks.md L984
# =============================================================================

# Exhaustive allowed values for PreToolUse ``permissionDecision``. Precedence
# when multiple hooks fire: deny > defer > ask > allow (hooks.md L989).
# Aliased from validate_hook.py:208 PRETOOLUSE_PERMISSION_DECISIONS to keep
# one single source of truth.
PRETOOLUSE_DECISIONS: frozenset[str] = frozenset({"allow", "deny", "ask", "defer"})

# =============================================================================
# Permission update entries — hooks.md L1115-1141
# =============================================================================

# The 6 permission update types accepted inside
# ``hookSpecificOutput.updatedPermissions[*].type`` on PermissionRequest when
# the decision is ``allow``.
PERMISSION_UPDATE_TYPES: frozenset[str] = frozenset(
    {
        "addRules",
        "replaceRules",
        "removeRules",
        "setMode",
        "addDirectories",
        "removeDirectories",
    }
)

# ``behavior`` enum on addRules/replaceRules/removeRules (hooks.md L1121).
PERMISSION_BEHAVIORS: frozenset[str] = frozenset({"allow", "deny", "ask"})

# ``mode`` enum on setMode (hooks.md L1124).
PERMISSION_MODES: frozenset[str] = frozenset(
    {"default", "acceptEdits", "dontAsk", "bypassPermissions", "plan"}
)

# ``destination`` enum on every permission update type (hooks.md L1134-1139).
PERMISSION_DESTINATIONS: frozenset[str] = frozenset(
    {"session", "localSettings", "projectSettings", "userSettings"}
)

# =============================================================================
# PermissionRequest — hooks.md L1089-1113
# =============================================================================

# ``behavior`` at hookSpecificOutput.decision.behavior for PermissionRequest.
PERMISSION_REQUEST_BEHAVIORS: frozenset[str] = frozenset({"allow", "deny"})

# =============================================================================
# Elicitation / ElicitationResult — hooks.md L2021-2024, L2067-2070
# =============================================================================

ELICITATION_ACTIONS: frozenset[str] = frozenset({"accept", "decline", "cancel"})

# =============================================================================
# Per-event output schemas — hooks.md L583-628 + per-event sections
# =============================================================================
# Each entry describes the event-specific extras allowed under the
# ``hookSpecificOutput`` key (or at top level where applicable). Events that
# have no decision control and no event-specific output fields are present
# with an empty set — the universal fields still apply to them.

HOOK_OUTPUT_EVENT_FIELDS: dict[str, frozenset[str]] = {
    # hooks.md L717
    "SessionStart": frozenset({"additionalContext"}),
    # hooks.md L842-847
    "UserPromptSubmit": frozenset(
        {"decision", "reason", "additionalContext", "sessionTitle"}
    ),
    # hooks.md L982-987
    "PreToolUse": frozenset(
        {
            "permissionDecision",
            "permissionDecisionReason",
            "updatedInput",
            "additionalContext",
        }
    ),
    # hooks.md L1093-1099
    "PermissionRequest": frozenset(
        {
            "behavior",
            "updatedInput",
            "updatedPermissions",
            "message",
            "interrupt",
            # hooks.md L1092 nests ``decision`` with behavior/updatedInput/etc.
            "decision",
        }
    ),
    # hooks.md L1177-1182
    "PostToolUse": frozenset(
        {"decision", "reason", "additionalContext", "updatedMCPToolOutput"}
    ),
    # hooks.md L1234
    "PostToolUseFailure": frozenset({"additionalContext"}),
    # hooks.md L1278
    "PermissionDenied": frozenset({"retry"}),
    # hooks.md L1344
    "Notification": frozenset({"additionalContext"}),
    # hooks.md L1367
    "SubagentStart": frozenset({"additionalContext"}),
    # hooks.md L1403 — same as Stop
    "SubagentStop": frozenset({"decision", "reason"}),
    # hooks.md L1440-1443 — exit 2 OR continue:false; no specific keys
    "TaskCreated": frozenset(),
    # hooks.md L1495-1498
    "TaskCompleted": frozenset(),
    # hooks.md L1542-1545
    "Stop": frozenset({"decision", "reason"}),
    # hooks.md L1580 — no specific output
    "StopFailure": frozenset(),
    # hooks.md L1611-1614
    "TeammateIdle": frozenset(),
    # hooks.md L1683-1686
    "ConfigChange": frozenset({"decision", "reason"}),
    # hooks.md L1722
    "CwdChanged": frozenset({"watchPaths"}),
    # hooks.md L1765
    "FileChanged": frozenset({"watchPaths"}),
    # hooks.md L1819
    "WorktreeCreate": frozenset({"worktreePath"}),
    # hooks.md L1860 — no specific output
    "WorktreeRemove": frozenset(),
    # hooks.md L1873
    "PreCompact": frozenset({"decision", "reason"}),
    # hooks.md L1918 — no specific output
    "PostCompact": frozenset(),
    # hooks.md L1950 — no specific output
    "SessionEnd": frozenset(),
    # hooks.md L2021-2024
    "Elicitation": frozenset({"action", "content"}),
    # hooks.md L2067-2070
    "ElicitationResult": frozenset({"action", "content"}),
    # Legacy (kept for input compatibility; emits WARNING on use).
    "Setup": frozenset(),
    # hooks.md InstructionsLoaded — no specific output per L1789 area
    "InstructionsLoaded": frozenset(),
}


def validate_output_payload(event_name: str, payload: Any) -> ValidationReport:
    """Validate a hook output payload against the per-event spec.

    Args:
        event_name: Hook event name (e.g., "PreToolUse", "SessionStart").
        payload: Parsed JSON payload (expected: dict).

    Returns:
        ValidationReport populated with results.
    """
    report = ValidationReport()

    # Event-name sanity.
    if event_name not in VALID_HOOK_EVENTS:
        report.major(
            f"Unknown hook event: {event_name!r}. "
            f"Expected one of: {sorted(VALID_HOOK_EVENTS)}"
        )
        return report

    # Legacy Setup → WARNING (matches validate_hook.py handling).
    if event_name == "Setup":
        report.warning(
            "Setup is a legacy event not listed in hooks.md as of v2.1.109; "
            "output payload validation is best-effort."
        )

    # Top-level type check.
    if not isinstance(payload, dict):
        report.critical(
            f"Hook output payload must be a JSON object, got "
            f"{type(payload).__name__}"
        )
        return report

    # Universal fields — only warn on unknown top-level keys.
    for key in payload:
        if key not in UNIVERSAL_OUTPUT_FIELDS:
            report.minor(
                f"Unknown universal output field at top level: {key!r}. "
                f"hooks.md L601-606 documents: "
                f"{sorted(UNIVERSAL_OUTPUT_FIELDS)}"
            )

    # Universal field type checks (hooks.md L601-606).
    if "continue" in payload and not isinstance(payload["continue"], bool):
        report.major(
            f"'continue' must be a boolean, got "
            f"{type(payload['continue']).__name__}"
        )
    if "suppressOutput" in payload and not isinstance(
        payload["suppressOutput"], bool
    ):
        report.major(
            f"'suppressOutput' must be a boolean, got "
            f"{type(payload['suppressOutput']).__name__}"
        )
    if "stopReason" in payload and not isinstance(payload["stopReason"], str):
        report.major(
            f"'stopReason' must be a string, got "
            f"{type(payload['stopReason']).__name__}"
        )
    if "systemMessage" in payload and not isinstance(
        payload["systemMessage"], str
    ):
        report.major(
            f"'systemMessage' must be a string, got "
            f"{type(payload['systemMessage']).__name__}"
        )

    # hookSpecificOutput — event-specific validation.
    hso = payload.get("hookSpecificOutput")
    if hso is not None:
        _validate_hook_specific_output(event_name, hso, report)

    # Top-level decision handling (PostToolUse, Stop, etc. — hooks.md L601).
    if "decision" in payload:
        _validate_top_level_decision(event_name, payload, report)

    # If nothing was flagged, record a PASS.
    if not (report.has_critical or report.has_major or report.has_minor):
        report.passed(f"Payload for event {event_name!r} is well-formed")

    return report


def _validate_hook_specific_output(
    event_name: str, hso: Any, report: ValidationReport
) -> None:
    """Validate the ``hookSpecificOutput`` dict for the given event."""
    if not isinstance(hso, dict):
        report.critical(
            f"hookSpecificOutput must be a JSON object, got "
            f"{type(hso).__name__}"
        )
        return

    # ``hookEventName`` is required on hookSpecificOutput per the per-event
    # examples in hooks.md (every event that uses hookSpecificOutput shows it).
    reported_event = hso.get("hookEventName")
    if reported_event is None:
        report.major(
            "hookSpecificOutput is missing required field 'hookEventName'"
        )
    elif reported_event != event_name:
        report.major(
            f"hookSpecificOutput.hookEventName={reported_event!r} does not "
            f"match --event {event_name!r}"
        )

    # Per-event specific fields.
    allowed = HOOK_OUTPUT_EVENT_FIELDS.get(event_name, frozenset())
    # hookEventName is always allowed; so is every allowed field for the event.
    allowed_with_name = allowed | {"hookEventName"}
    for key in hso:
        if key not in allowed_with_name:
            report.nit(
                f"Unknown hookSpecificOutput field for {event_name!r}: "
                f"{key!r}. Spec-allowed: {sorted(allowed_with_name)}"
            )

    # Event-specific deep checks.
    if event_name == "PreToolUse":
        _validate_pretooluse_hso(hso, report)
    elif event_name == "PermissionRequest":
        _validate_permission_request_hso(hso, report)
    elif event_name == "PermissionDenied":
        _validate_permission_denied_hso(hso, report)
    elif event_name in {"Elicitation", "ElicitationResult"}:
        _validate_elicitation_hso(event_name, hso, report)
    elif event_name == "SessionStart":
        _validate_session_start_hso(hso, report)
    elif event_name in {"CwdChanged", "FileChanged"}:
        _validate_watch_paths_hso(event_name, hso, report)
    elif event_name == "WorktreeCreate":
        _validate_worktree_create_hso(hso, report)


def _validate_pretooluse_hso(hso: dict[str, Any], report: ValidationReport) -> None:
    """Validate PreToolUse hookSpecificOutput (hooks.md L982-987)."""
    decision = hso.get("permissionDecision")
    if decision is not None:
        if not isinstance(decision, str):
            report.major(
                f"PreToolUse permissionDecision must be a string, got "
                f"{type(decision).__name__}"
            )
        elif decision not in PRETOOLUSE_DECISIONS:
            report.major(
                f"Unknown PreToolUse permissionDecision: {decision!r}. "
                f"Expected one of: {sorted(PRETOOLUSE_DECISIONS)} "
                f"(hooks.md L984)"
            )

    reason = hso.get("permissionDecisionReason")
    if reason is not None and not isinstance(reason, str):
        report.major(
            f"PreToolUse permissionDecisionReason must be a string, got "
            f"{type(reason).__name__}"
        )

    updated_input = hso.get("updatedInput")
    if updated_input is not None and not isinstance(updated_input, dict):
        report.major(
            f"PreToolUse updatedInput must be an object, got "
            f"{type(updated_input).__name__}"
        )

    additional_ctx = hso.get("additionalContext")
    if additional_ctx is not None and not isinstance(additional_ctx, str):
        report.major(
            f"PreToolUse additionalContext must be a string, got "
            f"{type(additional_ctx).__name__}"
        )


def _validate_permission_request_hso(
    hso: dict[str, Any], report: ValidationReport
) -> None:
    """Validate PermissionRequest hookSpecificOutput (hooks.md L1089-1113)."""
    # PermissionRequest nests the decision under ``decision`` in hso.
    decision = hso.get("decision")
    if decision is None:
        # Allowed: hso may carry only metadata; decision may be top-level.
        return

    if not isinstance(decision, dict):
        report.major(
            f"PermissionRequest hookSpecificOutput.decision must be an "
            f"object, got {type(decision).__name__}"
        )
        return

    behavior = decision.get("behavior")
    if behavior is None:
        report.major(
            "PermissionRequest decision.behavior is required (hooks.md L1093)"
        )
    elif not isinstance(behavior, str):
        report.major(
            f"PermissionRequest decision.behavior must be a string, got "
            f"{type(behavior).__name__}"
        )
    elif behavior not in PERMISSION_REQUEST_BEHAVIORS:
        report.major(
            f"Unknown PermissionRequest behavior: {behavior!r}. "
            f"Expected one of: {sorted(PERMISSION_REQUEST_BEHAVIORS)} "
            f"(hooks.md L1093)"
        )

    updated_perms = decision.get("updatedPermissions")
    if updated_perms is not None:
        _validate_permission_updates(updated_perms, report)


def _validate_permission_updates(
    updates: Any, report: ValidationReport
) -> None:
    """Validate an updatedPermissions list (hooks.md L1115-1141)."""
    if not isinstance(updates, list):
        report.major(
            f"updatedPermissions must be a list, got {type(updates).__name__}"
        )
        return

    for i, entry in enumerate(updates):
        if not isinstance(entry, dict):
            report.major(
                f"updatedPermissions[{i}] must be an object, got "
                f"{type(entry).__name__}"
            )
            continue

        type_ = entry.get("type")
        if type_ is None:
            report.major(f"updatedPermissions[{i}] missing required 'type'")
            continue

        if type_ not in PERMISSION_UPDATE_TYPES:
            report.major(
                f"Unknown permission update type at "
                f"updatedPermissions[{i}]: {type_!r}. "
                f"Expected one of: {sorted(PERMISSION_UPDATE_TYPES)} "
                f"(hooks.md L1119-1126)"
            )
            continue

        # Per-type field checks.
        if type_ in {"addRules", "replaceRules", "removeRules"}:
            behavior = entry.get("behavior")
            if behavior is not None and behavior not in PERMISSION_BEHAVIORS:
                report.major(
                    f"Unknown behavior at updatedPermissions[{i}]: "
                    f"{behavior!r}. Expected: "
                    f"{sorted(PERMISSION_BEHAVIORS)} (hooks.md L1121)"
                )
        if type_ == "setMode":
            mode = entry.get("mode")
            if mode is not None and mode not in PERMISSION_MODES:
                report.major(
                    f"Unknown mode at updatedPermissions[{i}]: {mode!r}. "
                    f"Expected: {sorted(PERMISSION_MODES)} (hooks.md L1124)"
                )

        destination = entry.get("destination")
        if destination is not None and destination not in PERMISSION_DESTINATIONS:
            report.major(
                f"Unknown destination at updatedPermissions[{i}]: "
                f"{destination!r}. Expected: "
                f"{sorted(PERMISSION_DESTINATIONS)} (hooks.md L1134-1139)"
            )


def _validate_permission_denied_hso(
    hso: dict[str, Any], report: ValidationReport
) -> None:
    """Validate PermissionDenied hookSpecificOutput (hooks.md L1278)."""
    retry = hso.get("retry")
    if retry is not None and not isinstance(retry, bool):
        report.major(
            f"PermissionDenied retry must be a boolean, got "
            f"{type(retry).__name__}"
        )


def _validate_elicitation_hso(
    event_name: str, hso: dict[str, Any], report: ValidationReport
) -> None:
    """Validate Elicitation/ElicitationResult HSO (hooks.md L2021-2024, L2067-2070)."""
    action = hso.get("action")
    if action is not None:
        if not isinstance(action, str):
            report.major(
                f"{event_name} action must be a string, got "
                f"{type(action).__name__}"
            )
        elif action not in ELICITATION_ACTIONS:
            report.major(
                f"Unknown {event_name} action: {action!r}. "
                f"Expected one of: {sorted(ELICITATION_ACTIONS)}"
            )


def _validate_session_start_hso(
    hso: dict[str, Any], report: ValidationReport
) -> None:
    """Validate SessionStart hookSpecificOutput (hooks.md L717)."""
    ctx = hso.get("additionalContext")
    if ctx is not None and not isinstance(ctx, str):
        report.major(
            f"SessionStart additionalContext must be a string, got "
            f"{type(ctx).__name__}"
        )


def _validate_watch_paths_hso(
    event_name: str, hso: dict[str, Any], report: ValidationReport
) -> None:
    """Validate CwdChanged/FileChanged watchPaths (hooks.md L1722, L1765)."""
    watch_paths = hso.get("watchPaths")
    if watch_paths is None:
        return
    if not isinstance(watch_paths, list):
        report.major(
            f"{event_name} watchPaths must be a list, got "
            f"{type(watch_paths).__name__}"
        )
        return
    for i, p in enumerate(watch_paths):
        if not isinstance(p, str):
            report.major(
                f"{event_name} watchPaths[{i}] must be a string, got "
                f"{type(p).__name__}"
            )


def _validate_worktree_create_hso(
    hso: dict[str, Any], report: ValidationReport
) -> None:
    """Validate WorktreeCreate hookSpecificOutput (hooks.md L1819)."""
    path = hso.get("worktreePath")
    if path is not None and not isinstance(path, str):
        report.major(
            f"WorktreeCreate worktreePath must be a string, got "
            f"{type(path).__name__}"
        )


def _validate_top_level_decision(
    event_name: str, payload: dict[str, Any], report: ValidationReport
) -> None:
    """Validate legacy top-level ``decision``/``reason`` (hooks.md L601, L1010)."""
    decision = payload.get("decision")

    # PreToolUse deprecation (hooks.md L1010): top-level decision is
    # deprecated; "approve" → "allow", "block" → "deny".
    if event_name == "PreToolUse":
        report.warning(
            "PreToolUse top-level 'decision' is deprecated; use "
            "hookSpecificOutput.permissionDecision instead (hooks.md L1010)"
        )

    # Events that legitimately use top-level "decision: block".
    block_only_events = {
        "PostToolUse",
        "Stop",
        "SubagentStop",
        "ConfigChange",
        "PreCompact",
        "UserPromptSubmit",
    }

    if decision is not None and not isinstance(decision, str):
        report.major(
            f"Top-level 'decision' must be a string, got "
            f"{type(decision).__name__}"
        )
    elif decision is not None and event_name in block_only_events:
        if decision != "block":
            report.major(
                f"{event_name} top-level 'decision' must be 'block' "
                f"(or omitted), got {decision!r} (hooks.md L601)"
            )


# =============================================================================
# CLI
# =============================================================================


def _print_hook_output_report(report: ValidationReport, verbose: bool = False) -> None:
    """Print a hook-output validation report (summary + details)."""
    print_report_summary(report, "Hook Output Validation Report")
    print_results_by_level(report, verbose=verbose)


def _load_payload(args: argparse.Namespace) -> Any:
    """Load JSON payload from file or stdin. Exits on parse errors."""
    if args.stdin:
        raw = sys.stdin.read()
        source = "<stdin>"
    else:
        if args.payload is None:
            print(
                "Error: either a payload file or --stdin is required",
                file=sys.stderr,
            )
            sys.exit(2)
        path = Path(args.payload).resolve()
        if not path.is_file():
            print(f"Error: payload file not found: {path}", file=sys.stderr)
            sys.exit(2)
        raw = path.read_text(encoding="utf-8")
        source = str(path)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(
            f"Error: ill-formed JSON in {source}: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


def main() -> int:
    """CLI entry point for hook output payload validation."""
    parser = argparse.ArgumentParser(
        description=(
            "Validate a hook output JSON payload against the per-event "
            "decision-control table in hooks.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  validate_hook_output.py --event PreToolUse payload.json
  cat payload.json | validate_hook_output.py --event SessionStart --stdin
  validate_hook_output.py --event Stop payload.json --report out.md

Exit codes:
  0 - All checks passed
  1 - CRITICAL issues (ill-formed JSON / wrong shape)
  2 - MAJOR issues (unknown decision / missing required fields)
  3 - MINOR issues (unknown universal fields)
        """,
    )
    parser.add_argument(
        "--event",
        required=True,
        choices=sorted(VALID_HOOK_EVENTS),
        help="Hook event name that produced this payload",
    )
    parser.add_argument(
        "payload",
        nargs="?",
        default=None,
        help="Path to JSON payload file (omit when using --stdin)",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read JSON payload from stdin instead of a file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show all results including INFO and PASSED",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--report-path",
        "--report",
        dest="report_path",
        type=str,
        default=None,
        help="Save detailed report to file; print only summary to stdout",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Strict mode — NIT issues also block validation",
    )

    args = parser.parse_args()
    payload = _load_payload(args)

    report = validate_output_payload(args.event, payload)

    if args.json:
        output = report.to_dict()
        output["event"] = args.event
        print(json.dumps(output, indent=2))
    elif args.report_path:
        save_report_and_print_summary(
            report,
            Path(args.report_path),
            f"Hook Output Validation: {args.event}",
            _print_hook_output_report,
            args.verbose,
        )
    else:
        _print_hook_output_report(report, verbose=args.verbose)

    if args.strict:
        return report.exit_code_strict()
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
