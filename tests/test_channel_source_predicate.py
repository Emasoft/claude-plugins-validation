"""TRDD-26446eed — Channel MCP server source-code prefilter tests.

The prefilter is a deterministic helper used by the semantic-validator
agent. It does NOT replace the LLM (Opus reads the source and renders
the actual security verdict). What it DOES do:

1. Detect whether the pillar is in scope (does plugin.json declare a
   non-empty ``channels`` array AND ship local MCP server source?).
2. Resolve each channel server's entry-point source file from the
   ``mcpServers.<server>.command`` / ``args`` declaration.
3. Identify candidate lines that forward inbound payloads to Claude
   (``mcp.notification('notifications/claude/channel', ...)`` /
   ``send_notification('notifications/claude/channel', ...)``) — these
   are the lines the LLM must audit.
4. Spot the obviously-safe shape (sender-ID allowlist Set + ``has()``
   compare) and the obviously-unsafe shape (chat-ID-only gating, no
   gating at all, ``claude/channel/permission`` capability without an
   accompanying permission handler that gates).

Every test in this module operates on a fixture under
``tests/fixtures/channel_source/`` — no LLM calls, no network, fully
deterministic. The fixtures double as the gold-standard inputs the
semantic-validator agent will be asked to grade in a real run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# conftest.py adds scripts/ to sys.path
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_channel_source_predicate import (  # noqa: E402
    ChannelSourceFinding,
    PrefilterVerdict,
    classify_channel_source,
    find_channel_forward_calls,
    find_chat_id_only_gating,
    find_permission_capability_declaration,
    find_sender_gating_patterns,
    plugin_declares_channels,
    resolve_channel_server_sources,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "channel_source"


# ---------------------------------------------------------------------------
# 1. Pillar scope — plugin.json gating
# ---------------------------------------------------------------------------


class TestPillarScopeDetection:
    """Pillar runs ONLY when plugin.json declares channels AND ships source."""

    def test_plugin_with_non_empty_channels_array_is_in_scope(self):
        """A plugin declaring channels must trigger the pillar."""
        plugin_dir = FIXTURE_ROOT / "ungated_telegram_ts"
        assert plugin_declares_channels(plugin_dir) is True

    def test_plugin_without_channels_array_is_out_of_scope(self):
        """No channels => pillar must be skipped — saves opus tokens."""
        plugin_dir = FIXTURE_ROOT / "plugin_no_channels"
        assert plugin_declares_channels(plugin_dir) is False

    def test_missing_plugin_json_is_out_of_scope(self):
        """Defensive — broken plugins must not crash the predicate."""
        plugin_dir = FIXTURE_ROOT  # no plugin.json at this level
        assert plugin_declares_channels(plugin_dir) is False


# ---------------------------------------------------------------------------
# 2. Source resolution — mcpServers.<server> -> entry-point file
# ---------------------------------------------------------------------------


class TestServerSourceResolution:
    """Resolve `args[0]` to a real file under the plugin tree."""

    def test_resolves_typescript_server_path_from_args(self):
        """Plugin.json points at .ts source — resolver returns that file."""
        plugin_dir = FIXTURE_ROOT / "gated_telegram_ts"
        sources = resolve_channel_server_sources(plugin_dir)
        assert len(sources) == 1
        path = sources[0]
        assert path.name == "server.ts"
        assert path.exists()

    def test_resolves_python_server_path_from_args(self):
        """Python servers resolve the same way."""
        plugin_dir = FIXTURE_ROOT / "gated_telegram_py"
        sources = resolve_channel_server_sources(plugin_dir)
        assert len(sources) == 1
        path = sources[0]
        assert path.name == "server.py"
        assert path.exists()

    def test_resolves_dist_path_to_src_when_dist_missing(self):
        """If ``args[0]`` points at dist/foo.js but only src/foo.ts exists,
        the resolver MUST return src/foo.ts — minified bundles are not
        useful for manual gating analysis."""
        plugin_dir = FIXTURE_ROOT / "ungated_telegram_ts"
        sources = resolve_channel_server_sources(plugin_dir)
        assert len(sources) == 1
        # plugin.json points at servers/telegram/dist/server.js (does not exist)
        # the resolver should fall back to src/server.ts
        path = sources[0]
        assert path.suffix == ".ts"
        assert path.name == "server.ts"
        assert path.exists()


# ---------------------------------------------------------------------------
# 3. Forward-call detection
# ---------------------------------------------------------------------------


class TestForwardCallDetection:
    """Locate the lines that forward inbound messages to Claude."""

    def test_typescript_notification_call_detected(self):
        """`mcp.notification('notifications/claude/channel', ...)` matches."""
        src = (FIXTURE_ROOT / "ungated_telegram_ts/servers/telegram/src/server.ts").read_text()
        calls = find_channel_forward_calls(src, language="typescript")
        assert len(calls) >= 1
        # Returned lines are 1-indexed line numbers
        assert all(line >= 1 for line in calls)

    def test_python_send_notification_call_detected(self):
        """`mcp.send_notification('notifications/claude/channel', ...)` matches."""
        src = (FIXTURE_ROOT / "ungated_telegram_py/servers/telegram/server.py").read_text()
        calls = find_channel_forward_calls(src, language="python")
        assert len(calls) >= 1

    def test_no_channel_calls_in_unrelated_source(self):
        """Source with no channel forward call returns empty list."""
        src = "console.log('hello world');\nexport function foo(){return 1;}\n"
        calls = find_channel_forward_calls(src, language="typescript")
        assert calls == []


# ---------------------------------------------------------------------------
# 4. Sender-gating detection
# ---------------------------------------------------------------------------


class TestSenderGatingDetection:
    """Detect the acceptable sender-ID allowlist patterns from rule 1."""

    def test_typescript_allowlist_set_with_has_check_passes(self):
        """`ALLOWED_USER_IDS.has(msg.from.id)` is the canonical safe shape."""
        src = (FIXTURE_ROOT / "gated_telegram_ts/servers/telegram/src/server.ts").read_text()
        gating = find_sender_gating_patterns(src, language="typescript")
        assert len(gating) >= 1, f"Expected sender-ID gating in safe fixture; got {gating}"

    def test_python_membership_check_against_allowlist_passes(self):
        """`message.from_user.id not in ALLOWED_USER_IDS` is the safe shape."""
        src = (FIXTURE_ROOT / "gated_telegram_py/servers/telegram/server.py").read_text()
        gating = find_sender_gating_patterns(src, language="python")
        assert len(gating) >= 1

    def test_ungated_typescript_returns_no_gating(self):
        """Source with zero gating returns empty list."""
        src = (FIXTURE_ROOT / "ungated_telegram_ts/servers/telegram/src/server.ts").read_text()
        gating = find_sender_gating_patterns(src, language="typescript")
        assert gating == [], f"Expected no gating in ungated fixture; got {gating}"

    def test_ungated_python_returns_no_gating(self):
        """Same for Python."""
        src = (FIXTURE_ROOT / "ungated_telegram_py/servers/telegram/server.py").read_text()
        gating = find_sender_gating_patterns(src, language="python")
        assert gating == []


# ---------------------------------------------------------------------------
# 5. Chat-ID-only gating detection (rule 3 — MAJOR)
# ---------------------------------------------------------------------------


class TestChatIdOnlyGating:
    """Detect the MAJOR pattern: gating on chat/room ID instead of sender."""

    def test_chat_id_only_typescript_is_flagged(self):
        """Chat-ID compare without an accompanying sender-ID compare matches."""
        src = (FIXTURE_ROOT / "chat_id_only_gated_ts/servers/telegram/src/server.ts").read_text()
        chat_only = find_chat_id_only_gating(src, language="typescript")
        assert len(chat_only) >= 1

    def test_compound_gating_does_not_fire_chat_id_only(self):
        """If sender-ID is also checked, chat-ID-only must NOT match."""
        src = (FIXTURE_ROOT / "gated_telegram_ts/servers/telegram/src/server.ts").read_text()
        # Source above has no chat.id check at all, so result must be empty.
        chat_only = find_chat_id_only_gating(src, language="typescript")
        assert chat_only == []


# ---------------------------------------------------------------------------
# 6. Permission-capability detection (rule 2 — CRITICAL)
# ---------------------------------------------------------------------------


class TestPermissionCapabilityDetection:
    """Detect declaration of `claude/channel/permission` capability."""

    def test_capability_declaration_typescript_is_detected(self):
        """`'claude/channel/permission': {}` inside `experimental` matches."""
        src = (FIXTURE_ROOT / "permission_capability_ungated_ts/servers/telegram/src/server.ts").read_text()
        decl = find_permission_capability_declaration(src)
        assert decl is True

    def test_capability_absent_when_no_permission_declared(self):
        """Plain channel servers do NOT declare the permission capability."""
        src = (FIXTURE_ROOT / "ungated_telegram_ts/servers/telegram/src/server.ts").read_text()
        decl = find_permission_capability_declaration(src)
        assert decl is False


# ---------------------------------------------------------------------------
# 7. Whole-fixture classification (the 5 TRDD test cases)
# ---------------------------------------------------------------------------


class TestFullClassification:
    """End-to-end fixture classification — the 5 TRDD test cases."""

    def test_channel_source_with_sender_gating_passes(self):
        """Rule 1 satisfied; classification is PASSED."""
        plugin_dir = FIXTURE_ROOT / "gated_telegram_ts"
        verdict = classify_channel_source(plugin_dir)
        assert isinstance(verdict, PrefilterVerdict)
        assert verdict.in_scope is True
        # Safe fixture — at least one PASSED finding for sender gating
        passed = [f for f in verdict.findings if f.severity == "PASSED"]
        assert passed, f"Expected at least one PASSED in {verdict.findings}"
        criticals = [f for f in verdict.findings if f.severity == "CRITICAL"]
        assert criticals == [], f"Unexpected CRITICALs in safe fixture: {criticals}"

    def test_channel_source_without_gating_fires_semantic_major(self):
        """Rule 1 violated => CRITICAL prefilter (MAJOR if naïve gating)."""
        plugin_dir = FIXTURE_ROOT / "ungated_telegram_ts"
        verdict = classify_channel_source(plugin_dir)
        assert verdict.in_scope is True
        # No gating at all => the prefilter must surface a MAJOR-or-CRITICAL
        # candidate to the LLM.
        critical_or_major = [
            f for f in verdict.findings if f.severity in ("CRITICAL", "MAJOR") and f.rule == "RULE-1-no-sender-gating"
        ]
        assert critical_or_major, f"Expected RULE-1 finding in ungated fixture; got {verdict.findings}"

    def test_channel_source_with_chat_id_only_gating_fires_semantic_major(self):
        """Rule 3 violated => MAJOR finding."""
        plugin_dir = FIXTURE_ROOT / "chat_id_only_gated_ts"
        verdict = classify_channel_source(plugin_dir)
        assert verdict.in_scope is True
        majors = [f for f in verdict.findings if f.severity == "MAJOR" and f.rule == "RULE-3-chat-id-only-gating"]
        assert majors, f"Expected RULE-3 MAJOR in chat-id-only fixture; got {verdict.findings}"

    def test_permission_capability_without_gating_fires_semantic_critical(self):
        """Rule 2 violated => CRITICAL finding."""
        plugin_dir = FIXTURE_ROOT / "permission_capability_ungated_ts"
        verdict = classify_channel_source(plugin_dir)
        assert verdict.in_scope is True
        crits = [
            f for f in verdict.findings if f.severity == "CRITICAL" and f.rule == "RULE-2-permission-capability-ungated"
        ]
        assert crits, f"Expected RULE-2 CRITICAL in permission-capability fixture; got {verdict.findings}"

    def test_plugin_without_channels_skips_semantic_source_check(self):
        """No channels => verdict.in_scope=False, zero findings."""
        plugin_dir = FIXTURE_ROOT / "plugin_no_channels"
        verdict = classify_channel_source(plugin_dir)
        assert verdict.in_scope is False
        assert verdict.findings == ()


# ---------------------------------------------------------------------------
# 8. Defensive — the predicate must never crash on malformed input
# ---------------------------------------------------------------------------


class TestPredicateDefensiveness:
    """The predicate must never raise on malformed input — only return data."""

    def test_classify_returns_out_of_scope_when_plugin_json_missing(self, tmp_path):
        """Empty dir => out-of-scope, no exception."""
        verdict = classify_channel_source(tmp_path)
        assert verdict.in_scope is False
        assert verdict.findings == ()

    def test_classify_returns_out_of_scope_when_plugin_json_malformed(self, tmp_path):
        """Broken JSON => out-of-scope, no exception."""
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin/plugin.json").write_text("{ not json")
        verdict = classify_channel_source(tmp_path)
        assert verdict.in_scope is False
        assert verdict.findings == ()

    def test_resolve_handles_missing_args(self, tmp_path):
        """mcpServers.<server> with no args returns empty list."""
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin/plugin.json").write_text(
            '{"name": "x", "version": "1.0.0", "description": "y", '
            '"mcpServers": {"foo": {"command": "node"}}, '
            '"channels": [{"server": "foo"}]}',
        )
        sources = resolve_channel_server_sources(tmp_path)
        assert sources == []

    def test_finding_dataclass_round_trip(self):
        """ChannelSourceFinding is a regular dataclass — positional init works."""
        f = ChannelSourceFinding(
            severity="CRITICAL",
            rule="RULE-1-no-sender-gating",
            file="servers/telegram/src/server.ts",
            line=12,
            message="No sender allowlist before forward call.",
        )
        assert f.severity == "CRITICAL"
        assert f.rule == "RULE-1-no-sender-gating"
        assert f.line == 12


# ---------------------------------------------------------------------------
# 9. Reference-file integrity — the doc the agent reads must exist
# ---------------------------------------------------------------------------


class TestReferenceFilePresence:
    """The semantic-validator's reference doc MUST be on disk and wired in."""

    @pytest.fixture
    def repo_root(self) -> Path:
        return Path(__file__).parent.parent

    def test_channel_source_reference_file_exists(self, repo_root):
        """Reference file must exist — the agent loads it conditionally."""
        ref = repo_root / "skills/semantic-validation-skill/references/channel-source-security.md"
        assert ref.exists(), f"Missing reference doc: {ref}"

    def test_skill_md_loads_reference_file(self, repo_root):
        """SKILL.md must Load the reference under a ``Conditional Pillar`` heading."""
        skill_md = (repo_root / "skills/semantic-validation-skill/SKILL.md").read_text()
        assert "channel-source-security" in skill_md
        assert "Conditional Pillar" in skill_md

    def test_agent_md_documents_pillar(self, repo_root):
        """The agent's instructions must mention the conditional pillar."""
        agent_md = (repo_root / "agents/semantic-validator.md").read_text()
        assert "Channel MCP Server Source-Code Security" in agent_md
        assert "channels" in agent_md
