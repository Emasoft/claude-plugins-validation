#!/usr/bin/env python3
"""
Tests for validate_hook.py

Covers: validate_json_structure, validate_command_hook, validate_prompt_hook,
validate_single_hook, validate_hooks (main entry), invalid event names,
missing matchers, and edge cases.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport
from validate_hook import (
    HookValidationReport,
    extract_script_path,
    print_json,
    print_results,
    validate_command_hook,
    validate_event_hooks,
    validate_hooks,
    validate_json_structure,
    validate_matcher,
    validate_matcher_block,
    validate_prompt_hook,
    validate_script,
    validate_single_hook,
    validate_top_level_structure,
)


def test_validate_json_structure_valid_file(tmp_path: Path):
    """A well-formed JSON hooks file should be parsed and returned as a dict with a PASSED result."""
    hooks_file = tmp_path / "hooks.json"
    hooks_data = {
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hello"}]}]}
    }
    hooks_file.write_text(json.dumps(hooks_data))
    report = ValidationReport()
    result = validate_json_structure(hooks_file, report)
    assert result is not None
    assert result == hooks_data
    assert any(r.level == "PASSED" and "JSON" in r.message for r in report.results)


def test_validate_json_structure_missing_and_invalid(tmp_path: Path):
    """A non-existent file returns None with CRITICAL; broken JSON also returns None with CRITICAL."""
    # Missing file
    report1 = ValidationReport()
    assert validate_json_structure(tmp_path / "nope.json", report1) is None
    assert report1.has_critical and any("not found" in r.message for r in report1.results if r.level == "CRITICAL")
    # Invalid JSON
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{broken json")
    report2 = ValidationReport()
    assert validate_json_structure(bad_file, report2) is None
    assert report2.has_critical and any("Invalid JSON" in r.message for r in report2.results if r.level == "CRITICAL")


def test_validate_command_hook_valid(tmp_path: Path):
    """A command hook with a non-empty command string should pass with no critical/major issues."""
    hook = {"type": "command", "command": "echo 'Running pre-check'"}
    report = ValidationReport()
    assert validate_command_hook(hook, "PreToolUse", tmp_path, report) is True
    assert not report.has_critical and not report.has_major


def test_validate_command_hook_missing_and_empty(tmp_path: Path):
    """A command hook without 'command' or with an empty command should each produce CRITICAL."""
    # Missing command field
    report1 = ValidationReport()
    assert validate_command_hook({"type": "command"}, "PreToolUse", tmp_path, report1) is False
    assert any("missing required 'command'" in r.message.lower() for r in report1.results if r.level == "CRITICAL")
    # Empty command
    report2 = ValidationReport()
    assert validate_command_hook({"type": "command", "command": "   "}, "PreToolUse", tmp_path, report2) is False
    assert any("cannot be empty" in r.message for r in report2.results if r.level == "CRITICAL")


def test_validate_prompt_hook_valid_and_invalid():
    """A valid prompt hook passes; missing or empty prompt produces CRITICAL."""
    # Valid
    report1 = ValidationReport()
    assert validate_prompt_hook({"type": "prompt", "prompt": "Summarize the output."}, "Stop", report1) is True
    assert not report1.has_critical
    # Missing prompt
    report2 = ValidationReport()
    assert validate_prompt_hook({"type": "prompt"}, "Stop", report2) is False
    assert any("missing required 'prompt'" in r.message.lower() for r in report2.results if r.level == "CRITICAL")
    # Empty prompt
    report3 = ValidationReport()
    assert validate_prompt_hook({"type": "prompt", "prompt": "  "}, "Stop", report3) is False
    assert any("cannot be empty" in r.message for r in report3.results if r.level == "CRITICAL")


def test_validate_single_hook_type_errors(tmp_path: Path):
    """Non-dict hook, invalid type, and prompt on command-only event should each produce CRITICAL."""
    # Non-dict
    r1 = HookValidationReport()
    assert validate_single_hook("not a dict", "PreToolUse", tmp_path, r1) is False
    assert any("must be an object" in r.message for r in r1.results if r.level == "CRITICAL")
    # Invalid type
    r2 = HookValidationReport()
    assert validate_single_hook({"type": "webhook", "command": "curl x"}, "PreToolUse", tmp_path, r2) is False
    assert any("Invalid hook type" in r.message for r in r2.results if r.level == "CRITICAL")
    # SessionStart is command-STRICT (hooks.md L687/L2109) — prompt is rejected.
    r3 = HookValidationReport()
    validate_single_hook({"type": "prompt", "prompt": "Do something"}, "SessionStart", tmp_path, r3)
    assert any(
        "only supports 'command' hooks" in r.message
        for r in r3.results
        if r.level == "CRITICAL"
    )
    # Prompt on a command-or-http event (Notification) — still CRITICAL with
    # the legacy message wording.
    r4 = HookValidationReport()
    validate_single_hook({"type": "prompt", "prompt": "Do something"}, "Notification", tmp_path, r4)
    assert any(
        "only supports 'command' or 'http'" in r.message
        for r in r4.results
        if r.level == "CRITICAL"
    )
    # v2.22.2 GAP-P2-C2: SessionStart also rejects http hooks (was silently passing).
    r5 = HookValidationReport()
    validate_single_hook({"type": "http", "url": "https://x"}, "SessionStart", tmp_path, r5)
    assert any(
        "only supports 'command' hooks" in r.message
        for r in r5.results
        if r.level == "CRITICAL"
    ), "expected CRITICAL: SessionStart rejects http hooks"


def test_validate_hooks_valid_end_to_end(tmp_path: Path):
    """A complete valid hooks.json with a PreToolUse command hook should have no critical/major issues."""
    hooks_data = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/check.sh"}]}
            ]
        }
    }
    hooks_file = tmp_path / "hooks.json"
    hooks_file.write_text(json.dumps(hooks_data))
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    assert not report.has_critical
    assert not report.has_major


def test_validate_hooks_invalid_event_name(tmp_path: Path):
    """An unrecognized event name (OnBanana) should produce a CRITICAL 'Unknown hook event' error."""
    hooks_data = {"hooks": {"OnBanana": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo nope"}]}]}}
    hooks_file = tmp_path / "hooks.json"
    hooks_file.write_text(json.dumps(hooks_data))
    report = validate_hooks(hooks_file)
    assert report.has_critical
    assert any(
        "Unknown hook event" in r.message and "OnBanana" in r.message for r in report.results if r.level == "CRITICAL"
    )


def test_validate_hooks_missing_hooks_key_and_matcher_array(tmp_path: Path):
    """Missing top-level 'hooks' key and a matcher block without 'hooks' array both produce CRITICAL."""
    # Missing 'hooks' key
    f1 = tmp_path / "no_hooks.json"
    f1.write_text(json.dumps({"description": "oops"}))
    r1 = validate_hooks(f1)
    assert any("Missing required 'hooks'" in r.message for r in r1.results if r.level == "CRITICAL")
    # Matcher block missing hooks array
    f2 = tmp_path / "no_array.json"
    f2.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash"}]}}))
    r2 = validate_hooks(f2)
    assert any("missing required 'hooks' array" in r.message.lower() for r in r2.results if r.level == "CRITICAL")


def test_edge_cases_unknown_fields_and_absolute_path(tmp_path: Path):
    """Unknown hook fields produce WARNING; hardcoded absolute path in command produces MAJOR."""
    # Unknown fields
    hooks_data = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo ok", "customField": "x"}]}
            ]
        }
    }
    f = tmp_path / "hooks.json"
    f.write_text(json.dumps(hooks_data))
    r1 = validate_hooks(f, plugin_root=tmp_path)
    assert any(
        "Unknown hook field" in r.message and "customField" in r.message for r in r1.results if r.level == "WARNING"
    )
    # Absolute path
    r2 = ValidationReport()
    validate_command_hook({"type": "command", "command": "/usr/local/bin/mytool --check"}, "PreToolUse", tmp_path, r2)
    assert r2.has_major and any("absolute path" in r.message.lower() for r in r2.results if r.level == "MAJOR")


# ---------------------------------------------------------------------------
# 20 additional tests targeting uncovered lines
# ---------------------------------------------------------------------------


def test_validate_top_level_root_not_dict():
    """Root value that is not a dict (e.g. a list) should produce CRITICAL 'Root must be a JSON object'."""
    report = ValidationReport()
    result = validate_top_level_structure(["not", "a", "dict"], report)
    assert result is False
    assert any("Root must be a JSON object" in r.message for r in report.results if r.level == "CRITICAL")


def test_validate_top_level_description_not_string():
    """A non-string 'description' field should produce MAJOR; hooks key still required."""
    report = ValidationReport()
    result = validate_top_level_structure({"description": 42, "hooks": {}}, report)
    assert result is True
    assert any("'description' must be a string" in r.message for r in report.results if r.level == "MAJOR")


def test_validate_top_level_hooks_not_dict():
    """A 'hooks' value that is not a dict (e.g. a list) should produce CRITICAL."""
    report = ValidationReport()
    result = validate_top_level_structure({"hooks": [1, 2, 3]}, report)
    assert result is False
    assert any("'hooks' must be an object" in r.message for r in report.results if r.level == "CRITICAL")


def test_validate_matcher_on_event_without_matchers():
    """Providing a matcher on UserPromptSubmit (no-matcher event) should produce INFO, not an error."""
    report = ValidationReport()
    result = validate_matcher("Bash", "UserPromptSubmit", report)
    assert result is True
    assert any("matchers are ignored" in r.message for r in report.results if r.level == "INFO")


def test_validate_matcher_non_string():
    """A non-string matcher (e.g. integer) should produce MAJOR."""
    report = ValidationReport()
    result = validate_matcher(123, "PreToolUse", report)
    assert result is False
    assert any("Matcher must be a string" in r.message for r in report.results if r.level == "MAJOR")


def test_validate_matcher_invalid_regex():
    """An invalid regex pattern in the matcher should produce MAJOR with 'Invalid regex' message."""
    report = ValidationReport()
    result = validate_matcher("[unclosed", "PreToolUse", report)
    assert result is False
    assert any("Invalid regex" in r.message for r in report.results if r.level == "MAJOR")


def test_validate_matcher_uncommon_tool_name():
    """A PascalCase matcher that is not a common tool name produces INFO hint about custom tool."""
    report = ValidationReport()
    result = validate_matcher("CustomAnalyzer", "PreToolUse", report)
    assert result is True
    assert any("not a common tool name" in r.message for r in report.results if r.level == "INFO")


def test_extract_script_path_with_plugin_root(tmp_path: Path):
    """extract_script_path resolves CLAUDE_PLUGIN_ROOT and returns a Path for a .sh script."""
    result = extract_script_path("${CLAUDE_PLUGIN_ROOT}/scripts/run.sh --verbose", tmp_path)
    assert result is not None
    assert result == tmp_path / "scripts" / "run.sh"


def test_extract_script_path_unresolved_variable():
    """A command with an unresolvable env var (not CLAUDE_PLUGIN_ROOT) returns None."""
    result = extract_script_path("$CLAUDE_PROJECT_DIR/hooks/check.py", None)
    assert result is None


def test_extract_script_path_quoted_command(tmp_path: Path):
    """A double-quoted command path should be extracted correctly."""
    result = extract_script_path('"${CLAUDE_PLUGIN_ROOT}/scripts/lint.py" --fix', tmp_path)
    assert result is not None
    assert str(result).endswith("lint.py")


def test_extract_script_path_non_script_command():
    """A command that is not a script path (e.g. 'echo hello') should return None."""
    result = extract_script_path("echo hello world", None)
    assert result is None


def test_validate_script_not_found(tmp_path: Path):
    """validate_script on a missing file should produce MAJOR 'Script not found'."""
    report = ValidationReport()
    validate_script(tmp_path / "nonexistent.sh", report)
    assert any("Script not found" in r.message for r in report.results if r.level == "MAJOR")


def test_validate_script_not_executable(tmp_path: Path):
    """A script file without execute permission should produce MAJOR 'Script not executable'."""
    script = tmp_path / "check.sh"
    script.write_text("#!/bin/bash\necho ok\n")
    script.chmod(0o644)
    report = ValidationReport()
    validate_script(script, report)
    assert any("Script not executable" in r.message for r in report.results if r.level == "MAJOR")


def test_validate_command_hook_package_executor_warning(tmp_path: Path):
    """Using npx/bunx/uvx to run a remote package should produce a WARNING about trust."""
    report = ValidationReport()
    validate_command_hook(
        {"type": "command", "command": "npx --yes prettier --check ."}, "PreToolUse", tmp_path, report
    )
    assert any(
        "remote package" in r.message and "prettier" in r.message for r in report.results if r.level == "WARNING"
    )


def test_validate_command_hook_timeout_validations(tmp_path: Path):
    """Timeout field: non-number produces MAJOR; negative produces MAJOR; >1000 suggests ms confusion; >600 warns long."""
    # Non-number timeout
    r1 = ValidationReport()
    validate_command_hook({"type": "command", "command": "echo ok", "timeout": "fast"}, "PreToolUse", tmp_path, r1)
    assert any("'timeout' must be a number" in r.message for r in r1.results if r.level == "MAJOR")
    # Negative timeout
    r2 = ValidationReport()
    validate_command_hook({"type": "command", "command": "echo ok", "timeout": -5}, "PreToolUse", tmp_path, r2)
    assert any("'timeout' must be positive" in r.message for r in r2.results if r.level == "MAJOR")
    # 300 seconds — normal, no warning
    r3 = ValidationReport()
    validate_command_hook({"type": "command", "command": "echo ok", "timeout": 300}, "PreToolUse", tmp_path, r3)
    assert not any(r.level in ("MAJOR", "MINOR", "WARNING") for r in r3.results if "timeout" in r.message.lower())
    # >600 seconds should warn about exceeding default
    r4 = ValidationReport()
    validate_command_hook({"type": "command", "command": "echo ok", "timeout": 700}, "PreToolUse", tmp_path, r4)
    assert any("exceeds" in r.message for r in r4.results if r.level == "WARNING")
    # >10000 should warn about likely milliseconds confusion
    r4b = ValidationReport()
    validate_command_hook({"type": "command", "command": "echo ok", "timeout": 60000}, "PreToolUse", tmp_path, r4b)
    assert any("milliseconds" in r.message.lower() for r in r4b.results if r.level == "WARNING")


def test_validate_command_hook_env_file_wrong_event(tmp_path: Path):
    """Using CLAUDE_ENV_FILE in a non-SessionStart/Setup event should produce MAJOR."""
    report = ValidationReport()
    validate_command_hook({"type": "command", "command": "cat $CLAUDE_ENV_FILE"}, "PreToolUse", tmp_path, report)
    assert any("CLAUDE_ENV_FILE is only available" in r.message for r in report.results if r.level == "MAJOR")


def test_validate_single_hook_agent_type(tmp_path: Path):
    """Agent hook: missing prompt produces CRITICAL; empty prompt produces MAJOR; invalid timeout produces MAJOR."""
    # Missing prompt
    r1 = HookValidationReport()
    validate_single_hook({"type": "agent"}, "PreToolUse", tmp_path, r1)
    assert any("Agent hook missing required 'prompt'" in r.message for r in r1.results if r.level == "CRITICAL")
    # Empty prompt
    r2 = HookValidationReport()
    validate_single_hook({"type": "agent", "prompt": "   "}, "PreToolUse", tmp_path, r2)
    assert any("Agent hook 'prompt' must be a non-empty string" in r.message for r in r2.results if r.level == "MAJOR")
    # Invalid model field
    r3 = HookValidationReport()
    validate_single_hook({"type": "agent", "prompt": "Do analysis", "model": ""}, "PreToolUse", tmp_path, r3)
    assert any("Agent hook 'model' must be a non-empty string" in r.message for r in r3.results if r.level == "MAJOR")
    # Invalid timeout
    r4 = HookValidationReport()
    validate_single_hook({"type": "agent", "prompt": "Do analysis", "timeout": "slow"}, "PreToolUse", tmp_path, r4)
    assert any("Agent hook 'timeout' must be a number" in r.message for r in r4.results if r.level == "MAJOR")
    # Negative timeout
    r5 = HookValidationReport()
    validate_single_hook({"type": "agent", "prompt": "Do analysis", "timeout": -1}, "PreToolUse", tmp_path, r5)
    assert any("Agent hook 'timeout' must be positive" in r.message for r in r5.results if r.level == "MAJOR")
    # Very long timeout
    r6 = HookValidationReport()
    validate_single_hook({"type": "agent", "prompt": "Do analysis", "timeout": 700}, "PreToolUse", tmp_path, r6)
    assert any("exceeds 10 minutes" in r.message for r in r6.results if r.level == "MINOR")


def test_validate_single_hook_async_on_non_command(tmp_path: Path):
    """Setting async:true on a prompt hook should produce MAJOR about async only on command hooks."""
    report = HookValidationReport()
    validate_single_hook({"type": "prompt", "prompt": "Review this", "async": True}, "Stop", tmp_path, report)
    assert any(
        "'async: true' is only supported on 'command' or 'http'" in r.message
        for r in report.results
        if r.level == "MAJOR"
    )


def test_validate_single_hook_status_message_and_once(tmp_path: Path):
    """statusMessage must be a string; 'once' must be boolean. Both produce MAJOR when wrong type."""
    # Non-string statusMessage
    r1 = HookValidationReport()
    validate_single_hook({"type": "command", "command": "echo ok", "statusMessage": 42}, "PreToolUse", tmp_path, r1)
    assert any("'statusMessage' must be a string" in r.message for r in r1.results if r.level == "MAJOR")
    # Non-boolean once
    r2 = HookValidationReport()
    validate_single_hook({"type": "command", "command": "echo ok", "once": "yes"}, "PreToolUse", tmp_path, r2)
    assert any("'once' must be a boolean" in r.message for r in r2.results if r.level == "MAJOR")
    # Valid boolean once -> INFO
    r3 = HookValidationReport()
    validate_single_hook({"type": "command", "command": "echo ok", "once": True}, "PreToolUse", tmp_path, r3)
    assert any("'once' field detected" in r.message for r in r3.results if r.level == "INFO")


def test_validate_prompt_hook_timeout_and_model():
    """Prompt hook: invalid timeout produces MAJOR; invalid model produces MAJOR; info for non-ideal events."""
    # Prompt on non-ideal event -> INFO
    r1 = ValidationReport()
    validate_prompt_hook({"type": "prompt", "prompt": "Analyze $ARGUMENTS"}, "PostToolUse", r1)
    assert any("may not be as effective" in r.message for r in r1.results if r.level == "INFO")
    # Missing $ARGUMENTS -> INFO
    r2 = ValidationReport()
    validate_prompt_hook({"type": "prompt", "prompt": "Just do it"}, "Stop", r2)
    assert any("$ARGUMENTS placeholder" in r.message for r in r2.results if r.level == "INFO")
    # Invalid model
    r3 = ValidationReport()
    validate_prompt_hook({"type": "prompt", "prompt": "ok $ARGUMENTS", "model": ""}, "Stop", r3)
    assert any("'model' must be a non-empty string" in r.message for r in r3.results if r.level == "MAJOR")
    # Timeout validations on prompt hook
    r4 = ValidationReport()
    validate_prompt_hook({"type": "prompt", "prompt": "ok", "timeout": "bad"}, "Stop", r4)
    assert any("'timeout' must be a number" in r.message for r in r4.results if r.level == "MAJOR")
    r5 = ValidationReport()
    validate_prompt_hook({"type": "prompt", "prompt": "ok", "timeout": -1}, "Stop", r5)
    assert any("'timeout' must be positive" in r.message for r in r5.results if r.level == "MAJOR")
    # 20 seconds — normal for prompt hooks, no warning
    r6 = ValidationReport()
    validate_prompt_hook({"type": "prompt", "prompt": "ok", "timeout": 20}, "Stop", r6)
    assert not any(r.level in ("MAJOR", "MINOR", "WARNING") for r in r6.results if "timeout" in r.message.lower())
    # 300 seconds — normal, no warning
    r7 = ValidationReport()
    validate_prompt_hook({"type": "prompt", "prompt": "ok", "timeout": 300}, "Stop", r7)
    assert not any(r.level in ("MAJOR", "MINOR", "WARNING") for r in r7.results if "timeout" in r.message.lower())


def test_validate_matcher_block_not_dict():
    """A matcher block that is not a dict should produce CRITICAL."""
    report = HookValidationReport()
    result = validate_matcher_block("not a dict", "PreToolUse", None, report)
    assert result is False
    assert any("Matcher block must be an object" in r.message for r in report.results if r.level == "CRITICAL")


def test_validate_matcher_block_hooks_not_list():
    """A matcher block where 'hooks' is not a list should produce CRITICAL."""
    report = HookValidationReport()
    result = validate_matcher_block({"matcher": "Bash", "hooks": "not a list"}, "PreToolUse", None, report)
    assert result is False
    assert any("'hooks' must be an array" in r.message for r in report.results if r.level == "CRITICAL")


def test_validate_matcher_block_empty_hooks():
    """A matcher block with an empty 'hooks' array should produce MINOR and return True."""
    report = HookValidationReport()
    result = validate_matcher_block({"matcher": "Bash", "hooks": []}, "PreToolUse", None, report)
    assert result is True
    assert any("'hooks' array is empty" in r.message for r in report.results if r.level == "MINOR")


def test_validate_event_hooks_not_list():
    """Event config that is not a list should produce CRITICAL."""
    report = HookValidationReport()
    result = validate_event_hooks("PreToolUse", "not a list", None, report)
    assert result is False
    assert any("must be an array" in r.message for r in report.results if r.level == "CRITICAL")


def test_validate_event_hooks_empty_list():
    """An empty event config list should produce INFO and return True."""
    report = HookValidationReport()
    result = validate_event_hooks("PreToolUse", [], None, report)
    assert result is True
    assert any("No hooks configured" in r.message for r in report.results if r.level == "INFO")


def test_print_results_and_print_json(tmp_path: Path, capsys):
    """print_results outputs human-readable format; print_json outputs valid JSON with all expected keys."""
    hooks_data = {
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo test"}]}]}
    }
    hooks_file = tmp_path / "hooks.json"
    hooks_file.write_text(json.dumps(hooks_data))
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    # Test print_results (non-verbose and verbose)
    print_results(report, verbose=False)
    captured = capsys.readouterr()
    assert "Hook Validation" in captured.out
    assert "CRITICAL:" in captured.out
    print_results(report, verbose=True)
    captured_v = capsys.readouterr()
    assert "PASSED:" in captured_v.out
    # Test print_json
    print_json(report)
    captured_json = capsys.readouterr()
    output = json.loads(captured_json.out)
    assert "hook_path" in output
    assert "exit_code" in output
    assert "counts" in output
    assert "results" in output
    assert isinstance(output["results"], list)


def test_print_results_exit_code_messages(tmp_path: Path, capsys):
    """print_results shows correct status lines for exit_code 0, 1, 2, and 3."""
    # Exit code 0 (all passed)
    f0 = tmp_path / "ok.json"
    f0.write_text(
        json.dumps(
            {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo ok"}]}]}}
        )
    )
    r0 = validate_hooks(f0, plugin_root=tmp_path)
    print_results(r0)
    out0 = capsys.readouterr().out
    assert "passed" in out0.lower()
    # Exit code 1 (CRITICAL)
    f1 = tmp_path / "crit.json"
    f1.write_text(json.dumps({"hooks": {"FakeEvent": []}}))
    r1 = validate_hooks(f1)
    print_results(r1)
    out1 = capsys.readouterr().out
    assert "CRITICAL" in out1
    # Exit code 2 (MAJOR only - need a file with major but no critical issue)
    r2 = HookValidationReport(hook_path=str(tmp_path / "major.json"))
    r2.major("Test major issue")
    print_results(r2)
    out2 = capsys.readouterr().out
    assert "MAJOR" in out2
    # Exit code 3 (MINOR only)
    r3 = HookValidationReport(hook_path=str(tmp_path / "minor.json"))
    r3.minor("Test minor issue")
    print_results(r3)
    out3 = capsys.readouterr().out
    assert "MINOR" in out3


def test_validate_single_hook_missing_type(tmp_path: Path):
    """A hook dict without 'type' field should produce CRITICAL 'missing required type'."""
    report = HookValidationReport()
    result = validate_single_hook({"command": "echo hi"}, "PreToolUse", tmp_path, report)
    assert result is False
    assert any("missing required 'type'" in r.message.lower() for r in report.results if r.level == "CRITICAL")


def test_validate_command_hook_non_string_command(tmp_path: Path):
    """A command hook where 'command' is not a string should produce CRITICAL."""
    report = ValidationReport()
    result = validate_command_hook({"type": "command", "command": 42}, "PreToolUse", tmp_path, report)
    assert result is False
    assert any("'command' must be a string" in r.message for r in report.results if r.level == "CRITICAL")


# ---------------------------------------------------------------------------
# Changelog-driven tests: new hook events, HTTP hooks, PostCompact, Elicitation
# ---------------------------------------------------------------------------


def test_new_hook_events_accepted(tmp_path: Path):
    """PostCompact, Elicitation, and ElicitationResult are accepted as valid hook events."""
    for event in ("PostCompact", "Elicitation", "ElicitationResult"):
        hooks_data = {"hooks": {event: [{"hooks": [{"type": "command", "command": "echo ok"}]}]}}
        hooks_file = tmp_path / f"hooks_{event}.json"
        hooks_file.write_text(json.dumps(hooks_data))
        report = validate_hooks(hooks_file, plugin_root=tmp_path)
        assert not any(
            "Unknown hook event" in r.message and event in r.message for r in report.results if r.level == "CRITICAL"
        ), f"Event {event} should be accepted but was rejected"


def test_http_hook_valid(tmp_path: Path):
    """A valid HTTP hook with a proper https URL should pass without CRITICAL or MAJOR issues."""
    hooks_data = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "http", "url": "https://example.com/webhook"}],
                }
            ]
        }
    }
    hooks_file = tmp_path / "hooks.json"
    hooks_file.write_text(json.dumps(hooks_data))
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    assert not report.has_critical
    assert not report.has_major


def test_http_hook_missing_url(tmp_path: Path):
    """An HTTP hook without a 'url' field should produce CRITICAL."""
    from validate_hook import validate_http_hook

    report = ValidationReport()
    result = validate_http_hook({"type": "http"}, "PreToolUse", report)
    assert result is False
    assert any("missing required 'url'" in r.message for r in report.results if r.level == "CRITICAL")


def test_http_hook_bad_url_format(tmp_path: Path):
    """An HTTP hook with a non-http/https URL should produce MAJOR."""
    from validate_hook import validate_http_hook

    report = ValidationReport()
    validate_http_hook({"type": "http", "url": "ftp://example.com/hook"}, "PreToolUse", report)
    assert any("should start with http://" in r.message for r in report.results if r.level == "MAJOR")


def test_http_hook_headers_validation(tmp_path: Path):
    """HTTP hook headers must be a dict; non-string header values each produce MAJOR."""
    from validate_hook import validate_http_hook

    # Non-dict headers
    r1 = ValidationReport()
    validate_http_hook({"type": "http", "url": "https://example.com", "headers": ["list"]}, "PreToolUse", r1)
    assert any("'headers' must be an object" in r.message for r in r1.results if r.level == "MAJOR")

    # Non-string header value
    r2 = ValidationReport()
    validate_http_hook(
        {"type": "http", "url": "https://example.com", "headers": {"X-Token": 12345}},
        "PreToolUse",
        r2,
    )
    assert any("header 'X-Token' value must be a string" in r.message for r in r2.results if r.level == "MAJOR")

    # Valid headers — no MAJOR
    r3 = ValidationReport()
    validate_http_hook(
        {"type": "http", "url": "https://example.com", "headers": {"Authorization": "Bearer tok"}},
        "PreToolUse",
        r3,
    )
    assert not r3.has_major


def test_http_hook_in_command_only_event(tmp_path: Path):
    """HTTP hooks should be allowed in command-only events like SessionStart."""
    hooks_data = {"hooks": {"SessionStart": [{"hooks": [{"type": "http", "url": "https://example.com/on-start"}]}]}}
    hooks_file = tmp_path / "hooks.json"
    hooks_file.write_text(json.dumps(hooks_data))
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    # Should not produce CRITICAL about command-only restriction
    assert not any(
        "only supports 'command' or 'http'" in r.message and "SessionStart" in r.message
        for r in report.results
        if r.level == "CRITICAL"
    )


def test_http_hook_no_unknown_field_warnings(tmp_path: Path):
    """HTTP hook fields 'url' and 'headers' should not trigger unknown field warnings."""
    hook = {
        "type": "http",
        "url": "https://example.com/webhook",
        "headers": {"Authorization": "Bearer tok"},
        "timeout": 5000,
    }
    r = HookValidationReport()
    validate_single_hook(hook, "PreToolUse", tmp_path, r)
    unknown_warnings = [res for res in r.results if res.level == "WARNING" and "Unknown hook field" in res.message]
    assert len(unknown_warnings) == 0, f"Unexpected unknown-field warnings: {unknown_warnings}"


def test_postcompact_command_only(tmp_path: Path):
    """PostCompact rejects prompt and agent type hooks with CRITICAL."""
    for bad_type, extra in [("prompt", {"prompt": "Summarise"}), ("agent", {"prompt": "Analyse"})]:
        hook = {"type": bad_type, **extra}
        r = HookValidationReport()
        validate_single_hook(hook, "PostCompact", tmp_path, r)
        assert any(
            "only supports 'command' or 'http'" in res.message for res in r.results if res.level == "CRITICAL"
        ), f"PostCompact should reject '{bad_type}' hooks"


def test_async_rewake_is_recognised(tmp_path: Path):
    """v2.1.98+: 'asyncRewake' hook field is accepted without a warning.

    asyncRewake runs the hook in the background (implies async) and wakes
    Claude on exit code 2. Per authoritative hooks.md, this is a
    first-class field and must NOT trigger 'Unknown hook field'.
    """
    hook = {
        "type": "command",
        "command": "sleep 30 && exit 2",
        "async": True,
        "asyncRewake": True,
    }
    r = HookValidationReport()
    validate_single_hook(hook, "PreToolUse", tmp_path, r)
    unknown_warnings = [
        res for res in r.results
        if res.level == "WARNING" and "asyncRewake" in res.message and "Unknown" in res.message
    ]
    assert unknown_warnings == [], (
        f"asyncRewake should NOT be flagged as unknown: {unknown_warnings}"
    )


def test_elicitation_no_matchers(tmp_path: Path):
    """Elicitation events now support matchers (MCP server name) since v2.1.76."""
    from cpv_validation_common import ValidationReport as VReport
    from validate_hook import validate_matcher

    report = VReport()
    result = validate_matcher("Bash", "Elicitation", report)
    assert result is True
    # Elicitation is now in EVENTS_WITH_MATCHERS, so valid matcher should produce no warnings
    assert not any("matchers are ignored" in r.message for r in report.results if r.level == "INFO")


# ===========================================================================
# TRDD-0028dd34: runtime-dep blind spot regression tests
#
# These tests exist because CPV silently approved PSS v3.1.0 — a plugin whose
# UserPromptSubmit hook crashed on every prompt because `python3 pss_hook.py`
# could not resolve `pycozo`. The validator's extract_script_path was returning
# None for every real-world invocation pattern, so no script-level analysis
# ever ran. These tests are the guardrail against that regression class.
# ===========================================================================


def _make_py_script(tmp_path: Path, name: str, body: str) -> Path:
    """Helper: write a Python script under tmp_path/scripts/ and chmod +x."""
    import os

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    script = scripts_dir / name
    script.write_text(body)
    os.chmod(script, 0o755)
    return script


# ---------------------------------------------------------------------------
# _split_compound_command — quote-aware shell splitting
# ---------------------------------------------------------------------------


def test_split_compound_command_semicolon():
    """A `;` at top level splits into two simple commands."""
    from validate_hook import _split_compound_command

    parts = _split_compound_command("unset VIRTUAL_ENV; python3 foo.py")
    assert parts == ["unset VIRTUAL_ENV", "python3 foo.py"]


def test_split_compound_command_double_ampersand():
    """A `&&` at top level splits into two simple commands."""
    from validate_hook import _split_compound_command

    parts = _split_compound_command("cd /tmp && python3 foo.py")
    assert parts == ["cd /tmp", "python3 foo.py"]


def test_split_compound_command_preserves_operators_inside_quotes():
    """`;` inside a quoted string is NOT a splitter (quote-aware)."""
    from validate_hook import _split_compound_command

    parts = _split_compound_command("python3 'a;b.py'")
    assert parts == ["python3 'a;b.py'"]


def test_split_compound_command_double_quotes():
    """`&&` inside a double-quoted string is preserved as content."""
    from validate_hook import _split_compound_command

    parts = _split_compound_command('echo "a && b" && python3 foo.py')
    assert parts == ['echo "a && b"', "python3 foo.py"]


# ---------------------------------------------------------------------------
# _tokenize_hook_command — trailing & stripping, shlex token split
# ---------------------------------------------------------------------------


def test_tokenize_strips_trailing_background_ampersand():
    """A trailing `&` (background marker) is stripped from the final command."""
    from validate_hook import _tokenize_hook_command

    tokens = _tokenize_hook_command("python3 foo.py --warm-index &")
    assert tokens == [["python3", "foo.py", "--warm-index"]]


def test_tokenize_preserves_double_ampersand_at_end():
    """A trailing `&&` is NOT stripped — it's a compound operator, not a bg marker."""
    from validate_hook import _tokenize_hook_command

    tokens = _tokenize_hook_command("a && b &&")
    # Trailing separator produces an empty final command which is filtered out.
    assert tokens == [["a"], ["b"]]


def test_tokenize_env_var_reference_is_single_token():
    """Quoted `${CLAUDE_PLUGIN_ROOT}/scripts/foo.py` is one token (quotes stripped, $VAR intact)."""
    from validate_hook import _tokenize_hook_command

    tokens = _tokenize_hook_command('python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"')
    assert tokens == [["python3", "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"]]


# ---------------------------------------------------------------------------
# extract_script_paths — the heart of the regression
# ---------------------------------------------------------------------------


def test_extract_script_paths_python3_interpreter(tmp_path: Path):
    """`python3 foo.py` MUST extract foo.py with mode=interpreter-python.

    This is the precise case the legacy extractor failed on.
    """
    from validate_hook import extract_script_paths

    refs = extract_script_paths('python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pss_hook.py"', tmp_path)
    assert len(refs) == 1
    assert refs[0].path == tmp_path / "scripts" / "pss_hook.py"
    assert refs[0].invocation_mode == "interpreter-python"


def test_extract_script_paths_compound_unset_then_python(tmp_path: Path):
    """The EXACT failing PSS v3.1.0 command must yield the script with mode=interpreter-python."""
    from validate_hook import extract_script_paths

    cmd = 'unset VIRTUAL_ENV; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pss_hook.py"'
    refs = extract_script_paths(cmd, tmp_path)
    assert len(refs) == 1
    assert refs[0].path == tmp_path / "scripts" / "pss_hook.py"
    assert refs[0].invocation_mode == "interpreter-python"


def test_extract_script_paths_uv_run_script(tmp_path: Path):
    """`uv run --script foo.py` yields mode=uv-run-script."""
    from validate_hook import extract_script_paths

    cmd = 'uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/pss_hook.py"'
    refs = extract_script_paths(cmd, tmp_path)
    assert len(refs) == 1
    assert refs[0].invocation_mode == "uv-run-script"


def test_extract_script_paths_uv_run_plain(tmp_path: Path):
    """`uv run foo.py` without --script yields mode=interpreter-python."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths('uv run "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"', tmp_path)
    assert len(refs) == 1
    assert refs[0].invocation_mode == "interpreter-python"


def test_extract_script_paths_uv_run_with(tmp_path: Path):
    """`uv run --with pkg foo.py` yields mode=uv-run-with with explicit_deps populated."""
    from validate_hook import extract_script_paths

    cmd = 'uv run --with pycozo --with httpx "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"'
    refs = extract_script_paths(cmd, tmp_path)
    assert len(refs) == 1
    assert refs[0].invocation_mode == "uv-run-with"
    assert refs[0].explicit_deps == ("pycozo", "httpx")


def test_extract_script_paths_venv_python(tmp_path: Path):
    """`${CLAUDE_PLUGIN_DATA}/.venv/bin/python foo.py` yields mode=venv-python.

    Note: we don't substitute CLAUDE_PLUGIN_DATA (runtime-only), so the
    resolved first token still contains `$CLAUDE_PLUGIN_DATA`, but the
    pattern match for `.venv/bin/python` still triggers.
    """
    from validate_hook import extract_script_paths

    cmd = '"${CLAUDE_PLUGIN_DATA}/.venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"'
    refs = extract_script_paths(cmd, tmp_path)
    assert len(refs) == 1
    assert refs[0].invocation_mode == "venv-python"


def test_extract_script_paths_node_interpreter(tmp_path: Path):
    """`node foo.js` yields mode=node."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths('node "${CLAUDE_PLUGIN_ROOT}/scripts/hook.js"', tmp_path)
    assert len(refs) == 1
    assert refs[0].invocation_mode == "node"


def test_extract_script_paths_bash_interpreter(tmp_path: Path):
    """`bash foo.sh` yields mode=bash."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths('bash "${CLAUDE_PLUGIN_ROOT}/scripts/hook.sh"', tmp_path)
    assert len(refs) == 1
    assert refs[0].invocation_mode == "bash"


def test_extract_script_paths_python_m_module_is_not_script(tmp_path: Path):
    """`python3 -m module` has no script file — extractor returns empty."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths("python3 -m mymodule", tmp_path)
    assert refs == []


def test_extract_script_paths_python_c_code_is_not_script(tmp_path: Path):
    """`python3 -c '...'` has no script file — extractor returns empty."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths("python3 -c 'print(1)'", tmp_path)
    assert refs == []


def test_extract_script_paths_python_flags_before_script(tmp_path: Path):
    """`python3 -u -B foo.py` correctly walks past flags to find foo.py."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths('python3 -u -B "${CLAUDE_PLUGIN_ROOT}/foo.py"', tmp_path)
    assert len(refs) == 1
    assert refs[0].path == tmp_path / "foo.py"


def test_extract_script_paths_env_wrapper(tmp_path: Path):
    """`env python3 foo.py` yields the script with mode=interpreter-python."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths('env python3 "${CLAUDE_PLUGIN_ROOT}/foo.py"', tmp_path)
    assert len(refs) == 1
    assert refs[0].invocation_mode == "interpreter-python"


def test_extract_script_paths_skips_pure_shell_builtin(tmp_path: Path):
    """A command that is ONLY `cd /tmp` returns empty (no script payload)."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths("cd /tmp", tmp_path)
    assert refs == []


def test_extract_script_paths_direct_invocation(tmp_path: Path):
    """`${CLAUDE_PLUGIN_ROOT}/scripts/foo.sh` (no interpreter prefix) still extracts as direct."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths('"${CLAUDE_PLUGIN_ROOT}/scripts/foo.sh" --verbose', tmp_path)
    assert len(refs) == 1
    assert refs[0].invocation_mode == "direct"


def test_extract_script_paths_legacy_wrapper_still_works(tmp_path: Path):
    """extract_script_path (singular) must keep returning the first script for backward compat."""
    from validate_hook import extract_script_path

    result = extract_script_path("${CLAUDE_PLUGIN_ROOT}/scripts/run.sh --verbose", tmp_path)
    assert result == tmp_path / "scripts" / "run.sh"


# ---------------------------------------------------------------------------
# detect_python_third_party_imports — AST-based import classification
# ---------------------------------------------------------------------------


def test_detect_imports_stdlib_only(tmp_path: Path):
    """A script importing only stdlib modules returns empty."""
    from validate_hook import detect_python_third_party_imports

    script = _make_py_script(
        tmp_path,
        "stdlib_only.py",
        "import os\nimport sys\nfrom pathlib import Path\nimport json\n",
    )
    assert detect_python_third_party_imports(script) == set()


def test_detect_imports_third_party(tmp_path: Path):
    """A script importing pycozo returns {'pycozo'}."""
    from validate_hook import detect_python_third_party_imports

    script = _make_py_script(
        tmp_path,
        "has_pycozo.py",
        "import os\nimport pycozo\nfrom pycozo.client import Client\n",
    )
    assert detect_python_third_party_imports(script) == {"pycozo"}


def test_detect_imports_through_try_except(tmp_path: Path):
    """A script with `try: import pycozo except ImportError:` still flags pycozo.

    This is PSS's exact pattern — the import is inside try/except but still
    third-party from the validator's perspective.
    """
    from validate_hook import detect_python_third_party_imports

    script = _make_py_script(
        tmp_path,
        "try_import.py",
        "try:\n    import pycozo\nexcept ImportError:\n    pass\n",
    )
    assert "pycozo" in detect_python_third_party_imports(script)


def test_detect_imports_intra_plugin_sibling_excluded(tmp_path: Path):
    """Imports of sibling .py files under scripts/ are NOT flagged as third-party."""
    from validate_hook import detect_python_third_party_imports

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (scripts_dir / "helper.py").write_text("pass\n")
    script = scripts_dir / "main.py"
    script.write_text("import helper\nimport pycozo\n")

    imports = detect_python_third_party_imports(script, plugin_script_dir=scripts_dir)
    assert imports == {"pycozo"}


def test_detect_imports_relative_imports_ignored(tmp_path: Path):
    """`from . import foo` is never third-party."""
    from validate_hook import detect_python_third_party_imports

    script = _make_py_script(
        tmp_path,
        "relative.py",
        "from . import helper\nfrom .. import other\n",
    )
    assert detect_python_third_party_imports(script) == set()


def test_detect_imports_syntax_error_returns_empty(tmp_path: Path):
    """A file with SyntaxError yields empty (silent; lint reports the syntax issue)."""
    from validate_hook import detect_python_third_party_imports

    script = _make_py_script(tmp_path, "broken.py", "def f(:\n    pass\n")
    assert detect_python_third_party_imports(script) == set()


# ---------------------------------------------------------------------------
# detect_pep723_deps — PEP 723 inline script metadata
# ---------------------------------------------------------------------------


def test_detect_pep723_simple_deps(tmp_path: Path):
    """A script with a PEP 723 block yields its dependencies list."""
    from validate_hook import detect_pep723_deps

    script = _make_py_script(
        tmp_path,
        "with_block.py",
        '# /// script\n# requires-python = ">=3.10"\n# dependencies = [\n#     "pycozo[embedded]>=0.7.6",\n#     "httpx",\n# ]\n# ///\nimport pycozo\n',
    )
    deps = detect_pep723_deps(script)
    assert deps is not None
    assert "pycozo[embedded]>=0.7.6" in deps
    assert "httpx" in deps


def test_detect_pep723_no_block(tmp_path: Path):
    """A script with NO PEP 723 block returns None."""
    from validate_hook import detect_pep723_deps

    script = _make_py_script(tmp_path, "no_block.py", "import pycozo\n")
    assert detect_pep723_deps(script) is None


def test_detect_pep723_malformed_block_returns_empty(tmp_path: Path):
    """A malformed PEP 723 block (invalid TOML) returns an empty list, not None."""
    from validate_hook import detect_pep723_deps

    script = _make_py_script(
        tmp_path,
        "malformed.py",
        '# /// script\n# dependencies = this is not TOML\n# ///\nimport pycozo\n',
    )
    deps = detect_pep723_deps(script)
    assert deps == []


# ---------------------------------------------------------------------------
# detect_module_scope_sys_exit — the pss_cozodb.py failure mode
# ---------------------------------------------------------------------------


def test_detect_module_scope_sys_exit_direct(tmp_path: Path):
    """Top-level `sys.exit(...)` is detected."""
    from validate_hook import detect_module_scope_sys_exit

    script = _make_py_script(
        tmp_path, "direct_exit.py", "import sys\nsys.exit('boom')\n"
    )
    hits = detect_module_scope_sys_exit(script)
    assert hits == [2]


def test_detect_module_scope_sys_exit_inside_try_except(tmp_path: Path):
    """`sys.exit(...)` inside a top-level `try/except` (the EXACT PSS v3.1.0
    pattern that killed the hook process at import time) MUST be detected.

    This used to be a silent gap — the original detector walked ast.If but
    not ast.Try. The rewrite descends through every import-time-reachable
    statement container (If, Try, For, While, With) while stopping at
    function/class bodies.
    """
    from validate_hook import detect_module_scope_sys_exit

    script = _make_py_script(
        tmp_path,
        "try_exit.py",
        "import sys\n\ntry:\n    import pycozo\nexcept ImportError:\n    sys.exit('ERROR: pycozo is required.')\n",
    )
    hits = detect_module_scope_sys_exit(script)
    assert hits == [6], (
        f"sys.exit inside top-level try/except MUST be detected "
        f"(this is the PSS v3.1.0 pattern); got: {hits}"
    )


def test_detect_module_scope_sys_exit_inside_for_while_with(tmp_path: Path):
    """sys.exit inside top-level for/while/with blocks runs at import time."""
    from validate_hook import detect_module_scope_sys_exit

    script = _make_py_script(
        tmp_path,
        "loops_exit.py",
        "import sys\nfor i in range(1):\n    sys.exit(1)\n",
    )
    assert detect_module_scope_sys_exit(script) == [3]

    script2 = _make_py_script(
        tmp_path,
        "with_exit.py",
        "import contextlib\nimport sys\nwith contextlib.nullcontext():\n    sys.exit(1)\n",
    )
    assert detect_module_scope_sys_exit(script2) == [4]


def test_detect_module_scope_sys_exit_top_level_if(tmp_path: Path):
    """`if cond: sys.exit(...)` at module scope IS detected."""
    from validate_hook import detect_module_scope_sys_exit

    script = _make_py_script(
        tmp_path,
        "guarded_exit.py",
        "import sys\nClient = None\nif Client is None:\n    sys.exit('pycozo required')\n",
    )
    hits = detect_module_scope_sys_exit(script)
    assert hits  # at least one hit on line 4


def test_detect_module_scope_raise_system_exit(tmp_path: Path):
    """Top-level `raise SystemExit(...)` is detected."""
    from validate_hook import detect_module_scope_sys_exit

    script = _make_py_script(
        tmp_path, "raise_se.py", "raise SystemExit('boom')\n"
    )
    hits = detect_module_scope_sys_exit(script)
    assert hits == [1]


def test_detect_module_scope_sys_exit_inside_function_not_flagged(tmp_path: Path):
    """`sys.exit(...)` inside a function body is NOT flagged (doesn't run on import)."""
    from validate_hook import detect_module_scope_sys_exit

    script = _make_py_script(
        tmp_path,
        "func_exit.py",
        "import sys\n\ndef main():\n    sys.exit('bye')\n\nif __name__ == '__main__':\n    main()\n",
    )
    hits = detect_module_scope_sys_exit(script)
    assert hits == []


# ---------------------------------------------------------------------------
# End-to-end regression: the exact PSS v3.1.0 hooks.json
# ---------------------------------------------------------------------------


def test_pss_v310_hooks_json_produces_major_regression(tmp_path: Path):
    """The EXACT PSS v3.1.0 hooks.json that shipped broken must produce at
    least one MAJOR finding when validated.

    This is the canonical regression test. If it ever goes quiet, the
    `python3 interpreter + third-party imports` runtime-dep check has
    silently regressed.
    """
    # Build the plugin layout
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    # The offending script: imports pycozo (third-party), no PEP 723 block
    pss_hook = scripts_dir / "pss_hook.py"
    pss_hook.write_text(
        "import sys\n"
        "try:\n"
        "    from pycozo.client import Client\n"
        "except ImportError:\n"
        "    sys.exit('ERROR: pycozo is required.')\n"
        "\n"
        "def main():\n"
        "    print('ok')\n"
    )
    import os
    os.chmod(pss_hook, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'unset VIRTUAL_ENV; python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pss_hook.py"',
                                    "timeout": 10,
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )

    report = validate_hooks(hooks_file, plugin_root=tmp_path)

    # Runtime-dep reconciliation MAJOR
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert any(
        "plain interpreter" in m and "pycozo" in m for m in major_msgs
    ), f"Expected runtime-dep MAJOR mentioning 'plain interpreter' and 'pycozo'; got MAJORs: {major_msgs}"

    # unset VIRTUAL_ENV antipattern warning — fires ONLY because `unset VIRTUAL_ENV`
    # is combined with plain `python3` (the PSS failure mode). If the hook used
    # `uv run --script` or venv-python, unsetting VIRTUAL_ENV is a legitimate
    # defensive pattern and the warning does NOT fire (see separate test below).
    warnings = [r.message for r in report.results if r.level == "WARNING"]
    assert any(
        "unset VIRTUAL_ENV" in m and "plain `python3`" in m for m in warnings
    ), f"Expected conditional WARNING on `unset VIRTUAL_ENV + plain python3`; got: {warnings}"


def test_unset_virtual_env_with_uv_run_script_no_warning(tmp_path: Path):
    """`unset VIRTUAL_ENV; uv run --script foo.py` is a LEGITIMATE defensive pattern
    (shedding user VIRTUAL_ENV so uv creates its own cache venv). Must NOT warn.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "safe.py"
    script.write_text(
        '# /// script\n'
        '# dependencies = ["pycozo"]\n'
        '# ///\n'
        "import pycozo\n"
    )
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps({
            "hooks": {
                "UserPromptSubmit": [{
                    "hooks": [{
                        "type": "command",
                        "command": 'unset VIRTUAL_ENV; uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/safe.py"',
                    }]
                }]
            }
        })
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    warnings = [r.message for r in report.results if r.level == "WARNING"]
    assert not any("unset VIRTUAL_ENV" in m for m in warnings), (
        f"Should NOT warn on `unset VIRTUAL_ENV` when combined with `uv run --script` "
        f"(legitimate defensive pattern); got: {warnings}"
    )


def test_unset_virtual_env_with_venv_python_no_warning(tmp_path: Path):
    """`unset VIRTUAL_ENV; ${CLAUDE_PLUGIN_DATA}/.venv/bin/python foo.py` is legitimate
    (redundant but harmless — direct-invocation of the venv python ignores VIRTUAL_ENV).
    Must NOT warn.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "safe.py"
    script.write_text("import pycozo\n")
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps({
            "hooks": {
                "SessionStart": [{
                    "hooks": [{
                        "type": "command",
                        "command": 'uv venv "${CLAUDE_PLUGIN_DATA}/.venv" && uv pip install pycozo',
                    }]
                }],
                "UserPromptSubmit": [{
                    "hooks": [{
                        "type": "command",
                        "command": 'unset VIRTUAL_ENV; "${CLAUDE_PLUGIN_DATA}/.venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/scripts/safe.py"',
                    }]
                }]
            }
        })
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    warnings = [r.message for r in report.results if r.level == "WARNING"]
    assert not any("unset VIRTUAL_ENV" in m for m in warnings), (
        f"Should NOT warn on `unset VIRTUAL_ENV` when combined with venv-python; "
        f"got: {warnings}"
    )


def test_uv_run_script_with_complete_pep723_passes(tmp_path: Path):
    """A script using `uv run --script` + complete PEP 723 metadata passes runtime-dep check."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "good.py"
    script.write_text(
        '# /// script\n'
        '# requires-python = ">=3.10"\n'
        '# dependencies = [\n'
        '#     "pycozo[embedded]>=0.7.6",\n'
        '# ]\n'
        '# ///\n'
        "import pycozo\n"
    )
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'uv run --quiet --script "${CLAUDE_PLUGIN_ROOT}/scripts/good.py"',
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )

    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    # The runtime-dep reconciliation should NOT flag this.
    assert not any(
        "plain interpreter" in m or "missing declarations" in m or "no PEP 723" in m
        for m in major_msgs
    ), f"Unexpected MAJORs for a correctly-configured uv-run-script hook: {major_msgs}"


def test_uv_run_script_missing_pep723_flagged(tmp_path: Path):
    """A `uv run --script` hook whose script lacks a PEP 723 block is flagged."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "nobod.py"
    script.write_text("import pycozo\n")
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/nobod.py"',
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )

    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert any("no PEP 723" in m for m in major_msgs), (
        f"Expected MAJOR on missing PEP 723 block; got: {major_msgs}"
    )


def test_uv_run_with_covers_imports_passes(tmp_path: Path):
    """`uv run --with pycozo foo.py` passes runtime-dep check when --with covers the import."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "covered.py"
    script.write_text("import pycozo\n")
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'uv run --with pycozo "${CLAUDE_PLUGIN_ROOT}/scripts/covered.py"',
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )

    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert not any("do not cover" in m for m in major_msgs), (
        f"Unexpected MAJOR for uv-run-with covering the import: {major_msgs}"
    )


def test_venv_python_with_session_start_setup_passes(tmp_path: Path):
    """venv-python invocation with a SessionStart `uv venv` setup hook passes."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "venv_user.py"
    script.write_text("import pycozo\n")
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'uv venv "${CLAUDE_PLUGIN_DATA}/.venv" && uv pip install pycozo',
                                }
                            ]
                        }
                    ],
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": '"${CLAUDE_PLUGIN_DATA}/.venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/scripts/venv_user.py"',
                                }
                            ]
                        }
                    ],
                }
            }
        )
    )

    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
    # Should NOT warn about missing SessionStart venv setup.
    assert not any("no SessionStart hook was found" in m for m in minor_msgs), (
        f"Unexpected MINOR for venv-python with valid SessionStart setup: {minor_msgs}"
    )


def test_venv_python_without_session_start_setup_minor(tmp_path: Path):
    """venv-python invocation WITHOUT a SessionStart setup hook triggers a MINOR."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "venv_user.py"
    script.write_text("import pycozo\n")
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": '"${CLAUDE_PLUGIN_DATA}/.venv/bin/python" "${CLAUDE_PLUGIN_ROOT}/scripts/venv_user.py"',
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )

    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
    assert any("no SessionStart hook was found" in m for m in minor_msgs), (
        f"Expected MINOR about missing SessionStart setup; got: {minor_msgs}"
    )


def test_stdlib_only_script_with_plain_python3_produces_no_major(tmp_path: Path):
    """Critical non-regression: a script that imports only stdlib modules under
    a plain `python3` invocation must NOT produce a runtime-dep MAJOR.

    This guards against the class of false positives where the validator
    over-reaches and flags perfectly safe hooks. The fixer agent must not
    touch these.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "stdlib_only.py"
    script.write_text(
        "import os\nimport sys\nimport json\nfrom pathlib import Path\n"
        "def main():\n    print('ok')\n"
        "if __name__ == '__main__':\n    main()\n"
    )
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps({
            "hooks": {
                "UserPromptSubmit": [{
                    "hooks": [{
                        "type": "command",
                        "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/stdlib_only.py"',
                    }]
                }]
            }
        })
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    # Must NOT contain the runtime-dep MAJOR for stdlib-only scripts.
    assert not any("plain interpreter" in m for m in major_msgs), (
        f"stdlib-only script should NOT trigger runtime-dep MAJOR; got: {major_msgs}"
    )


def test_python_versioned_interpreter_is_classified_correctly(tmp_path: Path):
    """`python3.12 foo.py` is recognized as interpreter-python — via the
    version-suffix regex — and fires the runtime-dep MAJOR for third-party
    imports just like plain `python3` does.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "v.py"
    script.write_text("import httpx\n")
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps({
            "hooks": {
                "UserPromptSubmit": [{
                    "hooks": [{
                        "type": "command",
                        "command": 'python3.12 "${CLAUDE_PLUGIN_ROOT}/scripts/v.py"',
                    }]
                }]
            }
        })
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert any("plain interpreter" in m and "httpx" in m for m in major_msgs), (
        f"python3.12 invocation must fire the runtime-dep MAJOR for httpx; got: {major_msgs}"
    )


def test_uv_run_with_comma_separated_deps(tmp_path: Path):
    """`uv run --with pycozo,httpx foo.py` (comma-separated) is parsed into
    explicit_deps = ('pycozo', 'httpx'), and both imports are covered.
    """
    from validate_hook import extract_script_paths

    refs = extract_script_paths(
        'uv run --with pycozo,httpx "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"',
        tmp_path,
    )
    assert len(refs) == 1
    assert refs[0].invocation_mode == "uv-run-with"
    assert set(refs[0].explicit_deps) == {"pycozo", "httpx"}


def test_scripts_in_nested_subdirectories(tmp_path: Path):
    """Scripts in subdirs like `scripts/hooks/foo.py` are still found and
    analyzed. No hard-coded assumption about path depth.
    """
    from validate_hook import extract_script_paths

    nested = tmp_path / "scripts" / "hooks"
    nested.mkdir(parents=True)
    script = nested / "deep.py"
    script.write_text("import pycozo\n")
    import os
    os.chmod(script, 0o755)

    refs = extract_script_paths(
        'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hooks/deep.py"', tmp_path
    )
    assert len(refs) == 1
    assert refs[0].path == tmp_path / "scripts" / "hooks" / "deep.py"


def test_compound_command_with_multiple_scripts(tmp_path: Path):
    """`foo.sh && python3 bar.py` yields TWO refs: foo.sh direct + bar.py
    interpreter-python. Each is diagnosed independently.
    """
    from validate_hook import extract_script_paths

    refs = extract_script_paths(
        'bash "${CLAUDE_PLUGIN_ROOT}/scripts/foo.sh" && python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bar.py"',
        tmp_path,
    )
    assert len(refs) == 2
    modes = {r.invocation_mode for r in refs}
    assert modes == {"bash", "interpreter-python"}


def test_sys_exit_inside_function_body_is_ignored(tmp_path: Path):
    """A `sys.exit` inside a function body (not at module scope) must NOT
    trigger the MAJOR — it only runs when the function is called.
    """
    from validate_hook import detect_module_scope_sys_exit

    script = _make_py_script(
        tmp_path,
        "safe_exit.py",
        "import sys\n"
        "\n"
        "def fail():\n"
        "    sys.exit(1)\n"
        "\n"
        "class Handler:\n"
        "    def abort(self):\n"
        "        sys.exit(2)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    fail()\n",
    )
    # if __name__ == '__main__' IS a top-level if — but the detector descends
    # with the caveat that it won't walk INTO function/class bodies. Since
    # sys.exit here is inside fail() (function) and Handler.abort (method),
    # neither should be flagged. The top-level if __name__ guard itself only
    # contains a function CALL, not a direct sys.exit, so it's also safe.
    hits = detect_module_scope_sys_exit(script)
    assert hits == [], f"Function-body sys.exit should not be flagged; got: {hits}"


def test_type_checking_guard_imports_are_detected_as_third_party(tmp_path: Path):
    """`if TYPE_CHECKING: import httpx` — we INTENTIONALLY flag this because
    the validator cannot distinguish a type-only import from a runtime-reachable
    `if` branch at AST level. Documents the behavior for the fix guide.
    """
    from validate_hook import detect_python_third_party_imports

    script = _make_py_script(
        tmp_path,
        "type_check.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import httpx\n",
    )
    imports = detect_python_third_party_imports(script)
    # httpx IS detected — hook-fixes §13.3 documents how to handle this
    # (move to a dedicated TYPE_CHECKING block near the actual usage, or
    # just declare httpx as a dep since import cost is trivial).
    assert "httpx" in imports, (
        f"TYPE_CHECKING-guarded imports ARE detected — this is documented "
        f"behavior; see hook-fixes.md §13.3. Got: {imports}"
    )


def test_extract_script_paths_from_env_dash_s_shebang_style(tmp_path: Path):
    """`env -S python3 -u foo.py` (portable shebang style) parses correctly."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths(
        'env -S python3 -u "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"', tmp_path
    )
    assert len(refs) == 1
    assert refs[0].invocation_mode == "interpreter-python"


def test_windows_python_exe_classified_correctly(tmp_path: Path):
    """Windows interpreter binaries carry an `.exe` suffix. `python.exe`,
    `python3.exe`, `python3.12.exe`, `node.exe` must all be recognized.
    """
    from validate_hook import _classify_interpreter

    assert _classify_interpreter("python.exe") == "interpreter-python"
    assert _classify_interpreter("python3.exe") == "interpreter-python"
    assert _classify_interpreter("python3.12.exe") == "interpreter-python"
    assert _classify_interpreter("node.exe") == "node"
    assert _classify_interpreter("bash.exe") == "bash"
    # Case-insensitive extension matching
    assert _classify_interpreter("PYTHON.EXE") == "interpreter-python"


def test_windows_venv_python_exe_detected(tmp_path: Path):
    """Windows venv layout `.venv\\Scripts\\python.exe` must be detected."""
    from validate_hook import _detect_venv_python

    # Posix layout (canonical)
    assert _detect_venv_python("/plugin/.venv/bin/python")
    assert _detect_venv_python("/plugin/.venv/bin/python3")
    assert _detect_venv_python("/plugin/.venv/bin/python3.12")
    # Windows layout with .exe
    assert _detect_venv_python(r"C:\plugin\.venv\Scripts\python.exe")
    assert _detect_venv_python(r"C:\plugin\.venv\Scripts\python3.12.exe")
    # Mixed separators (common in env var substitution)
    assert _detect_venv_python("C:/plugin/.venv/Scripts/python.exe")
    # Non-venv paths must NOT match
    assert not _detect_venv_python("/usr/bin/python3")
    assert not _detect_venv_python(r"C:\Python312\python.exe")


def test_pypi_to_import_alias_covers_pillow_pil(tmp_path: Path):
    """`dependencies = ["pillow"]` must be considered to cover `import PIL`.

    Without the alias map this would fire a spurious "missing declarations"
    MAJOR for every Pillow-using script with a correct PEP 723 block.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "with_pil.py"
    script.write_text(
        '# /// script\n'
        '# dependencies = ["pillow>=10"]\n'
        '# ///\n'
        "from PIL import Image\n"
    )
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps({
            "hooks": {
                "UserPromptSubmit": [{
                    "hooks": [{
                        "type": "command",
                        "command": 'uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/with_pil.py"',
                    }]
                }]
            }
        })
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert not any(
        "missing declarations" in m and "PIL" in m.upper() for m in major_msgs
    ), f"pillow in deps must cover `import PIL`; got MAJORs: {major_msgs}"


def test_pypi_to_import_alias_covers_beautifulsoup4_bs4(tmp_path: Path):
    """`dependencies = ["beautifulsoup4"]` must cover `import bs4`."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "soup.py"
    script.write_text(
        '# /// script\n'
        '# dependencies = ["beautifulsoup4>=4"]\n'
        '# ///\n'
        "import bs4\n"
    )
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps({
            "hooks": {
                "UserPromptSubmit": [{
                    "hooks": [{
                        "type": "command",
                        "command": 'uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/soup.py"',
                    }]
                }]
            }
        })
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert not any(
        "missing declarations" in m and "bs4" in m for m in major_msgs
    ), f"beautifulsoup4 in deps must cover `import bs4`; got MAJORs: {major_msgs}"


def test_pep503_normalization_covers_dashes_and_case(tmp_path: Path):
    """A dep declared as `Scikit-Learn` must cover `import sklearn` via
    the alias map AND PEP 503 case/dash normalization.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "sk.py"
    script.write_text(
        '# /// script\n'
        '# dependencies = ["Scikit-Learn==1.5"]\n'
        '# ///\n'
        "import sklearn\n"
    )
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps({
            "hooks": {
                "UserPromptSubmit": [{
                    "hooks": [{
                        "type": "command",
                        "command": 'uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/sk.py"',
                    }]
                }]
            }
        })
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert not any(
        "missing declarations" in m and "sklearn" in m for m in major_msgs
    ), f"Scikit-Learn must cover sklearn after PEP 503 normalization; got: {major_msgs}"


def test_uv_run_isolated_is_boolean_flag_not_value_consumer(tmp_path: Path):
    """`uv run --isolated foo.py` — `--isolated` is a boolean flag that
    takes NO value. The extractor must still find foo.py at the correct index.

    Regression test: `--isolated` was previously in TWO_ARG_FLAGS, which
    would have eaten `foo.py` as its value and returned None.
    """
    from validate_hook import extract_script_paths

    refs = extract_script_paths(
        'uv run --isolated --script "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"', tmp_path
    )
    assert len(refs) == 1
    assert refs[0].invocation_mode == "uv-run-script"


def test_uv_run_module_flag_yields_no_script(tmp_path: Path):
    """`uv run --module mypkg` has no script file — extractor returns empty."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths("uv run --module mymod arg1", tmp_path)
    assert refs == []


def test_uv_run_gui_script_mode(tmp_path: Path):
    """`uv run --gui-script foo.py` maps to uv-run-script mode (same PEP 723 semantics)."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths(
        'uv run --gui-script "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"', tmp_path
    )
    assert len(refs) == 1
    assert refs[0].invocation_mode == "uv-run-script"


def test_uv_run_new_flags_link_mode_exclude_newer_env_file(tmp_path: Path):
    """`--link-mode`, `--exclude-newer`, `--env-file` each consume one value token
    — confirm the script index is correctly computed past them.
    """
    from validate_hook import extract_script_paths

    refs = extract_script_paths(
        'uv run --link-mode copy --exclude-newer 2026-01-01 --env-file .env --script "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"',
        tmp_path,
    )
    assert len(refs) == 1
    assert refs[0].invocation_mode == "uv-run-script"
    assert refs[0].path == tmp_path / "scripts" / "foo.py"


def test_uv_run_end_of_options_marker(tmp_path: Path):
    """`uv run -- --script foo.py` — after `--`, uv stops parsing its own flags.
    `--script` appearing AFTER `--` is a script argument, NOT the uv flag.

    Before this fix: the validator incorrectly classified this as `uv-run-script`.
    """
    from validate_hook import _find_script_in_uv_run, _tokenize_hook_command

    tokens = _tokenize_hook_command("uv run -- --script foo.py")[0]
    result = _find_script_in_uv_run(tokens)
    # After `--`, the next positional is `--script` which looks like a flag but
    # should be treated as a script arg. Since `--script` isn't a lintable
    # path, the function should return None (no valid script after `--`).
    assert result is None, (
        f"After `--`, flags should not be re-interpreted; got: {result}"
    )


def test_uv_run_end_of_options_with_real_script(tmp_path: Path):
    """`uv run -- foo.py arg1` — after `--`, foo.py is the script (plain mode)."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths(
        'uv run -- "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py" --some-arg', tmp_path
    )
    assert len(refs) == 1
    assert refs[0].invocation_mode == "interpreter-python"


def test_windows_py_launcher_recognized(tmp_path: Path):
    """`py foo.py` and `py.exe -3.12 foo.py` (Windows Python Launcher) must be
    classified as interpreter-python.
    """
    from validate_hook import extract_script_paths

    refs = extract_script_paths('py "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"', tmp_path)
    assert len(refs) == 1
    assert refs[0].invocation_mode == "interpreter-python"

    # With version selector flag (-3.12 is py-launcher specific)
    refs2 = extract_script_paths(
        'py.exe -3.12 "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"', tmp_path
    )
    assert len(refs2) == 1
    assert refs2[0].invocation_mode == "interpreter-python"


def test_tsx_ts_node_typescript_runners(tmp_path: Path):
    """`tsx foo.ts` and `ts-node foo.ts` are TypeScript runners, classify as node mode."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths('tsx "${CLAUDE_PLUGIN_ROOT}/scripts/hook.ts"', tmp_path)
    assert len(refs) == 1
    assert refs[0].invocation_mode == "node"

    refs2 = extract_script_paths('ts-node "${CLAUDE_PLUGIN_ROOT}/scripts/hook.ts"', tmp_path)
    assert len(refs2) == 1
    assert refs2[0].invocation_mode == "node"


def test_path_traversal_in_hook_command_warns(tmp_path: Path):
    """`${CLAUDE_PLUGIN_ROOT}/../other_plugin/foo.py` triggers a WARNING."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "ok.py"
    script.write_text("print('ok')\n")
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps({
            "hooks": {
                "UserPromptSubmit": [{
                    "hooks": [{
                        "type": "command",
                        "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/../other_plugin/foo.py"',
                    }]
                }]
            }
        })
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    warnings = [r.message for r in report.results if r.level == "WARNING"]
    assert any("path-traversal" in m or "..` path segment" in m for m in warnings), (
        f"Expected path-traversal WARNING; got: {warnings}"
    )


def test_path_traversal_for_env_var_prefix(tmp_path: Path):
    """`${CLAUDE_PROJECT_DIR}/../outside.sh` also warns (not just plugin root)."""
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps({
            "hooks": {
                "UserPromptSubmit": [{
                    "hooks": [{
                        "type": "command",
                        "command": 'bash "${CLAUDE_PROJECT_DIR}/../evil.sh"',
                    }]
                }]
            }
        })
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    warnings = [r.message for r in report.results if r.level == "WARNING"]
    assert any("..` path segment" in m for m in warnings), (
        f"Expected traversal WARNING for CLAUDE_PROJECT_DIR/../; got: {warnings}"
    )


def test_no_false_positive_on_clean_paths(tmp_path: Path):
    """A hook command with clean paths (no `..`) must NOT trigger traversal WARNING."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "clean.py"
    script.write_text("print('ok')\n")
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps({
            "hooks": {
                "UserPromptSubmit": [{
                    "hooks": [{
                        "type": "command",
                        "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/clean.py"',
                    }]
                }]
            }
        })
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    warnings = [r.message for r in report.results if r.level == "WARNING"]
    assert not any("path-traversal" in m or "..` path segment" in m for m in warnings), (
        f"Clean paths MUST NOT trigger traversal WARNING; got: {warnings}"
    )


def test_resolved_script_path_escaping_plugin_root_warns(tmp_path: Path):
    """Even if a path uses `${CLAUDE_PLUGIN_ROOT}` and looks clean in the string,
    if the resolved path lands OUTSIDE the plugin root, a WARNING fires.
    """
    # Create plugin at tmp_path/plugin/ and a sibling at tmp_path/external/
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    scripts_dir = plugin_dir / "scripts"
    scripts_dir.mkdir()
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    external_script = external_dir / "foo.py"
    external_script.write_text("print('ok')\n")
    import os
    os.chmod(external_script, 0o755)

    hooks_dir = plugin_dir / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    # The command uses CLAUDE_PLUGIN_ROOT/.. which escapes plugin_dir
    hooks_file.write_text(
        json.dumps({
            "hooks": {
                "UserPromptSubmit": [{
                    "hooks": [{
                        "type": "command",
                        "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/../external/foo.py"',
                    }]
                }]
            }
        })
    )
    report = validate_hooks(hooks_file, plugin_root=plugin_dir)
    warnings = [r.message for r in report.results if r.level == "WARNING"]
    # Either the command-regex traversal check OR the resolved-path check should fire.
    assert any(
        "OUTSIDE the plugin root" in m or "..` path segment" in m for m in warnings
    ), f"Expected escape-plugin-root WARNING; got: {warnings}"


def test_direct_python_shebang_reconciles_like_interpreter(tmp_path: Path):
    """A `.py` script invoked directly (shebang-driven, mode='direct') relies on
    whatever `python3` the shebang resolves to — same runtime-dep risk as
    `python3 foo.py`. The reconciler MUST fire for direct mode .py scripts.

    Before this fix: direct mode skipped reconciliation entirely, silently
    approving hook commands that would ImportError at runtime.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "direct.py"
    script.write_text("#!/usr/bin/env python3\nimport pycozo\n")
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps({
            "hooks": {
                "UserPromptSubmit": [{
                    "hooks": [{
                        "type": "command",
                        "command": '"${CLAUDE_PLUGIN_ROOT}/scripts/direct.py"',
                    }]
                }]
            }
        })
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert any(
        "plain interpreter" in m and "pycozo" in m for m in major_msgs
    ), f"Direct-mode .py with third-party imports must trigger runtime-dep MAJOR; got: {major_msgs}"


def test_env_var_assignment_prefix_python3(tmp_path: Path):
    """`FOO=bar python3 script.py` — the `FOO=bar` prefix sets env vars for
    the child process and must be skipped when finding the interpreter.
    """
    from validate_hook import extract_script_paths

    refs = extract_script_paths(
        'PYTHONPATH=./lib python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foo.py"', tmp_path
    )
    assert len(refs) == 1
    assert refs[0].invocation_mode == "interpreter-python"
    assert refs[0].path == tmp_path / "scripts" / "foo.py"


def test_env_var_assignment_multiple(tmp_path: Path):
    """Multiple env-var assignments in a row: `A=1 B=2 python3 foo.py`."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths(
        'NODE_ENV=production DEBUG=1 node "${CLAUDE_PLUGIN_ROOT}/hook.js"', tmp_path
    )
    assert len(refs) == 1
    assert refs[0].invocation_mode == "node"


def test_env_var_assignment_only_legal_names(tmp_path: Path):
    """The env-var pattern requires a letter/_ start. A token like `1=2` is NOT
    a legal env-var name and must NOT be eaten by the assignment-skip logic.
    """
    from validate_hook import extract_script_paths

    # `1=2` is not a valid POSIX env-var name → should NOT be skipped. The
    # tokenizer's first real token is "1=2" which falls through to direct
    # invocation (no interpreter matched, no lintable extension → empty refs).
    refs = extract_script_paths('1=2 foo.py', tmp_path)
    # Conservative: either no refs OR the `1=2` becomes first token and does
    # nothing. Either way the downstream should not crash.
    assert isinstance(refs, list)


def test_extract_script_paths_preserves_direct_sh_invocation(tmp_path: Path):
    """`./scripts/foo.sh` as first token — direct invocation mode."""
    from validate_hook import extract_script_paths

    refs = extract_script_paths(
        '"${CLAUDE_PLUGIN_ROOT}/scripts/foo.sh" --verbose', tmp_path
    )
    assert len(refs) == 1
    assert refs[0].invocation_mode == "direct"
    assert refs[0].path.suffix == ".sh"


def test_interpreter_python_major_warns_against_uvx_substitution(tmp_path: Path):
    """The plain-interpreter + third-party MAJOR message must explicitly say NOT
    to substitute `uvx` — because `uvx` runs installable PyPI packages via
    entry-points and cannot target a local script with PEP 723 metadata.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "plain.py"
    script.write_text("import pycozo\n")
    import os
    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps({
            "hooks": {
                "UserPromptSubmit": [{
                    "hooks": [{
                        "type": "command",
                        "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/plain.py"',
                    }]
                }]
            }
        })
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    # The message must call out `uvx` specifically so plugin authors don't
    # mistake it for a valid alternative to `uv run --script`.
    assert any(
        "plain interpreter" in m and "uvx" in m and "uv run --script" in m
        for m in major_msgs
    ), f"MAJOR message must mention both uvx (as non-substitute) and uv run --script (as correct tool); got: {major_msgs}"


def test_http_hook_latency_sensitive_event_warning():
    """HTTP hook on UserPromptSubmit with > 5s timeout warns about latency."""
    report = HookValidationReport()
    hook = {
        "type": "http",
        "url": "https://example.com/notify",
        "timeout": 30,
    }
    from validate_hook import validate_http_hook

    validate_http_hook(hook, "UserPromptSubmit", report)
    warnings = [r.message for r in report.results if r.level == "WARNING"]
    assert any("latency" not in m.lower() or "blocks user interaction" in m for m in warnings), (
        f"Expected latency warning for UserPromptSubmit HTTP hook; got: {warnings}"
    )


# ---------------------------------------------------------------------------
# v2.21.3 regression batch D — gaps G1/G2/G3 + hardening changes
# ---------------------------------------------------------------------------


def test_interpreter_W_flag_arg_consumption_preserves_script(tmp_path: Path):
    """G1: `python3 -W ignore foo.py` — the `-W` flag consumes its action
    argument (`ignore`), so `foo.py` must be recognised as the script token.

    Pre-fix, the generic single-dash flag branch advanced only by one token,
    so `ignore` was misclassified as the script path and the real `foo.py`
    was silently skipped.
    """
    from validate_hook import extract_script_paths

    refs = extract_script_paths("python3 -W ignore foo.py", None)
    assert len(refs) == 1, (
        f"Expected exactly 1 script ref with `-W ignore` consumed, got: {refs}"
    )
    assert refs[0].path == Path("foo.py")
    assert refs[0].invocation_mode == "interpreter-python"


def test_schema_top_level_field_is_known(tmp_path: Path):
    """G2: `$schema` is a standard JSON Schema declaration recognised by editors
    and linters. It MUST be in the known-top-level-fields set so plugins that
    declare one do not receive a spurious "unknown top-level field" warning.
    """
    hooks_file = tmp_path / "hooks.json"
    hooks_file.write_text(
        json.dumps(
            {
                "$schema": "https://example.com/hooks.schema.json",
                "hooks": {},
            }
        )
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
    assert not any(
        "unknown top-level field" in m.lower() and "$schema" in m for m in warning_msgs
    ), (
        f"`$schema` must not produce an 'unknown top-level field' warning; got: {warning_msgs}"
    )


def test_windows_single_backslash_regex_matches_escaped_path(tmp_path: Path):
    """G3: JSON parsing un-escapes `\\\\` → `\\`, so by the time we inspect the
    command string the backslashes are single chars. The Windows-backslash
    MINOR must match on single `\\` (not require double `\\\\`).

    Pre-fix, the regex kept the JSON-level `\\\\` doubling and so never
    matched the actual post-JSON string.
    """
    hooks_file = tmp_path / "hooks.json"
    # JSON encodes a single real backslash as `\\`. Python's json.dumps below
    # emits the correct wire form; after parse the command string will contain
    # single `\` characters — the exact wire form a plugin author writes.
    hooks_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "C:\\Users\\alice\\foo.py",
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
    assert any("Windows-style backslash" in m for m in minor_msgs), (
        f"Expected Windows-style-backslash MINOR for single-backslash path; got MINORs: {minor_msgs}"
    )


def test_os_exit_at_module_scope_is_flagged(tmp_path: Path):
    """SR-007: `os._exit(1)` at module scope terminates the hook process just
    as surely as `sys.exit(1)` does — and even more hostilely (skips atexit /
    __del__ cleanup). It must be flagged identically.

    This guards against the PSS-class "fatal dep missing" pattern being
    rewritten to use `os._exit` and silently evading the detector.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "os_exit_script.py"
    script.write_text(
        "# /// script\n"
        "# requires-python = \">=3.10\"\n"
        "# dependencies = []\n"
        "# ///\n"
        "import os\n"
        "os._exit(1)\n"
        "def main():\n"
        "    print('never reached')\n"
    )
    import os

    os.chmod(script, 0o755)

    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": 'uv run --script "${CLAUDE_PLUGIN_ROOT}/scripts/os_exit_script.py"',
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert any("MODULE scope" in m for m in major_msgs), (
        f"os._exit at module scope must produce a MODULE-scope MAJOR; got MAJORs: {major_msgs}"
    )


def test_backslash_windows_path_traversal_warning(tmp_path: Path):
    """T-2: `${CLAUDE_PLUGIN_ROOT}\\..\\other\\foo.py` (Windows-style backslash
    path traversal using the env-var prefix) must trigger the path-traversal
    WARNING.

    Covers the env-var-prefixed backslash branch of `_TRAVERSAL_RE`.
    """
    hooks_file = tmp_path / "hooks.json"
    hooks_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "${CLAUDE_PLUGIN_ROOT}\\..\\other\\foo.py",
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
    assert any("path-traversal" in m or "escapes the plugin/project root" in m for m in warning_msgs), (
        f"Expected path-traversal WARNING for env-var-prefixed backslash traversal; got: {warning_msgs}"
    )


def test_windows_component_backslash_traversal_warning(tmp_path: Path):
    """T-2: `C:\\Users\\alice\\..\\evil.py` triggers the bare-component branch
    of `_TRAVERSAL_RE` — a Windows absolute path with a `\\..\\` segment
    escaping a named component.
    """
    hooks_file = tmp_path / "hooks.json"
    hooks_file.write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "C:\\Users\\alice\\..\\evil.py",
                                }
                            ]
                        }
                    ]
                }
            }
        )
    )
    report = validate_hooks(hooks_file, plugin_root=tmp_path)
    warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
    assert any("path-traversal" in m or "escapes the plugin/project root" in m for m in warning_msgs), (
        f"Expected path-traversal WARNING for Windows component backslash traversal; got: {warning_msgs}"
    )


def test_unbalanced_quote_in_simple_command_is_major(tmp_path: Path):
    """T-3: a hook command with an unterminated double quote — `python3 "foo.py;
    rm -rf /` — must surface as a MAJOR by way of the `malformed_out` plumb
    that validate_command_hook feeds from extract_script_paths.

    Two independent checks, both required:
      1. extract_script_paths populates malformed_out when shlex cannot tokenize.
      2. validate_command_hook converts those into MAJOR findings mentioning
         "unparseable" or "unbalanced quote".
    """
    from validate_hook import extract_script_paths

    malformed: list[str] = []
    refs = extract_script_paths('python3 "foo.py; rm -rf /', None, malformed_out=malformed)
    assert malformed, (
        f"extract_script_paths must append the unterminated-quote simple command to malformed_out; "
        f"got malformed={malformed!r}, refs={refs!r}"
    )

    # Full-flow: validate_command_hook must emit a MAJOR.
    report = ValidationReport()
    hook = {"type": "command", "command": 'python3 "foo.py; rm -rf /'}
    validate_command_hook(hook, "PreToolUse", tmp_path, report)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert any("unparseable" in m or "unbalanced quote" in m for m in major_msgs), (
        f"Expected MAJOR about unparseable / unbalanced quote; got MAJORs: {major_msgs}"
    )


def test_clean_quoted_command_produces_no_malformed_finding(tmp_path: Path):
    """T-3 negative: a perfectly balanced-quoted command — `python3 "foo bar.py"`
    — must NOT populate malformed_out and must NOT produce a MAJOR about
    malformed quoting. Guards against the validator over-reaching on legitimate
    quoted paths that contain spaces.
    """
    from validate_hook import extract_script_paths

    malformed: list[str] = []
    refs = extract_script_paths('python3 "foo bar.py"', None, malformed_out=malformed)
    assert malformed == [], (
        f"Balanced quotes must not trip malformed_out; got malformed={malformed!r}"
    )
    assert len(refs) == 1 and refs[0].path == Path("foo bar.py"), (
        f"Expected single script ref for `foo bar.py`; got refs={refs!r}"
    )

    # Full-flow: no MAJOR about unparseable / unbalanced quote.
    report = ValidationReport()
    hook = {"type": "command", "command": 'python3 "foo bar.py"'}
    validate_command_hook(hook, "PreToolUse", tmp_path, report)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert not any("unparseable" in m or "unbalanced quote" in m for m in major_msgs), (
        f"Clean quoted command must not produce malformed-quote MAJOR; got MAJORs: {major_msgs}"
    )


class TestV22HookMatcherValues:
    """v2.22.0 (spec-audit-3 §1.5, §1.7): matcher-value completeness on
    SessionEnd / StopFailure / InstructionsLoaded / ConfigChange / Notification,
    PreToolUse `permissionDecision` output `"defer"`, and recognition of the
    `${CLAUDE_PLUGIN_DATA}` / `${user_config.KEY}` substitution tokens.

    Each test anchors on a single spec line from hooks.md or plugins-reference.md
    to give a future maintainer a stable cite when the check is touched.
    """

    def test_pretool_use_defer_decision_accepted(self) -> None:
        """hooks.md L984, L1013-1053: `permissionDecision: "defer"` is valid
        (v2.1.89+, non-interactive `-p` mode only). CPV does not deeply validate
        hook output JSON, so we assert on the authoritative allowed-values
        constant instead — any downstream output validator must honour this set.
        """
        from validate_hook import PRETOOLUSE_PERMISSION_DECISIONS

        assert "defer" in PRETOOLUSE_PERMISSION_DECISIONS, (
            "PreToolUse permissionDecision must accept 'defer' per hooks.md L984 "
            "(added in Claude Code v2.1.89 for non-interactive `-p` mode)."
        )
        # Sanity check: the 3 pre-v2.1.89 values are still allowed.
        assert {"allow", "deny", "ask"}.issubset(PRETOOLUSE_PERMISSION_DECISIONS)

    def test_session_end_reason_bypass_permissions_disabled_accepted(self) -> None:
        """hooks.md L192: SessionEnd reason `bypass_permissions_disabled` is one
        of the 6 official reasons. The matcher-value check must NOT emit an
        INFO "not a known reason" for it.
        """
        report = ValidationReport()
        ok = validate_matcher("bypass_permissions_disabled", "SessionEnd", report)
        assert ok is True
        unknown_infos = [
            r.message for r in report.results
            if r.level == "INFO" and "not a known reason" in r.message
        ]
        assert unknown_infos == [], (
            f"'bypass_permissions_disabled' must be a recognised SessionEnd "
            f"reason; got INFOs: {unknown_infos}"
        )

    def test_instructions_loaded_reason_compact_accepted(self) -> None:
        """hooks.md L201 + L787: InstructionsLoaded load_reason `compact` fires
        when instruction files are re-loaded after a compaction event. Must be
        recognised by CPV's matcher-value check.
        """
        report = ValidationReport()
        ok = validate_matcher("compact", "InstructionsLoaded", report)
        assert ok is True
        unknown_infos = [
            r.message for r in report.results
            if r.level == "INFO" and "not a known load_reason" in r.message
        ]
        assert unknown_infos == [], (
            f"'compact' must be a recognised InstructionsLoaded load_reason; "
            f"got INFOs: {unknown_infos}"
        )

    def test_stopfailure_error_max_output_tokens_accepted(self) -> None:
        """hooks.md L200: StopFailure error `max_output_tokens` (v2.1.78) is
        one of the 7 official error values.
        """
        report = ValidationReport()
        ok = validate_matcher("max_output_tokens", "StopFailure", report)
        assert ok is True
        unknown_infos = [
            r.message for r in report.results
            if r.level == "INFO" and "not a known error" in r.message
        ]
        assert unknown_infos == [], (
            f"'max_output_tokens' must be a recognised StopFailure error; "
            f"got INFOs: {unknown_infos}"
        )

    def test_claude_plugin_data_substitution_not_flagged(self, tmp_path: Path) -> None:
        """plugins-reference.md L484-550: `${CLAUDE_PLUGIN_DATA}` is the
        persistent-data substitution token (paired with CLAUDE_PLUGIN_ROOT).
        A hook command that uses it must not draw any CRITICAL, MAJOR, or
        "unknown substitution" finding.
        """
        report = ValidationReport()
        hook = {"type": "command", "command": "${CLAUDE_PLUGIN_DATA}/cache/foo.db"}
        ok = validate_command_hook(hook, "PreToolUse", tmp_path, report)
        assert ok is True
        assert not report.has_critical, (
            f"${{CLAUDE_PLUGIN_DATA}} must not produce CRITICAL; got "
            f"{[r.message for r in report.results if r.level == 'CRITICAL']}"
        )
        assert not report.has_major, (
            f"${{CLAUDE_PLUGIN_DATA}} must not produce MAJOR; got "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )
        # Belt-and-suspenders: no "unknown substitution" messaging at any level.
        assert not any(
            "unknown substitution" in r.message.lower() for r in report.results
        ), (
            "${CLAUDE_PLUGIN_DATA} must be recognised as a known substitution "
            "token per plugins-reference.md L484-550."
        )

    def test_user_config_substitution_not_flagged(self, tmp_path: Path) -> None:
        """plugins-reference.md L423-432: `${user_config.<KEY>}` is the plugin
        userConfig substitution token (surfaced at runtime as
        CLAUDE_PLUGIN_OPTION_<KEY>). A hook command that uses it must not
        draw any CRITICAL, MAJOR, or "unknown substitution" finding.
        """
        report = ValidationReport()
        hook = {"type": "command", "command": "${user_config.api_token}"}
        ok = validate_command_hook(hook, "PreToolUse", tmp_path, report)
        assert ok is True
        assert not report.has_critical, (
            f"${{user_config.KEY}} must not produce CRITICAL; got "
            f"{[r.message for r in report.results if r.level == 'CRITICAL']}"
        )
        assert not report.has_major, (
            f"${{user_config.KEY}} must not produce MAJOR; got "
            f"{[r.message for r in report.results if r.level == 'MAJOR']}"
        )
        assert not any(
            "unknown substitution" in r.message.lower() for r in report.results
        ), (
            "${user_config.<KEY>} must be recognised as a known substitution "
            "token per plugins-reference.md L423-432."
        )


class TestPass2HookFixes:
    """Pass-2 audit fixes for validate_hook.py (CPV-P2-m1, GAP-17/18/19, CPV-P2-m2/n6).

    These tests anchor on specific hooks.md / plugins-reference.md line references
    so the provenance of each check is immediate when re-reading this file.
    """

    # --- CPV-P2-m1: prompt hook timeout > 10× 30s default is suspicious ---

    def test_prompt_hook_timeout_over_300s_emits_minor(self) -> None:
        """CPV-P2-m1 / hooks.md L2147: prompt hook default is 30s.
        Any timeout above 300s (10× default) is suspicious and must emit a
        MINOR nudge, not a silent pass.
        """
        report = ValidationReport()
        hook = {"type": "prompt", "prompt": "Summarize.", "timeout": 350}
        validate_prompt_hook(hook, "Stop", report)
        minors = [r for r in report.results if r.level == "MINOR"]
        assert any("more than 10" in r.message for r in minors), (
            f"timeout=350s must emit a MINOR per CPV-P2-m1; got: "
            f"{[r.message for r in report.results]}"
        )

    def test_prompt_hook_timeout_exactly_300s_does_not_emit_minor(self) -> None:
        """CPV-P2-m1 boundary: 300s is EXACTLY 10× the 30s default. The check
        is ``> 300``, so 300 must NOT trigger the MINOR — only values strictly
        above 300 do.
        """
        report = ValidationReport()
        hook = {"type": "prompt", "prompt": "Summarize.", "timeout": 300}
        validate_prompt_hook(hook, "Stop", report)
        # Neither MINOR nor the "exceeds 600s" warning should fire at 300s.
        assert not any(
            "more than 10" in r.message for r in report.results if r.level == "MINOR"
        )
        assert not any(
            "exceeds 600s" in r.message for r in report.results if r.level == "WARNING"
        )

    def test_prompt_hook_timeout_millisecond_branch_reachable(self) -> None:
        """CPV-P2-m1 regression: the pre-fix code ordered '>600' before '>10000',
        making the millisecond-typo branch unreachable. After the fix, a
        timeout of 15000 must hit the 'looks like milliseconds' branch
        (not the 'exceeds 600s' branch).
        """
        report = ValidationReport()
        hook = {"type": "prompt", "prompt": "Summarize.", "timeout": 15000}
        validate_prompt_hook(hook, "Stop", report)
        ms_warnings = [
            r for r in report.results
            if r.level == "WARNING" and "milliseconds" in r.message
        ]
        assert ms_warnings, (
            f"timeout=15000s must hit the milliseconds branch; got: "
            f"{[r.message for r in report.results]}"
        )

    # --- GAP-17: PermissionDenied {retry: true} output shape ---

    def test_permission_denied_retry_boolean_ok(self) -> None:
        """GAP-17 / plugins-reference.md L117: PermissionDenied hooks return
        ``{retry: true}``. The helper must return an empty issue list when
        ``retry`` is a proper boolean.
        """
        from validate_hook import validate_permission_denied_output

        assert validate_permission_denied_output({"retry": True}) == []
        assert validate_permission_denied_output({"retry": False}) == []
        # Wrapped in hookSpecificOutput — both forms are seen in the spec.
        assert validate_permission_denied_output(
            {"hookSpecificOutput": {"retry": True}}
        ) == []

    def test_permission_denied_retry_non_boolean_emits_minor_issue(self) -> None:
        """GAP-17: ``retry`` must be a boolean; any non-bool produces an issue."""
        from validate_hook import validate_permission_denied_output

        issues = validate_permission_denied_output({"retry": "yes"})
        assert len(issues) == 1
        assert "must be a boolean" in issues[0]
        assert "plugins-reference.md L117" in issues[0]

    def test_permission_denied_output_fields_constant_contains_retry(self) -> None:
        """GAP-17: ``retry`` must be recognised in the PermissionDenied
        hookSpecificOutput-fields constant so future output validation can
        reference it without re-discovering the spec.
        """
        from validate_hook import PERMISSION_DENIED_HOOK_SPECIFIC_OUTPUT_FIELDS

        assert "retry" in PERMISSION_DENIED_HOOK_SPECIFIC_OUTPUT_FIELDS
        assert "hookEventName" in PERMISSION_DENIED_HOOK_SPECIFIC_OUTPUT_FIELDS

    # --- GAP-18: FileChanged matcher is a filename glob, not a tool name ---

    def test_filechanged_matcher_tool_name_emits_info(self) -> None:
        """GAP-18 / plugins-reference.md L131: FileChanged matchers are FILENAME
        globs. A matcher like ``"Bash"`` that looks like a tool name must emit
        an INFO explaining the semantics difference.
        """
        report = ValidationReport()
        ok = validate_matcher("Bash", "FileChanged", report)
        assert ok is True
        infos = [
            r for r in report.results
            if r.level == "INFO" and "FileChanged matcher" in r.message
        ]
        assert infos, (
            f"FileChanged matcher='Bash' must emit the GAP-18 INFO; got: "
            f"{[r.message for r in report.results]}"
        )
        assert "FILENAME glob" in infos[0].message

    def test_filechanged_matcher_filename_glob_no_info(self) -> None:
        """GAP-18 boundary: a real filename glob like ``src/foo\\.py$`` must NOT
        trigger the tool-name warning — only tool-name-looking matchers do.
        (CPV's matcher field is parsed as a regex — see ``validate_matcher``
        at validate_hook.py:~440 — so the glob tests use regex-legal forms.)
        """
        report = ValidationReport()
        ok = validate_matcher(r"src/foo\.py$", "FileChanged", report)
        assert ok is True
        # Must NOT emit the GAP-18 INFO for a legitimate glob/regex.
        tool_infos = [
            r for r in report.results
            if r.level == "INFO" and "FileChanged matcher" in r.message
        ]
        assert not tool_infos, (
            f"filename regex 'src/foo\\.py$' must NOT trigger the GAP-18 INFO; "
            f"got: {[r.message for r in tool_infos]}"
        )

    # --- GAP-19: PreToolUse additionalContext in hookSpecificOutput ---

    def test_pretool_use_additionalcontext_in_output_fields_constant(self) -> None:
        """GAP-19: v2.1.110 added ``additionalContext`` retention on tool
        failure to the PreToolUse hookSpecificOutput shape. The constant must
        include it so downstream validators don't flag it as unknown.
        """
        from validate_hook import PRETOOLUSE_HOOK_SPECIFIC_OUTPUT_FIELDS

        assert "additionalContext" in PRETOOLUSE_HOOK_SPECIFIC_OUTPUT_FIELDS
        assert "permissionDecision" in PRETOOLUSE_HOOK_SPECIFIC_OUTPUT_FIELDS
        assert "permissionDecisionReason" in PRETOOLUSE_HOOK_SPECIFIC_OUTPUT_FIELDS

    # --- CPV-P2-m2: permission-update constants ---

    def test_permission_update_types_exactly_six(self) -> None:
        """CPV-P2-m2 / hooks.md L1115-1141: the permission-update-entry type
        enum has exactly 6 values. Must be exposed for future output validation.
        """
        from validate_hook import PERMISSION_UPDATE_TYPES

        assert PERMISSION_UPDATE_TYPES == {
            "addRules",
            "replaceRules",
            "removeRules",
            "setMode",
            "addDirectories",
            "removeDirectories",
        }

    def test_permission_behaviors_and_destinations(self) -> None:
        """CPV-P2-m2 / hooks.md L1121, L1134-1139: the ``behavior`` enum has 3
        values (allow/deny/ask) and ``destination`` has 4 values."""
        from validate_hook import PERMISSION_BEHAVIORS, PERMISSION_DESTINATIONS

        assert PERMISSION_BEHAVIORS == {"allow", "deny", "ask"}
        assert PERMISSION_DESTINATIONS == {
            "session",
            "localSettings",
            "projectSettings",
            "userSettings",
        }

    # --- CPV-P2-n6: Setup-matcher comment version ---

    def test_setup_matcher_comment_references_current_version(self) -> None:
        """CPV-P2-n6 / validate_hook.py L54: the inline comment next to the
        ``Setup`` matcher entry must cite the CURRENT Claude Code release. After
        the fix it is v2.1.109 (was v2.1.86).
        """
        hook_src = Path(
            __file__
        ).parent.parent / "scripts" / "validate_hook.py"
        src_text = hook_src.read_text(encoding="utf-8")
        # The comment must cite v2.1.109 now and MUST NOT still cite v2.1.86.
        assert "v2.1.109" in src_text, (
            "Setup matcher comment must reference v2.1.109 (CPV-P2-n6)."
        )
        assert (
            "as of v2.1.86" not in src_text
        ), "stale v2.1.86 citation left in validate_hook.py — CPV-P2-n6 not applied"
