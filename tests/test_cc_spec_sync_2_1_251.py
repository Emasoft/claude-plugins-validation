#!/usr/bin/env python3
"""
CC spec-drift sync (Claude Code v2.1.251) — two-sided regression locks.

GitHub issue #222: CPV rejected the `PreModelSwitch` / `PostModelSwitch` hook
events (added in CC v2.1.251) as CRITICAL "Unknown hook event". Every new
value below is verified two-sided: the newly-accepted shape is now accepted,
AND a positive control proves the same code path still REJECTS a bogus
sibling / an unrelated event — a one-sided "it is accepted now" assertion
would also pass if the whole check were simply disabled.

Doc ground truth (fetched from hooks.md, not recalled):
- Both events TAKE a matcher (the canonical name of the target model,
  ignoring any `[1m]` suffix) — neither belongs in EVENTS_WITHOUT_MATCHERS.
- `PreModelSwitch` "runs `command`, `http`, and `mcp_tool` hooks only, so the
  `prompt` and `agent` defaults don't apply" — tier 2 (HOOK_EVENTS_NO_PROMPT_OR_AGENT).
- `PostModelSwitch` "can't block, because the model has already changed" —
  the doc states NO hook-type restriction for it, so it stays in the
  unrestricted tier (full 5-type set), unlike its sibling.
- `PreModelSwitch` accepts a top-level `decision: "block"` (or exit code 2)
  to cancel the switch — it belongs in TOP_LEVEL_BLOCK_EVENTS.
- `PreModelSwitch` hookSpecificOutput accepts `permissionDecision` ("allow" /
  "deny" / "ask" — NOT "defer") + `permissionDecisionReason`.
- `PostModelSwitch` hookSpecificOutput accepts only `additionalContext`.

Explicitly rejected (per the dispatching orchestrator's directive): the
"unknown events degrade to WARNING" suggestion. An unknown hook event never
fires at runtime, so it stays CRITICAL — only these two named events change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (
    HOOK_EVENTS_NO_PROMPT_OR_AGENT,
    VALID_HOOK_EVENTS,
    hook_types_allowed_for_event,
)
from validate_hook import EVENTS_WITHOUT_MATCHERS, validate_hooks
from validate_hook_output import (
    HOOK_OUTPUT_EVENT_FIELDS,
    PREMODELSWITCH_DECISIONS,
    TOP_LEVEL_BLOCK_EVENTS,
    validate_output_payload,
)


# ---------------------------------------------------------------------------
# S1 — PreModelSwitch / PostModelSwitch are valid hook events (v2.1.251)
# ---------------------------------------------------------------------------
class TestS1ModelSwitchEventsValid:
    """`PreModelSwitch` / `PostModelSwitch` (hooks.md, v2.1.251) are valid hook events."""

    def test_pre_model_switch_is_valid_event(self) -> None:
        assert "PreModelSwitch" in VALID_HOOK_EVENTS

    def test_post_model_switch_is_valid_event(self) -> None:
        assert "PostModelSwitch" in VALID_HOOK_EVENTS

    def test_bogus_event_still_rejected(self) -> None:
        """Positive control: made-up siblings are still unknown events."""
        assert "PreModelSwitchXYZ" not in VALID_HOOK_EVENTS
        assert "ModelSwitch" not in VALID_HOOK_EVENTS
        assert "PostFooBar" not in VALID_HOOK_EVENTS

    def test_both_events_have_an_output_schema(self) -> None:
        """A registered event without its output schema is an incomplete change.

        `test_hook_output_event_fields_covers_all_events` enforces the same
        invariant repo-wide; this asserts the specific rows so a failure
        names the event directly.
        """
        assert "PreModelSwitch" in HOOK_OUTPUT_EVENT_FIELDS
        assert "PostModelSwitch" in HOOK_OUTPUT_EVENT_FIELDS

    def test_pre_model_switch_output_schema(self) -> None:
        """PreModelSwitch accepts permissionDecision + permissionDecisionReason only."""
        assert HOOK_OUTPUT_EVENT_FIELDS["PreModelSwitch"] == frozenset(
            {"permissionDecision", "permissionDecisionReason"}
        )

    def test_post_model_switch_output_schema(self) -> None:
        """PostModelSwitch accepts additionalContext only — it can never block."""
        assert HOOK_OUTPUT_EVENT_FIELDS["PostModelSwitch"] == frozenset({"additionalContext"})


# ---------------------------------------------------------------------------
# S2 — both events TAKE matchers (v2.1.251)
# ---------------------------------------------------------------------------
class TestS2ModelSwitchEventsTakeMatchers:
    """Both events match against the canonical name of the target model.

    Listing either in EVENTS_WITHOUT_MATCHERS would make CPV flag a CORRECT
    hook config that scopes on the model being switched to/from.
    """

    def test_pre_model_switch_not_in_matcherless_set(self) -> None:
        assert "PreModelSwitch" not in EVENTS_WITHOUT_MATCHERS

    def test_post_model_switch_not_in_matcherless_set(self) -> None:
        assert "PostModelSwitch" not in EVENTS_WITHOUT_MATCHERS

    def test_matcherless_set_is_non_vacuous(self) -> None:
        """Control: genuinely matcher-less events ARE in the set."""
        assert "Stop" in EVENTS_WITHOUT_MATCHERS
        assert "UserPromptSubmit" in EVENTS_WITHOUT_MATCHERS


# ---------------------------------------------------------------------------
# S3 — PreModelSwitch is command/http/mcp_tool ONLY; PostModelSwitch is unrestricted
# ---------------------------------------------------------------------------
class TestS3ModelSwitchHookTypeRestriction:
    """Per hooks.md: "PreModelSwitch runs command, http, and mcp_tool hooks
    only, so the prompt and agent defaults don't apply." PostModelSwitch
    carries no such statement, so it stays in the unrestricted (full 5-type)
    tier — unlike its sibling.
    """

    def test_pre_model_switch_in_no_prompt_or_agent_tier(self) -> None:
        assert "PreModelSwitch" in HOOK_EVENTS_NO_PROMPT_OR_AGENT

    def test_pre_model_switch_allowed_types(self) -> None:
        assert hook_types_allowed_for_event("PreModelSwitch") == frozenset({"command", "http", "mcp_tool"})

    def test_post_model_switch_not_restricted(self) -> None:
        """Positive control: PostModelSwitch stays OUT of the restricted tier."""
        assert "PostModelSwitch" not in HOOK_EVENTS_NO_PROMPT_OR_AGENT

    def test_post_model_switch_allowed_types_is_full_set(self) -> None:
        assert hook_types_allowed_for_event("PostModelSwitch") == frozenset(
            {"command", "http", "mcp_tool", "prompt", "agent"}
        )

    def test_sibling_command_only_tier_unaffected(self) -> None:
        """Control: the tier-3 command-only events are untouched by this sync."""
        assert hook_types_allowed_for_event("SessionStart") == frozenset({"command", "mcp_tool"})


# ---------------------------------------------------------------------------
# S4 — PreModelSwitch top-level decision:"block" + permissionDecision
# ---------------------------------------------------------------------------
class TestS4PreModelSwitchDecisionControl:
    """PreModelSwitch cancels a switch via exit 2 / top-level decision:"block",
    or via hookSpecificOutput.permissionDecision ("allow"/"deny"/"ask" — NOT
    "defer", unlike PreToolUse's superset).
    """

    def test_pre_model_switch_in_top_level_block_events(self) -> None:
        assert "PreModelSwitch" in TOP_LEVEL_BLOCK_EVENTS

    def test_post_model_switch_not_in_top_level_block_events(self) -> None:
        """Positive control: PostModelSwitch cannot block — it already changed the model."""
        assert "PostModelSwitch" not in TOP_LEVEL_BLOCK_EVENTS

    def test_pre_model_switch_decisions_exclude_defer(self) -> None:
        assert PREMODELSWITCH_DECISIONS == frozenset({"allow", "deny", "ask"})
        assert "defer" not in PREMODELSWITCH_DECISIONS

    def test_pre_model_switch_valid_permission_decision_is_clean(self) -> None:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PreModelSwitch",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Opus 4.6 is retired for this project.",
            }
        }
        report = validate_output_payload("PreModelSwitch", payload)
        assert not report.has_critical
        assert not report.has_major

    def test_pre_model_switch_defer_is_rejected(self) -> None:
        """Positive control: 'defer' is a real PreToolUse value but NOT valid here."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PreModelSwitch",
                "permissionDecision": "defer",
            }
        }
        report = validate_output_payload("PreModelSwitch", payload)
        assert report.has_major

    def test_post_model_switch_additional_context_is_clean(self) -> None:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostModelSwitch",
                "additionalContext": "On Opus, delegate implementation work to subagents.",
            }
        }
        report = validate_output_payload("PostModelSwitch", payload)
        assert not report.has_critical
        assert not report.has_major

    def test_post_model_switch_permission_decision_is_unknown_field(self) -> None:
        """Positive control: permissionDecision is not a valid PostModelSwitch field."""
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PostModelSwitch",
                "permissionDecision": "deny",
            }
        }
        report = validate_output_payload("PostModelSwitch", payload)
        assert any("Unknown hookSpecificOutput field" in r.message for r in report.results if r.level == "NIT")


# ---------------------------------------------------------------------------
# S5 — end-to-end hooks.json (the exact CONTEXT.md acceptance criteria)
# ---------------------------------------------------------------------------
class TestS5EndToEndHooksJson:
    """A real hooks.json using PostModelSwitch passes with 0 CRITICAL, and a
    still-unknown event (PostFooBar) still fires CRITICAL.
    """

    def test_post_model_switch_hooks_json_passes_clean(self, tmp_path: Path) -> None:
        hooks_data = {
            "hooks": {
                "PostModelSwitch": [
                    {
                        "matcher": ".*opus.*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "${CLAUDE_PLUGIN_ROOT}/scripts/opus-guidance.sh",
                            }
                        ],
                    }
                ]
            }
        }
        hooks_file = tmp_path / "hooks.json"
        hooks_file.write_text(json.dumps(hooks_data))
        report = validate_hooks(hooks_file, plugin_root=tmp_path)
        assert not report.has_critical

    def test_pre_model_switch_hooks_json_passes_clean(self, tmp_path: Path) -> None:
        hooks_data = {
            "hooks": {
                "PreModelSwitch": [
                    {
                        "matcher": "claude-opus-4-6",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "${CLAUDE_PLUGIN_ROOT}/scripts/block-opus-46.sh",
                            }
                        ],
                    }
                ]
            }
        }
        hooks_file = tmp_path / "hooks.json"
        hooks_file.write_text(json.dumps(hooks_data))
        report = validate_hooks(hooks_file, plugin_root=tmp_path)
        assert not report.has_critical

    def test_still_unknown_event_fires_critical(self, tmp_path: Path) -> None:
        """Positive control: an unknown event never fires at runtime, and stays CRITICAL.

        Explicitly rejects the "unknown events degrade to WARNING" suggestion
        — only PreModelSwitch/PostModelSwitch were registered by this sync.
        """
        hooks_data = {
            "hooks": {
                "PostFooBar": [
                    {"matcher": "*", "hooks": [{"type": "command", "command": "echo nope"}]},
                ]
            }
        }
        hooks_file = tmp_path / "hooks.json"
        hooks_file.write_text(json.dumps(hooks_data))
        report = validate_hooks(hooks_file, plugin_root=tmp_path)
        assert report.has_critical
        assert any(
            "Unknown hook event" in r.message and "PostFooBar" in r.message
            for r in report.results
            if r.level == "CRITICAL"
        )

    def test_pre_model_switch_prompt_type_is_rejected(self, tmp_path: Path) -> None:
        """Positive control: a prompt-type PreModelSwitch hook must be CRITICAL."""
        hooks_data = {
            "hooks": {
                "PreModelSwitch": [
                    {"matcher": "*", "hooks": [{"type": "prompt", "prompt": "Should I allow this?"}]},
                ]
            }
        }
        hooks_file = tmp_path / "hooks.json"
        hooks_file.write_text(json.dumps(hooks_data))
        report = validate_hooks(hooks_file, plugin_root=tmp_path)
        assert report.has_critical
