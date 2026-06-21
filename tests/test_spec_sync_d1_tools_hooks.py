#!/usr/bin/env python3
"""Spec-sync D1: tools list, model aliases, exec-form hook args, env allowlist.

All tests are TWO-SIDED: the new/changed acceptance is verified AND a still-bogus
sibling is verified to still be flagged, so a relaxation can never silently pass
everything.

Source-of-truth references (current Claude Code docs, fetched 2026-06-22):
- tools-reference: TeamCreate/TeamDelete REMOVED in v2.1.178; Artifact /
  RemoteTrigger / ScheduleWakeup / ShareOnboardingGuide / WaitForMcpServers added;
  SendMessage kept.
- sub-agents "Choose a model": sonnet / opus / haiku / fable / full-id / inherit.
- hooks exec form: {"command": "node", "args": ["…/x.js", "--fix"]} — the script
  lives in args, not command.
- env-vars: CLAUDE_CODE_CHILD_SESSION (v2.1.172) set in CC-spawned subprocesses.
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (
    VALID_MODELS,
    VALID_TOOLS,
    ValidationReport,
    is_valid_model,
    is_valid_plugin_env_var,
)
from validate_agent import AgentValidationReport, validate_model_field, validate_tools_field
from validate_hook import COMMON_TOOL_NAMES, extract_script_paths, validate_command_hook

# ---------------------------------------------------------------------------
# 1. Model: `fable` is now valid; a bogus alias still MAJORs.
# ---------------------------------------------------------------------------


def test_fable_is_a_valid_model_alias():
    """`fable` (sub-agents doc) is now an accepted short model alias."""
    assert "fable" in VALID_MODELS
    assert is_valid_model("fable") is True
    assert is_valid_model("fable[1m]") is True  # 1M-context suffix
    assert is_valid_model("FABLE") is True  # case-insensitive short-alias regex


def test_fable_agent_frontmatter_passes_no_major():
    """A `model: fable` agent draws NO MAJOR (previously a false positive)."""
    report = AgentValidationReport()
    validate_model_field({"model": "fable"}, "agent.md", report)
    majors = [r for r in report.results if r.level == "MAJOR"]
    assert majors == [], f"unexpected MAJOR on model: fable -> {[m.message for m in majors]}"
    assert any(r.level == "PASSED" for r in report.results)


def test_bogus_model_still_majors():
    """A made-up model value still fails (the relaxation did not open the gate)."""
    assert is_valid_model("zzz") is False
    report = AgentValidationReport()
    validate_model_field({"model": "zzz"}, "agent.md", report)
    assert any(r.level == "MAJOR" for r in report.results)


# ---------------------------------------------------------------------------
# 2. Tools: the 5 new tools are accepted; a bogus tool is still flagged.
# ---------------------------------------------------------------------------

NEW_TOOLS = ("Artifact", "RemoteTrigger", "ScheduleWakeup", "ShareOnboardingGuide", "WaitForMcpServers")


def test_new_tools_are_in_valid_tools():
    """Each of the 5 newly-added tools is a known tool."""
    for tool in NEW_TOOLS:
        assert tool in VALID_TOOLS, f"{tool} should be in VALID_TOOLS"


def test_new_tools_accepted_in_agent_tools_list_no_unknown_finding():
    """A `tools:` list of the new tools yields no 'Unknown tools' finding."""
    report = AgentValidationReport()
    validate_tools_field({"tools": list(NEW_TOOLS)}, "agent.md", report)
    unknown = [r for r in report.results if "Unknown tools" in r.message]
    assert unknown == [], f"new tools wrongly reported unknown: {[u.message for u in unknown]}"
    assert any(r.level == "PASSED" for r in report.results)


def test_bogus_tool_still_flagged_as_unknown():
    """A made-up tool name still lands in the 'Unknown tools' bucket."""
    report = AgentValidationReport()
    validate_tools_field({"tools": ["Read", "ZzzNotARealTool2026"]}, "agent.md", report)
    assert any("Unknown tools" in r.message and "ZzzNotARealTool2026" in r.message for r in report.results)


def test_sendmessage_still_valid():
    """SendMessage survives the v2.1.178 team-tool removal."""
    assert "SendMessage" in VALID_TOOLS
    assert "SendMessage" in COMMON_TOOL_NAMES


# ---------------------------------------------------------------------------
# 3. TeamCreate / TeamDelete were REMOVED — they are no longer valid tools.
# ---------------------------------------------------------------------------


def test_teamcreate_teamdelete_no_longer_valid_tools():
    """TeamCreate/TeamDelete used to be valid; they are now rejected (v2.1.178)."""
    assert "TeamCreate" not in VALID_TOOLS
    assert "TeamDelete" not in VALID_TOOLS
    # And removed from the hook matcher-hint set for consistency.
    assert "TeamCreate" not in COMMON_TOOL_NAMES
    assert "TeamDelete" not in COMMON_TOOL_NAMES


def test_teamcreate_in_agent_tools_now_reported_unknown():
    """An agent listing TeamCreate now gets the 'Unknown tools' finding (was clean)."""
    report = AgentValidationReport()
    validate_tools_field({"tools": ["Read", "TeamCreate"]}, "agent.md", report)
    assert any("Unknown tools" in r.message and "TeamCreate" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# 4. Exec-form hook `args`: accepted, and the args-referenced script is scanned.
# ---------------------------------------------------------------------------


def test_exec_form_args_referenced_script_is_extracted():
    """extract_script_paths with `args` returns the script that lives in args[0]."""
    refs = extract_script_paths(
        "node",
        Path("/tmp/plug"),
        args=["${CLAUDE_PLUGIN_ROOT}/scripts/x.js", "--fix"],
    )
    paths = [str(r.path) for r in refs]
    assert paths == ["/tmp/plug/scripts/x.js"], paths
    assert refs[0].invocation_mode == "node"


def test_exec_form_args_only_extracts_argv0_script():
    """args-only legacy form (no command): argv[0] is the script to lint."""
    refs = extract_script_paths(
        "",
        Path("/tmp/plug"),
        args=["${CLAUDE_PLUGIN_ROOT}/scripts/run.py"],
    )
    paths = [str(r.path) for r in refs]
    assert paths == ["/tmp/plug/scripts/run.py"], paths


def test_shell_form_command_extracts_as_before():
    """A plain shell-form command (args=None) parses exactly as before."""
    refs = extract_script_paths("bash ${CLAUDE_PLUGIN_ROOT}/x.sh", Path("/tmp/plug"))
    paths = [str(r.path) for r in refs]
    assert paths == ["/tmp/plug/x.sh"], paths
    assert refs[0].invocation_mode == "bash"


def test_args_none_is_byte_identical_to_omitting_it():
    """Passing args=None must not change the legacy single-string extraction."""
    a = extract_script_paths("python3 ${CLAUDE_PLUGIN_ROOT}/h.py", Path("/tmp/p"))
    b = extract_script_paths("python3 ${CLAUDE_PLUGIN_ROOT}/h.py", Path("/tmp/p"), args=None)
    assert [str(r.path) for r in a] == [str(r.path) for r in b] == ["/tmp/p/h.py"]


def test_validate_command_hook_accepts_exec_form_args_no_unknown_field_error():
    """The exec-form hook accepts `args` with no critical/unknown-field error."""
    report = ValidationReport()
    ok = validate_command_hook(
        {"type": "command", "command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/x.js", "--fix"]},
        "PostToolUse",
        Path("/tmp/plug"),
        report,
    )
    assert ok is True
    criticals = [r for r in report.results if r.level == "CRITICAL"]
    assert criticals == [], f"exec form should not error: {[c.message for c in criticals]}"
    # The exec-form PASS line confirms args were recognized, not flagged unknown.
    assert any("Exec form" in r.message for r in report.results)


def test_validate_command_hook_shell_form_unchanged():
    """A shell-form command hook still validates as the shell form (no regression)."""
    report = ValidationReport()
    ok = validate_command_hook(
        {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/x.sh"},
        "PostToolUse",
        Path("/tmp/plug"),
        report,
    )
    assert ok is True
    assert any("shell form" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# 5. Env allowlist: CLAUDE_CODE_CHILD_SESSION accepted; a bogus env still flagged.
# ---------------------------------------------------------------------------


def test_claude_code_child_session_is_allowed():
    """CLAUDE_CODE_CHILD_SESSION (v2.1.172) is a known plugin env var."""
    assert is_valid_plugin_env_var("CLAUDE_CODE_CHILD_SESSION") is True


def test_bogus_env_var_still_flagged():
    """A made-up CLAUDE_CODE_* env var is still not in the allowlist."""
    assert is_valid_plugin_env_var("CLAUDE_CODE_NOT_A_REAL_VAR_2026") is False
