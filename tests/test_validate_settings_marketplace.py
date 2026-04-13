#!/usr/bin/env python3
"""
Tests for validate_settings_marketplace.py

Covers: validate_settings_marketplace_file, validate_extra_known_marketplaces,
validate_source_object, and the major source types defined in the v2.1.80
spec for settings.json::extraKnownMarketplaces.

Each test describes its aim in the docstring and asserts against the real
ValidationReport (no mocking).
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
from validate_settings_marketplace import (
    EXTRA_KNOWN_MARKETPLACES_KEY,
    validate_extra_known_marketplaces,
    validate_settings_marketplace_file,
    validate_source_object,
)


def _write_settings(tmp_path: Path, data: dict) -> Path:
    """Helper: write a settings.json file with the given dict."""
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(data), encoding="utf-8")
    return settings_path


def test_valid_github_source_passes(tmp_path: Path):
    """A settings.json with a valid github-typed marketplace entry produces no CRITICAL/MAJOR findings."""
    settings_path = _write_settings(
        tmp_path,
        {
            EXTRA_KNOWN_MARKETPLACES_KEY: {
                "emasoft-marketplace": {
                    "source": {
                        "source": "github",
                        "repo": "Emasoft/claude-plugins",
                    }
                }
            }
        },
    )
    report = validate_settings_marketplace_file(settings_path)
    assert not report.has_critical, (
        f"unexpected CRITICAL: {[r.message for r in report.results if r.level == 'CRITICAL']}"
    )
    assert not report.has_major, f"unexpected MAJOR: {[r.message for r in report.results if r.level == 'MAJOR']}"
    assert any(r.level == "PASSED" and "github source valid" in r.message for r in report.results), (
        "expected a PASSED result confirming the github source"
    )


def test_valid_inline_settings_source_with_plugins(tmp_path: Path):
    """An inline 'settings' source type with a non-empty plugins array is valid."""
    settings_path = _write_settings(
        tmp_path,
        {
            EXTRA_KNOWN_MARKETPLACES_KEY: {
                "my-inline-marketplace": {
                    "source": {
                        "source": "settings",
                        "name": "my-inline-marketplace",
                        "plugins": [
                            {"name": "plugin-one", "source": {"source": "github", "repo": "acme/plugin-one"}},
                            {"name": "plugin-two", "source": "./relative/path"},
                        ],
                    }
                }
            }
        },
    )
    report = validate_settings_marketplace_file(settings_path)
    assert not report.has_critical, (
        f"unexpected CRITICAL: {[r.message for r in report.results if r.level == 'CRITICAL']}"
    )
    assert not report.has_major, f"unexpected MAJOR: {[r.message for r in report.results if r.level == 'MAJOR']}"
    assert any(r.level == "PASSED" and "inline settings source" in r.message for r in report.results), (
        "expected a PASSED result confirming the inline settings source"
    )


def test_unknown_source_type_is_major(tmp_path: Path):
    """An unknown source type (e.g., 'quantum') produces a MAJOR finding."""
    settings_path = _write_settings(
        tmp_path,
        {EXTRA_KNOWN_MARKETPLACES_KEY: {"weird-marketplace": {"source": {"source": "quantum", "data": "irrelevant"}}}},
    )
    report = validate_settings_marketplace_file(settings_path)
    assert report.has_major, "expected MAJOR for unknown source type"
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert any("unknown source type 'quantum'" in m for m in major_msgs), (
        f"expected unknown-source-type MAJOR, got: {major_msgs}"
    )


def test_missing_required_fields_per_source_type(tmp_path: Path):
    """Each source type missing its required fields produces a MAJOR finding.

    github without 'repo' -> MAJOR
    git-subdir without 'path' -> MAJOR
    settings without 'plugins' -> MAJOR
    """
    settings_path = _write_settings(
        tmp_path,
        {
            EXTRA_KNOWN_MARKETPLACES_KEY: {
                "github-missing-repo": {"source": {"source": "github"}},
                "gitsub-missing-path": {"source": {"source": "git-subdir", "url": "https://github.com/foo/bar.git"}},
                "settings-missing-plugins": {"source": {"source": "settings", "name": "settings-missing-plugins"}},
            }
        },
    )
    report = validate_settings_marketplace_file(settings_path)
    assert report.has_major, "expected MAJOR findings for missing-required-field cases"
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]

    assert any("github-missing-repo" in m and "repo" in m for m in major_msgs), (
        f"expected MAJOR for github missing 'repo', got: {major_msgs}"
    )
    assert any("gitsub-missing-path" in m and "path" in m for m in major_msgs), (
        f"expected MAJOR for git-subdir missing 'path', got: {major_msgs}"
    )
    assert any("settings-missing-plugins" in m and "plugins" in m for m in major_msgs), (
        f"expected MAJOR for settings missing 'plugins', got: {major_msgs}"
    )


def test_no_extra_known_marketplaces_key_passes(tmp_path: Path):
    """A settings.json without an extraKnownMarketplaces key validates successfully (the block is optional)."""
    settings_path = _write_settings(
        tmp_path,
        {
            "model": "sonnet",
            "env": {"FOO": "bar"},
        },
    )
    report = validate_settings_marketplace_file(settings_path)
    assert not report.has_critical
    assert not report.has_major
    assert not report.has_minor
    assert any(r.level == "PASSED" and "no 'extraKnownMarketplaces' block" in r.message for r in report.results), (
        "expected a PASSED result stating the block is absent"
    )


def test_inline_plugin_missing_name_and_source_is_major(tmp_path: Path):
    """An inline plugin entry missing both 'name' and 'source' is flagged as MAJOR."""
    settings_path = _write_settings(
        tmp_path,
        {
            EXTRA_KNOWN_MARKETPLACES_KEY: {
                "inline-broken": {
                    "source": {
                        "source": "settings",
                        "name": "inline-broken",
                        "plugins": [
                            {"description": "no name, no source"},
                        ],
                    }
                }
            }
        },
    )
    report = validate_settings_marketplace_file(settings_path)
    assert report.has_major
    major_msgs = [r.message for r in report.results if r.level == "MAJOR"]
    assert any("missing required 'name'" in m for m in major_msgs), (
        f"expected MAJOR for missing plugin name, got: {major_msgs}"
    )
    assert any("missing required 'source'" in m for m in major_msgs), (
        f"expected MAJOR for missing plugin source, got: {major_msgs}"
    )


def test_nonexistent_file_is_critical(tmp_path: Path):
    """Pointing the validator at a non-existent settings.json yields a CRITICAL finding."""
    missing = tmp_path / "does_not_exist.json"
    report = validate_settings_marketplace_file(missing)
    assert report.has_critical
    assert any("not found" in r.message for r in report.results if r.level == "CRITICAL")


def test_broken_json_is_critical(tmp_path: Path):
    """A settings.json with broken JSON is reported as CRITICAL (parse error)."""
    broken = tmp_path / "settings.json"
    broken.write_text("{not valid json", encoding="utf-8")
    report = validate_settings_marketplace_file(broken)
    assert report.has_critical
    assert any("parse error" in r.message.lower() for r in report.results if r.level == "CRITICAL")


def test_directory_source_emits_warning(tmp_path: Path):
    """A directory source (dev-only local path) is recognized but emits a WARNING about portability."""
    settings_path = _write_settings(
        tmp_path,
        {
            EXTRA_KNOWN_MARKETPLACES_KEY: {
                "local-dev": {"source": {"source": "directory", "path": "/Users/me/local-marketplace"}}
            }
        },
    )
    report = validate_settings_marketplace_file(settings_path)
    assert not report.has_critical
    assert not report.has_major
    assert any(r.level == "WARNING" and "directory source" in r.message for r in report.results), (
        "expected a WARNING about directory-source portability"
    )


def test_validate_source_object_direct_call_for_npm():
    """validate_source_object directly accepts an npm source dict and marks it PASSED."""
    report = ValidationReport()
    validate_source_object(
        {"source": "npm", "package": "@claude/some-marketplace", "version": "^1.2.3"},
        "npm-market",
        report,
        "settings.json",
    )
    assert not report.has_major
    assert any(r.level == "PASSED" and "npm source valid" in r.message for r in report.results)


def test_extra_known_marketplaces_not_an_object_is_critical():
    """If extraKnownMarketplaces is a list instead of an object, it is reported as CRITICAL."""
    report = ValidationReport()
    validate_extra_known_marketplaces(["not", "a", "dict"], report, "settings.json")  # type: ignore[arg-type]
    assert report.has_critical
    assert any("must be an object" in r.message for r in report.results if r.level == "CRITICAL")
