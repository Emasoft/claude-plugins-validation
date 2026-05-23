#!/usr/bin/env python3
"""Tests for the v2.102.0 validate_plugin → cache-validator CALL.

Per the user's design choice: `validate_plugin` CALLS the cache validator as
a SEPARATE step (it does NOT merge the cache logic in). The cache findings —
all WARNING since v2.102.0 — are written to their OWN report file; only a
one-line pointer lands in the main report. Cache findings never enter the
main report's results, counts, or VALID/INVALID verdict.

These tests pin that contract directly against the two helpers in
`validate_plugin`: `_derive_cache_report_path` and `_run_cache_audit_separate`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import validate_plugin as vp  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402


def _make_plugin(tmp_path: Path, *, with_model_agent: bool = True) -> Path:
    """Minimal plugin scaffold; optionally with an agent that pins `model:`."""
    plugin = tmp_path / "demo"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo", "version": "0.0.1", "description": "x"})
    )
    if with_model_agent:
        (plugin / "agents").mkdir()
        (plugin / "agents" / "a.md").write_text("---\nname: a\ndescription: x\nmodel: opus\n---\n\nbody\n")
    return plugin


# ---------------------------------------------------------------------------
# _derive_cache_report_path
# ---------------------------------------------------------------------------


class TestDeriveCacheReportPath:
    def test_inserts_cache_audit_suffix_before_extension(self) -> None:
        main = Path("/x/reports/validate_plugin/20260522_120000+0200-demo.md")
        out = vp._derive_cache_report_path(main)
        assert out.name == "20260522_120000+0200-demo-cache-audit.md"
        assert out.parent == main.parent  # same directory as the main report

    def test_handles_no_dotmd_gracefully(self) -> None:
        main = Path("/x/report")  # no .md suffix
        out = vp._derive_cache_report_path(main)
        assert out.name == "report-cache-audit"


# ---------------------------------------------------------------------------
# _run_cache_audit_separate — the CALL contract
# ---------------------------------------------------------------------------


class TestRunCacheAuditSeparate:
    def test_writes_separate_report_and_returns_pointer(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        main_report = tmp_path / "reports" / "validate_plugin" / "20260522_x-demo.md"
        report = ValidationReport()

        pointer = vp._run_cache_audit_separate(plugin, str(main_report), report)

        # A pointer string is returned and it names the separate cache report.
        assert pointer is not None
        cache_report = vp._derive_cache_report_path(main_report)
        assert str(cache_report) in pointer
        assert cache_report.is_file(), "the cache audit must write its OWN report file"
        # The agent's model: pin produced at least one CA-04 WARNING.
        assert "WARNING" in pointer

    def test_pointer_is_added_to_main_report_as_info(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        main_report = tmp_path / "reports" / "validate_plugin" / "20260522_x-demo.md"
        report = ValidationReport()
        vp._run_cache_audit_separate(plugin, str(main_report), report)
        info_pointers = [r for r in report.results if r.level == "INFO" and "Cache audit" in r.message]
        assert info_pointers, "a one-line cache-audit pointer must be added to the main report (INFO)"

    def test_cache_findings_are_NOT_merged_into_main_report(self, tmp_path: Path) -> None:
        """CA-* findings stay in the separate report — never in the main results.

        The INFO pointer legitimately references "CA-01..CA-06", so it is
        excluded; we assert that no actual CA FINDING (WARNING-level, e.g.
        "CA-04: agent declares model:") leaked into the main report.
        """
        plugin = _make_plugin(tmp_path)
        main_report = tmp_path / "reports" / "validate_plugin" / "20260522_x-demo.md"
        report = ValidationReport()
        vp._run_cache_audit_separate(plugin, str(main_report), report)
        ca_findings_in_main = [r for r in report.results if r.level != "INFO" and "CA-0" in r.message]
        assert ca_findings_in_main == [], (
            f"cache findings must NOT be merged into the main report: {ca_findings_in_main}"
        )

    def test_cache_call_does_not_change_main_verdict(self, tmp_path: Path) -> None:
        """All cache findings are WARNING; the main report's exit code must stay 0."""
        plugin = _make_plugin(tmp_path)
        main_report = tmp_path / "reports" / "validate_plugin" / "20260522_x-demo.md"
        report = ValidationReport()
        vp._run_cache_audit_separate(plugin, str(main_report), report)
        # Only an INFO pointer was added — no CRITICAL/MAJOR/MINOR/NIT.
        assert report.exit_code == 0

    def test_no_report_path_returns_pointer_without_file(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path)
        report = ValidationReport()
        pointer = vp._run_cache_audit_separate(plugin, None, report)
        assert pointer is not None
        # No sibling file is written when there's no anchor path.
        assert "Cache audit" in pointer

    def test_marketplace_only_no_manifest_is_skipped(self, tmp_path: Path) -> None:
        """A tree with no .claude-plugin/plugin.json skips the cache audit (returns None)."""
        bare = tmp_path / "mkt"
        bare.mkdir()
        report = ValidationReport()
        pointer = vp._run_cache_audit_separate(bare, str(tmp_path / "r.md"), report)
        assert pointer is None

    def test_clean_plugin_reports_zero_warnings(self, tmp_path: Path) -> None:
        plugin = _make_plugin(tmp_path, with_model_agent=False)
        main_report = tmp_path / "reports" / "validate_plugin" / "20260522_x-demo.md"
        report = ValidationReport()
        pointer = vp._run_cache_audit_separate(plugin, str(main_report), report)
        assert pointer is not None
        assert "clean" in pointer.lower() or "0 cache-discipline" in pointer
