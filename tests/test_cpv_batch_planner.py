#!/usr/bin/env python3
"""Tests for ``scripts/cpv_batch_planner.py``.

Covers:
* ``filter_findings`` — severity floor, drop INFO/PASSED, drop file-less rows.
* ``group_by_file`` — one FileGroup per file; stable ordering.
* ``shard_groups`` — First-Fit Decreasing packing; oversized-file handling.
* ``plan`` — end-to-end with an existing JSON report; index + manifests.
* CLI exit code on missing plugin.

All tests use ``tmp_path`` and a pre-existing JSON validation report
(no live ``validate_plugin.py`` invocation).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import cpv_batch_planner as planner  # noqa: E402

# ---------------------------------------------------------------------------
# filter_findings
# ---------------------------------------------------------------------------


class TestFilterFindings:
    """Pinning the severity-floor + file-required behaviour."""

    def test_includes_critical_major_minor_at_minor_floor(self) -> None:
        results = [
            {"level": "CRITICAL", "message": "c", "file": "a.md", "line": 1},
            {"level": "MAJOR", "message": "m", "file": "a.md", "line": 2},
            {"level": "MINOR", "message": "n", "file": "a.md", "line": 3},
        ]
        out = planner.filter_findings(results, "minor")
        assert len(out) == 3

    def test_drops_nit_at_minor_floor(self) -> None:
        results = [
            {"level": "MAJOR", "message": "m", "file": "a.md", "line": 1},
            {"level": "NIT", "message": "nit", "file": "a.md", "line": 2},
        ]
        out = planner.filter_findings(results, "minor")
        assert len(out) == 1
        assert out[0].level == "MAJOR"

    def test_drops_warning_at_minor_floor(self) -> None:
        results = [
            {"level": "MAJOR", "message": "m", "file": "a.md", "line": 1},
            {"level": "WARNING", "message": "w", "file": "a.md", "line": 2},
        ]
        out = planner.filter_findings(results, "minor")
        assert len(out) == 1

    def test_drops_findings_with_no_file_ref(self) -> None:
        results = [
            {"level": "MAJOR", "message": "no file", "file": None, "line": None},
            {"level": "MAJOR", "message": "good", "file": "a.md", "line": 1},
        ]
        out = planner.filter_findings(results, "minor")
        assert len(out) == 1
        assert out[0].file == "a.md"

    def test_drops_info_and_passed(self) -> None:
        results = [
            {"level": "INFO", "message": "info", "file": "a.md", "line": 1},
            {"level": "PASSED", "message": "ok", "file": "a.md", "line": 1},
            {"level": "MAJOR", "message": "real", "file": "a.md", "line": 2},
        ]
        out = planner.filter_findings(results, "minor")
        assert len(out) == 1
        assert out[0].level == "MAJOR"

    def test_major_floor_drops_minor(self) -> None:
        results = [
            {"level": "MAJOR", "message": "m", "file": "a.md", "line": 1},
            {"level": "MINOR", "message": "n", "file": "a.md", "line": 2},
        ]
        out = planner.filter_findings(results, "major")
        assert len(out) == 1
        assert out[0].level == "MAJOR"

    def test_critical_floor_drops_major(self) -> None:
        results = [
            {"level": "CRITICAL", "message": "c", "file": "a.md", "line": 1},
            {"level": "MAJOR", "message": "m", "file": "a.md", "line": 2},
        ]
        out = planner.filter_findings(results, "critical")
        assert len(out) == 1
        assert out[0].level == "CRITICAL"


# ---------------------------------------------------------------------------
# group_by_file
# ---------------------------------------------------------------------------


class TestGroupByFile:
    """One FileGroup per distinct file path; stable ordering."""

    def test_one_group_per_file(self) -> None:
        findings = [
            planner.Finding("MAJOR", "m1", "a.md", 1),
            planner.Finding("MAJOR", "m2", "a.md", 2),
            planner.Finding("MAJOR", "m3", "b.md", 1),
        ]
        groups = planner.group_by_file(findings)
        assert len(groups) == 2
        a = next(g for g in groups if g.file_path == "a.md")
        b = next(g for g in groups if g.file_path == "b.md")
        assert a.count == 2
        assert b.count == 1

    def test_orders_by_descending_count_then_alpha(self) -> None:
        findings = [
            planner.Finding("MAJOR", "x", "z.md", 1),
            planner.Finding("MAJOR", "y", "a.md", 1),
            planner.Finding("MAJOR", "z", "a.md", 2),
        ]
        groups = planner.group_by_file(findings)
        # a.md has 2 findings, z.md has 1 → a.md first
        assert groups[0].file_path == "a.md"
        assert groups[1].file_path == "z.md"


# ---------------------------------------------------------------------------
# shard_groups
# ---------------------------------------------------------------------------


def _scope_file(path: str, n: int) -> "planner.Scope":
    """Test helper: build a file-scope (single .md outside skills/)."""
    return planner.Scope(
        scope_path=path,
        scope_kind=planner.SCOPE_KIND_FILE,
        findings=[planner.Finding("MAJOR", "m", path, j) for j in range(n)],
    )


def _scope_skill(skill_name: str, n: int) -> "planner.Scope":
    """Test helper: build a skill_dir scope spanning a whole skill."""
    return planner.Scope(
        scope_path=f"skills/{skill_name}/",
        scope_kind=planner.SCOPE_KIND_SKILL_DIR,
        findings=[planner.Finding("MAJOR", "m", f"skills/{skill_name}/SKILL.md", j) for j in range(n)],
    )


class TestShardGroups:
    """Packing scopes into shards of bounded size."""

    def test_single_scope_fits_in_one_shard(self) -> None:
        shards = planner.shard_groups([_scope_file("a.md", 5)], shard_size=30)
        assert len(shards) == 1
        assert shards[0].finding_count == 5

    def test_packs_multiple_small_scopes_into_one_shard(self) -> None:
        scopes = [_scope_file(f"f{i}.md", 1) for i in range(10)]
        shards = planner.shard_groups(scopes, shard_size=30)
        # 10 findings, shard_size 30 → 1 shard
        assert len(shards) == 1
        assert shards[0].finding_count == 10

    def test_splits_into_multiple_shards_when_over_size(self) -> None:
        scopes = [_scope_file(f"f{i}.md", 10) for i in range(10)]
        # 100 findings, shard_size 30 → 4 shards
        shards = planner.shard_groups(scopes, shard_size=30)
        assert len(shards) == 4
        total = sum(s.finding_count for s in shards)
        assert total == 100
        # Every shard except possibly the last should be at or near 30
        for s in shards[:-1]:
            assert s.finding_count <= 30

    def test_oversized_single_scope_gets_own_shard(self, capfd) -> None:
        # A single scope with 50 findings, shard_size 30
        big = _scope_file("huge.md", 50)
        small = _scope_file("small.md", 1)
        shards = planner.shard_groups([big, small], shard_size=30)
        # Two shards: oversized + normal
        assert len(shards) == 2
        # The oversized shard must contain the huge scope
        oversized = next(s for s in shards if any(sc.scope_path == "huge.md" for sc in s.scopes))
        assert oversized.finding_count == 50
        # Stderr warning printed
        captured = capfd.readouterr()
        assert "exceeds shard_size" in captured.err

    def test_one_scope_one_shard_invariant(self) -> None:
        """A scope must never be split across two shards (concurrent edits forbidden)."""
        scopes = [_scope_file(f"f{i}.md", 10) for i in range(20)]
        shards = planner.shard_groups(scopes, shard_size=30)
        # Build set of (scope, shard_id) — each scope should appear in only ONE shard
        scope_to_shard: dict[str, int] = {}
        for s in shards:
            for sc in s.scopes:
                assert sc.scope_path not in scope_to_shard, (
                    f"Scope {sc.scope_path} appears in shards {scope_to_shard[sc.scope_path]} and {s.shard_id}"
                )
                scope_to_shard[sc.scope_path] = s.shard_id

    def test_skill_dir_scopes_pack_alongside_file_scopes(self) -> None:
        """Skill-dir + file scopes share shards as long as they're disjoint."""
        scopes = [
            _scope_skill("foo", 10),
            _scope_skill("bar", 5),
            _scope_file("README.md", 3),
        ]
        shards = planner.shard_groups(scopes, shard_size=30)
        # All three fit in one shard (18 findings, room for 30)
        assert len(shards) == 1
        scope_paths = {sc.scope_path for sc in shards[0].scopes}
        assert scope_paths == {"skills/foo/", "skills/bar/", "README.md"}
        scope_kinds = {sc.scope_path: sc.scope_kind for sc in shards[0].scopes}
        assert scope_kinds["skills/foo/"] == planner.SCOPE_KIND_SKILL_DIR
        assert scope_kinds["README.md"] == planner.SCOPE_KIND_FILE


class TestDeriveScope:
    """Scope derivation rules (v2)."""

    def test_skill_findings_get_skill_dir_scope(self) -> None:
        scope_path, scope_kind = planner.derive_scope("skills/foo/SKILL.md")
        assert scope_path == "skills/foo/"
        assert scope_kind == planner.SCOPE_KIND_SKILL_DIR

    def test_skill_subdir_findings_get_same_skill_dir_scope(self) -> None:
        # Findings under references/ still belong to the parent skill scope
        scope_path, scope_kind = planner.derive_scope("skills/foo/references/X.md")
        assert scope_path == "skills/foo/"
        assert scope_kind == planner.SCOPE_KIND_SKILL_DIR

    def test_agent_findings_get_file_scope(self) -> None:
        scope_path, scope_kind = planner.derive_scope("agents/bar.md")
        assert scope_path == "agents/bar.md"
        assert scope_kind == planner.SCOPE_KIND_FILE

    def test_command_findings_get_file_scope(self) -> None:
        scope_path, scope_kind = planner.derive_scope("commands/baz.md")
        assert scope_path == "commands/baz.md"
        assert scope_kind == planner.SCOPE_KIND_FILE

    def test_root_findings_get_file_scope(self) -> None:
        scope_path, scope_kind = planner.derive_scope("README.md")
        assert scope_path == "README.md"
        assert scope_kind == planner.SCOPE_KIND_FILE


class TestGroupByScope:
    """Per-skill scope grouping unifies findings on different files inside the same skill."""

    def test_skill_findings_on_skill_md_and_references_collapse_to_one_scope(self) -> None:
        findings = [
            planner.Finding("MAJOR", "m1", "skills/foo/SKILL.md", 1),
            planner.Finding("MAJOR", "m2", "skills/foo/references/X.md", 1),
            planner.Finding("MAJOR", "m3", "skills/foo/SKILL.md", 5),
        ]
        scopes = planner.group_by_scope(findings)
        assert len(scopes) == 1
        assert scopes[0].scope_path == "skills/foo/"
        assert scopes[0].scope_kind == planner.SCOPE_KIND_SKILL_DIR
        assert scopes[0].count == 3

    def test_different_skills_get_different_scopes(self) -> None:
        findings = [
            planner.Finding("MAJOR", "m", "skills/foo/SKILL.md", 1),
            planner.Finding("MAJOR", "m", "skills/bar/SKILL.md", 1),
        ]
        scopes = planner.group_by_scope(findings)
        assert len(scopes) == 2
        paths = {sc.scope_path for sc in scopes}
        assert paths == {"skills/foo/", "skills/bar/"}

    def test_skills_and_agents_are_separate_scopes(self) -> None:
        findings = [
            planner.Finding("MAJOR", "m", "skills/foo/SKILL.md", 1),
            planner.Finding("MAJOR", "m", "agents/bar.md", 1),
        ]
        scopes = planner.group_by_scope(findings)
        assert len(scopes) == 2
        kinds = {sc.scope_kind for sc in scopes}
        assert kinds == {planner.SCOPE_KIND_SKILL_DIR, planner.SCOPE_KIND_FILE}


# ---------------------------------------------------------------------------
# count_by_severity
# ---------------------------------------------------------------------------


class TestCountBySeverity:
    def test_counts_each_level(self) -> None:
        findings = [
            planner.Finding("CRITICAL", "c", "a.md", 1),
            planner.Finding("MAJOR", "m1", "a.md", 2),
            planner.Finding("MAJOR", "m2", "b.md", 1),
            planner.Finding("MINOR", "n", "a.md", 3),
        ]
        counts = planner.count_by_severity(findings)
        assert counts == {"CRITICAL": 1, "MAJOR": 2, "MINOR": 1, "NIT": 0, "WARNING": 0}


# ---------------------------------------------------------------------------
# plan() end-to-end with a pre-existing report
# ---------------------------------------------------------------------------


class TestPlanEndToEnd:
    """End-to-end: write a fake report, run plan(), verify outputs."""

    def _write_report(self, path: Path, results: list[dict]) -> None:
        path.write_text(json.dumps({"exit_code": 1, "counts": {}, "results": results}))

    def test_plan_writes_index_and_manifests(self, tmp_path: Path) -> None:
        plugin = tmp_path / "myplugin"
        plugin.mkdir()
        report = tmp_path / "report.json"
        results = [
            {"level": "MAJOR", "message": f"m{i}", "file": f"skills/s{i}/SKILL.md", "line": 1} for i in range(50)
        ]
        self._write_report(report, results)
        session = tmp_path / "session"
        out = planner.plan(
            plugin,
            shard_size=20,
            max_parallel=4,
            min_severity="minor",
            session_dir=session,
            report_path=report,
        )
        # 50 findings, shard 20 → 3 shards (20 + 20 + 10)
        # but each file has 1 finding so they pack to exactly 20-per-shard
        assert out["total_findings"] == 50
        assert out["shard_count"] == 3
        assert (session / "index.json").exists()
        for i in range(1, out["shard_count"] + 1):
            assert (session / f"shard-{i}.json").exists()

        # index has the right shard count and counts
        idx = json.loads((session / "index.json").read_text())
        assert idx["shard_count"] == 3
        assert idx["counts_by_severity"]["MAJOR"] == 50

    def test_plan_handles_zero_findings(self, tmp_path: Path) -> None:
        plugin = tmp_path / "myplugin"
        plugin.mkdir()
        report = tmp_path / "report.json"
        self._write_report(report, [])
        session = tmp_path / "session"
        out = planner.plan(
            plugin,
            shard_size=30,
            max_parallel=8,
            min_severity="minor",
            session_dir=session,
            report_path=report,
        )
        assert out["shard_count"] == 0
        assert out["total_findings"] == 0
        # Index file still exists (caller may want it for telemetry)
        assert (session / "index.json").exists()

    def test_plan_rejects_invalid_shard_size(self, tmp_path: Path) -> None:
        plugin = tmp_path / "myplugin"
        plugin.mkdir()
        report = tmp_path / "report.json"
        self._write_report(report, [])
        try:
            planner.plan(
                plugin,
                shard_size=0,
                max_parallel=4,
                min_severity="minor",
                session_dir=tmp_path / "s",
                report_path=report,
            )
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "shard_size" in str(exc)

    def test_plan_rejects_invalid_max_parallel(self, tmp_path: Path) -> None:
        plugin = tmp_path / "myplugin"
        plugin.mkdir()
        report = tmp_path / "report.json"
        self._write_report(report, [])
        try:
            planner.plan(
                plugin,
                shard_size=30,
                max_parallel=99,
                min_severity="minor",
                session_dir=tmp_path / "s",
                report_path=report,
            )
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "max_parallel" in str(exc)


# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_exits_nonzero_on_missing_plugin(self, tmp_path: Path) -> None:
        script = Path(__file__).parent.parent / "scripts" / "cpv_batch_planner.py"
        result = subprocess.run(
            [sys.executable, str(script), str(tmp_path / "does-not-exist")],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 1
        assert "error" in result.stderr.lower()

    def test_cli_succeeds_with_empty_report(self, tmp_path: Path) -> None:
        plugin = tmp_path / "myplugin"
        plugin.mkdir()
        report = tmp_path / "report.json"
        report.write_text(json.dumps({"exit_code": 0, "counts": {}, "results": []}))
        session = tmp_path / "session"
        script = Path(__file__).parent.parent / "scripts" / "cpv_batch_planner.py"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                str(plugin),
                "--report",
                str(report),
                "--session-dir",
                str(session),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["shard_count"] == 0


# ---------------------------------------------------------------------------
# _parse_validate_plugin_json
# ---------------------------------------------------------------------------


class TestParseValidatePluginJson:
    def test_strips_lint_banners_before_json(self) -> None:
        mixed = (
            "═══ [REPO LINT] starting ═══\n"
            "  some banner output\n"
            '{\n  "exit_code": 0,\n  "counts": {},\n  "results": []\n}\n'
        )
        out = planner._parse_validate_plugin_json(mixed)
        assert out["exit_code"] == 0

    def test_raises_on_missing_marker(self) -> None:
        try:
            planner._parse_validate_plugin_json("just banner output no json here")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "exit_code" in str(exc)
