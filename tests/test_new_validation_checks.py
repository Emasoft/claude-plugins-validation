#!/usr/bin/env python3
"""Tests for 8 new validation checks added to validate_plugin.py and validate_hook.py.

Covers:
  1. Misplaced scripts/ inside .claude-plugin/ (validate_structure)
  2. settings.json validation (validate_structure)
  3. Content presence check (validate_structure)
  4. Script shebang check (validate_scripts)
  5. Fuzzy event name matching (validate_event_name)
  6. Matcher validation for Notification/SessionStart/PreCompact (validate_matcher)
  7. Bash command portability checks (validate_command_hook)
  8. Relative path without $CLAUDE_PLUGIN_ROOT (validate_command_hook)

16 tests total (2 per check: one positive, one negative).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_hook import (  # noqa: E402
    validate_command_hook,
    validate_event_name,
    validate_matcher,
)
from validate_plugin import (  # noqa: E402
    validate_scripts,
    validate_structure,
)

# ===========================================================================
# Check 1: Misplaced scripts/ inside .claude-plugin/
# ===========================================================================


def test_misplaced_scripts_inside_claude_plugin_reports_critical(tmp_path: Path):
    """A scripts/ directory inside .claude-plugin/ triggers CRITICAL for misplaced component."""
    plugin_dir = tmp_path / "bad-scripts-plugin"
    plugin_dir.mkdir()
    claude_dir = plugin_dir / ".claude-plugin"
    claude_dir.mkdir()
    (claude_dir / "plugin.json").write_text(json.dumps({"name": "bad-scripts", "version": "1.0.0"}))
    # Misplace scripts/ inside .claude-plugin/
    wrong_scripts = claude_dir / "scripts"
    wrong_scripts.mkdir()
    (wrong_scripts / "run.py").write_text("print('hello')")
    report = ValidationReport()
    validate_structure(plugin_dir, report, marketplace_only=False)
    critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
    assert any("scripts/" in m and "must be at plugin root" in m for m in critical_msgs)


def test_scripts_at_root_no_misplaced_critical(tmp_path: Path):
    """A scripts/ directory at the plugin root does NOT trigger CRITICAL about misplacement."""
    plugin_dir = tmp_path / "good-scripts-plugin"
    plugin_dir.mkdir()
    claude_dir = plugin_dir / ".claude-plugin"
    claude_dir.mkdir()
    (claude_dir / "plugin.json").write_text(json.dumps({"name": "good-scripts", "version": "1.0.0"}))
    # Place scripts/ correctly at root
    root_scripts = plugin_dir / "scripts"
    root_scripts.mkdir()
    (root_scripts / "run.py").write_text("#!/usr/bin/env python3\nprint('hello')")
    report = ValidationReport()
    validate_structure(plugin_dir, report, marketplace_only=False)
    critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
    assert not any("scripts/" in m and "must be at plugin root" in m for m in critical_msgs)


# ===========================================================================
# Check 2: settings.json validation
# ===========================================================================


def test_settings_json_with_recognized_key_passes(tmp_path: Path):
    """A settings.json containing only the recognized key 'agent' produces PASSED."""
    plugin_dir = tmp_path / "settings-good"
    plugin_dir.mkdir()
    claude_dir = plugin_dir / ".claude-plugin"
    claude_dir.mkdir()
    (claude_dir / "plugin.json").write_text(json.dumps({"name": "settings-good", "version": "1.0.0"}))
    # Add a commands/ dir so the content check does not fire
    (plugin_dir / "commands").mkdir()
    (plugin_dir / "settings.json").write_text(json.dumps({"agent": "custom"}))
    report = ValidationReport()
    validate_structure(plugin_dir, report, marketplace_only=False)
    passed_msgs = [r.message for r in report.results if r.level == "PASSED"]
    assert any("settings.json" in m for m in passed_msgs)
    # No MINOR about unrecognized key
    minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
    assert not any("unrecognized key" in m for m in minor_msgs)


def test_settings_json_with_bogus_key_reports_minor(tmp_path: Path):
    """A settings.json containing an unrecognized key produces MINOR warning."""
    plugin_dir = tmp_path / "settings-bad"
    plugin_dir.mkdir()
    claude_dir = plugin_dir / ".claude-plugin"
    claude_dir.mkdir()
    (claude_dir / "plugin.json").write_text(json.dumps({"name": "settings-bad", "version": "1.0.0"}))
    (plugin_dir / "commands").mkdir()
    (plugin_dir / "settings.json").write_text(json.dumps({"bogus_key": True}))
    report = ValidationReport()
    validate_structure(plugin_dir, report, marketplace_only=False)
    minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
    assert any("unrecognized key" in m and "bogus_key" in m for m in minor_msgs)


# ===========================================================================
# Check 3: Content presence check
# ===========================================================================


def test_plugin_with_only_manifest_reports_major_no_content(tmp_path: Path):
    """A plugin with only .claude-plugin/plugin.json and nothing else produces MAJOR about no content."""
    plugin_dir = tmp_path / "empty-content"
    plugin_dir.mkdir()
    claude_dir = plugin_dir / ".claude-plugin"
    claude_dir.mkdir()
    (claude_dir / "plugin.json").write_text(json.dumps({"name": "empty-content", "version": "1.0.0"}))
    report = ValidationReport()
    validate_structure(plugin_dir, report, marketplace_only=False)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert any("no content" in m.lower() for m in major_msgs)


def test_plugin_with_commands_dir_no_content_error(tmp_path: Path):
    """A plugin with .claude-plugin/plugin.json AND commands/ dir does NOT produce 'no content' error."""
    plugin_dir = tmp_path / "has-content"
    plugin_dir.mkdir()
    claude_dir = plugin_dir / ".claude-plugin"
    claude_dir.mkdir()
    (claude_dir / "plugin.json").write_text(json.dumps({"name": "has-content", "version": "1.0.0"}))
    (plugin_dir / "commands").mkdir()
    report = ValidationReport()
    validate_structure(plugin_dir, report, marketplace_only=False)
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert not any("no content" in m.lower() for m in major_msgs)


# ===========================================================================
# Check 4: Script shebang check
# ===========================================================================


def test_script_with_shebang_no_warning(tmp_path: Path):
    """Script files with valid shebangs should not trigger shebang warnings."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / ".claude-plugin").mkdir()
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text('{"name": "test"}')
    scripts_dir = plugin_dir / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "hello.py"
    script.write_text("#!/usr/bin/env python3\nprint('hello')\n")
    report = ValidationReport()
    # v2.64.0: validate_scripts() no longer calls resolve_tool_command — lint
    # moved to cpv_lint_engine. The function now does exec-bit + shebang only.
    validate_scripts(plugin_dir, report)
    minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
    assert not any("shebang" in m.lower() for m in minor_msgs)


def test_script_without_shebang_reports_minor(tmp_path: Path):
    """Script files without shebangs should report a MINOR warning."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / ".claude-plugin").mkdir()
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text('{"name": "test"}')
    scripts_dir = plugin_dir / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "hello.py"
    script.write_text("print('hello')\n")
    report = ValidationReport()
    # v2.64.0: validate_scripts() no longer calls resolve_tool_command — lint
    # moved to cpv_lint_engine. The function now does exec-bit + shebang only.
    validate_scripts(plugin_dir, report)
    minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
    assert any("shebang" in m.lower() for m in minor_msgs)


# ---------------------------------------------------------------------------
# Check 4b: the "has shebang but is not executable" WARNING carries the
# additive fixable/fix_id="chmod-exec" tag (Phase 2, TRDD-GVMOKJBB). Two-sided:
# it STILL fires at WARNING severity with unchanged text, AND now carries the
# tag; a look-alike (already-executable shebang script) produces NO chmod-exec
# finding. Unix-only: the emitting branch is gated on `not IS_WINDOWS`.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="exec-bit check is Unix-only (validate_scripts gates on not IS_WINDOWS)")
def test_shebang_not_executable_warning_is_tagged_chmod_exec(tmp_path: Path):
    """A shebang script that is NOT executable still WARNs and now carries fixable/fix_id='chmod-exec'."""
    plugin_dir = tmp_path / "plugin"
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text('{"name": "test"}')
    scripts_d = plugin_dir / "scripts"
    scripts_d.mkdir()
    script = scripts_d / "hello.py"
    script.write_text("#!/usr/bin/env python3\nprint('hi')\n")
    os.chmod(script, 0o644)  # shebang present, NOT executable → the finding fires
    report = ValidationReport()
    validate_scripts(plugin_dir, report)
    hits = [r for r in report.results if r.level == "WARNING" and "has shebang but is not executable" in r.message]
    # Side 1: the finding STILL fires, at unchanged WARNING severity.
    assert len(hits) == 1
    r = hits[0]
    assert r.file == "scripts/hello.py"
    # Side 2: the additive tag is present (severity + message untouched).
    assert r.fixable is True
    assert r.fix_id == "chmod-exec"
    serialized = r.to_dict()
    assert serialized.get("fixable") is True
    assert serialized.get("fix_id") == "chmod-exec"


@pytest.mark.skipif(sys.platform == "win32", reason="exec-bit check is Unix-only")
def test_executable_shebang_script_emits_no_chmod_exec_finding(tmp_path: Path):
    """A shebang script that IS executable is a look-alike: no chmod-exec finding is emitted."""
    plugin_dir = tmp_path / "plugin"
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text('{"name": "test"}')
    scripts_d = plugin_dir / "scripts"
    scripts_d.mkdir()
    script = scripts_d / "ok.py"
    script.write_text("#!/usr/bin/env python3\nprint('ok')\n")
    os.chmod(script, 0o755)  # shebang present AND executable → nothing to fix
    report = ValidationReport()
    validate_scripts(plugin_dir, report)
    tagged = [r for r in report.results if r.fixable and r.fix_id == "chmod-exec"]
    assert tagged == []


# ===========================================================================
# Check 5: Fuzzy event name matching
# ===========================================================================


def test_fuzzy_event_name_suggests_correction():
    """A misspelled event name like 'preToolUse' triggers CRITICAL with 'did you mean PreToolUse'."""
    report = ValidationReport()
    result = validate_event_name("preToolUse", report)
    assert result is False
    critical_msgs = [r.message for r in report.results if r.level == "CRITICAL"]
    assert any("did you mean" in m and "PreToolUse" in m for m in critical_msgs)


def test_valid_event_name_no_errors():
    """A valid event name 'PreToolUse' passes with no errors."""
    report = ValidationReport()
    result = validate_event_name("PreToolUse", report)
    assert result is True
    assert not any(r.level == "CRITICAL" for r in report.results)


# ===========================================================================
# Check 6: Matcher validation for Notification/SessionStart/PreCompact
# ===========================================================================


def test_notification_matcher_unknown_type_reports_info():
    """An unknown Notification matcher type triggers INFO about known types."""
    report = ValidationReport()
    result = validate_matcher("unknown_type", "Notification", report)
    assert result is True
    info_msgs = [r.message for r in report.results if r.level == "INFO"]
    assert any("not a known type" in m and "known values" in m for m in info_msgs)


def test_notification_matcher_known_type_no_info():
    """A known Notification matcher type like 'permission_prompt' triggers no info warnings."""
    report = ValidationReport()
    result = validate_matcher("permission_prompt", "Notification", report)
    assert result is True
    info_msgs = [r.message for r in report.results if r.level == "INFO"]
    # Should NOT have any info about unknown types
    assert not any("not a common type" in m for m in info_msgs)


# ===========================================================================
# Check 7: Bash command portability checks
# ===========================================================================


def test_tilde_path_reports_minor(tmp_path: Path):
    """A hook command starting with '~/' triggers MINOR about tilde expansion."""
    hook = {"type": "command", "command": "~/bin/my-tool"}
    report = ValidationReport()
    validate_command_hook(hook, "PreToolUse", tmp_path, report)
    minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
    assert any("~/" in m and "tilde" in m.lower() for m in minor_msgs)


def test_bare_cd_reports_minor(tmp_path: Path):
    """A hook command with bare 'cd /tmp' (no chaining) triggers MINOR about no effect."""
    hook = {"type": "command", "command": "cd /tmp"}
    report = ValidationReport()
    validate_command_hook(hook, "PreToolUse", tmp_path, report)
    minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
    assert any("cd" in m and "no effect" in m for m in minor_msgs)


# ===========================================================================
# Check 8: Relative path without $CLAUDE_PLUGIN_ROOT
# ===========================================================================


def test_relative_path_without_plugin_root_reports_minor(tmp_path: Path):
    """A command with './scripts/run.sh' without $CLAUDE_PLUGIN_ROOT triggers MINOR."""
    hook = {"type": "command", "command": "./scripts/run.sh"}
    report = ValidationReport()
    validate_command_hook(hook, "PreToolUse", tmp_path, report)
    minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
    assert any("relative path" in m.lower() and "CLAUDE_PLUGIN_ROOT" in m for m in minor_msgs)


def test_command_with_plugin_root_no_relative_path_warning(tmp_path: Path):
    """A command using ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh does NOT trigger relative path warning."""
    hook = {"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/scripts/run.sh"}
    report = ValidationReport()
    validate_command_hook(hook, "PreToolUse", tmp_path, report)
    minor_msgs = [r.message for r in report.results if r.level == "MINOR"]
    assert not any("relative path" in m.lower() for m in minor_msgs)
