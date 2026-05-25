#!/usr/bin/env python3
"""Tests for ``scripts/cpv_batch_aggregator.py``.

Covers:
* ``load_index`` — index.json required.
* ``load_shard_status`` — missing/malformed files marked with ``error``.
* ``render_report`` — markdown structure, per-shard breakdown.
* ``aggregate`` — end-to-end with fixture session dirs.
* CLI exit code on clean / partial / error sessions.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import cpv_batch_aggregator as aggregator  # noqa: E402


def _write_index(session_dir: Path, n_shards: int, plugin_path: str = "/tmp/plugin") -> None:
    """Write a minimal index.json + n shard placeholders."""
    session_dir.mkdir(parents=True, exist_ok=True)
    index = {
        # Match the producer (cpv_batch_planner.SCHEMA_VERSION). load_index now
        # warns on a mismatch, so the fixture tracks the real schema version to
        # stay warning-free for the existing aggregate/render tests.
        "schema_version": aggregator.SCHEMA_VERSION,
        "created_at": "2026-05-19T11:50:00+0200",
        "plugin_path": plugin_path,
        "report_source": "test",
        "shard_count": n_shards,
        "max_parallel": n_shards,
        "shard_size": 30,
        "total_findings": 30 * n_shards,
        "counts_by_severity": {"CRITICAL": 0, "MAJOR": n_shards * 30, "MINOR": 0, "NIT": 0, "WARNING": 0},
        "shards": [
            {
                "shard_id": i,
                "file_count": 5,
                "finding_count": 30,
                "manifest_path": str(session_dir / f"shard-{i}.json"),
                "status_path": str(session_dir / f"shard-{i}.status.json"),
            }
            for i in range(1, n_shards + 1)
        ],
    }
    (session_dir / "index.json").write_text(json.dumps(index, indent=2))


def _write_status(
    session_dir: Path,
    shard_id: int,
    *,
    fixed: int = 30,
    failed: int = 0,
    remaining: int = 0,
    exit_reason: str = "clean",
) -> None:
    payload = {
        "schema_version": 1,
        "shard_id": shard_id,
        "started_at": "2026-05-19T11:50:00+0200",
        "finished_at": "2026-05-19T11:54:00+0200",
        "fixed": fixed,
        "failed": failed,
        "remaining": remaining,
        "agent_exit_reason": exit_reason,
        "per_file": [],
    }
    (session_dir / f"shard-{shard_id}.status.json").write_text(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# load_index
# ---------------------------------------------------------------------------


class TestLoadIndex:
    def test_loads_valid_index(self, tmp_path: Path) -> None:
        _write_index(tmp_path, 2)
        idx = aggregator.load_index(tmp_path)
        assert idx["shard_count"] == 2

    def test_raises_on_missing(self, tmp_path: Path) -> None:
        try:
            aggregator.load_index(tmp_path)
            raise AssertionError("expected FileNotFoundError")
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# load_shard_status
# ---------------------------------------------------------------------------


class TestLoadShardStatus:
    def test_loads_valid_status(self, tmp_path: Path) -> None:
        _write_status(tmp_path, 1, fixed=20, failed=2, remaining=8, exit_reason="partial")
        summary = aggregator.load_shard_status(tmp_path / "shard-1.status.json", 1)
        assert summary.fixed == 20
        assert summary.failed == 2
        assert summary.remaining == 8
        assert summary.agent_exit_reason == "partial"
        assert summary.error is None

    def test_marks_missing_file(self, tmp_path: Path) -> None:
        summary = aggregator.load_shard_status(tmp_path / "shard-1.status.json", 1)
        assert summary.error is not None
        assert "missing" in summary.error.lower()
        assert summary.shard_id == 1

    def test_marks_malformed_json(self, tmp_path: Path) -> None:
        (tmp_path / "shard-1.status.json").write_text("not valid json {")
        summary = aggregator.load_shard_status(tmp_path / "shard-1.status.json", 1)
        assert summary.error is not None
        assert "unparseable" in summary.error.lower()


# ---------------------------------------------------------------------------
# render_report
# ---------------------------------------------------------------------------


class TestRenderReport:
    def test_clean_all_shards(self, tmp_path: Path) -> None:
        _write_index(tmp_path, 3)
        for i in range(1, 4):
            _write_status(tmp_path, i)
        idx = aggregator.load_index(tmp_path)
        summaries = [aggregator.load_shard_status(tmp_path / f"shard-{i}.status.json", i) for i in range(1, 4)]
        body = aggregator.render_report(idx, summaries)
        assert "Aggregate result" in body
        assert "Per-shard outcomes" in body
        assert "All shards completed cleanly" in body
        assert "needing follow-up" not in body

    def test_includes_followup_section_for_failed_shards(self, tmp_path: Path) -> None:
        _write_index(tmp_path, 2)
        _write_status(tmp_path, 1)
        _write_status(tmp_path, 2, fixed=20, failed=5, remaining=5, exit_reason="partial")
        idx = aggregator.load_index(tmp_path)
        summaries = [aggregator.load_shard_status(tmp_path / f"shard-{i}.status.json", i) for i in range(1, 3)]
        body = aggregator.render_report(idx, summaries)
        assert "needing follow-up" in body
        assert "Shard 2" in body

    def test_per_shard_rows_numbered(self, tmp_path: Path) -> None:
        """Table rows have leading # column with numbers (per user feedback)."""
        _write_index(tmp_path, 3)
        for i in range(1, 4):
            _write_status(tmp_path, i)
        idx = aggregator.load_index(tmp_path)
        summaries = [aggregator.load_shard_status(tmp_path / f"shard-{i}.status.json", i) for i in range(1, 4)]
        body = aggregator.render_report(idx, summaries)
        assert "| # |" in body  # column header
        assert "| 1 |" in body
        assert "| 3 |" in body


# ---------------------------------------------------------------------------
# aggregate end-to-end
# ---------------------------------------------------------------------------


class TestAggregateEndToEnd:
    def test_all_clean_returns_all_clean_true(self, tmp_path: Path) -> None:
        _write_index(tmp_path, 2)
        for i in range(1, 3):
            _write_status(tmp_path, i)
        out = aggregator.aggregate(tmp_path, report_path=tmp_path / "report.md")
        assert out["all_clean"] is True
        assert out["fixed"] == 60
        assert out["failed"] == 0
        assert out["remaining"] == 0

    def test_partial_returns_all_clean_false(self, tmp_path: Path) -> None:
        _write_index(tmp_path, 2)
        _write_status(tmp_path, 1)
        _write_status(tmp_path, 2, fixed=10, failed=10, remaining=10, exit_reason="partial")
        out = aggregator.aggregate(tmp_path, report_path=tmp_path / "report.md")
        assert out["all_clean"] is False
        assert out["fixed"] == 40
        assert out["failed"] == 10
        assert out["remaining"] == 10

    def test_missing_shard_status_marked_with_error(self, tmp_path: Path) -> None:
        _write_index(tmp_path, 2)
        _write_status(tmp_path, 1)
        # shard 2 status missing on purpose
        out = aggregator.aggregate(tmp_path, report_path=tmp_path / "report.md")
        assert out["all_clean"] is False
        # Find the shard 2 summary in the output
        s2 = next(s for s in out["shard_summaries"] if s["shard_id"] == 2)
        assert s2["error"] is not None

    def test_writes_report_at_explicit_path(self, tmp_path: Path) -> None:
        _write_index(tmp_path, 1)
        _write_status(tmp_path, 1)
        report = tmp_path / "explicit.md"
        out = aggregator.aggregate(tmp_path, report_path=report)
        assert report.exists()
        assert out["report_path"] == str(report)


# ---------------------------------------------------------------------------
# CLI exit code
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_exits_zero_on_clean(self, tmp_path: Path) -> None:
        _write_index(tmp_path, 1)
        _write_status(tmp_path, 1)
        script = Path(__file__).parent.parent / "scripts" / "cpv_batch_aggregator.py"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                str(tmp_path),
                "--report-path",
                str(tmp_path / "r.md"),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 0
        assert "DONE:" in result.stdout

    def test_cli_exits_one_on_partial(self, tmp_path: Path) -> None:
        _write_index(tmp_path, 1)
        _write_status(tmp_path, 1, fixed=10, failed=5, remaining=15, exit_reason="partial")
        script = Path(__file__).parent.parent / "scripts" / "cpv_batch_aggregator.py"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                str(tmp_path),
                "--report-path",
                str(tmp_path / "r.md"),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 1

    def test_cli_exits_one_on_missing_index(self, tmp_path: Path) -> None:
        # No index.json written
        script = Path(__file__).parent.parent / "scripts" / "cpv_batch_aggregator.py"
        result = subprocess.run(
            [sys.executable, str(script), str(tmp_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 1
