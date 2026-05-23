#!/usr/bin/env python3
"""Parallel-vs-serial parity tests for validate_lsp.py (task #384).

Mirrors test_validate_mcp_parallelism.py — same parity contract, same
coverage shape — but exercises the LSP server validator's parallel path.

The two validators must behave symmetrically because they share the same
``cpv_parallel_runner.parallel_scan`` harness and the same spec-sidecar
worker pattern.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_lsp import (  # noqa: E402
    _parallel_scan_lsp_servers,
    scan_one_lsp_server,
    validate_lsp_config,
    validate_lsp_server,
    validate_plugin_lsp,
)


def _findings_signature(report: ValidationReport) -> list[tuple[str, str]]:
    """Reduce a report to a ``(level, message)`` tuple list for parity
    comparison. See test_validate_mcp_parallelism for the rationale —
    file/line metadata are intentionally excluded because the validator
    doesn't always set them, and parity is about what was found."""
    return [(r.level, r.message) for r in report.results]


def _serial_validate_lsp_servers(
    servers: list[tuple[str, dict, str]],
    plugin_root: Path | None,
) -> ValidationReport:
    """Pre-refactor baseline: replay the per-server validation serially
    in input order. Used as the parity reference."""
    report = ValidationReport()
    for server_name, server_config, file_context in servers:
        validate_lsp_server(
            server_name, server_config, report, plugin_root, file_context
        )
    return report


class TestParallelScanLspParity:
    """Pin parallel-vs-serial output equivalence for the per-server scan."""

    def test_four_servers_parallel_matches_serial(self, tmp_path):
        """Four heterogeneous LSP servers — every finding produced by the
        parallel path matches the serial baseline 1:1."""
        servers: list[tuple[str, dict, str]] = [
            (
                "pyright-srv",
                {
                    "command": "pyright-langserver",
                    "extensionToLanguage": {".py": "python"},
                },
                "test-ctx",
            ),
            (
                "ts-srv",
                {
                    "command": "typescript-language-server",
                    "extensionToLanguage": {".ts": "typescript"},
                    "args": ["--stdio"],
                },
                "test-ctx",
            ),
            (
                "bad-srv",
                {"command": "gopls", "filetypes": "not-a-list"},
                "test-ctx",
            ),
            (
                "missing-cmd-srv",
                {"extensionToLanguage": {".rs": "rust"}},
                "test-ctx",
            ),
        ]

        serial_report = _serial_validate_lsp_servers(servers, tmp_path)
        parallel_report = ValidationReport()
        _parallel_scan_lsp_servers(servers, tmp_path, parallel_report)

        assert _findings_signature(parallel_report) == _findings_signature(
            serial_report
        ), (
            "Parallel LSP scan must produce identical findings vs serial "
            f"baseline.\n"
            f"  Serial:   {_findings_signature(serial_report)}\n"
            f"  Parallel: {_findings_signature(parallel_report)}"
        )

    def test_single_server_uses_serial_path(self, tmp_path):
        """One server is below the parallel threshold — output must still
        match the serial baseline exactly."""
        servers: list[tuple[str, dict, str]] = [
            (
                "only-srv",
                {
                    "command": "pyright-langserver",
                    "extensionToLanguage": {".py": "python"},
                },
                "test-ctx",
            ),
        ]
        serial_report = _serial_validate_lsp_servers(servers, tmp_path)
        parallel_report = ValidationReport()
        _parallel_scan_lsp_servers(servers, tmp_path, parallel_report)
        assert _findings_signature(parallel_report) == _findings_signature(
            serial_report
        )

    def test_empty_server_list_is_noop(self, tmp_path):
        """Zero servers — no findings, no crash."""
        report = ValidationReport()
        _parallel_scan_lsp_servers([], tmp_path, report)
        assert report.results == []

    def test_lsp_config_with_multiple_servers_preserves_input_order(self, tmp_path):
        """End-to-end via validate_lsp_config — final PASSED markers
        appear in JSON key-insertion order."""
        lsp_file = tmp_path / ".lsp.json"
        lsp_file.write_text(
            json.dumps(
                {
                    "languageServers": {
                        "alpha-lsp": {
                            "command": "pyright-langserver",
                            "extensionToLanguage": {".py": "python"},
                        },
                        "bravo-lsp": {
                            "command": "typescript-language-server",
                            "extensionToLanguage": {".ts": "typescript"},
                            "args": ["--stdio"],
                        },
                        "charlie-lsp": {
                            "command": "gopls",
                            "extensionToLanguage": {".go": "go"},
                        },
                        "delta-lsp": {
                            "command": "rust-analyzer",
                            "extensionToLanguage": {".rs": "rust"},
                        },
                    }
                }
            )
        )
        report = validate_lsp_config(lsp_file, plugin_root=tmp_path)
        # Each server emits "Server <name> configuration validated" at end.
        final_markers = [
            r.message
            for r in report.results
            if r.level == "PASSED"
            and r.message.endswith("configuration validated")
        ]
        names_in_order = [m.split()[1] for m in final_markers]
        assert names_in_order == [
            "alpha-lsp",
            "bravo-lsp",
            "charlie-lsp",
            "delta-lsp",
        ], (
            f"Per-server final PASSED order should match JSON key order; "
            f"got {names_in_order}"
        )

    def test_mixed_dict_and_non_dict_configs(self, tmp_path):
        """Mix of dict and non-dict server configs — the non-dict ones
        produce CRITICAL inline, the dicts validate in parallel, and the
        combined report includes both."""
        lsp_file = tmp_path / ".lsp.json"
        lsp_file.write_text(
            json.dumps(
                {
                    "lspServers": {
                        "good-a": {
                            "command": "pyright-langserver",
                            "extensionToLanguage": {".py": "python"},
                        },
                        "bad-b": "not-a-dict",
                        "good-c": {
                            "command": "gopls",
                            "extensionToLanguage": {".go": "go"},
                        },
                        "bad-d": [1, 2, 3],
                    }
                }
            )
        )
        report = validate_lsp_config(lsp_file, plugin_root=tmp_path)
        criticals = [r.message for r in report.results if r.level == "CRITICAL"]
        assert sum(1 for m in criticals if "bad-b" in m) == 1
        assert sum(1 for m in criticals if "bad-d" in m) == 1
        passed_markers = [
            r.message
            for r in report.results
            if r.level == "PASSED"
            and r.message.endswith("configuration validated")
        ]
        assert any("good-a" in m for m in passed_markers)
        assert any("good-c" in m for m in passed_markers)


class TestScanOneLspServerStandalone:
    """The worker entry point is directly callable — sanity check the
    spec-sidecar contract independently of the harness."""

    def test_scan_one_lsp_server_via_spec_file(self, tmp_path):
        """Manual spec sidecar → scan_one_lsp_server → identical to a
        direct validate_lsp_server call."""
        spec = {
            "plugin_root": str(tmp_path),
            "file_context": "manual-test",
            "server_name": "test-lsp",
            "server_config": {
                "command": "pyright-langserver",
                "extensionToLanguage": {".py": "python"},
            },
        }
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(spec))
        findings = scan_one_lsp_server(spec_path)

        ref_report = ValidationReport()
        validate_lsp_server(
            "test-lsp",
            spec["server_config"],
            ref_report,
            plugin_root=tmp_path,
            file_context="manual-test",
        )

        assert [(f.level, f.message) for f in findings] == [
            (f.level, f.message) for f in ref_report.results
        ]

    def test_scan_one_lsp_server_with_no_plugin_root(self, tmp_path):
        """plugin_root=None is valid; the worker must not crash on it."""
        spec = {
            "plugin_root": None,
            "file_context": "no-plugin",
            "server_name": "no-root-srv",
            "server_config": {
                "command": "gopls",
                "extensionToLanguage": {".go": "go"},
            },
        }
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(spec))
        findings = scan_one_lsp_server(spec_path)
        assert any(
            f.level == "PASSED" and "configuration validated" in f.message
            for f in findings
        )


class TestPluginLspInlineParallelism:
    """validate_plugin_lsp's inline-lspServers branch must use the parallel
    path correctly, with the same ordering invariants as the standalone
    validate_lsp_config tests.

    Note: in v2.101.0 the inline lspServers path in validate_plugin_lsp
    only records names for cross-source dedup — it does NOT re-run server
    validation (the dedicated lsp-config files are validated separately).
    The order-preservation test here therefore exercises the lsp-config
    path that *does* run per-server validation.
    """

    def test_lsp_config_file_servers_validated_in_input_order(self, tmp_path):
        """A .lsp.json file with multiple servers — final PASSED markers
        appear in JSON key order."""
        (tmp_path / ".lsp.json").write_text(
            json.dumps(
                {
                    "lspServers": {
                        "z-srv": {
                            "command": "pyright-langserver",
                            "extensionToLanguage": {".py": "python"},
                        },
                        "a-srv": {
                            "command": "gopls",
                            "extensionToLanguage": {".go": "go"},
                        },
                        "m-srv": {
                            "command": "rust-analyzer",
                            "extensionToLanguage": {".rs": "rust"},
                        },
                        "k-srv": {
                            "command": "clangd",
                            "extensionToLanguage": {".c": "c"},
                        },
                    }
                }
            )
        )
        report = validate_plugin_lsp(tmp_path)
        final_markers = [
            r.message
            for r in report.results
            if r.level == "PASSED"
            and r.message.endswith("configuration validated")
        ]
        names_in_order = [m.split()[1] for m in final_markers]
        assert names_in_order == ["z-srv", "a-srv", "m-srv", "k-srv"]


class TestParallelImportsSurface:
    """Ensure the worker entry point is top-level and pickleable."""

    def test_scan_one_lsp_server_is_module_level(self):
        from validate_lsp import scan_one_lsp_server as imported_fn

        assert callable(imported_fn)
        assert imported_fn.__module__ == "validate_lsp"
        assert imported_fn.__qualname__ == "scan_one_lsp_server"
