"""Audit-fix regression tests for scripts/validate_marketplace_pipeline.py (batch b10).

Covers four full-audit findings, each with a guard that would have caught the
original bug plus an assertion of the corrected behavior:

- #19  module docstring + --help exit codes were the inverse of what
       PipelineValidationReport.exit_code() returns for the B/C and F bands.
- #88  check_python_syntax() only caught SyntaxError, so a non-UTF8 .py file
       crashed the whole validator with an unhandled UnicodeDecodeError.
- #89  the plugin-workflow push-trigger check only recognised the `on:` mapping
       form and silently missed the equally-valid YAML list (`on: [push]`) and
       bare-string (`on: push`) forms.
- #162 a dead/unreachable `else` branch in Check 5 awarded a PASSED
       "Submodule entries present" (+4.0) under a condition that can never hold.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import EXIT_CRITICAL, EXIT_MAJOR, EXIT_MINOR, EXIT_OK  # noqa: E402
from validate_marketplace_pipeline import (  # noqa: E402
    PipelineValidationReport,
    check_python_syntax,
    main,
    validate_marketplace_structure,
    validate_plugin_workflows,
)

SOURCE_PATH = scripts_dir / "validate_marketplace_pipeline.py"


def _make_plugin_with_notify(tmp_path, notify_yaml: str):
    """Create a marketplace with one submodule plugin carrying a notify workflow."""
    mp = tmp_path / "marketplace"
    mp.mkdir()
    (mp / ".gitmodules").write_text(
        '[submodule "plugin-a"]\n\tpath = plugin-a\n\turl = https://github.com/org/plugin-a.git\n',
        encoding="utf-8",
    )
    wf_dir = mp / "plugin-a" / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "notify-marketplace.yml").write_text(notify_yaml, encoding="utf-8")
    return mp


# ---------------------------------------------------------------------------
# #19 — exit-code documentation must match exit_code()
# ---------------------------------------------------------------------------


def _score_to_exit(score: float) -> int:
    """Reconstruct the real exit code for a given total score via a stubbed report."""
    report = PipelineValidationReport(marketplace_path=Path("."))
    # Inject a single category whose weighted score equals the target so that
    # total_score == score, then read the authoritative exit_code().
    report.categories.clear()
    from validate_marketplace_pipeline import CategoryScore

    cat = CategoryScore(name="x", weight=100)
    cat.points_possible = 100.0
    cat.points_earned = score  # percentage == score, weighted == score
    report.categories["x"] = cat
    assert abs(report.total_score - score) < 1e-9
    return report.exit_code()


def test_exit_code_band_mapping_is_authoritative():
    """exit_code() maps A->0, B/C->EXIT_MINOR(3), D->EXIT_MAJOR(2), F->EXIT_CRITICAL(1)."""
    assert _score_to_exit(95.0) == EXIT_OK == 0
    assert _score_to_exit(75.0) == EXIT_MINOR == 3  # B/C band
    assert _score_to_exit(65.0) == EXIT_MAJOR == 2  # D band
    assert _score_to_exit(40.0) == EXIT_CRITICAL == 1  # F band


def test_docstring_exit_codes_match_real_behavior():
    """The module docstring + --help epilog must document the REAL band->code mapping (#19).

    Guard against the original inversion: the B/C band returns 3 (not 1) and the
    F band returns 1 (not 3). Both the module docstring and the argparse epilog
    embed an exit-code table; assert the correct lines are present and the
    inverted lines are absent.
    """
    text = SOURCE_PATH.read_text(encoding="utf-8")
    # Correct mapping lines (appear in BOTH the module docstring and the epilog).
    assert "3 - Score >= 70" in text, "B/C band must document exit 3 (EXIT_MINOR)"
    assert "1 - Score < 60" in text, "F band must document exit 1 (EXIT_CRITICAL)"
    # The inverted lines from the original bug must not survive anywhere.
    assert "1 - Score >= 70" not in text, "stale inverted B/C->1 line still present (#19)"
    assert "3 - Score < 60" not in text, "stale inverted F->3 line still present (#19)"


def test_main_returns_exit_minor_for_missing_path(tmp_path):
    """main() returns EXIT_MINOR for a non-existent path (sanity that codes are wired)."""
    missing = tmp_path / "does-not-exist"
    rc = main_with_argv([str(missing)])
    assert rc == EXIT_MINOR


def main_with_argv(argv: list[str]) -> int:
    """Run main() with a synthetic argv, restoring sys.argv afterwards."""
    saved = sys.argv
    try:
        sys.argv = ["validate_marketplace_pipeline.py", *argv]
        return main()
    finally:
        sys.argv = saved


# ---------------------------------------------------------------------------
# #88 — check_python_syntax must not crash on non-UTF8 / unreadable files
# ---------------------------------------------------------------------------


def test_check_python_syntax_valid_file_still_true(tmp_path):
    """Benign side: a clean UTF-8 Python file still returns True (no regression)."""
    p = tmp_path / "ok.py"
    p.write_text("import json\n\n\ndef sync():\n    return json.dumps({})\n", encoding="utf-8")
    assert check_python_syntax(p) is True


def test_check_python_syntax_syntax_error_still_false(tmp_path):
    """A genuine SyntaxError still returns False (no regression)."""
    p = tmp_path / "bad.py"
    p.write_text("def broken(:\n    pass\n", encoding="utf-8")
    assert check_python_syntax(p) is False


def test_check_python_syntax_non_utf8_returns_false_not_crash(tmp_path):
    """Non-UTF8 .py source returns False instead of raising UnicodeDecodeError (#88).

    Guard: before the fix, open(encoding='utf-8').read() raised
    UnicodeDecodeError (NOT a SyntaxError subclass), which propagated and
    crashed the whole validator. The byte 0xff is an invalid UTF-8 start byte.
    """
    p = tmp_path / "latin1.py"
    p.write_bytes(b"x = '\xff\xfe not utf8'\n")
    # Must not raise — the call returning at all is half the assertion.
    assert check_python_syntax(p) is False


def test_check_python_syntax_nul_byte_returns_false(tmp_path):
    """A file with an embedded NUL byte (ValueError from ast.parse) returns False (#88)."""
    p = tmp_path / "nul.py"
    p.write_bytes(b"x = 1\x00\n")
    assert check_python_syntax(p) is False


# ---------------------------------------------------------------------------
# #89 — push-trigger check must recognise mapping, list, and string `on:` forms
# ---------------------------------------------------------------------------


def _push_trigger_detected(mp) -> bool:
    report = PipelineValidationReport(marketplace_path=mp)
    validate_plugin_workflows(mp, report)
    cat = report.categories["plugin_workflows"]
    # The pass line for push trigger; with one plugin and one notify workflow,
    # detection yields a PASSED "All notify workflows have push trigger".
    return any(r.level == "PASSED" and "push trigger" in r.message for r in cat.results)


def test_push_trigger_detected_mapping_form(tmp_path):
    """Mapping form `on:\\n  push:` is detected (no regression)."""
    mp = _make_plugin_with_notify(
        tmp_path,
        "name: notify\non:\n  push:\n    branches: [main]\njobs:\n  n:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n",
    )
    assert _push_trigger_detected(mp) is True


def test_push_trigger_detected_list_form(tmp_path):
    """List form `on: [push, pull_request]` is now detected (#89).

    Guard: before the fix the value was coerced to {} (not a dict) and
    `'push' in {}` was False, wrongly flagging a valid workflow.
    """
    mp = _make_plugin_with_notify(
        tmp_path,
        "name: notify\non: [push, pull_request]\njobs:\n  n:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n",
    )
    assert _push_trigger_detected(mp) is True


def test_push_trigger_detected_bare_string_form(tmp_path):
    """Bare-string form `on: push` is now detected (#89)."""
    mp = _make_plugin_with_notify(
        tmp_path,
        "name: notify\non: push\njobs:\n  n:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n",
    )
    assert _push_trigger_detected(mp) is True


def test_push_trigger_absent_when_only_other_triggers(tmp_path):
    """Negative side: a workflow with NO push trigger is still flagged as missing it (#89).

    Ensures the broadened normalisation did not start matching push spuriously —
    a `on: [workflow_dispatch]` notify workflow must NOT count as having push.
    """
    mp = _make_plugin_with_notify(
        tmp_path,
        "name: notify\non: [workflow_dispatch]\njobs:\n  n:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n",
    )
    report = PipelineValidationReport(marketplace_path=mp)
    validate_plugin_workflows(mp, report)
    cat = report.categories["plugin_workflows"]
    # No PASSED push-trigger line; instead a MAJOR "no ... push trigger".
    assert not any(r.level == "PASSED" and "push trigger" in r.message for r in cat.results)
    assert any(r.level == "MAJOR" and "push trigger" in r.message for r in cat.results)


# ---------------------------------------------------------------------------
# #162 — dead PASSED branch removed; invalid JSON must not bank submodule points
# ---------------------------------------------------------------------------


def _make_marketplace(tmp_path, marketplace_json_text: str, gitmodules: str | None = None):
    mp = tmp_path / "marketplace"
    mp.mkdir()
    (mp / "marketplace.json").write_text(marketplace_json_text, encoding="utf-8")
    if gitmodules is not None:
        (mp / ".gitmodules").write_text(gitmodules, encoding="utf-8")
    return mp


def test_invalid_json_does_not_bank_submodule_mapping_points(tmp_path):
    """Invalid marketplace.json emits an INFO (0 pts) for the submodule-mapping check (#162).

    Guard against the dead branch: it would have produced a PASSED
    "Submodule entries present" (+4.0) — which must never appear — and the
    skipped check must score-neutral (INFO, 0 possible / 0 earned), mirroring
    the Check-6 m4 convention.
    """
    mp = _make_marketplace(tmp_path, "{not valid json!!", gitmodules='[submodule "x"]\n\tpath = x\n')
    report = PipelineValidationReport(marketplace_path=mp)
    validate_marketplace_structure(mp, report)
    cat = report.categories["marketplace_structure"]

    # The dead PASSED line must NOT exist.
    assert not any(r.message == "Submodule entries present" for r in cat.results), (
        "unreachable dead PASSED branch resurfaced (#162)"
    )
    # The skip line must be INFO with 0 points.
    skip_lines = [r for r in cat.results if "Submodule mapping check skipped" in r.message]
    assert skip_lines, "expected a submodule-mapping skip line on invalid JSON"
    assert all(r.level == "INFO" for r in skip_lines)
    assert all(r.points_possible == 0.0 and r.points_earned == 0.0 for r in skip_lines)


def test_valid_json_no_plugins_flags_major(tmp_path):
    """Valid JSON with an empty plugins list flags MAJOR 'No plugins found' (#162 benign side).

    Distinct from the invalid-JSON case: here marketplace_data is valid, so the
    legitimate gap (no plugins to map) is a real MAJOR finding, not a skip.
    """
    mp = _make_marketplace(
        tmp_path,
        '{"name": "ok", "version": "1.0.0", "plugins": []}',
        gitmodules='[submodule "x"]\n\tpath = x\n',
    )
    report = PipelineValidationReport(marketplace_path=mp)
    validate_marketplace_structure(mp, report)
    cat = report.categories["marketplace_structure"]
    assert any(
        r.level == "MAJOR" and "No plugins found in marketplace.json" in r.message for r in cat.results
    )
    # And no resurrected dead PASSED line.
    assert not any(r.message == "Submodule entries present" for r in cat.results)
