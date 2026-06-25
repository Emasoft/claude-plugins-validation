#!/usr/bin/env python3
"""Two-sided tests for the v2.1.186 skill-frontmatter keys (TRDD-ABFRMED0).

Claude Code v2.1.186 makes `display-name`, `default-enabled`, `fallback`, and
`metadata` valid SKILL frontmatter keys, accepted in kebab-case, snake_case, AND
camelCase. Before this fix CPV emitted a false "Unknown frontmatter field"
WARNING for them. These tests prove the four keys clear in all three casings
across the basic + comprehensive skill validators and the command validator,
while genuine typos STILL warn (casing tolerance must not swallow typos).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (  # noqa: E402
    SKILL_FRONTMATTER_FIELDS,
    ValidationReport,
    _to_kebab,
    is_known_skill_frontmatter_key,
)
from validate_command import (  # noqa: E402
    CommandValidationReport,
    validate_frontmatter_exists,
)
from validate_skill import validate_frontmatter as skill_validate_frontmatter  # noqa: E402
from validate_skill_comprehensive import validate_field_whitelist  # noqa: E402

_V2_1_186_KEYS = ["display-name", "default-enabled", "fallback", "metadata"]
_CASINGS = {
    "display-name": ["display-name", "display_name", "displayName"],
    "default-enabled": ["default-enabled", "default_enabled", "defaultEnabled"],
    "fallback": ["fallback", "fallback", "fallback"],  # single-word: all casings identical
    "metadata": ["metadata", "metadata", "metadata"],
}
_TYPOS = ["displaynam", "defaultEnable", "metadta", "fallbck", "bogus-field"]


def _all_casing_variants() -> list[str]:
    seen: list[str] = []
    for variants in _CASINGS.values():
        for v in variants:
            if v not in seen:
                seen.append(v)
    return seen


def _unknown_field_warnings(report: ValidationReport) -> list[str]:
    """The messages of every WARNING about an unknown frontmatter field."""
    return [
        r.message
        for r in report.results
        if r.level == "WARNING" and "Unknown frontmatter field" in r.message
    ]


# --------------------------------------------------------------------------- #
# The shared helper — the SSOT both validators route through
# --------------------------------------------------------------------------- #


def test_helper_accepts_four_keys_all_three_casings() -> None:
    """is_known_skill_frontmatter_key accepts the 4 v2.1.186 keys in kebab/snake/camel."""
    for variants in _CASINGS.values():
        for v in variants:
            assert is_known_skill_frontmatter_key(v) is True, v


def test_helper_still_rejects_typos() -> None:
    """A near-miss typo of a casing-tolerant key is NOT swallowed — still unknown."""
    for typo in _TYPOS:
        assert is_known_skill_frontmatter_key(typo) is False, typo


def test_helper_accepts_stable_keys_exactly() -> None:
    """Every pre-existing stable field still matches exactly."""
    for key in SKILL_FRONTMATTER_FIELDS:
        assert is_known_skill_frontmatter_key(key) is True, key


def test_to_kebab_normalizes_camel_and_snake() -> None:
    """_to_kebab maps camelCase and snake_case to kebab; kebab is a no-op."""
    assert _to_kebab("displayName") == "display-name"
    assert _to_kebab("display_name") == "display-name"
    assert _to_kebab("display-name") == "display-name"
    assert _to_kebab("defaultEnabled") == "default-enabled"
    assert _to_kebab("metadata") == "metadata"


def test_four_keys_present_in_set() -> None:
    """The four canonical (kebab) keys are in SKILL_FRONTMATTER_FIELDS."""
    for key in _V2_1_186_KEYS:
        assert key in SKILL_FRONTMATTER_FIELDS, key


# --------------------------------------------------------------------------- #
# Basic skill validator — validate_skill.validate_frontmatter
# --------------------------------------------------------------------------- #


def _skill_content(extra_key: str) -> str:
    return (
        "---\n"
        "name: test-skill\n"
        "description: A skill used to test frontmatter casing tolerance.\n"
        f"{extra_key}: some-value\n"
        "---\n\n"
        "Body content for the test skill.\n"
    )


def test_basic_validator_accepts_all_casings_no_warning() -> None:
    """validate_skill: the 4 keys in any casing produce no unknown-field WARNING."""
    for variant in _all_casing_variants():
        report = ValidationReport()
        skill_validate_frontmatter(Path("SKILL.md"), _skill_content(variant), report)
        assert _unknown_field_warnings(report) == [], variant


def test_basic_validator_still_warns_on_typo() -> None:
    """validate_skill: a typo'd key STILL raises the unknown-field WARNING."""
    report = ValidationReport()
    skill_validate_frontmatter(Path("SKILL.md"), _skill_content("displaynam"), report)
    warns = _unknown_field_warnings(report)
    assert any("displaynam" in w for w in warns), warns


# --------------------------------------------------------------------------- #
# Comprehensive skill validator — validate_field_whitelist
# --------------------------------------------------------------------------- #


def _comprehensive_unknown(frontmatter: Mapping[str, object]) -> list[str]:
    report = ValidationReport()
    validate_field_whitelist(frontmatter, report)
    return [
        r.message
        for r in report.results
        if r.level == "WARNING" and "Unknown frontmatter field" in r.message
    ]


def test_comprehensive_validator_accepts_all_casings() -> None:
    """validate_field_whitelist: the 4 keys in any casing produce no WARNING."""
    for variant in _all_casing_variants():
        fm = {"name": "test-skill", "description": "desc", variant: "v"}
        assert _comprehensive_unknown(fm) == [], variant


def test_basic_and_comprehensive_agree_on_metadata() -> None:
    """The two skill validators now AGREE: neither warns on `metadata` (was inconsistent)."""
    report = ValidationReport()
    skill_validate_frontmatter(Path("SKILL.md"), _skill_content("metadata"), report)
    assert _unknown_field_warnings(report) == []
    assert _comprehensive_unknown({"name": "s", "description": "d", "metadata": {}}) == []


# --------------------------------------------------------------------------- #
# Command validator — commands share the skill field set
# --------------------------------------------------------------------------- #


def _command_content(extra_key: str) -> str:
    return (
        "---\n"
        "description: A command used to test frontmatter casing tolerance.\n"
        f"{extra_key}: some-value\n"
        "---\n\n"
        "Command body content goes here for the casing test.\n"
    )


def test_command_validator_accepts_all_casings() -> None:
    """validate_command: the 4 keys in any casing produce no unknown-field WARNING."""
    for variant in _all_casing_variants():
        report = CommandValidationReport()
        validate_frontmatter_exists(_command_content(variant), report, "test.md")
        warns = [
            r.message
            for r in report.results
            if r.level == "WARNING" and "Unknown frontmatter field" in r.message
        ]
        assert warns == [], (variant, warns)


def test_command_validator_still_warns_on_typo() -> None:
    """validate_command: a typo'd key STILL raises the unknown-field WARNING."""
    report = CommandValidationReport()
    validate_frontmatter_exists(_command_content("metadta"), report, "test.md")
    warns = [
        r.message
        for r in report.results
        if r.level == "WARNING" and "Unknown frontmatter field" in r.message
    ]
    assert any("metadta" in w for w in warns), warns


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
