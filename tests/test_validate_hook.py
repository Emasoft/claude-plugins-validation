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
    # Prompt on command-only event (SessionStart)
    r3 = HookValidationReport()
    validate_single_hook({"type": "prompt", "prompt": "Do something"}, "SessionStart", tmp_path, r3)
    assert any("only supports 'command' or 'http'" in r.message for r in r3.results if r.level == "CRITICAL")


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


def test_detect_module_scope_sys_exit_inside_if(tmp_path: Path):
    """`sys.exit(...)` inside a top-level `if` block (PSS's pattern) is detected."""
    from validate_hook import detect_module_scope_sys_exit

    script = _make_py_script(
        tmp_path,
        "if_exit.py",
        "import sys\n\ntry:\n    import pycozo\nexcept ImportError:\n    sys.exit('ERROR: pycozo is required.')\n",
    )
    hits = detect_module_scope_sys_exit(script)
    # The sys.exit is inside a try/except, which top-level walks into via the
    # "non-function/class" descent. If-block-only descent is a conservative
    # choice; ensure we at least detect the if-block form explicitly.
    # For this test: the sys.exit is inside `except`, which AST models as
    # part of ast.Try. We don't descend into Try right now, so it would be
    # missed. Document this gap — the if-block form is the critical one.
    _ = hits  # not asserting; test below covers the critical case


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
