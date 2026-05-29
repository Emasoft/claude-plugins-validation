#!/usr/bin/env python3
"""Tests for the Claude Code v2.1.146 → v2.1.154 changelog catch-up (TRDD-1b8efb4c).

Nine spec items reach the plugin-validator surface. Each test is two-sided
where a behaviour (not just set membership) is involved: the valid shape must
pass AND the wrong shape must still be flagged, so a positive assertion can
never pass vacuously against a validator that accepts everything.

* MessageDisplay hook event + ``displayContent`` output (v2.1.152)
* SessionStart ``reloadSkills`` (bool) / ``sessionTitle`` (str) output (v2.1.152)
* ``disallowed-tools`` skill + slash-command frontmatter field (v2.1.152)
* ``pluginSuggestionMarketplaces`` managed setting (v2.1.152)
* ``allowAllClaudeAiMcps`` managed setting (v2.1.149)
* ``skipLfs`` marketplace source field for github/git (v2.1.153)
* ``defaultEnabled`` plugin.json + marketplace-entry field (v2.1.154)
* ``Workflow`` tool (v2.1.154)

Already-aligned items (xhigh effort, CLAUDE_CODE_SESSION_ID, worktree.bgIsolation)
are covered by their own prior changelog tests and are not re-asserted here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import validate_hook_output as hook_output  # noqa: E402
from cc_scope_rules import KNOWN_SETTINGS_KEYS, MANAGED_ONLY_KEYS  # noqa: E402
from cpv_validation_common import (  # noqa: E402
    SKILL_FRONTMATTER_FIELDS,
    VALID_HOOK_EVENTS,
    VALID_TOOLS,
    ValidationReport,
)
from validate_command import KNOWN_FRONTMATTER_FIELDS as COMMAND_FRONTMATTER_FIELDS  # noqa: E402
from validate_marketplace import (  # noqa: E402
    _KNOWN_SOURCE_FIELDS_BY_TYPE,
    OPTIONAL_PLUGIN_FIELDS,
)
from validate_plugin import validate_manifest  # noqa: E402


class TestMessageDisplayEvent:
    """v2.1.152 — MessageDisplay hook event + displayContent output."""

    def test_event_is_recognised(self) -> None:
        """``MessageDisplay`` must be a valid hook event so authors using it
        do not trip a false-positive 'unknown hook event' finding."""
        assert "MessageDisplay" in VALID_HOOK_EVENTS

    def test_display_content_allowed(self) -> None:
        """``displayContent`` is the documented hookSpecificOutput field."""
        assert "displayContent" in hook_output.HOOK_OUTPUT_EVENT_FIELDS["MessageDisplay"]

    def test_valid_payload_passes(self) -> None:
        """A well-formed MessageDisplay payload validates clean."""
        report = hook_output.validate_output_payload(
            "MessageDisplay",
            {"hookSpecificOutput": {"hookEventName": "MessageDisplay", "displayContent": "shown text"}},
        )
        assert not (report.has_critical or report.has_major)

    def test_non_string_display_content_is_major(self) -> None:
        """displayContent must be a string — a non-string value is MAJOR."""
        report = hook_output.validate_output_payload(
            "MessageDisplay",
            {"hookSpecificOutput": {"hookEventName": "MessageDisplay", "displayContent": 123}},
        )
        assert report.has_major


class TestSessionStartNewOutputFields:
    """v2.1.152 — SessionStart reloadSkills (bool) + sessionTitle (str)."""

    def test_fields_allowed(self) -> None:
        """Both new fields are accepted on SessionStart hookSpecificOutput."""
        allowed = hook_output.HOOK_OUTPUT_EVENT_FIELDS["SessionStart"]
        assert {"reloadSkills", "sessionTitle"} <= allowed

    def test_valid_payload_passes(self) -> None:
        """reloadSkills:true + sessionTitle:str validates clean."""
        report = hook_output.validate_output_payload(
            "SessionStart",
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "reloadSkills": True,
                    "sessionTitle": "my session",
                }
            },
        )
        assert not (report.has_critical or report.has_major)

    def test_non_bool_reload_skills_is_major(self) -> None:
        """reloadSkills must be a boolean — a string value is MAJOR."""
        report = hook_output.validate_output_payload(
            "SessionStart",
            {"hookSpecificOutput": {"hookEventName": "SessionStart", "reloadSkills": "yes"}},
        )
        assert report.has_major

    def test_non_string_session_title_is_major(self) -> None:
        """sessionTitle must be a string — a non-string value is MAJOR."""
        report = hook_output.validate_output_payload(
            "SessionStart",
            {"hookSpecificOutput": {"hookEventName": "SessionStart", "sessionTitle": 42}},
        )
        assert report.has_major


class TestDisallowedToolsFrontmatter:
    """v2.1.152 — disallowed-tools frontmatter for skills and slash commands."""

    def test_skill_field_recognised(self) -> None:
        """``disallowed-tools`` is a valid skill frontmatter field."""
        assert "disallowed-tools" in SKILL_FRONTMATTER_FIELDS

    def test_command_field_recognised(self) -> None:
        """``disallowed-tools`` is a valid slash-command frontmatter field."""
        assert "disallowed-tools" in COMMAND_FRONTMATTER_FIELDS


class TestManagedSettingsKeys:
    """v2.1.149 / v2.1.152 — two new managed-only settings keys."""

    def test_allow_all_claude_ai_mcps_managed(self) -> None:
        """``allowAllClaudeAiMcps`` is managed-only AND known (typo aid)."""
        assert "allowAllClaudeAiMcps" in MANAGED_ONLY_KEYS
        assert "allowAllClaudeAiMcps" in KNOWN_SETTINGS_KEYS

    def test_plugin_suggestion_marketplaces_managed(self) -> None:
        """``pluginSuggestionMarketplaces`` is managed-only AND known."""
        assert "pluginSuggestionMarketplaces" in MANAGED_ONLY_KEYS
        assert "pluginSuggestionMarketplaces" in KNOWN_SETTINGS_KEYS

    def test_unrelated_key_not_managed(self) -> None:
        """Sanity: a made-up key is neither managed nor known, so the positive
        assertions above are not vacuous."""
        assert "thisKeyDoesNotExist2026" not in MANAGED_ONLY_KEYS
        assert "thisKeyDoesNotExist2026" not in KNOWN_SETTINGS_KEYS


class TestMarketplaceSkipLfs:
    """v2.1.153 — skipLfs source field, github/git only."""

    def test_skip_lfs_on_github_and_git(self) -> None:
        """``skipLfs`` is accepted on github and git marketplace sources."""
        assert "skipLfs" in _KNOWN_SOURCE_FIELDS_BY_TYPE["github"]
        assert "skipLfs" in _KNOWN_SOURCE_FIELDS_BY_TYPE["git"]

    def test_skip_lfs_not_on_url_or_npm(self) -> None:
        """skipLfs is github/git-only per the changelog — not url/npm sources."""
        assert "skipLfs" not in _KNOWN_SOURCE_FIELDS_BY_TYPE["url"]
        assert "skipLfs" not in _KNOWN_SOURCE_FIELDS_BY_TYPE["npm"]


class TestDefaultEnabled:
    """v2.1.154 — defaultEnabled in plugin.json and marketplace entries."""

    def test_marketplace_entry_field_recognised(self) -> None:
        """``defaultEnabled`` is a valid marketplace plugin-entry field."""
        assert "defaultEnabled" in OPTIONAL_PLUGIN_FIELDS

    @staticmethod
    def _write_manifest(plugin_root: Path, default_enabled: object) -> None:
        manifest_dir = plugin_root / ".claude-plugin"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "demo-plugin",
                    "version": "1.0.0",
                    "description": "A demo plugin for the defaultEnabled type test.",
                    "defaultEnabled": default_enabled,
                }
            ),
            encoding="utf-8",
        )

    def test_bool_default_enabled_passes(self, tmp_path: Path) -> None:
        """plugin.json with ``defaultEnabled: false`` (a bool) raises no MAJOR
        for that field."""
        self._write_manifest(tmp_path, False)
        report = ValidationReport()
        validate_manifest(tmp_path, report)
        assert not any("defaultEnabled" in r.message for r in report.results if r.level == "MAJOR")

    def test_non_bool_default_enabled_is_major(self, tmp_path: Path) -> None:
        """A non-boolean ``defaultEnabled`` must produce a MAJOR finding."""
        self._write_manifest(tmp_path, "yes")
        report = ValidationReport()
        validate_manifest(tmp_path, report)
        assert any("defaultEnabled" in r.message for r in report.results if r.level == "MAJOR")


class TestWorkflowTool:
    """v2.1.154 — Workflow tool."""

    def test_workflow_recognised(self) -> None:
        """``Workflow`` is a valid tool name (dynamic-workflows orchestration)."""
        assert "Workflow" in VALID_TOOLS

    def test_bogus_tool_not_recognised(self) -> None:
        """Sanity: a made-up tool name is not in VALID_TOOLS, so the positive
        assertion above is not vacuous."""
        assert "WorkflowFooBar2026" not in VALID_TOOLS
