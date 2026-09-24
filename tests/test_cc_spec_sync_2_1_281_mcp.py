"""CC spec sync 2.1.258-2.1.281 — MCP transport handling (validate_mcp).

Two-sided: each newly-accepted shape is paired with a control proving the same
code path still rejects the bad sibling.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_mcp import validate_mcp_server  # noqa: E402


def _run(config: dict) -> ValidationReport:
    report = ValidationReport()
    validate_mcp_server("srv", config, report)
    return report


def _msgs(report: ValidationReport, level: str) -> list[str]:
    return [r.message for r in report.results if r.level == level]


def _blocking(report: ValidationReport) -> list[str]:
    return [r.message for r in report.results if r.level in ("CRITICAL", "MAJOR", "MINOR")]


class TestWebSocketTransport:
    def test_wss_remote_is_not_blocking(self) -> None:
        """A documented `type: ws` entry over wss:// draws no blocking finding."""
        assert _blocking(_run({"type": "ws", "url": "wss://mcp.example.com/socket"})) == []

    def test_ws_localhost_is_clean(self) -> None:
        """Plaintext ws:// to localhost is fine, like http://localhost."""
        report = _run({"type": "ws", "url": "ws://localhost:8080/mcp"})
        assert _blocking(report) == []
        assert not any("remote URL" in m for m in _msgs(report, "WARNING"))

    def test_ws_remote_plaintext_is_major(self) -> None:
        """ws:// to a remote host is flagged exactly like remote http://."""
        majors = _msgs(_run({"type": "ws", "url": "ws://mcp.example.com/socket"}), "MAJOR")
        assert any("unencrypted" in m for m in majors)

    def test_ws_without_url_is_critical(self) -> None:
        """A ws entry still needs a url."""
        crits = _msgs(_run({"type": "ws"}), "CRITICAL")
        assert any("missing 'url'" in m for m in crits)

    def test_ws_with_http_scheme_is_major(self) -> None:
        """The scheme must match the transport."""
        majors = _msgs(_run({"type": "ws", "url": "https://mcp.example.com"}), "MAJOR")
        assert any("should be ws(s)://" in m for m in majors)

    def test_unknown_type_still_rejected(self) -> None:
        """Positive control: a bogus transport is still MAJOR."""
        majors = _msgs(_run({"type": "websocket", "url": "wss://x.example.com"}), "MAJOR")
        assert any("Invalid transport type 'websocket'" in m for m in majors)

    def test_streamable_http_alias_accepted(self) -> None:
        """`streamable-http` is a documented alias for `http`."""
        report = _run({"type": "streamable-http", "url": "https://mcp.example.com/mcp"})
        assert not any("Invalid transport" in m for m in _msgs(report, "MAJOR"))
        assert _blocking(report) == []

    def test_non_string_type_is_major_not_crash(self) -> None:
        """An unhashable type must be reported, never raise."""
        majors = _msgs(_run({"type": ["http"], "command": "x"}), "MAJOR")
        assert any("Invalid transport type" in m for m in majors)


class TestSdkType:
    def test_sdk_is_one_major_and_no_missing_command(self) -> None:
        """`type: sdk` (skipped since CC 2.1.274) → exactly one MAJOR, no spurious CRITICAL."""
        report = _run({"type": "sdk"})
        assert _msgs(report, "CRITICAL") == []
        majors = _msgs(report, "MAJOR")
        assert len(majors) == 1
        assert "type 'sdk' is skipped" in majors[0]


class TestUrlWithoutType:
    def test_url_no_type_single_blocking_finding(self) -> None:
        """url + no type → one CRITICAL with CC's remediation text, no missing-command finding."""
        report = _run({"url": "https://mcp.example.com/mcp"})
        blocking = _blocking(report)
        assert len(blocking) == 1
        assert 'has a "url" but no "type"' in blocking[0]
        assert "missing required 'command'" not in blocking[0]
        assert _msgs(report, "CRITICAL") == blocking

    def test_command_url_without_type_is_stdio_not_critical(self) -> None:
        """command + url + no type is a WORKING stdio server (CC 2.1.281 probe): no CRITICAL, INFO kept."""
        report = _run({"command": "node", "args": ["server.js"], "url": "https://mcp.example.com/mcp"})
        assert _msgs(report, "CRITICAL") == []
        assert any("url will be ignored" in m for m in _msgs(report, "INFO"))

    def test_explicit_stdio_without_command_still_critical(self) -> None:
        """Control: a real stdio server missing its command keeps the old CRITICAL."""
        crits = _msgs(_run({"type": "stdio"}), "CRITICAL")
        assert any("missing required 'command'" in m for m in crits)
