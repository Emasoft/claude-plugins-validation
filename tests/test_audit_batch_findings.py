#!/usr/bin/env python3
"""Regression tests for the BATCH audit findings #4–#7, #10, #11, #13.

Each finding is covered with a TWO-SIDED test: the well-formed / happy path
keeps working AND the malformed / edge-case path is handled gracefully (no
raw traceback, correct timestamp shape).

Source: reports/audit/20260525_105207+0200-batch-menu-cli-content.md
Owned modules: cpv_batch_planner, cpv_batch_aggregator, cpv_batch_orchestrator.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import cpv_batch_aggregator as aggregator  # noqa: E402
import cpv_batch_orchestrator as orchestrator  # noqa: E402
import cpv_batch_planner as planner  # noqa: E402

# A GMT-offset token like +0200 / -0500 / +0000 (compact, no colon).
_TZ_OFFSET = re.compile(r"[+-]\d{4}")


def _write_report(path: Path, results: list[Any]) -> None:
    """Write a minimal validate_plugin-style JSON report."""
    path.write_text(json.dumps({"exit_code": 1, "results": results}))


def _minimal_plugin(root: Path) -> Path:
    """Create a throwaway plugin dir (planner only needs the path to exist)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"name": "x", "version": "0.0.0"}')
    return root


# ---------------------------------------------------------------------------
# #4 — main() must convert TimeoutExpired + OSError to a clean exit, not a TB.
# ---------------------------------------------------------------------------


class TestPlannerMainExceptionSurface:
    """Finding #4: subprocess.TimeoutExpired and OSError handled cleanly."""

    def test_valid_run_with_report_returns_zero(self, tmp_path: Path, capsys: Any) -> None:
        # Two-sided: the happy path (existing report, no subprocess) still exits 0.
        plugin = _minimal_plugin(tmp_path / "plug")
        report = tmp_path / "report.json"
        _write_report(report, [{"level": "MAJOR", "message": "m", "file": "a.md", "line": 1}])
        rc = planner.main(
            [str(plugin), "--report", str(report), "--session-dir", str(tmp_path / "sess")]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert json.loads(out)  # stdout is valid JSON plan output

    def test_timeout_exits_one_with_clean_message(
        self, tmp_path: Path, capsys: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # subprocess is an EXTERNAL dependency of main(); the unit under test is
        # main()'s exception handling. Force the real timeout that timeout=600
        # would raise on a hung validate_plugin.py.
        plugin = _minimal_plugin(tmp_path / "plug")

        def _boom(*_a: Any, **_k: Any) -> Any:
            raise subprocess.TimeoutExpired(cmd="validate_plugin.py", timeout=600)

        monkeypatch.setattr(planner.subprocess, "run", _boom)
        rc = planner.main([str(plugin), "--session-dir", str(tmp_path / "sess")])
        assert rc == 1
        err = capsys.readouterr().err
        assert "error:" in err
        assert "timed out" in err
        assert "Traceback" not in err

    def test_oserror_on_session_dir_exits_one(self, tmp_path: Path, capsys: Any) -> None:
        # Make the session-dir parent a FILE so mkdir raises NotADirectoryError
        # (an OSError subclass) — must surface as a clean error, not a traceback.
        plugin = _minimal_plugin(tmp_path / "plug")
        report = tmp_path / "report.json"
        _write_report(report, [{"level": "MAJOR", "message": "m", "file": "a.md", "line": 1}])
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")
        rc = planner.main(
            [str(plugin), "--report", str(report), "--session-dir", str(blocker / "sub")]
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "error:" in err
        assert "Traceback" not in err


# ---------------------------------------------------------------------------
# #5 — filter_findings must guard each item with isinstance(r, dict).
# ---------------------------------------------------------------------------


class TestFilterFindingsDictGuard:
    """Finding #5: a non-dict result element must not crash filter_findings."""

    def test_wellformed_findings_filter_correctly(self) -> None:
        results = [
            {"level": "CRITICAL", "message": "c", "file": "a.md", "line": 1},
            {"level": "NIT", "message": "n", "file": "a.md", "line": 2},
        ]
        out = planner.filter_findings(results, "minor")
        assert [f.level for f in out] == ["CRITICAL"]  # NIT dropped at minor floor

    def test_malformed_string_item_is_skipped_not_crash(self) -> None:
        # A hand-edited report with a bare string in the results list.
        results: list[Any] = [
            "this is not a dict",
            {"level": "MAJOR", "message": "m", "file": "a.md", "line": 1},
            42,
            None,
        ]
        out = planner.filter_findings(results, "minor")  # must NOT raise AttributeError
        assert len(out) == 1
        assert out[0].level == "MAJOR"


# ---------------------------------------------------------------------------
# #7 — derive_scope must normalise separators before splitting.
# ---------------------------------------------------------------------------


class TestDeriveScopeNormalisation:
    """Finding #7: skill scope detected cross-platform; non-skill → file."""

    def test_posix_skill_path_is_skill_scope(self) -> None:
        scope_path, kind = planner.derive_scope("skills/foo/SKILL.md")
        assert kind == planner.SCOPE_KIND_SKILL_DIR
        assert scope_path == "skills/foo/"

    def test_windows_skill_path_is_skill_scope(self) -> None:
        scope_path, kind = planner.derive_scope("skills\\foo\\SKILL.md")
        assert kind == planner.SCOPE_KIND_SKILL_DIR
        assert scope_path == "skills/foo/"

    def test_dot_slash_prefixed_skill_path_is_skill_scope(self) -> None:
        scope_path, kind = planner.derive_scope("./skills/foo/SKILL.md")
        assert kind == planner.SCOPE_KIND_SKILL_DIR
        assert scope_path == "skills/foo/"

    def test_non_skill_path_is_file_scope(self) -> None:
        # Two-sided: a path that is NOT a skill must stay file-scoped.
        scope_path, kind = planner.derive_scope("commands/foo.md")
        assert kind == planner.SCOPE_KIND_FILE
        assert scope_path == "commands/foo.md"

    def test_top_level_skills_file_is_file_scope(self) -> None:
        # "skills" with no second segment is not a skill dir.
        _scope_path, kind = planner.derive_scope("skills")
        assert kind == planner.SCOPE_KIND_FILE


# ---------------------------------------------------------------------------
# #6 — load_shard_status must tolerate non-numeric fields + garbage bytes.
# ---------------------------------------------------------------------------


class TestLoadShardStatusTolerance:
    """Finding #6: malformed status degrades to an error-carrying summary."""

    def test_valid_status_loads(self, tmp_path: Path) -> None:
        status = tmp_path / "shard-1.status.json"
        status.write_text(
            json.dumps(
                {
                    "shard_id": 1,
                    "fixed": 5,
                    "failed": 0,
                    "remaining": 0,
                    "agent_exit_reason": "clean",
                }
            )
        )
        summary = aggregator.load_shard_status(status, 1)
        assert summary.error is None
        assert summary.fixed == 5
        assert summary.agent_exit_reason == "clean"

    def test_nonnumeric_field_degrades_gracefully(self, tmp_path: Path) -> None:
        status = tmp_path / "shard-2.status.json"
        status.write_text(json.dumps({"shard_id": 2, "fixed": "oops", "failed": 0}))
        summary = aggregator.load_shard_status(status, 2)  # must NOT raise ValueError
        assert summary.error is not None
        assert "invalid field" in summary.error
        assert summary.shard_id == 2  # falls back to the index-provided id

    def test_null_field_degrades_gracefully(self, tmp_path: Path) -> None:
        # int(None) raises TypeError — must be caught too.
        status = tmp_path / "shard-3.status.json"
        status.write_text(json.dumps({"shard_id": 3, "remaining": None}))
        summary = aggregator.load_shard_status(status, 3)
        assert summary.error is not None
        assert summary.shard_id == 3

    def test_garbage_bytes_degrade_gracefully(self, tmp_path: Path) -> None:
        # Non-UTF-8 bytes → read_text() raises UnicodeDecodeError — must be caught.
        status = tmp_path / "shard-4.status.json"
        status.write_bytes(b"\xff\xfe\x00\x01garbage")
        summary = aggregator.load_shard_status(status, 4)  # must NOT raise
        assert summary.error is not None
        assert summary.shard_id == 4

    def test_missing_file_is_error(self, tmp_path: Path) -> None:
        summary = aggregator.load_shard_status(tmp_path / "nope.json", 9)
        assert summary.error is not None
        assert summary.shard_id == 9


# ---------------------------------------------------------------------------
# #13 — SCHEMA_VERSION is read; load_index warns on mismatch.
# ---------------------------------------------------------------------------


class TestAggregatorSchemaVersion:
    """Finding #13: SCHEMA_VERSION is consumed (matches producer + validated)."""

    def test_schema_version_matches_planner_producer(self) -> None:
        # The aggregator consumes the planner's index; the constants must agree.
        assert aggregator.SCHEMA_VERSION == planner.SCHEMA_VERSION

    def test_matching_index_loads_without_warning(self, tmp_path: Path, capsys: Any) -> None:
        sess = tmp_path / "sess"
        sess.mkdir()
        (sess / "index.json").write_text(
            json.dumps({"schema_version": aggregator.SCHEMA_VERSION, "shards": []})
        )
        aggregator.load_index(sess)
        assert "schema_version" not in capsys.readouterr().err

    def test_mismatched_index_warns(self, tmp_path: Path, capsys: Any) -> None:
        # Two-sided: a wrong/unknown schema version surfaces a warning (not silent).
        sess = tmp_path / "sess"
        sess.mkdir()
        (sess / "index.json").write_text(json.dumps({"schema_version": 999, "shards": []}))
        aggregator.load_index(sess)
        err = capsys.readouterr().err
        assert "warning:" in err
        assert "schema_version" in err


# ---------------------------------------------------------------------------
# #10 / #11 — timestamps use a single strftime("...%z") token (offset present).
# ---------------------------------------------------------------------------


class TestTimestampSingleStrftime:
    """Findings #10 & #11: filename/session timestamps carry a %z offset."""

    def test_planner_session_dir_has_offset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # #10 planner: default session dir name embeds a GMT offset.
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        sd = planner.make_session_dir(None)
        assert _TZ_OFFSET.search(sd.name), f"no GMT offset in {sd.name!r}"

    def test_aggregator_report_name_has_offset(self, tmp_path: Path) -> None:
        # #10 aggregator: default report filename embeds a GMT offset.
        sess = tmp_path / "sess"
        sess.mkdir()
        (sess / "index.json").write_text(
            json.dumps(
                {
                    "schema_version": aggregator.SCHEMA_VERSION,
                    "plugin_path": "/tmp/myplugin",
                    "shards": [],
                }
            )
        )
        out = aggregator.aggregate(sess, report_path=None)
        name = Path(out["report_path"]).name
        assert _TZ_OFFSET.search(name), f"no GMT offset in {name!r}"

    def test_orchestrator_session_dir_has_offset(self, tmp_path: Path) -> None:
        # #11 orchestrator: _new_session_dir name embeds a GMT offset.
        sd = orchestrator._new_session_dir("plugin-fixer", base=tmp_path)
        assert _TZ_OFFSET.search(sd.name), f"no GMT offset in {sd.name!r}"
        assert sd.name.endswith("-plugin-fixer")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
