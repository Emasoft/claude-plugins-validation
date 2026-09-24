#!/usr/bin/env python3
"""CC v2.1.280: PermissionRequest does not support `agent` hooks (two-sided).

hooks.md: "`PermissionRequest` supports `command`, `http`, `mcp_tool`, and
`prompt` hooks but not `agent` hooks. If you configure an agent hook on this
event, Claude Code skips it" → the hook is dead config → MAJOR.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import pytest  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402
from validate_hook import HookValidationReport, validate_hooks, validate_single_hook  # noqa: E402
from validate_mcp import validate_mcp_server  # noqa: E402

_NEEDLE = "does not support 'agent' hooks"

_HOOKS = {
    "agent": {"type": "agent", "prompt": "Decide whether to allow this."},
    "prompt": {"type": "prompt", "prompt": "Decide whether to allow this."},
    "command": {"type": "command", "command": "echo ok"},
    "http": {"type": "http", "url": "https://example.com/hook"},
}


def _findings(hook: dict, event: str) -> list:
    report = HookValidationReport(hook_path="hooks/hooks.json")
    validate_single_hook(hook, event, None, report)
    return [r for r in report.results if _NEEDLE in r.message]


def test_permission_request_agent_hook_is_major() -> None:
    hits = _findings(_HOOKS["agent"], "PermissionRequest")
    assert len(hits) == 1
    assert hits[0].level == "MAJOR"


@pytest.mark.parametrize("hook_type", ["command", "http", "prompt"])
def test_permission_request_supported_types_not_flagged(hook_type: str) -> None:
    assert _findings(_HOOKS[hook_type], "PermissionRequest") == []


def test_agent_hook_on_other_event_not_flagged() -> None:
    assert _findings(_HOOKS["agent"], "PreToolUse") == []


def test_mcp_url_only_without_type_is_critical() -> None:
    """url-only, no type, no command → CC drops it at load → one CRITICAL with CC's text."""
    report = ValidationReport()
    validate_mcp_server("srv", {"url": "https://mcp.example.com/mcp"}, report)
    crits = [r.message for r in report.results if r.level == "CRITICAL"]
    assert len(crits) == 1 and 'has a "url" but no "type"' in crits[0]


def test_mcp_command_url_without_type_falls_through_to_stdio() -> None:
    """command + url + no type → a working stdio server: no CRITICAL, 'url will be ignored' INFO."""
    report = ValidationReport()
    validate_mcp_server("srv", {"command": "node", "args": ["s.js"], "url": "https://x"}, report)
    assert not any(r.level == "CRITICAL" for r in report.results)
    assert any(r.level == "INFO" and "url will be ignored" in r.message for r in report.results)


@pytest.mark.parametrize("bad_command", ["", "   ", None])
def test_mcp_url_with_empty_or_null_command_blocks(bad_command: object) -> None:
    """url + empty/null command + no type → exactly one blocking finding, never zero."""
    report = ValidationReport()
    validate_mcp_server("srv", {"command": bad_command, "url": "https://x"}, report)
    blocking = [r for r in report.results if r.level in ("CRITICAL", "MAJOR")]
    assert len(blocking) == 1, [r.message for r in report.results]
    assert "command" in blocking[0].message


def test_fires_through_hooks_json_file(tmp_path: Path) -> None:
    """End-to-end: the rule reaches a report produced from a real hooks.json."""
    hooks_json = tmp_path / "hooks" / "hooks.json"
    hooks_json.parent.mkdir()
    hooks_json.write_text(
        json.dumps({"hooks": {"PermissionRequest": [{"matcher": "Bash", "hooks": [_HOOKS["agent"]]}]}}),
        encoding="utf-8",
    )
    report = validate_hooks(hooks_json, tmp_path)
    hits = [r for r in report.results if _NEEDLE in r.message]
    assert hits and all(r.level == "MAJOR" for r in hits)
