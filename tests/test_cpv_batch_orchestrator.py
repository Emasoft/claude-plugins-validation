#!/usr/bin/env python3
"""Unit tests for ``scripts/cpv_batch_orchestrator.py`` (TRDD-3dcbb37c §2).

The orchestrator is side-effect-free with respect to subagent
dispatch: it just turns a list of ``ResolvedInput`` into a JSON
``plan.json`` + ``status_table.json`` the slash-command body reads
to know what to fan out and how to render the per-plugin status
table.

Test classes:

1. ``TestShardGroups`` — the pure ``shard_groups()`` helper.
2. ``TestMakePlan`` — building a ``BatchPlan`` from ``ResolvedInput``.
3. ``TestStatusTableJson`` — the rows emitted for ``format_menu.py status_table``.
4. ``TestWritePlan`` — JSON round-trip of plan.json and status_table.json.
5. ``TestAggregateStatus`` — re-reading per-plugin status files merges
   into a current status_table.
6. ``TestCli`` — end-to-end CLI smoke (uses ``Resolved`` inputs from
   a tmp marketplace fixture).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_batch_orchestrator import (  # noqa: E402
    DEFAULT_MAX_PARALLEL,
    MAX_PARALLEL_CAP,
    BatchPlan,
    PluginEntry,
    aggregate_status,
    make_plan,
    shard_groups,
    status_table_json,
    write_plan,
    write_status_table,
)
from cpv_marketplace_input import ResolvedInput  # noqa: E402


def _ri(name: str, abs_path: Path, kind: str = "plugin", source_url: str | None = None) -> ResolvedInput:
    return ResolvedInput(
        kind=kind,  # type: ignore[arg-type]
        abs_path=abs_path,
        source_url=source_url,
        display_name=name,
    )


# ----------------------- 1. shard_groups ---------------------------------


class TestShardGroups:
    def test_zero_inputs_returns_empty(self) -> None:
        assert shard_groups(0, max_parallel=8) == []

    def test_single_input_one_group(self) -> None:
        assert shard_groups(1, max_parallel=8) == [[0]]

    def test_eight_inputs_one_group(self) -> None:
        assert shard_groups(8, max_parallel=8) == [list(range(8))]

    def test_seventeen_inputs_three_groups_of_eight(self) -> None:
        groups = shard_groups(17, max_parallel=8)
        assert len(groups) == 3
        assert groups[0] == list(range(0, 8))
        assert groups[1] == list(range(8, 16))
        assert groups[2] == [16]

    def test_max_parallel_above_cap_is_clamped(self) -> None:
        groups = shard_groups(17, max_parallel=100)
        assert len(groups) == 2  # capped to 16
        assert groups[0] == list(range(0, MAX_PARALLEL_CAP))
        assert groups[1] == [MAX_PARALLEL_CAP]

    def test_max_parallel_minimum_one(self) -> None:
        assert shard_groups(3, max_parallel=0) == [[0], [1], [2]]

    def test_every_index_appears_exactly_once(self) -> None:
        groups = shard_groups(45, max_parallel=8)
        flat = [i for g in groups for i in g]
        assert flat == list(range(45))


# ----------------------- 2. make_plan ------------------------------------


class TestMakePlan:
    def test_empty_input_produces_zero_plugin_plan(self, tmp_path: Path) -> None:
        plan = make_plan(
            [],
            agent_type="plugin-validator",
            agent_mode="batch_validate",
            session_dir=tmp_path / "sd",
        )
        assert isinstance(plan, BatchPlan)
        assert plan.plugin_count == 0
        assert plan.plugins == []
        assert plan.dispatch_groups == []
        assert plan.agent_type == "plugin-validator"
        assert plan.agent_mode == "batch_validate"

    def test_three_plugins_one_group(self, tmp_path: Path) -> None:
        plugins = [
            _ri("plug-a", tmp_path / "a", "plugin"),
            _ri("plug-b", tmp_path / "b", "plugin"),
            _ri("plug-c", tmp_path / "c", "plugin"),
        ]
        plan = make_plan(
            plugins,
            agent_type="plugin-fixer",
            agent_mode="batch_validate_and_fix",
            session_dir=tmp_path / "sd",
        )
        assert plan.plugin_count == 3
        assert plan.dispatch_groups == [[0, 1, 2]]
        assert [p.display_name for p in plan.plugins] == ["plug-a", "plug-b", "plug-c"]
        assert [p.plugin_index for p in plan.plugins] == [0, 1, 2]

    def test_seventeen_plugins_three_groups(self, tmp_path: Path) -> None:
        plugins = [_ri(f"plug-{i}", tmp_path / f"p{i}") for i in range(17)]
        plan = make_plan(
            plugins,
            agent_type="plugin-validator",
            agent_mode="batch_validate",
            session_dir=tmp_path / "sd",
        )
        assert plan.plugin_count == 17
        assert len(plan.dispatch_groups) == 3
        assert plan.dispatch_groups[-1] == [16]

    def test_metadata_is_forwarded(self, tmp_path: Path) -> None:
        ri = ResolvedInput(
            kind="plugin",
            abs_path=tmp_path / "p",
            display_name="meta-plug",
            metadata={"plugin_version": "0.4.13"},
        )
        plan = make_plan(
            [ri],
            agent_type="cache-optimizer-agent",
            agent_mode="batch_audit",
            session_dir=tmp_path / "sd",
        )
        assert plan.plugins[0].metadata["plugin_version"] == "0.4.13"

    def test_extra_field_is_stored(self, tmp_path: Path) -> None:
        plan = make_plan(
            [],
            agent_type="cpv-doctor-agent",
            agent_mode="batch_scope_diagnose",
            session_dir=tmp_path / "sd",
            extra={"scope": "full"},
        )
        assert plan.extra == {"scope": "full"}

    def test_max_parallel_clamped(self, tmp_path: Path) -> None:
        plan = make_plan(
            [],
            agent_type="x",
            agent_mode="y",
            max_parallel=999,
            session_dir=tmp_path / "sd",
        )
        assert plan.max_parallel == MAX_PARALLEL_CAP

    def test_default_max_parallel(self, tmp_path: Path) -> None:
        plan = make_plan(
            [],
            agent_type="x",
            agent_mode="y",
            session_dir=tmp_path / "sd",
        )
        assert plan.max_parallel == DEFAULT_MAX_PARALLEL


# ----------------------- 3. status_table_json ----------------------------


class TestStatusTableJson:
    def test_local_plugin_row_shows_path(self, tmp_path: Path) -> None:
        entries = [
            PluginEntry(
                plugin_index=0,
                display_name="local-plug",
                abs_path=str(tmp_path / "local"),
                source_url=None,
                kind="plugin",
            )
        ]
        out = status_table_json(entries)
        assert out["rows"][0]["status_symbol"] == "○"
        assert out["rows"][0]["status_label"] == "queued"
        assert "local" in out["rows"][0]["notes"]
        assert "kind=plugin" in out["rows"][0]["notes"]

    def test_remote_plugin_row_shows_url(self) -> None:
        entries = [
            PluginEntry(
                plugin_index=0,
                display_name="remote-plug",
                abs_path="/tmp/clone/remote-plug",
                source_url="https://github.com/Emasoft/remote-plug",
                kind="plugin",
            )
        ]
        out = status_table_json(entries)
        assert "https://github.com/Emasoft/remote-plug" in out["rows"][0]["notes"]

    def test_initial_status_override(self) -> None:
        entries = [
            PluginEntry(0, "p", "/p", None, "plugin"),
        ]
        out = status_table_json(entries, initial_status="◐", initial_status_label="working")
        assert out["rows"][0]["status_symbol"] == "◐"
        assert out["rows"][0]["status_label"] == "working"


# ----------------------- 4. write_plan + write_status_table --------------


class TestWritePlan:
    def test_plan_json_round_trip(self, tmp_path: Path) -> None:
        plugins = [_ri(f"p{i}", tmp_path / f"p{i}") for i in range(3)]
        plan = make_plan(
            plugins,
            agent_type="plugin-validator",
            agent_mode="batch_validate",
            session_dir=tmp_path / "sd",
        )
        plan_path = write_plan(plan)
        assert plan_path.is_file()
        loaded = json.loads(plan_path.read_text(encoding="utf-8"))
        assert loaded["agent_type"] == "plugin-validator"
        assert loaded["agent_mode"] == "batch_validate"
        assert loaded["plugin_count"] == 3
        assert loaded["dispatch_groups"] == [[0, 1, 2]]
        assert [p["display_name"] for p in loaded["plugins"]] == ["p0", "p1", "p2"]

    def test_status_table_json_written(self, tmp_path: Path) -> None:
        plugins = [_ri("x", tmp_path / "x")]
        plan = make_plan(
            plugins, agent_type="x", agent_mode="y",
            session_dir=tmp_path / "sd",
        )
        path = write_status_table(plan)
        assert path.is_file()
        data = json.loads(path.read_text())
        assert data["rows"][0]["name"] == "x"


# ----------------------- 5. aggregate_status -----------------------------


class TestAggregateStatus:
    def test_no_per_plugin_files_returns_all_queued(self, tmp_path: Path) -> None:
        plan = make_plan(
            [_ri("a", tmp_path / "a"), _ri("b", tmp_path / "b")],
            agent_type="plugin-validator",
            agent_mode="batch_validate",
            session_dir=tmp_path / "sd",
        )
        plan_path = write_plan(plan)
        data = aggregate_status(plan_path)
        assert {r["status_label"] for r in data["rows"]} == {"queued"}
        assert {r["status_symbol"] for r in data["rows"]} == {"○"}

    def test_per_plugin_status_overrides_default(self, tmp_path: Path) -> None:
        sd = tmp_path / "sd"
        plan = make_plan(
            [_ri("a", tmp_path / "a"), _ri("b", tmp_path / "b")],
            agent_type="plugin-validator",
            agent_mode="batch_validate",
            session_dir=sd,
        )
        plan_path = write_plan(plan)
        (sd / "plugin-0.status.json").write_text(
            json.dumps(
                {"status_symbol": "✓", "status_label": "clean", "notes": "0/0/0/0"}
            )
        )
        (sd / "plugin-1.status.json").write_text(
            json.dumps(
                {"status_symbol": "✗", "status_label": "failed", "notes": "3 CRITICAL"}
            )
        )
        data = aggregate_status(plan_path)
        symbols = [r["status_symbol"] for r in data["rows"]]
        labels = [r["status_label"] for r in data["rows"]]
        assert symbols == ["✓", "✗"]
        assert labels == ["clean", "failed"]
        assert "0/0/0/0" in data["rows"][0]["notes"]
        assert "3 CRITICAL" in data["rows"][1]["notes"]

    def test_malformed_status_json_falls_back_to_queued(self, tmp_path: Path) -> None:
        sd = tmp_path / "sd"
        plan = make_plan(
            [_ri("a", tmp_path / "a")],
            agent_type="x", agent_mode="y", session_dir=sd,
        )
        plan_path = write_plan(plan)
        (sd / "plugin-0.status.json").write_text("not json at all")
        data = aggregate_status(plan_path)
        assert data["rows"][0]["status_label"] == "queued"


# ----------------------- 6. CLI smoke ------------------------------------


def _make_plugin(root: Path, name: str = "demo-plugin") -> Path:
    plugin_dir = root / name
    (plugin_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "0.1.0"})
    )
    return plugin_dir


class TestCli:
    def test_plan_subcommand_round_trip(self, tmp_path: Path) -> None:
        p1 = _make_plugin(tmp_path, "plug-a")
        p2 = _make_plugin(tmp_path, "plug-b")
        sd = tmp_path / "session"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "cpv_batch_orchestrator.py"),
                "plan",
                str(p1),
                str(p2),
                "--agent", "plugin-validator",
                "--mode", "batch_validate",
                "--session-dir", str(sd),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "PLAN:" in result.stdout
        assert "PLUGIN_COUNT: 2" in result.stdout
        plan_data = json.loads((sd / "plan.json").read_text())
        assert plan_data["plugin_count"] == 2
        assert plan_data["agent_type"] == "plugin-validator"

    def test_status_subcommand_reads_per_plugin_files(self, tmp_path: Path) -> None:
        p1 = _make_plugin(tmp_path, "plug-x")
        sd = tmp_path / "session"
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "cpv_batch_orchestrator.py"),
                "plan",
                str(p1),
                "--agent", "plugin-validator",
                "--mode", "batch_validate",
                "--session-dir", str(sd),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        # Simulate the dispatched agent writing its per-plugin status.
        (sd / "plugin-0.status.json").write_text(
            json.dumps(
                {"status_symbol": "✓", "status_label": "validated", "notes": "0/0/0/0"}
            )
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "cpv_batch_orchestrator.py"),
                "status",
                str(sd / "plan.json"),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        data = json.loads(result.stdout)
        assert data["rows"][0]["status_symbol"] == "✓"
        assert data["rows"][0]["status_label"] == "validated"

    def test_no_url_flag_rejects_url(self, tmp_path: Path) -> None:
        sd = tmp_path / "sd"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "cpv_batch_orchestrator.py"),
                "plan",
                "https://github.com/owner/plugin",
                "--agent", "cpv-doctor-agent",
                "--mode", "batch_scope_diagnose",
                "--no-url",
                "--session-dir", str(sd),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1
        assert "not allowed" in result.stderr
