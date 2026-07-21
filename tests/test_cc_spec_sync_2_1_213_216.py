#!/usr/bin/env python3
"""
CC spec-drift sync (Claude Code v2.1.213-216) — two-sided regression locks.

Allowlist-widening / FP-reduction adds carrying the spec forward from the
v2.1.205-212 sweep to v2.1.216. Every new value is verified two-sided: the
newly-accepted value is now accepted (or no longer flagged), AND a positive
control proves the same code path still rejects a bogus sibling.

S1 — EndConversation tool (v2.1.214)   VALID_TOOLS + CANONICAL_TOOLS
S2 — SessionStart source `fork`        (v2.1.212/.214) SESSION_START_SOURCES
S3 — new plugin env vars               (v2.1.208/.211/.212/.214) VALID_PLUGIN_ENV_VARS
S4 — new built-in slash commands       (v2.1.211/.212/.215) BUILTIN_SLASH_COMMANDS
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_tool_permission_match import CANONICAL_TOOLS
from cpv_validation_common import (
    BUILTIN_SLASH_COMMANDS,
    VALID_PLUGIN_ENV_VARS,
    VALID_TOOLS,
    ValidationReport,
    is_valid_plugin_env_var,
)
from validate_agent import AgentValidationReport, validate_tools_field
from validate_hook import validate_matcher


# ---------------------------------------------------------------------------
# S1 — EndConversation tool (v2.1.214)
# ---------------------------------------------------------------------------
class TestS1EndConversationTool:
    """`EndConversation` (tools-reference, min-version 2.1.213/.214) is a valid tool."""

    def test_end_conversation_in_valid_tools(self) -> None:
        """EndConversation must be in the strict validity allowlist VALID_TOOLS."""
        assert "EndConversation" in VALID_TOOLS

    def test_end_conversation_in_canonical_tools(self) -> None:
        """A new current tool is in the detection-breadth set CANONICAL_TOOLS too."""
        assert "EndConversation" in CANONICAL_TOOLS

    def test_agent_declaring_end_conversation_not_flagged_unknown(self) -> None:
        """An agent with tools:[EndConversation] draws NO 'Unknown tools' finding."""
        report = AgentValidationReport()
        validate_tools_field({"tools": ["EndConversation"]}, "agents/x.md", report)
        assert not any("Unknown tools" in r.message for r in report.results)

    def test_bogus_tool_still_flagged_unknown(self) -> None:
        """Positive control: a genuinely-unknown tool STILL draws 'Unknown tools'."""
        report = AgentValidationReport()
        validate_tools_field({"tools": ["TotallyBogusTool"]}, "agents/x.md", report)
        assert any("Unknown tools" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# S2 — SessionStart source `fork` (v2.1.212/.214)
# ---------------------------------------------------------------------------
class TestS2SessionStartForkSource:
    """A session begun via /fork reports SessionStart source `fork` (hooks.md)."""

    def test_fork_source_no_unknown_info(self) -> None:
        """`fork` as a SessionStart matcher must NOT draw the 'is not a known' INFO."""
        report = ValidationReport()
        validate_matcher("fork", "SessionStart", report)
        assert not any("is not a known" in r.message for r in report.results)

    def test_prior_source_still_accepted(self) -> None:
        """A pre-existing source (`resume`) must STILL be accepted (no regression)."""
        report = ValidationReport()
        validate_matcher("resume", "SessionStart", report)
        assert not any("is not a known" in r.message for r in report.results)

    def test_bogus_source_still_hinted(self) -> None:
        """Positive control: a genuinely-unknown source STILL draws the INFO hint."""
        report = ValidationReport()
        validate_matcher("not_a_real_source", "SessionStart", report)
        assert any("is not a known" in r.message for r in report.results if r.level == "INFO")


# ---------------------------------------------------------------------------
# S3 — new plugin env vars (v2.1.208 / .211 / .212 / .214)
# ---------------------------------------------------------------------------
_NEW_ENV_VARS = (
    "CLAUDE_CODE_PROCESS_WRAPPER",  # v2.1.208
    "CLAUDE_AX_SCREEN_READER",  # v2.1.181 (env-var form skipped by the prior sweep)
    "CLAUDE_CODE_FORWARD_SUBAGENT_TEXT",  # v2.1.211
    "CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION",  # v2.1.212
    "CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION",  # v2.1.212
    "CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS",  # v2.1.212
    "CLAUDE_CODE_OTEL_CONTENT_MAX_LENGTH",  # v2.1.214
    "OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT",  # v2.1.214
    "OTEL_LOGRECORD_ATTRIBUTE_VALUE_LENGTH_LIMIT",  # v2.1.214
    "OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT",  # v2.1.214
)


class TestS3NewPluginEnvVars:
    """The v2.1.208-214 env vars must no longer draw an 'unknown env var' FP."""

    def test_each_new_env_var_is_valid(self) -> None:
        """is_valid_plugin_env_var must accept every one of the new env vars."""
        for name in _NEW_ENV_VARS:
            assert is_valid_plugin_env_var(name) is True, f"{name} not accepted"

    def test_each_new_env_var_in_set(self) -> None:
        """Each new env var is present in the VALID_PLUGIN_ENV_VARS allowlist."""
        for name in _NEW_ENV_VARS:
            assert name in VALID_PLUGIN_ENV_VARS, f"{name} missing from set"

    def test_prior_env_var_still_valid(self) -> None:
        """A pre-existing env var (CLAUDE_PLUGIN_ROOT) is still accepted (no regression)."""
        assert is_valid_plugin_env_var("CLAUDE_PLUGIN_ROOT") is True

    def test_bogus_env_var_still_rejected(self) -> None:
        """Positive control: a genuinely-unknown CLAUDE_CODE_* var is STILL rejected."""
        assert is_valid_plugin_env_var("CLAUDE_CODE_TOTALLY_BOGUS_ZZZ") is False

    def test_dynamic_option_var_still_valid(self) -> None:
        """Positive control: the CLAUDE_PLUGIN_OPTION_<KEY> pattern still resolves."""
        assert is_valid_plugin_env_var("CLAUDE_PLUGIN_OPTION_MY_KEY") is True


# ---------------------------------------------------------------------------
# S4 — new built-in slash commands (v2.1.211 / .212 / .215)
# ---------------------------------------------------------------------------
_NEW_SLASH_COMMANDS = (
    "fork",  # v2.1.212
    "subtask",  # v2.1.212
    "background",  # v2.1.211/.212
    "btw",  # v2.1.212
    "usage-credits",  # v2.1.211
    "verify",  # v2.1.215
    "code-review",  # v2.1.215
)


class TestS4NewBuiltinSlashCommands:
    """The v2.1.211-215 built-in slash commands must be recognized for collision checks.

    `validate_command`'s collision check is exactly `stem in BUILTIN_SLASH_COMMANDS`,
    so membership IS the behavior under test.
    """

    def test_each_new_command_recognized(self) -> None:
        """Each new built-in command is in the set → a plugin colliding with it warns."""
        for name in _NEW_SLASH_COMMANDS:
            assert name in BUILTIN_SLASH_COMMANDS, f"{name} missing → collision missed"

    def test_prior_command_still_recognized(self) -> None:
        """A pre-existing built-in (`clear`) is still recognized (no regression)."""
        assert "clear" in BUILTIN_SLASH_COMMANDS

    def test_ordinary_plugin_command_name_not_a_builtin(self) -> None:
        """Positive control: an ordinary plugin command name is NOT flagged a collision."""
        assert "my-unique-plugin-cmd" not in BUILTIN_SLASH_COMMANDS
