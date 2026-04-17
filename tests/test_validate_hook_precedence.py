#!/usr/bin/env python3
"""
Tests for validate_hook_precedence.py

Covers group_hooks_by_event_matcher, extract_inline_permission_decision,
detect_precedence_conflicts, validate_hook_precedence, precedence ordering,
and the "all hooks share one decision" happy path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_hook_precedence import (
    NO_MATCHER_SENTINEL,
    PRECEDENCE_ORDER,
    PRECEDENCE_RANK,
    _resolve_by_precedence,
    detect_precedence_conflicts,
    extract_inline_permission_decision,
    group_hooks_by_event_matcher,
    validate_hook_precedence,
)


def _inline_hook(decision: str) -> dict[str, object]:
    """Build a minimal hook entry whose inline decision is ``decision``."""
    return {
        "type": "command",
        "command": "echo inline",
        "hookSpecificOutput": {"permissionDecision": decision},
    }


def _exec_hook(command: str = "echo runtime") -> dict[str, object]:
    """Build a hook entry with no inline decision (runtime exec script)."""
    return {"type": "command", "command": command}


def _hooks_doc(
    event: str,
    matcher: str,
    hooks: list[dict[str, object]],
) -> dict[str, object]:
    """Wrap a list of hook entries into a complete hooks.json document."""
    return {"hooks": {event: [{"matcher": matcher, "hooks": hooks}]}}


def test_two_pretooluse_bash_hooks_allow_and_deny_produces_minor(tmp_path: Path) -> None:
    """Two PreToolUse[Bash] hooks with allow + deny produce a MINOR finding."""
    hooks_doc = _hooks_doc("PreToolUse", "Bash", [_inline_hook("allow"), _inline_hook("deny")])
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(json.dumps(hooks_doc), encoding="utf-8")

    report = validate_hook_precedence(hooks_path)

    assert report.has_minor is True
    minor_messages = [r.message for r in report.results if r.level == "MINOR"]
    assert len(minor_messages) == 1
    message = minor_messages[0]
    assert "(PreToolUse, Bash)" in message
    assert "2 hooks" in message
    assert "'allow'" in message and "'deny'" in message
    # Precedence: deny beats allow
    assert "resolves to deny" in message


def test_two_pretooluse_hooks_on_different_matchers_pass(tmp_path: Path) -> None:
    """Two PreToolUse hooks on different matchers do not conflict."""
    hooks_doc = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [_inline_hook("allow")]},
                {"matcher": "Read", "hooks": [_inline_hook("deny")]},
            ]
        }
    }
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(json.dumps(hooks_doc), encoding="utf-8")

    report = validate_hook_precedence(hooks_path)

    assert report.has_minor is False
    assert report.has_critical is False
    # Exactly one PASSED saying no conflicts
    passed_messages = [r.message for r in report.results if r.level == "PASSED"]
    assert any("No cross-hook precedence conflicts" in m for m in passed_messages)


def test_different_events_on_same_matcher_pass(tmp_path: Path) -> None:
    """One PreToolUse and one PostToolUse on the same matcher do not aggregate."""
    hooks_doc = {
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [_inline_hook("allow")]}],
            "PostToolUse": [{"matcher": "Bash", "hooks": [_inline_hook("deny")]}],
        }
    }
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(json.dumps(hooks_doc), encoding="utf-8")

    report = validate_hook_precedence(hooks_path)

    assert report.has_minor is False
    assert report.exit_code == 0


def test_three_hooks_all_allow_pass(tmp_path: Path) -> None:
    """Three PreToolUse[Bash] hooks all declaring 'allow' do not conflict."""
    hooks_doc = _hooks_doc(
        "PreToolUse",
        "Bash",
        [_inline_hook("allow"), _inline_hook("allow"), _inline_hook("allow")],
    )
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(json.dumps(hooks_doc), encoding="utf-8")

    report = validate_hook_precedence(hooks_path)

    assert report.has_minor is False
    assert report.has_critical is False
    assert any("No cross-hook precedence" in r.message for r in report.results if r.level == "PASSED")


def test_all_exec_scripts_yield_info_not_minor(tmp_path: Path) -> None:
    """When >=2 hooks are exec scripts with no inline decision, INFO (not MINOR)."""
    hooks_doc = _hooks_doc("PreToolUse", "Bash", [_exec_hook(), _exec_hook()])
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(json.dumps(hooks_doc), encoding="utf-8")

    report = validate_hook_precedence(hooks_path)

    assert report.has_minor is False
    info_messages = [r.message for r in report.results if r.level == "INFO"]
    assert len(info_messages) == 1
    assert "unknowable" in info_messages[0]


def test_mixed_exec_and_inline_deny_produces_info_with_precedence(tmp_path: Path) -> None:
    """A mix of exec script + inline deny emits INFO mentioning precedence."""
    hooks_doc = _hooks_doc("PreToolUse", "Bash", [_exec_hook(), _inline_hook("deny")])
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(json.dumps(hooks_doc), encoding="utf-8")

    report = validate_hook_precedence(hooks_path)

    assert report.has_minor is False
    info_messages = [r.message for r in report.results if r.level == "INFO"]
    assert len(info_messages) == 1
    msg = info_messages[0]
    assert "'deny'" in msg
    assert "exec scripts" in msg
    assert "resolves to deny" in msg


def test_precedence_ordering_deny_beats_all() -> None:
    """Precedence: deny wins over any of {defer, ask, allow}."""
    # Constant itself: deny is first.
    assert PRECEDENCE_ORDER[0] == "deny"
    assert PRECEDENCE_RANK["deny"] < PRECEDENCE_RANK["defer"]
    assert PRECEDENCE_RANK["deny"] < PRECEDENCE_RANK["ask"]
    assert PRECEDENCE_RANK["deny"] < PRECEDENCE_RANK["allow"]
    # Functional resolution on every pair that contains deny:
    assert _resolve_by_precedence({"deny", "defer"}) == "deny"
    assert _resolve_by_precedence({"deny", "ask"}) == "deny"
    assert _resolve_by_precedence({"deny", "allow"}) == "deny"
    assert _resolve_by_precedence({"deny", "defer", "ask", "allow"}) == "deny"


def test_precedence_ordering_defer_beats_ask_and_allow() -> None:
    """Precedence: defer wins over {ask, allow} when deny is absent."""
    assert PRECEDENCE_RANK["defer"] < PRECEDENCE_RANK["ask"]
    assert PRECEDENCE_RANK["defer"] < PRECEDENCE_RANK["allow"]
    assert _resolve_by_precedence({"defer", "ask"}) == "defer"
    assert _resolve_by_precedence({"defer", "allow"}) == "defer"
    assert _resolve_by_precedence({"defer", "ask", "allow"}) == "defer"


def test_precedence_ordering_ask_beats_allow() -> None:
    """Precedence: ask wins over allow when no higher-priority decisions exist."""
    assert PRECEDENCE_RANK["ask"] < PRECEDENCE_RANK["allow"]
    assert _resolve_by_precedence({"ask", "allow"}) == "ask"


def test_empty_hooks_json_passes(tmp_path: Path) -> None:
    """An empty hooks.json (no hooks section) produces PASSED."""
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(json.dumps({}), encoding="utf-8")

    report = validate_hook_precedence(hooks_path)

    assert report.has_critical is False
    assert report.has_minor is False
    assert report.exit_code == 0
    assert any("No cross-hook precedence" in r.message for r in report.results if r.level == "PASSED")


def test_missing_permission_decision_silently_skipped() -> None:
    """A hookSpecificOutput without permissionDecision yields None from extractor."""
    hook = {
        "type": "command",
        "command": "echo x",
        "hookSpecificOutput": {"reason": "audit"},
    }
    assert extract_inline_permission_decision(hook) is None


def test_extract_decision_top_level_shorthand() -> None:
    """A shorthand top-level permissionDecision string also counts as inline."""
    hook = {"type": "command", "command": "echo x", "permissionDecision": "ask"}
    assert extract_inline_permission_decision(hook) == "ask"


def test_extract_decision_rejects_unknown_string() -> None:
    """An unknown decision string is ignored (treated as unknowable)."""
    hook = {
        "type": "command",
        "command": "echo x",
        "hookSpecificOutput": {"permissionDecision": "approve-maybe"},
    }
    assert extract_inline_permission_decision(hook) is None


def test_group_hooks_aggregates_across_blocks() -> None:
    """Two PreToolUse blocks on the same matcher merge into one group."""
    hooks_doc = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [_inline_hook("allow")]},
                {"matcher": "Bash", "hooks": [_inline_hook("deny")]},
            ]
        }
    }
    groups = group_hooks_by_event_matcher(hooks_doc)
    assert ("PreToolUse", "Bash") in groups
    assert len(groups[("PreToolUse", "Bash")]) == 2


def test_group_hooks_missing_matcher_uses_sentinel() -> None:
    """Hooks for events without a matcher collapse onto the sentinel key."""
    hooks_doc: dict[str, object] = {
        "hooks": {
            "Stop": [
                {"hooks": [_inline_hook("allow")]},
                {"hooks": [_inline_hook("deny")]},
            ]
        }
    }
    groups = group_hooks_by_event_matcher(hooks_doc)
    assert ("Stop", NO_MATCHER_SENTINEL) in groups
    assert len(groups[("Stop", NO_MATCHER_SENTINEL)]) == 2


def test_detect_precedence_conflicts_skips_single_hook_groups() -> None:
    """A group with only 1 hook never produces a finding."""
    groups = {("PreToolUse", "Bash"): [_inline_hook("allow")]}
    findings = detect_precedence_conflicts(groups)
    assert findings == []


def test_invalid_json_reports_critical(tmp_path: Path) -> None:
    """Malformed JSON in hooks.json produces CRITICAL."""
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text("{not-json", encoding="utf-8")
    report = validate_hook_precedence(hooks_path)
    assert report.has_critical is True


def test_missing_file_reports_critical(tmp_path: Path) -> None:
    """A non-existent hooks.json produces CRITICAL."""
    report = validate_hook_precedence(tmp_path / "nope.json")
    assert report.has_critical is True


def test_message_format_matches_spec_example(tmp_path: Path) -> None:
    """The spec example format is emitted verbatim for the canonical case."""
    hooks_doc = _hooks_doc(
        "PreToolUse",
        "Bash",
        [_inline_hook("allow"), _inline_hook("allow"), _inline_hook("deny")],
    )
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(json.dumps(hooks_doc), encoding="utf-8")

    report = validate_hook_precedence(hooks_path)
    minor_messages = [r.message for r in report.results if r.level == "MINOR"]
    assert len(minor_messages) == 1
    msg = minor_messages[0]
    # Spec format: "(PreToolUse, Bash): 3 hooks; inline decisions {...} — precedence deny>defer>ask>allow resolves to deny"
    assert msg.startswith("(PreToolUse, Bash): 3 hooks;")
    assert "inline decisions {'allow', 'deny'}" in msg
    assert "precedence deny>defer>ask>allow resolves to deny" in msg
