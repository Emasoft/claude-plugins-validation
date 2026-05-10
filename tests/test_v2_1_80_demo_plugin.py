#!/usr/bin/env python3
"""Tests for the v2.1.80+ demo plugin fixture.

Exercises the demo plugin in tests/fixtures/v2_1_80_plugin/ against
the validators that touch v2.1.80+ features:

- userConfig structure (5-type whitelist, required title+type)
- channels (cross-reference to mcpServers)
- Monitor tool acceptance in agent frontmatter
- CLAUDE_PLUGIN_OPTION_<KEY> env var auto-acceptance
- Plugin skill name field (v2.1.98) cross-check with directory name

The fixture is a CI guardrail: a regression that breaks any of the
above features will surface here as a CRITICAL or MAJOR finding before
it ships.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_plugin import (  # noqa: E402
    validate_channels_structure,
    validate_user_config_structure,
)

FIXTURE = Path(__file__).parent / "fixtures" / "v2_1_80_plugin"


def _load_manifest() -> dict:
    """Load the v2.1.80+ demo plugin's plugin.json."""
    manifest_path = FIXTURE / ".claude-plugin" / "plugin.json"
    return json.loads(manifest_path.read_text(encoding="utf-8"))


class TestV2_1_80DemoPluginManifest:
    """Verify the v2.1.80+ demo plugin manifest is well-formed."""

    def test_manifest_loads_as_json(self):
        """plugin.json parses cleanly as JSON."""
        manifest = _load_manifest()
        assert isinstance(manifest, dict)

    def test_manifest_declares_user_config(self):
        """plugin.json declares the userConfig root."""
        manifest = _load_manifest()
        assert "userConfig" in manifest
        assert isinstance(manifest["userConfig"], dict)

    def test_user_config_uses_all_five_types(self):
        """userConfig demonstrates every type in the v2.1.121 whitelist."""
        manifest = _load_manifest()
        types = {entry["type"] for entry in manifest["userConfig"].values()}
        assert types == {"string", "number", "boolean", "directory", "file"}

    def test_every_user_config_entry_has_required_fields(self):
        """Every userConfig entry has title and type (Zod-required)."""
        manifest = _load_manifest()
        for key, entry in manifest["userConfig"].items():
            assert "title" in entry, f"userConfig.{key} missing required 'title'"
            assert "type" in entry, f"userConfig.{key} missing required 'type'"

    def test_sensitive_user_config_entry_present(self):
        """At least one userConfig entry sets sensitive=true."""
        manifest = _load_manifest()
        sensitive_keys = [k for k, v in manifest["userConfig"].items() if v.get("sensitive") is True]
        assert sensitive_keys, "No sensitive userConfig entries — demo should show one"

    def test_manifest_declares_channels(self):
        """plugin.json declares channels."""
        manifest = _load_manifest()
        assert "channels" in manifest
        assert isinstance(manifest["channels"], list)
        assert len(manifest["channels"]) >= 1

    def test_channels_cross_reference_mcp_servers(self):
        """Every channels[].server matches a key in mcpServers."""
        manifest = _load_manifest()
        servers = set(manifest.get("mcpServers", {}).keys())
        for ch in manifest["channels"]:
            assert ch["server"] in servers, f"channels[].server={ch['server']!r} has no matching mcpServers entry"


class TestV2_1_80DemoPluginValidators:
    """Run CPV validators against the demo plugin and check no errors fire."""

    def test_validate_user_config_emits_no_findings(self):
        """validate_user_config_structure accepts the demo userConfig."""
        manifest = _load_manifest()
        report = ValidationReport()
        validate_user_config_structure(manifest, report)
        # No critical / major findings on a well-formed userConfig.
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert not criticals, f"userConfig validator emitted CRITICAL: {criticals}"
        assert not majors, f"userConfig validator emitted MAJOR: {majors}"

    def test_validate_channels_emits_no_findings(self):
        """validate_channels_structure accepts the demo channels array."""
        manifest = _load_manifest()
        report = ValidationReport()
        validate_channels_structure(manifest, FIXTURE, report)
        criticals = [r for r in report.results if r.level == "CRITICAL"]
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert not criticals, f"channels validator emitted CRITICAL: {criticals}"
        assert not majors, f"channels validator emitted MAJOR: {majors}"


class TestV2_1_80DemoPluginAgent:
    """Verify the log-watcher agent declares the Monitor tool."""

    def test_agent_file_exists(self):
        """The log-watcher agent file is present."""
        agent = FIXTURE / "agents" / "log-watcher.md"
        assert agent.exists(), f"agent file missing: {agent}"

    def test_agent_declares_monitor_tool(self):
        """log-watcher.md frontmatter lists Monitor in tools."""
        agent = FIXTURE / "agents" / "log-watcher.md"
        body = agent.read_text(encoding="utf-8")
        assert "- Monitor" in body, "log-watcher does not declare Monitor in tools"


class TestV2_1_80DemoPluginSkill:
    """Verify the demo skill uses the v2.1.98 name field correctly."""

    def test_skill_directory_matches_name_field(self):
        """The skill directory name matches the SKILL.md frontmatter name."""
        skill_dir = FIXTURE / "skills" / "v2-1-80-demo-skill"
        skill_md = skill_dir / "SKILL.md"
        assert skill_md.exists(), f"SKILL.md missing: {skill_md}"
        body = skill_md.read_text(encoding="utf-8")
        # Frontmatter name MUST match the directory basename per CPV's
        # skill-frontmatter-name rule.
        assert "name: v2-1-80-demo-skill" in body
        assert skill_dir.name == "v2-1-80-demo-skill"

    def test_skill_uses_claude_plugin_option_env_var(self):
        """SKILL.md body references CLAUDE_PLUGIN_OPTION_<KEY>."""
        skill_md = FIXTURE / "skills" / "v2-1-80-demo-skill" / "SKILL.md"
        body = skill_md.read_text(encoding="utf-8")
        assert "${CLAUDE_PLUGIN_OPTION_WORKSPACE_DIR}" in body, (
            "SKILL.md does not demonstrate CLAUDE_PLUGIN_OPTION_<KEY> env var usage"
        )
