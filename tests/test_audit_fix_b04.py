#!/usr/bin/env python3
"""Regression tests for the b04 audit-fix batch on scripts/validate_hook.py.

Each test pins the corrected behavior for one audit finding AND embeds a guard
that would have failed against the pre-fix code, so a future regression is
caught rather than silently re-introduced.

Findings covered:
  #11  — known_hook_fields was missing the spec fields the validator itself
         requires/validates: 'args' (exec form), 'server'/'tool'/'input'
         (mcp_tool), 'continueOnBlock' (PostToolUse). They produced
         false-positive "Unknown hook field" WARNINGs.
  #43  — validate_event_hooks emitted "All hooks valid" PASSED even when a
         MAJOR finding existed for the same event (CRITICAL-only gate).
  #44  — venv-setup detection in reconcile_python_runtime_deps scanned only
         the `command` field, so it missed v2.1.139 exec-form setup hooks
         (command="uv", args=["venv", ...]) and fired a false-positive MINOR.
  #128 — SETUP_TRIGGERS was defined but never referenced; the Setup matcher
         branch in validate_matcher was missing.
  #129 — _TRAVERSAL_RE was compiled inside validate_command_hook (recompiled
         on every call) instead of at module scope like every sibling regex.

All probes force CPV_HOOK_PARALLEL=0 (serial path) so the in-process tmp-dir
fixtures are not shipped to ProcessPool workers, and CPV_SCAN_CACHE=0 so a
stale scan cache cannot mask a regression.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("CPV_HOOK_PARALLEL", "0")
os.environ.setdefault("CPV_SCAN_CACHE", "0")

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import validate_hook as vh  # noqa: E402
from validate_hook import (  # noqa: E402
    HookValidationReport,
    ScriptRef,
    reconcile_python_runtime_deps,
    validate_command_hook,
    validate_event_hooks,
    validate_matcher,
    validate_single_hook,
)

_FAKE_ROOT = Path("/tmp/cpv_b04_fake_plugin_root")


def _unknown_field_warnings(hook: dict, event: str) -> list[str]:
    report = HookValidationReport(hook_path="hooks.json")
    validate_single_hook(hook, event, None, report)
    return [r.message for r in report.results if r.level == "WARNING" and "Unknown hook field" in r.message]


# --------------------------------------------------------------------------- #
# Finding #11 — known_hook_fields completeness
# --------------------------------------------------------------------------- #
def test_finding11_mcp_tool_fields_not_flagged_unknown():
    """server/tool/input on an mcp_tool hook must NOT raise 'Unknown hook field'."""
    warns = _unknown_field_warnings(
        {"type": "mcp_tool", "server": "srv", "tool": "do_thing", "input": {"k": "v"}},
        "PreToolUse",
    )
    # Pre-fix: 3 warnings (server, tool, input). Fixed: none.
    assert warns == [], f"unexpected unknown-field warnings: {warns}"


def test_finding11_args_field_not_flagged_unknown():
    """The v2.1.139 exec-form 'args' field must NOT raise 'Unknown hook field'."""
    warns = _unknown_field_warnings(
        {"type": "command", "command": "uv", "args": ["venv"]},
        "SessionStart",
    )
    assert warns == [], f"unexpected unknown-field warnings: {warns}"


def test_finding11_continue_on_block_not_flagged_unknown():
    """'continueOnBlock' (v2.1.139 PostToolUse field) must NOT raise 'Unknown hook field'."""
    warns = _unknown_field_warnings(
        {"type": "command", "command": "echo hi", "continueOnBlock": True},
        "PostToolUse",
    )
    assert warns == [], f"unexpected unknown-field warnings: {warns}"


def test_finding11_genuinely_unknown_field_still_warns():
    """Guard: a field outside the spec STILL warns — the check was not disabled."""
    warns = _unknown_field_warnings(
        {"type": "command", "command": "echo hi", "totallyMadeUpField": 1},
        "PreToolUse",
    )
    assert any("totallyMadeUpField" in w for w in warns), warns


def test_finding11_all_five_fields_present_in_known_set():
    """The five validated fields are now members of known_hook_fields (source-level guard)."""
    # Read the function source and confirm the literal set entries exist — a
    # direct guard against someone deleting them from the set again.
    import inspect

    src = inspect.getsource(validate_single_hook)
    for field in ("args", "server", "tool", "input", "continueOnBlock"):
        assert f'"{field}"' in src, f"{field!r} missing from validate_single_hook known_hook_fields"


# --------------------------------------------------------------------------- #
# Finding #43 — "All hooks valid" must not coexist with a MAJOR
# --------------------------------------------------------------------------- #
def test_finding43_no_all_hooks_valid_when_major_present():
    """A MAJOR finding for an event must suppress the 'All hooks valid' PASSED line."""
    report = HookValidationReport(hook_path="hooks.json")
    event_config = [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "/tmp/cpv_b04_missing/foo.py"}]}
    ]
    validate_event_hooks("PreToolUse", event_config, _FAKE_ROOT, report)
    has_major = any(r.level == "MAJOR" for r in report.results)
    said_all_valid = any(r.level == "PASSED" and "All hooks valid" in r.message for r in report.results)
    # The whole point of the bug: a MAJOR existed AND it still said "All hooks valid".
    assert has_major, "fixture should produce a MAJOR (script not found / absolute path)"
    assert not said_all_valid, "report claimed 'All hooks valid' while a MAJOR finding exists"


def test_finding43_all_hooks_valid_still_emitted_when_clean():
    """Guard: a genuinely-clean event STILL emits the 'All hooks valid' PASSED line."""
    report = HookValidationReport(hook_path="hooks.json")
    # Runtime-resolved env-var path → not-found is suppressed → no MAJOR.
    event_config = [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/scripts/runtime_only.py"}]}
    ]
    validate_event_hooks("PreToolUse", event_config, _FAKE_ROOT, report)
    has_blocking = any(r.level in ("CRITICAL", "MAJOR") for r in report.results)
    said_all_valid = any(r.level == "PASSED" and "All hooks valid" in r.message for r in report.results)
    assert not has_blocking, [r.message for r in report.results if r.level in ("CRITICAL", "MAJOR")]
    assert said_all_valid, "clean event should still emit 'All hooks valid'"


def test_finding43_return_value_contract_preserved():
    """The legacy boolean-return contract (CRITICAL-only flips False) is unchanged."""
    # not-a-list → CRITICAL → False
    rep_bad = HookValidationReport(hook_path="hooks.json")
    assert validate_event_hooks("PreToolUse", "not a list", None, rep_bad) is False
    # empty list → True
    rep_empty = HookValidationReport(hook_path="hooks.json")
    assert validate_event_hooks("PreToolUse", [], None, rep_empty) is True
    # MAJOR-only (no CRITICAL) → still returns True (legacy semantics) even
    # though the PASSED line is now suppressed.
    rep_major = HookValidationReport(hook_path="hooks.json")
    cfg = [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/tmp/cpv_b04_missing/foo.py"}]}]
    assert validate_event_hooks("PreToolUse", cfg, _FAKE_ROOT, rep_major) is True


# --------------------------------------------------------------------------- #
# Finding #44 — exec-form venv-setup detection
# --------------------------------------------------------------------------- #
def _venv_setup_detected(setup_hook: dict, tmp_path: Path) -> tuple[bool, bool]:
    """Returns (setup_detected_PASSED, missing_setup_MINOR) for a venv-python ref."""
    scripts_d = tmp_path / "scripts"
    scripts_d.mkdir(exist_ok=True)
    script = scripts_d / "needs_dep.py"
    script.write_text("import requests\nprint(requests)\n")
    report = HookValidationReport(hook_path="hooks.json")
    hooks_json = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [setup_hook]}]}}
    ref = ScriptRef(path=script, invocation_mode="venv-python")
    reconcile_python_runtime_deps(ref, tmp_path, hooks_json, report)
    detected = any(r.level == "PASSED" and "venv-setup hook present" in r.message for r in report.results)
    missing = any(r.level == "MINOR" and "no SessionStart hook was found" in r.message for r in report.results)
    return detected, missing


def test_finding44_exec_form_venv_setup_detected(tmp_path: Path):
    """Exec-form setup hook (command='uv', args=['venv', ...]) must be recognized as setup."""
    detected, missing = _venv_setup_detected(
        {"type": "command", "command": "uv", "args": ["venv", "${CLAUDE_PLUGIN_DATA}/.venv"]},
        tmp_path,
    )
    assert detected, "exec-form venv-setup hook was not detected"
    assert not missing, "false-positive MINOR fired for an exec-form venv-setup hook"


def test_finding44_exec_form_with_flag_detected(tmp_path: Path):
    """Flag tokens between `uv` and `venv` (exec form) must still be recognized."""
    detected, _ = _venv_setup_detected(
        {"type": "command", "command": "uv", "args": ["--quiet", "venv", "${CLAUDE_PLUGIN_DATA}/.venv"]},
        tmp_path,
    )
    assert detected


def test_finding44_shell_form_still_detected(tmp_path: Path):
    """Guard: the original shell-form detection path still works."""
    detected, _ = _venv_setup_detected(
        {"type": "command", "command": "uv venv ${CLAUDE_PLUGIN_DATA}/.venv"},
        tmp_path,
    )
    assert detected


def test_finding44_uv_run_with_not_mistaken_for_setup(tmp_path: Path):
    """Guard against over-match: `uv run --with venv-thing` is NOT venv creation."""
    detected, missing = _venv_setup_detected(
        {"type": "command", "command": "uv", "args": ["run", "--with", "venv-thing", "${CLAUDE_PLUGIN_DATA}/x.py"]},
        tmp_path,
    )
    assert not detected, "`uv run --with` was wrongly classified as venv setup"
    assert missing, "expected the MINOR no-setup hint for a non-setup SessionStart hook"


# --------------------------------------------------------------------------- #
# Finding #128 — SETUP_TRIGGERS wired into validate_matcher
# --------------------------------------------------------------------------- #
def _setup_matcher_hints(matcher: str) -> list[str]:
    report = HookValidationReport(hook_path="hooks.json")
    validate_matcher(matcher, "Setup", report)
    return [r.message for r in report.results if r.level == "INFO" and "Setup matcher" in r.message]


def test_finding128_setup_known_triggers_silent():
    """Known Setup triggers (init, maintenance) produce no hint."""
    assert _setup_matcher_hints("init") == []
    assert _setup_matcher_hints("maintenance") == []


def test_finding128_setup_unknown_trigger_hinted():
    """An unknown Setup trigger produces exactly one INFO hint listing the known values."""
    hints = _setup_matcher_hints("frobnicate")
    assert len(hints) == 1, hints
    assert "init" in hints[0] and "maintenance" in hints[0]


def test_finding128_setup_triggers_constant_referenced():
    """SETUP_TRIGGERS is now referenced in validate_matcher's source (dead-code guard)."""
    import inspect

    assert vh.SETUP_TRIGGERS == {"init", "maintenance"}
    assert "SETUP_TRIGGERS" in inspect.getsource(validate_matcher)


# --------------------------------------------------------------------------- #
# Finding #129 — _TRAVERSAL_RE compiled at module scope
# --------------------------------------------------------------------------- #
def test_finding129_traversal_re_is_module_level():
    """_TRAVERSAL_RE is a module attribute (compiled once at import), not a local."""
    import re as _re

    assert hasattr(vh, "_TRAVERSAL_RE")
    assert isinstance(vh._TRAVERSAL_RE, _re.Pattern)


def test_finding129_traversal_re_not_recompiled_in_function():
    """Source guard: validate_command_hook must NOT contain a `_TRAVERSAL_RE = re.compile(` local."""
    import inspect

    src = inspect.getsource(validate_command_hook)
    assert "_TRAVERSAL_RE = re.compile(" not in src, "regex is still recompiled inside validate_command_hook"
    # It must still USE the module-level compiled regex.
    assert "_TRAVERSAL_RE.search(" in src


def _traversal_warns(cmd: str) -> bool:
    report = HookValidationReport(hook_path="hooks.json")
    validate_command_hook({"type": "command", "command": cmd}, "PreToolUse", _FAKE_ROOT, report)
    return any(r.level == "WARNING" and "path-traversal" in r.message for r in report.results)


def test_finding129_traversal_behavior_unchanged():
    """Guard: hoisting to module scope did not change detection behavior."""
    assert _traversal_warns("${CLAUDE_PLUGIN_ROOT}/../other/x.py") is True
    assert _traversal_warns(r"C:\plug\..\other\x.py") is True
    assert _traversal_warns("${CLAUDE_PLUGIN_ROOT}/scripts/x.py") is False


# --------------------------------------------------------------------------- #
# HIGH finding — unbraced $CLAUDE_PLUGIN_ROOT must suppress "Script not found"
# (this is the headline HIGH; the table rows above are the MED/LOW findings)
# --------------------------------------------------------------------------- #
def _script_not_found_majors(cmd: str) -> list[str]:
    report = HookValidationReport(hook_path="hooks.json")
    validate_command_hook({"type": "command", "command": cmd}, "SessionStart", _FAKE_ROOT, report)
    return [r.message for r in report.results if r.level == "MAJOR" and "Script not found" in r.message]


def test_high_unbraced_plugin_root_suppresses_not_found():
    """A bare $CLAUDE_PLUGIN_ROOT path (no braces) must NOT trigger a false 'Script not found' MAJOR."""
    # Pre-fix: the braced-only suppressor missed this and fired a MAJOR.
    assert _script_not_found_majors("$CLAUDE_PLUGIN_ROOT/scripts/nonexistent_b04.py") == []


def test_high_braced_plugin_root_still_suppressed():
    """Guard: braced ${CLAUDE_PLUGIN_ROOT} stays suppressed (unchanged)."""
    assert _script_not_found_majors("${CLAUDE_PLUGIN_ROOT}/scripts/nonexistent_b04.py") == []


def test_high_genuine_missing_absolute_still_flagged():
    """Guard: a real missing absolute path (no env var) STILL produces a MAJOR — detection not weakened."""
    majors = _script_not_found_majors("/tmp/cpv_b04_definitely_missing/foo.py")
    assert len(majors) == 1, majors
