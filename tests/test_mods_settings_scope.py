#!/usr/bin/env python3
"""Mods settings-scope gaps B1/B2/B3/B5/B6 (mods admin chapter + mods-reference).

Each rule is two-sided: the positive case fires exactly once, and a sibling
(managed-shaped / unrelated / correctly placed) draws nothing new. Both the
project (.claude/settings.json) and local (.claude/settings.local.json)
entry points are covered.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import cpv_validation_common as cvc  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402
from validate_local_scope import validate_settings_local_json  # noqa: E402
from validate_project_scope import validate_settings_json_project_scope  # noqa: E402

GUARD_ID = "cc-plugin-sec-default@builtin"
ENTRY_POINTS = [
    pytest.param(validate_settings_json_project_scope, "settings.json", id="project"),
    pytest.param(validate_settings_local_json, "settings.local.json", id="local"),
]


def _run(entry, filename: str, tmp_path: Path, data: dict) -> ValidationReport:
    f = tmp_path / filename
    f.write_text(json.dumps(data), encoding="utf-8")
    report = ValidationReport()
    entry(f, report)
    return report


def _about(report: ValidationReport, needle: str, level: str | None = None):
    return [r for r in report.results if needle in r.message and (level is None or r.level == level)]


def _unknown_hints(report: ValidationReport, key: str):
    return [r for r in report.results if "not a known Claude Code settings key" in r.message and key in r.message]


@pytest.mark.parametrize(("entry", "filename"), ENTRY_POINTS)
class TestMods:
    @pytest.mark.parametrize("key", ["prependPlugins", "appendPlugins"])
    def test_b1_plugin_list_in_repo_settings_is_major(self, entry, filename, tmp_path, key):
        """B1: prependPlugins/appendPlugins in a repository settings file -> one MAJOR naming managed settings."""
        report = _run(entry, filename, tmp_path, {key: [f"x@{GUARD_ID.split('@')[1]}"]})
        hits = _about(report, f"'{key}'", "MAJOR")
        assert len(hits) == 1, [(r.level, r.message) for r in report.results]
        assert "managed settings" in hits[0].message

    def test_b1_unrelated_key_draws_no_plugin_list_finding(self, entry, filename, tmp_path):
        """B1 control: an unrelated key draws no prepend/append finding."""
        report = _run(entry, filename, tmp_path, {"model": "sonnet"})
        assert not _about(report, "prependPlugins") and not _about(report, "appendPlugins")

    @pytest.mark.parametrize("key", ["allowManagedModsOnly", "allowModsToOverrideDenyRules"])
    def test_b2_top_level_guard_option_is_one_major(self, entry, filename, tmp_path, key):
        """B2: a top-level guard option -> exactly ONE finding (MAJOR), no extra unknown-key hint."""
        report = _run(entry, filename, tmp_path, {key: True})
        about_key = _about(report, key)
        assert len(about_key) == 1, [(r.level, r.message) for r in report.results]
        assert about_key[0].level == "MAJOR"
        assert f'pluginConfigs["{GUARD_ID}"].options' in about_key[0].message
        assert "no effect" in about_key[0].message
        assert not _unknown_hints(report, key)

    def test_b2_correct_placement_draws_no_guard_finding(self, entry, filename, tmp_path):
        """B2 control: the guard option under pluginConfigs[...].options draws no guard-option/id finding here
        (CPV does not validate managed-settings files; repo-scope pluginConfigs is covered by validate_plugin)."""
        data = {"pluginConfigs": {GUARD_ID: {"options": {"allowManagedModsOnly": True}}}}
        report = _run(entry, filename, tmp_path, data)
        assert not _about(report, "misplaced guard option")
        assert not _about(report, "sec-default@builtin")

    def test_b3_wrong_guard_id_is_major(self, entry, filename, tmp_path):
        """B3: pluginConfigs key 'sec-default@builtin' -> MAJOR naming the correct id."""
        report = _run(entry, filename, tmp_path, {"pluginConfigs": {"sec-default@builtin": {"options": {}}}})
        hits = _about(report, "pluginConfigs uses id", "MAJOR")
        assert len(hits) == 1, [(r.level, r.message) for r in report.results]
        assert GUARD_ID in hits[0].message

    def test_b5_function_hooks_env_warns_at_any_value(self, entry, filename, tmp_path):
        """B5: CLAUDE_CODE_ENABLE_FUNCTION_HOOKS in env -> WARNING, even with value '0'."""
        report = _run(entry, filename, tmp_path, {"env": {"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "0"}})
        hits = _about(report, "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS", "WARNING")
        assert len(hits) == 1, [(r.level, r.message) for r in report.results]
        assert "2.1.287" in hits[0].message

    def test_b5_unrelated_env_draws_no_function_hooks_warning(self, entry, filename, tmp_path):
        """B5 control: another env var draws no function-hooks finding."""
        report = _run(entry, filename, tmp_path, {"env": {"CLAUDE_CODE_PLUGIN_DIR_WATCH": "1"}})
        assert not _about(report, "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS")


def test_b6_plugin_dir_watch_is_known_env_var():
    """B6: CLAUDE_CODE_PLUGIN_DIR_WATCH clears the unknown-env-var check."""
    assert cvc.is_valid_plugin_env_var("CLAUDE_CODE_PLUGIN_DIR_WATCH")


def test_b6_near_miss_env_var_still_unknown():
    """B6 control: a near-miss spelling is still unknown."""
    assert not cvc.is_valid_plugin_env_var("CLAUDE_CODE_PLUGIN_DIR_WATCHER")
