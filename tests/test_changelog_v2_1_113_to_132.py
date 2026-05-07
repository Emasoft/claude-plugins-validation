#!/usr/bin/env python3
"""Regression tests for Claude Code v2.1.113 → v2.1.132 changelog updates.

Covers four CPV behavioural changes added when the upstream changelog
introduced new spec surface:

1. New env vars accepted in `VALID_PLUGIN_ENV_VARS`:
   - `CLAUDE_CODE_SESSION_ID` (v2.1.132)
   - `CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN` (v2.1.132)
   - `CLAUDE_CODE_FORCE_SYNC_OUTPUT` (v2.1.129)
   - `CLAUDE_CODE_PACKAGE_MANAGER_AUTO_UPDATE` (v2.1.129)
   - `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY` (v2.1.129)
2. plugin.json `experimental` wrapper (v2.1.129) accepted at top level; legacy
   top-level `themes` / `monitors` emits a NIT recommending the new shape.
3. `.mcp.json` server name `workspace` (v2.1.128) — reserved by CC, server is
   silently skipped at load. CPV emits MAJOR so authors rename before publish.
4. PostToolUse / PostToolUseFailure hook field constants (v2.1.119 +
   v2.1.121) — `duration_ms` (input) and `updatedToolOutput` (output).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (  # noqa: E402
    VALID_PLUGIN_ENV_VARS,
    ValidationReport,
    is_valid_plugin_env_var,
)
from validate_hook import (  # noqa: E402
    POSTTOOLUSE_HOOK_INPUT_FIELDS,
    POSTTOOLUSE_HOOK_SPECIFIC_OUTPUT_FIELDS,
)
from validate_mcp import (  # noqa: E402
    RESERVED_MCP_SERVER_NAMES,
    validate_mcp_server,
)
from validate_plugin import validate_manifest  # noqa: E402

# ---------------------------------------------------------------------------
# 1. env var allowlist
# ---------------------------------------------------------------------------


class TestNewEnvVars:
    """v2.1.129 + v2.1.132 — env vars added to the recognised allowlist."""

    def test_v2_1_132_env_vars_accepted(self):
        """v2.1.132 env vars should be in VALID_PLUGIN_ENV_VARS."""
        assert "CLAUDE_CODE_SESSION_ID" in VALID_PLUGIN_ENV_VARS
        assert "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN" in VALID_PLUGIN_ENV_VARS

    def test_v2_1_129_env_vars_accepted(self):
        """v2.1.129 env vars should be in VALID_PLUGIN_ENV_VARS."""
        assert "CLAUDE_CODE_FORCE_SYNC_OUTPUT" in VALID_PLUGIN_ENV_VARS
        assert "CLAUDE_CODE_PACKAGE_MANAGER_AUTO_UPDATE" in VALID_PLUGIN_ENV_VARS
        assert "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY" in VALID_PLUGIN_ENV_VARS

    def test_is_valid_plugin_env_var_recognises_new_names(self):
        """is_valid_plugin_env_var() should return True for every new name."""
        for name in (
            "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN",
            "CLAUDE_CODE_FORCE_SYNC_OUTPUT",
            "CLAUDE_CODE_PACKAGE_MANAGER_AUTO_UPDATE",
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
        ):
            assert is_valid_plugin_env_var(name), f"{name} not recognised"

    def test_unknown_env_var_still_rejected(self):
        """Negative case: a fabricated name still returns False."""
        assert not is_valid_plugin_env_var("CLAUDE_CODE_TOTALLY_MADE_UP_2026")


# ---------------------------------------------------------------------------
# 2. experimental wrapper + legacy top-level themes/monitors NIT
# ---------------------------------------------------------------------------


def _write_manifest(tmp_path: Path, manifest: dict) -> Path:
    plugin_root = tmp_path / "p"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    manifest_path = plugin_root / ".claude-plugin" / "plugin.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return plugin_root


def _validate(plugin_root: Path) -> ValidationReport:
    report = ValidationReport()
    validate_manifest(plugin_root, report)
    return report


def _has_message(report: ValidationReport, severity: str, fragment: str) -> bool:
    """Return True when any result at `severity` level contains `fragment`."""
    for result in report.results:
        if result.level != severity:
            continue
        if fragment in result.message:
            return True
    return False


class TestExperimentalWrapper:
    """v2.1.129 — `experimental: { themes, monitors }` wrapper."""

    def test_experimental_field_accepted(self, tmp_path):
        """`experimental` at top level should NOT trigger an unknown-field warning."""
        plugin_root = _write_manifest(
            tmp_path,
            {
                "name": "demo",
                "version": "0.1.0",
                "description": "x",
                "experimental": {"themes": ["dark.json"], "monitors": []},
            },
        )
        report = _validate(plugin_root)
        assert not _has_message(report, "WARNING", "Unknown manifest field 'experimental'")

    def test_top_level_themes_emits_nit(self, tmp_path):
        """Top-level `themes` should emit a NIT recommending the experimental wrapper."""
        plugin_root = _write_manifest(
            tmp_path,
            {
                "name": "demo",
                "version": "0.1.0",
                "description": "x",
                "themes": ["dark.json"],
            },
        )
        report = _validate(plugin_root)
        assert _has_message(report, "NIT", "should be nested under 'experimental")

    def test_top_level_monitors_emits_nit(self, tmp_path):
        """Top-level `monitors` should emit a NIT recommending the experimental wrapper."""
        plugin_root = _write_manifest(
            tmp_path,
            {
                "name": "demo",
                "version": "0.1.0",
                "description": "x",
                "monitors": [],
            },
        )
        report = _validate(plugin_root)
        assert _has_message(report, "NIT", "should be nested under 'experimental")

    def test_experimental_with_nested_does_not_double_warn(self, tmp_path):
        """When the field is already nested under experimental, no NIT for the same key."""
        plugin_root = _write_manifest(
            tmp_path,
            {
                "name": "demo",
                "version": "0.1.0",
                "description": "x",
                "experimental": {"themes": ["dark.json"]},
                # absent at top level — should not trigger NIT
            },
        )
        report = _validate(plugin_root)
        assert not _has_message(report, "NIT", "themes")

    def test_experimental_must_be_object(self, tmp_path):
        """`experimental` as a non-object should emit MAJOR."""
        plugin_root = _write_manifest(
            tmp_path,
            {
                "name": "demo",
                "version": "0.1.0",
                "description": "x",
                "experimental": ["not", "an", "object"],
            },
        )
        report = _validate(plugin_root)
        assert _has_message(report, "MAJOR", "'experimental' must be an object")

    def test_unknown_experimental_key_warns(self, tmp_path):
        """Unrecognised key inside `experimental` should emit a WARNING."""
        plugin_root = _write_manifest(
            tmp_path,
            {
                "name": "demo",
                "version": "0.1.0",
                "description": "x",
                "experimental": {"frobnicators": []},
            },
        )
        report = _validate(plugin_root)
        assert _has_message(report, "WARNING", "experimental.frobnicators")


# ---------------------------------------------------------------------------
# 3. workspace MCP name reserved
# ---------------------------------------------------------------------------


class TestReservedMcpServerName:
    """v2.1.128 — `workspace` is now reserved by Claude Code."""

    def test_workspace_in_reserved_set(self):
        assert "workspace" in RESERVED_MCP_SERVER_NAMES

    def test_workspace_server_emits_major(self):
        """A server named `workspace` should emit a MAJOR finding."""
        report = ValidationReport()
        validate_mcp_server(
            "workspace",
            {"command": "/bin/echo", "args": []},
            report,
            plugin_root=None,
            file_context="mcp-config",
        )
        assert _has_message(report, "MAJOR", "is reserved by Claude Code")

    def test_non_reserved_name_does_not_emit_reserved_warning(self):
        """A normal MCP server name should NOT emit the reserved-name finding."""
        report = ValidationReport()
        validate_mcp_server(
            "my-server",
            {"command": "/bin/echo", "args": []},
            report,
            plugin_root=None,
            file_context="mcp-config",
        )
        assert not _has_message(report, "MAJOR", "is reserved by Claude Code")


# ---------------------------------------------------------------------------
# 4. PostToolUse hook field constants
# ---------------------------------------------------------------------------


class TestPostToolUseHookFields:
    """v2.1.119 (duration_ms) + v2.1.121 (updatedToolOutput) constants."""

    def test_updated_tool_output_in_specific_output_fields(self):
        """v2.1.121 — PostToolUse hookSpecificOutput now has updatedToolOutput."""
        assert "updatedToolOutput" in POSTTOOLUSE_HOOK_SPECIFIC_OUTPUT_FIELDS

    def test_event_name_in_specific_output_fields(self):
        """hookEventName is required on the output object."""
        assert "hookEventName" in POSTTOOLUSE_HOOK_SPECIFIC_OUTPUT_FIELDS

    def test_duration_ms_in_input_fields(self):
        """v2.1.119 — PostToolUse stdin payload now carries duration_ms."""
        assert "duration_ms" in POSTTOOLUSE_HOOK_INPUT_FIELDS

    def test_input_field_set_includes_canonical_keys(self):
        """Sanity check that the input field set matches the documented stdin shape."""
        for required in ("hook_event_name", "tool_name", "tool_input", "session_id"):
            assert required in POSTTOOLUSE_HOOK_INPUT_FIELDS, f"missing {required}"
