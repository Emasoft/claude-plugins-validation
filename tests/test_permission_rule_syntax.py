#!/usr/bin/env python3
"""Tests for the permission-rule-syntax checks (CC v2.1.260 spec sync).

Covers the shared checker ``cpv_validation_common.check_permission_rule_syntax``
plus its wiring into ``validate_project_scope._flag_malformed_permission_rules``
and ``validate_local_scope._flag_malformed_permission_rules_local``.

Every rule is tested two-sided: the malformed rule FIRES, and a well-formed
sibling stays SILENT — a suppression/acceptance assertion without a positive
control passes vacuously. No mocks; real files under ``tmp_path``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport, check_permission_rule_syntax  # noqa: E402
from validate_local_scope import validate_settings_local_json  # noqa: E402
from validate_project_scope import validate_settings_json_project_scope  # noqa: E402


def _write_settings(path: Path, rules: dict[str, list[str]]) -> Path:
    path.write_text(json.dumps({"permissions": rules}), encoding="utf-8")
    return path


def _is_unrelated_row(result: object) -> bool:
    """True for the report rows a settings file emits whatever its permissions hold.

    Observed first-hand for both scopes: a missing-``$schema`` NIT and the scope's
    own ``PASSED`` row. The malformed-bucket guard tests assert "nothing EXCEPT
    these" rather than "none of MAJOR/WARNING/INFO", because an inclusion list
    encodes the assumption that today's severities are the only ones — so a future
    permission-rule check added at NIT (which blocks ``--strict`` here) would slip
    past it and the guard test would go silently vacuous. An exclusion list fails
    LOUDLY when the world changes, which is the correct direction.

    The ``$schema`` prose dependency is deliberate and safe: it NARROWS what is
    ignored, so a reword makes these tests over-strict and fail, never go quiet.
    """
    level = getattr(result, "level", "")
    message = getattr(result, "message", "")
    return level == "PASSED" or (level == "NIT" and "$schema" in message)


def _levels(findings: list[tuple[str, str]]) -> list[str]:
    return [severity for severity, _ in findings]


# =============================================================================
# Rule 1 — MAJOR: text after the closing paren
# =============================================================================


def test_rule1_trailing_text_after_paren_fires_major() -> None:
    """A rule with text after the closing paren is MAJOR (CC v2.1.260 rejects it)."""
    findings = check_permission_rule_syntax("Bash(ls) x")
    assert "major" in _levels(findings)


def test_rule1_well_formed_sibling_is_silent() -> None:
    """The identical rule WITHOUT trailing text draws no rule-1 finding."""
    findings = check_permission_rule_syntax("Bash(ls)")
    assert findings == []


def test_rule1_trailing_text_after_star_fires_major() -> None:
    """Trailing text after the ``)*`` suffix is also caught."""
    findings = check_permission_rule_syntax("Bash(rm -rf *)* extra")
    assert "major" in _levels(findings)


def test_rule1_trailing_whitespace_only_is_silent() -> None:
    """A rule ending in only whitespace after the paren is NOT flagged (no real text)."""
    findings = check_permission_rule_syntax("Bash(ls)   ")
    assert findings == []


# =============================================================================
# Rule 2 — MAJOR: unclosed '[' in a file-permission specifier
# =============================================================================


def test_rule2_unclosed_bracket_fires_major() -> None:
    """An unclosed '[' in an Edit specifier is an uncompilable pattern."""
    findings = check_permission_rule_syntax("Edit(src/[abc.py)")
    assert "major" in _levels(findings)


def test_rule2_balanced_bracket_sibling_is_silent() -> None:
    """The balanced-bracket sibling draws no rule-2 finding."""
    findings = check_permission_rule_syntax("Edit(src/[abc].py)")
    assert findings == []


def test_rule2_applies_to_read_write_multiedit_glob() -> None:
    """The unclosed-bracket check applies to every path-specifier tool."""
    for tool in ("Read", "Write", "MultiEdit", "Glob"):
        findings = check_permission_rule_syntax(f"{tool}(src/[abc.py)")
        assert "major" in _levels(findings), tool


def test_rule2_does_not_apply_to_bash() -> None:
    """Bash has no path-glob specifier, so an unclosed '[' there is not rule-2."""
    findings = check_permission_rule_syntax("Bash(echo [abc)")
    assert "major" not in _levels(findings)


# =============================================================================
# Rule 3 — WARNING: wildcard before the subcommand
# =============================================================================


def test_rule3_wildcard_before_subcommand_fires_warning() -> None:
    """``git * main`` also matches inserted options and auto-approves them."""
    findings = check_permission_rule_syntax("Bash(git * main)")
    assert "warning" in _levels(findings)


def test_rule3_trailing_wildcard_sibling_is_silent() -> None:
    """A TRAILING wildcard (``git commit *``) is the normal idiom — not flagged."""
    findings = check_permission_rule_syntax("Bash(git commit *)")
    assert findings == []


def test_rule3_bare_wildcard_whole_command_is_silent() -> None:
    """A bare '*' with nothing after it (whole-command wildcard) is not flagged."""
    findings = check_permission_rule_syntax("Bash(*)")
    assert findings == []


def test_rule3_does_not_apply_to_non_bash_tools() -> None:
    """A '*' token shape in a non-Bash specifier draws no rule-3 finding."""
    findings = check_permission_rule_syntax("Read(* foo)")
    assert "warning" not in _levels(findings)


# =============================================================================
# Rule 4 — WARNING: Windows escaped-paren path
# =============================================================================


def test_rule4_windows_escaped_paren_fires_warning() -> None:
    """A Windows path with '\\(' reads as an escaped parenthesis, not a separator."""
    findings = check_permission_rule_syntax(r"Edit(C:\dir\(name)\**)")
    assert "warning" in _levels(findings)


def test_rule4_forward_slash_sibling_is_silent() -> None:
    """The forward-slash spelling of the same path draws no rule-4 finding."""
    findings = check_permission_rule_syntax("Edit(C:/dir/(name)/**)")
    assert not any(sev == "warning" and "Windows-style" in msg for sev, msg in findings)


def test_rule4_windows_path_without_escaped_paren_is_silent() -> None:
    """A plain Windows path with no parens at all draws no rule-4 finding."""
    findings = check_permission_rule_syntax(r"Edit(C:\Users\dev\project\**)")
    assert findings == []


# =============================================================================
# Rule 5 — INFO: parentheses inside a path specifier
# =============================================================================


def test_rule5_parens_in_path_fires_info() -> None:
    """Balanced parens in an Edit path draw an advisory INFO."""
    findings = check_permission_rule_syntax("Edit(./src/(gen)/**)")
    assert "info" in _levels(findings)


def test_rule5_no_parens_sibling_is_silent() -> None:
    """The equivalent path without parens draws no rule-5 finding."""
    findings = check_permission_rule_syntax("Edit(./src/gen/**)")
    assert findings == []


def test_rule5_suppressed_when_rule4_already_fired() -> None:
    """A Windows escaped-paren rule explains itself via rule 4, not also rule 5."""
    findings = check_permission_rule_syntax(r"Edit(C:\dir\(name)\**)")
    assert "info" not in _levels(findings)


def test_rule5_does_not_apply_to_bash() -> None:
    """Parens in a Bash specifier (not a path tool) draw no rule-5 finding."""
    findings = check_permission_rule_syntax("Bash(echo (hi))")
    assert "info" not in _levels(findings)


# =============================================================================
# Fail-safe / non-rule-matching inputs
# =============================================================================


def test_unrecognized_tool_prefix_is_silent() -> None:
    """A string with no recognized tool prefix yields no finding."""
    assert check_permission_rule_syntax("NotARealTool(foo)") == []


def test_bare_tool_name_no_parens_is_silent() -> None:
    """A bare tool name with no specifier at all is a legal shape — silent."""
    assert check_permission_rule_syntax("Bash") == []


def test_non_string_value_is_silent() -> None:
    """A non-string permission-rule entry (malformed JSON shape) never crashes."""
    assert check_permission_rule_syntax(123) == []
    assert check_permission_rule_syntax(None) == []


def test_unbalanced_never_closing_parens_is_silent() -> None:
    """Parentheses that never balance are unparseable — fail-safe, no finding."""
    assert check_permission_rule_syntax("Edit(unterminated") == []


# =============================================================================
# Wiring — validate_settings_json_project_scope (.claude/settings.json)
# =============================================================================


def test_project_scope_flags_trailing_text_major(tmp_path: Path) -> None:
    """The project-scope validator surfaces a rule-1 MAJOR through the real file path."""
    settings = _write_settings(tmp_path / "settings.json", {"allow": ["Bash(ls) x"]})
    report = ValidationReport()
    validate_settings_json_project_scope(settings, report)
    assert any(r.level == "MAJOR" and "closing parenthesis" in r.message for r in report.results)


def test_project_scope_well_formed_rule_draws_no_permission_rule_finding(tmp_path: Path) -> None:
    """A well-formed permission rule draws no permission-rule-syntax finding."""
    settings = _write_settings(tmp_path / "settings.json", {"allow": ["Bash(ls)", "Read(./docs/**)"]})
    report = ValidationReport()
    validate_settings_json_project_scope(settings, report)
    assert not any("Permission rule" in r.message for r in report.results)


def test_project_scope_flags_unclosed_bracket_major(tmp_path: Path) -> None:
    """The project-scope validator surfaces a rule-2 MAJOR for an unclosed '['."""
    settings = _write_settings(tmp_path / "settings.json", {"deny": ["Edit(src/[abc.py)"]})
    report = ValidationReport()
    validate_settings_json_project_scope(settings, report)
    assert any(r.level == "MAJOR" and "unclosed" in r.message for r in report.results)


def test_project_scope_flags_wildcard_before_subcommand_warning(tmp_path: Path) -> None:
    """The project-scope validator surfaces a rule-3 WARNING."""
    settings = _write_settings(tmp_path / "settings.json", {"ask": ["Bash(git * main)"]})
    report = ValidationReport()
    validate_settings_json_project_scope(settings, report)
    assert any(r.level == "WARNING" and "wildcard" in r.message for r in report.results)


def test_project_scope_checks_all_three_buckets(tmp_path: Path) -> None:
    """allow/ask/deny are all inspected, not just one bucket."""
    settings = _write_settings(
        tmp_path / "settings.json",
        {
            "allow": ["Bash(ls) x"],
            "ask": ["Bash(git * main)"],
            "deny": ["Edit(src/[abc.py)"],
        },
    )
    report = ValidationReport()
    validate_settings_json_project_scope(settings, report)
    permission_findings = [r for r in report.results if "Permission rule" in r.message]
    assert len(permission_findings) == 3


# =============================================================================
# Wiring — validate_settings_local_json (.claude/settings.local.json)
# =============================================================================


def test_local_scope_flags_trailing_text_major(tmp_path: Path) -> None:
    """The local-scope validator mirrors the project-scope rule-1 check."""
    settings = _write_settings(tmp_path / "settings.local.json", {"allow": ["Bash(ls) x"]})
    report = ValidationReport()
    validate_settings_local_json(settings, report)
    assert any(r.level == "MAJOR" and "closing parenthesis" in r.message for r in report.results)


def test_local_scope_well_formed_rule_draws_no_permission_rule_finding(tmp_path: Path) -> None:
    """A well-formed permission rule draws no permission-rule-syntax finding (local scope)."""
    settings = _write_settings(tmp_path / "settings.local.json", {"allow": ["Bash(ls)"]})
    report = ValidationReport()
    validate_settings_local_json(settings, report)
    assert not any("Permission rule" in r.message for r in report.results)


def test_local_scope_flags_windows_escaped_paren_warning(tmp_path: Path) -> None:
    """The local-scope validator surfaces the rule-4 WARNING."""
    settings = _write_settings(tmp_path / "settings.local.json", {"deny": [r"Edit(C:\dir\(name)\**)"]})
    report = ValidationReport()
    validate_settings_local_json(settings, report)
    assert any(r.level == "WARNING" and "escaped parenthesis" in r.message for r in report.results)


def test_local_scope_flags_parens_in_path_info(tmp_path: Path) -> None:
    """The local-scope validator surfaces the rule-5 INFO."""
    settings = _write_settings(tmp_path / "settings.local.json", {"allow": ["Edit(./src/(gen)/**)"]})
    report = ValidationReport()
    validate_settings_local_json(settings, report)
    assert any(r.level == "INFO" and "parentheses" in r.message for r in report.results)


def test_local_scope_and_project_scope_agree_on_same_rule(tmp_path: Path) -> None:
    """The two wirings emit the identical severity for the identical rule string."""
    rule = "Bash(git * main)"
    proj_settings = _write_settings(tmp_path / "settings.json", {"ask": [rule]})
    local_settings = _write_settings(tmp_path / "settings.local.json", {"ask": [rule]})
    proj_report = ValidationReport()
    local_report = ValidationReport()
    validate_settings_json_project_scope(proj_settings, proj_report)
    validate_settings_local_json(local_settings, local_report)
    proj_levels = sorted(r.level for r in proj_report.results if "Permission rule" in r.message)
    local_levels = sorted(r.level for r in local_report.results if "Permission rule" in r.message)
    assert proj_levels == local_levels == ["WARNING"]


def test_non_list_permissions_bucket_does_not_crash(tmp_path: Path) -> None:
    """A string allow/ask/deny value is handled without crashing.

    NOTE the honest scope: this asserts crash-safety ONLY. It canNOT prove the
    bucket was skipped rather than iterated, because a string's characters
    ('n','o','t',...) match no tool name, so per-character iteration emits
    exactly the same empty finding set as a correct skip. The guard itself is
    proven by ``test_dict_permissions_bucket_is_skipped_not_iterated`` below,
    which uses a bucket whose iteration IS observable.
    """
    settings = _write_settings(
        tmp_path / "settings.json",
        cast(dict[str, list[str]], {"allow": "not-a-list"}),  # deliberate: non-list bucket
    )
    report = ValidationReport()
    result = validate_settings_json_project_scope(settings, report)
    assert result is not None


def test_dict_permissions_bucket_is_skipped_not_iterated(tmp_path: Path) -> None:
    """A dict allow/ask/deny value is SKIPPED, not iterated over its keys.

    Mutation-detectable by construction: iterating a dict yields its KEYS, and
    this key is a genuinely malformed rule that fires a MAJOR when passed to
    ``check_permission_rule_syntax``. So dropping the caller's
    ``isinstance(rules, list)`` guard makes this test FAIL — unlike the string
    case above, where iteration is unobservable. The positive control that the
    filter can see findings at all is ``test_trailing_text_after_paren_fires``.

    PROOF (a recipe, not a claim — re-run it): in
    ``validate_project_scope._flag_malformed_permission_rules`` swap
    ``if not isinstance(rules, list):`` for ``if rules is None:`` and this test
    FAILS; restore it and it passes. Measured: exit 1, exactly 1 FAILED.
    """
    settings = _write_settings(
        tmp_path / "settings.json",
        cast(dict[str, list[str]], {"allow": {"Bash(ls) x": True}}),  # deliberate: dict bucket
    )
    report = ValidationReport()
    result = validate_settings_json_project_scope(settings, report)
    assert result is not None
    assert [r for r in report.results if not _is_unrelated_row(r)] == []


def test_dict_permissions_bucket_is_skipped_not_iterated_local(tmp_path: Path) -> None:
    """Local-scope sibling of the guard test above — the loop is a SECOND copy.

    ``validate_local_scope._flag_malformed_permission_rules_local`` carries its
    own ``isinstance(rules, list)`` guard. Proving only the project-scope copy
    would leave this one correct-but-untested, which is exactly how two copies
    drift apart.

    PROOF (a recipe, not a claim — re-run it): in
    ``validate_local_scope._flag_malformed_permission_rules_local`` swap
    ``if not isinstance(rules, list):`` for ``if rules is None:`` and this test
    FAILS; restore it and it passes. Measured: exit 1, exactly 1 FAILED — and
    the project-scope twin above stayed green, which is what proves these are
    two independent guards rather than one.
    """
    settings = _write_settings(
        tmp_path / "settings.local.json",
        cast(dict[str, list[str]], {"allow": {"Bash(ls) x": True}}),  # deliberate: dict bucket
    )
    report = ValidationReport()
    result = validate_settings_local_json(settings, report)
    assert result is not None
    assert [r for r in report.results if not _is_unrelated_row(r)] == []


def test_no_permissions_key_does_not_crash(tmp_path: Path) -> None:
    """A settings file with no 'permissions' key at all is handled cleanly."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {}}), encoding="utf-8")
    report = ValidationReport()
    result = validate_settings_json_project_scope(settings, report)
    assert result is not None
    assert not any("Permission rule" in r.message for r in report.results)
