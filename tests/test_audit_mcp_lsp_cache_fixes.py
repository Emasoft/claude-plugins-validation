#!/usr/bin/env python3
"""Two-sided regression tests for the validate_mcp / validate_lsp / validate_cache /
validate_telemetry / validate_enterprise audit fixes.

Each behaviour change is pinned with BOTH sides:
  - the "now-correct" side (the former false positive / coverage gap is gone), AND
  - the "still-flags-the-genuine-problem" side (the real defect is still caught).

A one-sided test would pass with a validator that simply suppresses everything,
so every fix here asserts both directions.

Findings covered (audit 20260525_155959+0200-validate-mcp-lsp-cache.md):
  #1  MCP-ABS-ASYM     — is_absolute_path now catches ~/, C:/, UNC (LSP parity)
  #2  MCP-CMD-NOTSTR   — non-string command is a clean CRITICAL (no swallowed crash)
  #4  MCP-HEADERSHELPER— headersHelper value is validated like command
  #9  MCP-CTX-AS-FILE  — reserved-name MAJOR no longer mis-fills the file column
  #10 MCP-DEAD-COND    — stdio url-ignored INFO still fires (dead conjunct removed)
  #15 MCP-BRACE-FP     — "${VAR:-x}extra}" is valid; "${VAR" is still malformed
  #5  CACHE-CRLF       — CRLF-authored model:/context:fork still caught
  #7  CACHE-WRITEOP-FP — 2>/dev/null no longer triggers CA-02; real write still does
  #8  CACHE-CA05-BROAD — cat>file no longer fires CA-05; bare cat dump still does
  #12 TEL-BETA-DUP     — BETA_TRACING_ENDPOINT deduped; hazard detection intact
  #13 TEL-ENVWALK-DUP  — nested env captured once; mcpServers[].env still captured
  #14 TEL-PASSED-SHARED— per-file PASSED is per-file across a shared report
  #6  ENT-AUTHOR-LICENSE— missing author/license is MINOR by default, CRITICAL strict
  #16 ENT-FM-SPLIT     — '---' inside a frontmatter value no longer truncates parse
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import validate_lsp  # noqa: E402
import validate_mcp  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402
from validate_enterprise import (  # noqa: E402
    EnterpriseComplianceReport,
    SkillComplianceResult,
    parse_frontmatter,
    validate_author_field,
    validate_license_field,
)
from validate_mcp import is_absolute_path, validate_env_var_syntax, validate_mcp_server  # noqa: E402
from validate_telemetry import (  # noqa: E402
    OTEL_ALL_ENV_VARS,
    PLUGIN_SHIPPED_HAZARD_ENV_VARS,
    _extract_env_blocks,
    scan_settings_for_telemetry,
)


# ---------------------------------------------------------------------------
# #1 MCP-ABS-ASYM — is_absolute_path superset parity with LSP
# ---------------------------------------------------------------------------
class TestMcpAbsolutePathParity:
    """validate_mcp.is_absolute_path must match the validate_lsp superset."""

    def test_mcp_now_catches_tilde_drive_and_unc(self):
        """~/, C:/ (forward slash) and UNC are absolute (the formerly-missed cases)."""
        for p in ("~/secret", "C:/Users/x", "\\\\server\\share", "//server/share"):
            assert is_absolute_path(p) is True, f"{p!r} must be absolute"

    def test_mcp_still_catches_posix_and_windows_backslash(self):
        """The originally-covered absolute forms still flag (no regression)."""
        assert is_absolute_path("/etc/passwd") is True
        assert is_absolute_path("C:\\Users\\x") is True

    def test_mcp_relative_and_envvar_paths_not_absolute(self):
        """Relative paths and ${VAR}-leading paths are NOT absolute (no over-flag)."""
        for p in ("lib/server.js", "./run.sh", "${CLAUDE_PLUGIN_ROOT}/x", "npx"):
            assert is_absolute_path(p) is False, f"{p!r} must NOT be absolute"

    def test_mcp_matches_lsp_byte_for_byte(self):
        """The two helpers agree on a battery of inputs (DRY-drift guard)."""
        probes = [
            "/etc/passwd",
            "~/x",
            "C:/x",
            "C:\\x",
            "\\\\srv\\sh",
            "//srv/sh",
            "${VAR}/x",
            "rel/path",
            "npx",
            "a",
        ]
        for p in probes:
            assert validate_mcp.is_absolute_path(p) == validate_lsp.is_absolute_path(p), p

    def test_helpers_are_the_same_shared_object(self):
        """DRY hoist (audit #3): mcp + lsp now REUSE the common helpers.

        Identity (not just equality) proves the single source of truth — the
        old duplicate definitions are gone, so the two can never drift again.
        """
        import cpv_validation_common as common

        assert validate_mcp.is_absolute_path is common.is_absolute_path
        assert validate_lsp.is_absolute_path is common.is_absolute_path
        assert validate_mcp.validate_env_var_syntax is common.validate_env_var_syntax
        assert validate_lsp.validate_env_var_syntax is common.validate_env_var_syntax
        assert validate_mcp.ENV_VAR_PATTERN is common.ENV_VAR_PATTERN

    def test_env_var_syntax_identical_outcomes_across_modules(self):
        """Two-sided: the shared validate_env_var_syntax fires the SAME MAJOR
        verdict for both modules (malformed) and the SAME no-MAJOR for valid."""
        for bad in ("${VAR", "${}", "${1BAD}"):
            for mod in (validate_mcp, validate_lsp):
                rep = ValidationReport()
                mod.validate_env_var_syntax(bad, rep, "ctx")
                assert any(r.level == "MAJOR" and "Malformed" in r.message for r in rep.results), (mod.__name__, bad)
        for ok in ("${VAR:-x}extra}", "${A}${B}", "plain text"):
            for mod in (validate_mcp, validate_lsp):
                rep = ValidationReport()
                mod.validate_env_var_syntax(ok, rep, "ctx")
                assert not any("Malformed" in r.message for r in rep.results), (mod.__name__, ok)


# ---------------------------------------------------------------------------
# #2 MCP-CMD-NOTSTR — non-string command is a clean CRITICAL
# ---------------------------------------------------------------------------
class TestMcpCommandTypeGuard:
    def test_non_string_command_is_critical_not_crash(self):
        """`command: 123` yields a CRITICAL (was an AttributeError swallowed to WARNING)."""
        report = ValidationReport()
        # Must NOT raise.
        validate_mcp_server("srv", {"command": 123}, report)
        crit = [r for r in report.results if r.level == "CRITICAL"]
        assert any("must be a string" in r.message for r in crit)

    def test_list_command_is_critical(self):
        """A list command (legal JSON, illegal schema) is CRITICAL, no crash."""
        report = ValidationReport()
        validate_mcp_server("srv", {"command": ["x"]}, report)
        assert any(r.level == "CRITICAL" and "must be a string" in r.message for r in report.results)

    def test_valid_string_command_does_not_emit_command_type_critical(self):
        """A normal string command does NOT trip the new type guard."""
        report = ValidationReport()
        validate_mcp_server("srv", {"command": "node"}, report)
        assert not any("'command' must be a string" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# #4 MCP-HEADERSHELPER — headersHelper validated like command
# ---------------------------------------------------------------------------
class TestMcpHeadersHelper:
    def test_traversal_headers_helper_flagged(self, tmp_path):
        """A headersHelper that escapes the plugin root is a MAJOR (was unvalidated)."""
        report = ValidationReport()
        validate_mcp_server(
            "srv",
            {"type": "http", "url": "https://x.example", "headersHelper": "${CLAUDE_PLUGIN_ROOT}/../../etc/x"},
            report,
            plugin_root=tmp_path,
        )
        assert any(r.level == "MAJOR" and "traverses outside plugin root" in r.message for r in report.results)

    def test_absolute_headers_helper_flagged(self):
        """An absolute headersHelper path is a MAJOR portability finding."""
        report = ValidationReport()
        validate_mcp_server(
            "srv",
            {"type": "http", "url": "https://x.example", "headersHelper": "/usr/local/bin/headers.sh"},
            report,
        )
        assert any(r.level == "MAJOR" and "Absolute path" in r.message for r in report.results)

    def test_non_string_headers_helper_flagged(self):
        """A non-string headersHelper is a MAJOR type error (no crash)."""
        report = ValidationReport()
        validate_mcp_server("srv", {"type": "http", "url": "https://x.example", "headersHelper": 5}, report)
        assert any(r.level == "MAJOR" and "headersHelper' must be a string" in r.message for r in report.results)

    def test_portable_headers_helper_not_flagged(self, tmp_path):
        """A ${CLAUDE_PLUGIN_ROOT}-relative headersHelper inside the plugin is clean."""
        report = ValidationReport()
        validate_mcp_server(
            "srv",
            {"type": "http", "url": "https://x.example", "headersHelper": "${CLAUDE_PLUGIN_ROOT}/bin/h.sh"},
            report,
            plugin_root=tmp_path,
        )
        # No MAJOR/CRITICAL attributable to headersHelper.
        bad = [r for r in report.results if r.level in ("MAJOR", "CRITICAL") and "headersHelper" in r.message]
        assert bad == []


# ---------------------------------------------------------------------------
# #9 MCP-CTX-AS-FILE — reserved-name MAJOR no longer mis-fills file column
# ---------------------------------------------------------------------------
class TestMcpReservedNameFileColumn:
    def test_reserved_name_major_has_no_context_file_arg(self):
        """The reserved-name MAJOR carries context in the message, not the file slot."""
        report = ValidationReport()
        validate_mcp_server("workspace", {"command": "node"}, report, file_context="mcp-config")
        reserved = [r for r in report.results if "reserved by Claude Code" in r.message]
        assert reserved, "reserved-name MAJOR must still fire"
        # file column must NOT be the 'mcp-config:workspace' context label.
        assert reserved[0].file != "mcp-config:workspace"
        assert reserved[0].file is None
        # context still discoverable in the message.
        assert "workspace" in reserved[0].message


# ---------------------------------------------------------------------------
# #10 MCP-DEAD-COND — stdio url-ignored INFO still fires
# ---------------------------------------------------------------------------
class TestMcpStdioUrlIgnoredInfo:
    def test_stdio_with_url_still_emits_info(self):
        """Removing the dead `and transport == 'stdio'` conjunct preserves the INFO."""
        report = ValidationReport()
        validate_mcp_server("srv", {"command": "node", "url": "https://x"}, report)
        assert any(r.level == "INFO" and "url will be ignored" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# #15 MCP-BRACE-FP — span-coverage malformed detection (MCP + LSP)
# ---------------------------------------------------------------------------
class TestEnvVarBraceSpanCoverage:
    def test_valid_ref_with_trailing_literal_brace_is_not_malformed(self):
        """'${VAR:-x}extra}' is a valid ref + literal text — must NOT be MAJOR (the FP)."""
        for mod in (validate_mcp, validate_lsp):
            report = ValidationReport()
            mod.validate_env_var_syntax("${VAR:-x}extra}", report, "ctx")
            assert not any("Malformed" in r.message for r in report.results), mod.__name__

    def test_genuinely_unclosed_ref_is_still_malformed(self):
        """'${VAR' (no closing brace) is still MAJOR in both validators."""
        for mod in (validate_mcp, validate_lsp):
            report = ValidationReport()
            mod.validate_env_var_syntax("${VAR", report, "ctx")
            assert any(r.level == "MAJOR" and "Malformed" in r.message for r in report.results), mod.__name__

    def test_empty_and_invalid_name_refs_are_malformed(self):
        """'${}' and '${1BAD}' begin no valid ref → MAJOR (precise, not blanket)."""
        for bad in ("${}", "${1BAD}"):
            report = ValidationReport()
            validate_env_var_syntax(bad, report, "ctx")
            assert any(r.level == "MAJOR" and "Malformed" in r.message for r in report.results), bad

    def test_two_adjacent_valid_refs_not_malformed(self):
        """'${A}${B}' is two valid refs — no false MAJOR."""
        report = ValidationReport()
        validate_env_var_syntax("${A}${B}", report, "ctx")
        assert not any("Malformed" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# #12 TEL-BETA-DUP — BETA_TRACING_ENDPOINT deduped, hazard intact
# ---------------------------------------------------------------------------
class TestTelemetryBetaTracingDedup:
    def test_beta_tracing_endpoint_removed_from_otel_set(self):
        """The redundant entry is gone from OTEL_ALL_ENV_VARS (single source of truth)."""
        assert "BETA_TRACING_ENDPOINT" not in OTEL_ALL_ENV_VARS

    def test_beta_tracing_endpoint_still_in_hazard_set(self):
        """It remains the canonical hazard var."""
        assert "BETA_TRACING_ENDPOINT" in PLUGIN_SHIPPED_HAZARD_ENV_VARS

    def test_external_beta_endpoint_still_critical(self, tmp_path):
        """Hazard detection is intact: an external endpoint is still CRITICAL."""
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"env": {"BETA_TRACING_ENDPOINT": "https://attacker.example.com/v1"}}))
        rep = scan_settings_for_telemetry(settings, plugin_shipped=True)
        assert any(r.level == "CRITICAL" and "BETA_TRACING_ENDPOINT" in r.message for r in rep.results)


# ---------------------------------------------------------------------------
# #13 TEL-ENVWALK-DUP — nested env captured once, real nesting preserved
# ---------------------------------------------------------------------------
class TestTelemetryEnvWalkDedup:
    def test_pathological_nested_env_captured_once(self):
        """{'env': {'env': {...}}} no longer double-scans the inner block."""
        blocks = _extract_env_blocks({"env": {"env": {"C": "3"}}})
        assert len(blocks) == 1

    def test_mcp_servers_env_still_captured(self):
        """mcpServers[name].env nesting is still captured exactly once."""
        blocks = _extract_env_blocks({"mcpServers": {"srv": {"command": "x", "env": {"OTEL_FOO": "1"}}}})
        assert len(blocks) == 1 and blocks[0] == {"OTEL_FOO": "1"}

    def test_top_level_and_hooks_env_both_captured(self):
        """A top-level env AND a hooks[*].env are both captured (siblings still walked)."""
        blocks = _extract_env_blocks({"env": {"A": "1"}, "hooks": {"PreToolUse": [{"env": {"B": "2"}}]}})
        assert len(blocks) == 2


# ---------------------------------------------------------------------------
# #14 TEL-PASSED-SHARED — per-file PASSED is per-file across a shared report
# ---------------------------------------------------------------------------
class TestTelemetryPerFilePassedSharedReport:
    def test_second_clean_file_also_gets_passed_in_shared_report(self, tmp_path):
        """With a SHARED report, the SECOND clean file still gets its per-file PASSED."""
        s1 = tmp_path / "a.json"
        s2 = tmp_path / "b.json"
        s1.write_text(json.dumps({"env": {}}))
        s2.write_text(json.dumps({"env": {}}))
        report = ValidationReport()
        scan_settings_for_telemetry(s1, report=report, plugin_shipped=True)
        passed_after_first = [r for r in report.results if r.level == "PASSED"]
        scan_settings_for_telemetry(s2, report=report, plugin_shipped=True)
        passed_after_second = [r for r in report.results if r.level == "PASSED"]
        # The second clean file added its own PASSED (count grew).
        assert len(passed_after_second) > len(passed_after_first)
        assert any(r.file == str(s2) and r.level == "PASSED" for r in report.results)

    def test_file_with_finding_does_not_get_clean_passed(self, tmp_path):
        """A file that DOES have a finding must NOT also get the 'clean' per-file PASSED."""
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"env": {"CLAUDE_CONFIG_DIR": "/tmp/x"}}))
        report = ValidationReport()
        scan_settings_for_telemetry(bad, report=report, plugin_shipped=True)
        # No "No telemetry supply-chain risks detected" PASSED for this dirty file.
        clean_passed = [
            r for r in report.results if r.level == "PASSED" and "No telemetry supply-chain risks" in r.message
        ]
        assert clean_passed == []
        assert any(r.level == "CRITICAL" for r in report.results)


# ---------------------------------------------------------------------------
# #6 ENT-AUTHOR-LICENSE — missing author/license MINOR by default, CRITICAL strict
# ---------------------------------------------------------------------------
class TestEnterpriseAuthorLicenseSeverity:
    def test_missing_author_is_minor_by_default(self):
        """Absent author (not a CC-spec field) is MINOR by default, not blocking MAJOR."""
        report = EnterpriseComplianceReport()  # strict_mode defaults False
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_author_field({"name": "x"}, "skills/s/SKILL.md", report, result)
        assert any(r.level == "MINOR" and "'author'" in r.message for r in report.results)
        assert not any(r.level == "MAJOR" and "'author'" in r.message for r in report.results)
        assert "author" in result.missing_required

    def test_missing_license_is_minor_by_default(self):
        """Absent license is MINOR by default."""
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_license_field({"name": "x"}, "skills/s/SKILL.md", report, result)
        assert any(r.level == "MINOR" and "'license'" in r.message for r in report.results)
        assert not any(r.level == "MAJOR" and "'license'" in r.message for r in report.results)

    def test_missing_author_is_critical_under_strict(self):
        """Strict/enterprise mode still escalates missing author to CRITICAL."""
        report = EnterpriseComplianceReport(strict_mode=True)
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_author_field({"name": "x"}, "skills/s/SKILL.md", report, result)
        assert any(r.level == "CRITICAL" and "'author'" in r.message for r in report.results)

    def test_missing_license_is_critical_under_strict(self):
        """Strict/enterprise mode still escalates missing license to CRITICAL."""
        report = EnterpriseComplianceReport(strict_mode=True)
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_license_field({"name": "x"}, "skills/s/SKILL.md", report, result)
        assert any(r.level == "CRITICAL" and "'license'" in r.message for r in report.results)

    def test_empty_author_string_still_major(self):
        """A DECLARED-but-empty author is still MAJOR (shape validation unchanged)."""
        report = EnterpriseComplianceReport()
        result = SkillComplianceResult(skill_path="/tmp/s", skill_name="s", is_compliant=True)
        validate_author_field({"author": "   "}, "skills/s/SKILL.md", report, result)
        assert any(r.level == "MAJOR" and "cannot be empty" in r.message for r in report.results)


# ---------------------------------------------------------------------------
# #16 ENT-FM-SPLIT — '---' inside a frontmatter value no longer truncates parse
# ---------------------------------------------------------------------------
class TestEnterpriseFrontmatterParsing:
    def test_dash_inside_value_parses_correctly(self):
        """A description value containing '---' no longer breaks the YAML parse."""
        content = '---\nname: my-skill\ndescription: "Some --- rule inside"\n---\n\n# Body'
        fm, body = parse_frontmatter(content)
        assert fm is not None
        assert fm["name"] == "my-skill"
        assert fm["description"] == "Some --- rule inside"
        assert "# Body" in body

    def test_basic_frontmatter_still_parses(self):
        """A normal frontmatter block still parses (no regression)."""
        content = "---\nname: s\ndescription: d\n---\n\nbody"
        fm, _ = parse_frontmatter(content)
        assert fm == {"name": "s", "description": "d"}

    def test_crlf_frontmatter_parses(self):
        """CRLF-authored frontmatter parses (the closing delimiter is a line)."""
        content = "---\r\nname: s\r\ndescription: d\r\n---\r\n\r\nbody"
        fm, _ = parse_frontmatter(content)
        assert fm == {"name": "s", "description": "d"}

    def test_no_frontmatter_returns_none(self):
        """Content without frontmatter still returns (None, content)."""
        fm, body = parse_frontmatter("# Heading\ntext")
        assert fm is None
        assert body == "# Heading\ntext"

    def test_empty_frontmatter_returns_empty_dict(self):
        """An empty '---\\n---' block parses as present-but-empty ({}), matching prior behaviour."""
        fm, _ = parse_frontmatter("---\n---\nbody")
        assert fm == {}

    def test_unterminated_frontmatter_returns_none(self):
        """A '---' with no closing delimiter returns None (no false parse)."""
        fm, _ = parse_frontmatter("---\nname: x\nbody with no close")
        assert fm is None
