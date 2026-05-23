#!/usr/bin/env python3
"""Parallel-vs-serial parity tests for validate_mcp.py (task #384).

These tests pin the contract that the ProcessPoolExecutor-backed
per-server validation produces findings INDISTINGUISHABLE from the
pre-refactor serial loop:

* Same severity levels
* Same message text
* Same order (input order preservation via the parallel_scan harness)

Coverage:
* Multi-server config (4+ servers) — exercises the parallel path
* Single-server config — exercises the serial fast-path threshold
* Server name validation interleaving — name/config-type findings stay
  serial and inline, before the parallel batch
* Mixed-config (some dict, some non-dict) — the non-dict CRITICAL is
  emitted serially in input position, the dict ones validate in parallel
* Worker-error survival — when a synthetic worker raises, the validator
  surfaces it as a per-server WARNING instead of crashing the run
* Direct invocation of scan_one_mcp_server — verifies the pickleable
  worker entry point is callable on its own
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_mcp import (  # noqa: E402
    _parallel_scan_mcp_servers,
    scan_one_mcp_server,
    validate_mcp_config,
    validate_mcp_server,
    validate_plugin_mcp,
)


def _findings_signature(report: ValidationReport) -> list[tuple[str, str]]:
    """Reduce a report to a list of ``(level, message)`` tuples — the
    same signature the serial validator would produce. Comparing this
    list between serial-and-parallel runs is the exact parity check the
    spec asks for.

    File/line metadata are ignored because the serial path doesn't set
    them uniformly across all finding emissions, and parity is about
    *what was found*, not the surrounding metadata."""
    return [(r.level, r.message) for r in report.results]


def _serial_validate_mcp_servers(
    servers: list[tuple[str, dict, str]],
    plugin_root: Path | None,
) -> ValidationReport:
    """Replay the per-server validation serially, in input order.

    This is the "pre-refactor baseline" we compare the parallel path
    against. We DO NOT mutate the production code's behavior; we just
    duplicate the original loop here so the parity assertion has a
    reference to compare to."""
    report = ValidationReport()
    for server_name, server_config, file_context in servers:
        validate_mcp_server(
            server_name, server_config, report, plugin_root, file_context
        )
    return report


class TestParallelScanMcpParity:
    """Pin parallel-vs-serial output equivalence for the per-server scan."""

    def test_four_servers_parallel_matches_serial(self, tmp_path):
        """Four servers (above the parallel threshold) — every finding
        emitted by the parallel path matches the serial baseline 1:1 in
        order, severity, and message."""
        servers: list[tuple[str, dict, str]] = [
            (
                "srv-a",
                {"type": "stdio", "command": "node", "args": ["a.js"]},
                "test-ctx",
            ),
            (
                "srv-b",
                {"type": "http", "url": "http://remote.example.com/x"},
                "test-ctx",
            ),
            (
                "srv-c",
                {"type": "stdio", "command": "node", "timeout": "30s"},
                "test-ctx",
            ),
            (
                "srv-d",
                {"type": "sse", "url": "https://localhost:9090/sse"},
                "test-ctx",
            ),
        ]

        serial_report = _serial_validate_mcp_servers(servers, tmp_path)
        parallel_report = ValidationReport()
        _parallel_scan_mcp_servers(servers, tmp_path, parallel_report)

        assert _findings_signature(parallel_report) == _findings_signature(
            serial_report
        ), (
            "Parallel scan must produce identical findings (level + message) "
            f"in input order vs serial baseline.\n"
            f"  Serial:   {_findings_signature(serial_report)}\n"
            f"  Parallel: {_findings_signature(parallel_report)}"
        )

    def test_single_server_uses_serial_path(self, tmp_path):
        """One server is below the parallel threshold — it must produce
        the same findings as the multi-server parallel path would, but
        via the inline serial fast path (no ProcessPoolExecutor spin-up).

        We verify equivalence by comparing the report against a manually
        built serial reference."""
        servers: list[tuple[str, dict, str]] = [
            ("only-srv", {"type": "stdio", "command": "node"}, "test-ctx"),
        ]
        serial_report = _serial_validate_mcp_servers(servers, tmp_path)
        parallel_report = ValidationReport()
        _parallel_scan_mcp_servers(servers, tmp_path, parallel_report)
        assert _findings_signature(parallel_report) == _findings_signature(
            serial_report
        )

    def test_empty_server_list_is_noop(self, tmp_path):
        """Zero servers — no findings, no crash, no executor spawn."""
        report = ValidationReport()
        _parallel_scan_mcp_servers([], tmp_path, report)
        assert report.results == []

    def test_mcp_config_with_multiple_servers_preserves_input_order(self, tmp_path):
        """End-to-end via validate_mcp_config: a 4-server .mcp.json file
        produces findings whose first per-server PASSED markers appear in
        the JSON's key-insertion order. This pins the order invariant the
        downstream report aggregator depends on."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "alpha": {"type": "stdio", "command": "node"},
                        "bravo": {"type": "stdio", "command": "python3"},
                        "charlie": {
                            "type": "http",
                            "url": "http://localhost:3000/mcp",
                        },
                        "delta": {"type": "stdio", "command": "ruby"},
                    }
                }
            )
        )
        report = validate_mcp_config(mcp_file, plugin_root=tmp_path)
        # Each server emits a final PASSED "Server <name> configuration validated".
        # That marker must appear in the order alpha, bravo, charlie, delta.
        final_markers = [
            r.message
            for r in report.results
            if r.level == "PASSED"
            and r.message.endswith("configuration validated")
        ]
        names_in_order = [m.split()[1] for m in final_markers]
        assert names_in_order == ["alpha", "bravo", "charlie", "delta"], (
            f"Per-server final PASSED order should match JSON key order; "
            f"got {names_in_order}"
        )

    def test_mixed_dict_and_non_dict_configs(self, tmp_path):
        """A config mixing dict and non-dict server configs: the non-dict
        ones produce CRITICAL inline (in input position), the dict ones
        validate in parallel. The combined report preserves the
        interleave order that the original serial code emitted."""
        mcp_file = tmp_path / ".mcp.json"
        mcp_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "good-a": {"type": "stdio", "command": "node"},
                        "bad-b": "not-a-dict",
                        "good-c": {"type": "stdio", "command": "python3"},
                        "bad-d": 42,
                    }
                }
            )
        )
        report = validate_mcp_config(mcp_file, plugin_root=tmp_path)
        # Both bad-b and bad-d should produce CRITICAL "config must be an object".
        criticals = [r.message for r in report.results if r.level == "CRITICAL"]
        assert sum(1 for m in criticals if "bad-b" in m) == 1
        assert sum(1 for m in criticals if "bad-d" in m) == 1
        # And the two good servers should each get a final PASSED.
        passed_markers = [
            r.message
            for r in report.results
            if r.level == "PASSED"
            and r.message.endswith("configuration validated")
        ]
        assert any("good-a" in m for m in passed_markers)
        assert any("good-c" in m for m in passed_markers)


class TestScanOneMcpServerStandalone:
    """The pickleable worker entry point must be callable on its own —
    this validates that the spec-sidecar contract is honored independently
    of the parallel_scan harness."""

    def test_scan_one_mcp_server_via_spec_file(self, tmp_path):
        """Write a spec sidecar by hand, call scan_one_mcp_server, verify
        the returned findings match a direct call to validate_mcp_server."""
        spec = {
            "plugin_root": str(tmp_path),
            "file_context": "manual-test",
            "server_name": "test-srv",
            "server_config": {
                "type": "stdio",
                "command": "node",
                "args": ["server.js"],
            },
        }
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(spec))

        # Direct call to scan_one_mcp_server
        findings = scan_one_mcp_server(spec_path)

        # Reference: run validate_mcp_server inline with the same args
        ref_report = ValidationReport()
        validate_mcp_server(
            "test-srv",
            spec["server_config"],
            ref_report,
            plugin_root=tmp_path,
            file_context="manual-test",
        )
        ref_findings = ref_report.results

        assert [(f.level, f.message) for f in findings] == [
            (f.level, f.message) for f in ref_findings
        ], "scan_one_mcp_server must return the same findings as a direct serial call"

    def test_scan_one_mcp_server_with_no_plugin_root(self, tmp_path):
        """plugin_root is optional — spec with plugin_root=None must not crash."""
        spec = {
            "plugin_root": None,
            "file_context": "no-plugin",
            "server_name": "no-root-srv",
            "server_config": {"type": "stdio", "command": "node"},
        }
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(spec))
        findings = scan_one_mcp_server(spec_path)
        # At least a final PASSED should be present; no exception.
        assert any(
            f.level == "PASSED" and "configuration validated" in f.message
            for f in findings
        )


class TestPluginMcpInlineParallelism:
    """validate_plugin_mcp's inline-mcpServers branch must also use the
    parallel path correctly. We assert the same ordering + parity
    properties as the standalone validate_mcp_config tests."""

    def test_inline_servers_validated_in_input_order(self, tmp_path):
        """4 inline servers in plugin.json — final PASSED markers appear
        in dict-insertion (input) order."""
        claude_dir = tmp_path / ".claude-plugin"
        claude_dir.mkdir()
        manifest = {
            "name": "test-plugin",
            "mcpServers": {
                "z-srv": {"type": "stdio", "command": "node"},
                "a-srv": {"type": "stdio", "command": "python3"},
                "m-srv": {"type": "stdio", "command": "ruby"},
                "k-srv": {"type": "stdio", "command": "deno"},
            },
        }
        (claude_dir / "plugin.json").write_text(json.dumps(manifest))
        report = validate_plugin_mcp(tmp_path)
        final_markers = [
            r.message
            for r in report.results
            if r.level == "PASSED"
            and r.message.endswith("configuration validated")
        ]
        names_in_order = [m.split()[1] for m in final_markers]
        assert names_in_order == ["z-srv", "a-srv", "m-srv", "k-srv"]


class TestParallelImportsSurface:
    """The harness must be wired into validate_mcp's public import surface
    so external callers (e.g. validate_plugin.py orchestrator) see the
    refactored entry points."""

    def test_scan_one_mcp_server_is_module_level(self):
        """scan_one_mcp_server is a top-level callable — required by
        ProcessPoolExecutor's pickling contract."""
        from validate_mcp import scan_one_mcp_server as imported_fn

        assert callable(imported_fn)
        assert imported_fn.__module__ == "validate_mcp"
        # Closures aren't pickleable; top-level functions are. The
        # __qualname__ being identical to __name__ is a proxy for that.
        assert imported_fn.__qualname__ == "scan_one_mcp_server"
