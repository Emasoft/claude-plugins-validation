"""Tests for the shared dependency-element schema SSOT (cpv_dependency_schema).

This module is the single source of truth both validate_plugin and
validate_marketplace call to validate one `dependencies[i]` element, resolving
the issue-#106 contradiction (the two validators used to disagree on whether the
object form `{name, version}` is valid). These tests pin the report-agnostic
`(level, message)` contract directly, independent of either caller.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure scripts dir is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cpv_dependency_schema import (  # noqa: E402
    is_valid_semver_range,
    validate_dependency_element,
)


def _levels(findings):
    return [lvl for lvl, _ in findings]


def _messages(findings):
    return [msg for _, msg in findings]


class TestValidateDependencyElement:
    """The shared element validator returns the correct (level, message) findings."""

    def test_versioned_object_is_clean(self):
        """A well-formed {name, version} object yields no findings."""
        assert validate_dependency_element(0, {"name": "dev-browser", "version": "~1.2.0"}) == []

    def test_object_with_all_subkeys_is_clean(self):
        """{name, marketplace, version} (full recognized subkey set) yields no findings."""
        assert validate_dependency_element(3, {"name": "x", "marketplace": "acme", "version": "^2.0"}) == []

    def test_bare_versioned_via_object_is_clean(self):
        """Every documented semver-range idiom is accepted on a version subkey."""
        for rng in ("~2.1.0", "^2.0", "^2.0.0-0", ">=1.4", "=2.1.0", "1.2.3", "1.0.0 - 2.0.0", "^1.0 || ^2.0"):
            assert validate_dependency_element(0, {"name": "lib", "version": rng}) == [], f"range {rng!r} rejected"

    def test_bare_unversioned_string_warns(self):
        """A bare kebab string with no version → exactly one WARNING (pin-it)."""
        findings = validate_dependency_element(0, "dev-browser")
        assert _levels(findings) == ["WARNING"]
        assert "no version constraint" in findings[0][1]
        assert "dependencies[0]" in findings[0][1]

    def test_non_kebab_bare_string_major(self):
        """A non-kebab bare string → MAJOR (invalid plugin name)."""
        findings = validate_dependency_element(1, "Bad_Name")
        assert _levels(findings) == ["MAJOR"]
        assert "kebab-case" in findings[0][1]

    def test_non_string_non_dict_major(self):
        """A number or a list element → MAJOR 'string or object'."""
        for bad in (42, ["nested"], 3.14, True, None):
            findings = validate_dependency_element(2, bad)
            assert _levels(findings) == ["MAJOR"], f"{bad!r} did not yield a single MAJOR"
            assert "string or object" in findings[0][1]

    def test_object_missing_name_major(self):
        """An object without `name` (but valid version) → exactly one missing-name MAJOR."""
        findings = validate_dependency_element(0, {"version": "1.0.0"})
        assert _levels(findings) == ["MAJOR"]
        assert "missing required 'name'" in findings[0][1]

    def test_object_bad_name_type_major(self):
        """A non-string / non-kebab `name` → MAJOR."""
        for bad_name in (123, "Bad!", "with space"):
            findings = validate_dependency_element(0, {"name": bad_name})
            assert any(lvl == "MAJOR" and ".name" in msg for lvl, msg in findings), f"{bad_name!r} not flagged"

    def test_object_bad_version_major(self):
        """An invalid semver range → MAJOR referencing .version."""
        findings = validate_dependency_element(0, {"name": "lib", "version": "not-a-version"})
        assert any(lvl == "MAJOR" and ".version" in msg and "semver range" in msg for lvl, msg in findings)

    def test_object_non_string_version_major(self):
        """A non-string version (e.g. a number) → MAJOR (not silently accepted)."""
        findings = validate_dependency_element(0, {"name": "lib", "version": 2})
        assert any(lvl == "MAJOR" and ".version" in msg for lvl, msg in findings)

    def test_object_bad_marketplace_major(self):
        """A non-kebab `marketplace` → MAJOR referencing .marketplace."""
        findings = validate_dependency_element(0, {"name": "lib", "marketplace": "Bad Mkpl"})
        assert any(lvl == "MAJOR" and ".marketplace" in msg for lvl, msg in findings)

    def test_object_unknown_subkey_minor(self):
        """An unknown sub-key → MINOR referencing the index + key name."""
        findings = validate_dependency_element(0, {"name": "lib", "version": "1.0.0", "wat": "x"})
        assert _levels(findings) == ["MINOR"]
        assert "dependencies[0].wat" in findings[0][1]
        assert "not a recognized dependency sub-field" in findings[0][1]

    def test_object_multiple_unknown_subkeys_sorted_minor(self):
        """Multiple unknown sub-keys → one MINOR each, deterministically sorted."""
        findings = validate_dependency_element(0, {"name": "lib", "zed": 1, "abc": 2})
        assert _levels(findings) == ["MINOR", "MINOR"]
        # sorted(set(...)) → 'abc' before 'zed'
        assert ".abc" in _messages(findings)[0]
        assert ".zed" in _messages(findings)[1]

    def test_index_is_echoed(self):
        """The array index passed in is reflected in every message."""
        findings = validate_dependency_element(7, "BadName!")
        assert "dependencies[7]" in findings[0][1]


class TestIsValidSemverRange:
    """The shared semver-range predicate matches the documented idioms."""

    def test_valid_ranges(self):
        for rng in (
            "~2.1.0",
            "^2.0",
            "^2.0.0-0",
            ">=1.4",
            "=2.1.0",
            "1.2.3",
            "1.0.0 - 2.0.0",
            "^1.0 || ^2.0",
            "1.2.3-rc.1",
            "1.2.3+build.5",
        ):
            assert is_valid_semver_range(rng), f"{rng!r} wrongly rejected"

    def test_invalid_ranges(self):
        for bad in ("", "not-a-version", "1.2.3foo", "1 . 2 . 3", "café", 2, None, [1]):
            assert not is_valid_semver_range(bad), f"{bad!r} wrongly accepted"
