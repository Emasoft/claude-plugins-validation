#!/usr/bin/env python3
"""Batch-skill orchestrator (TRDD-3dcbb37c §2).

Given a list of ``ResolvedInput`` (the output of
``cpv_marketplace_input.resolve``), an ``agent_type``, and an
``agent_mode``, this module produces:

1. A **batch plan** JSON written to a session directory:
   ``${TMPDIR}/cpv-batch/<ts>-<agent>/plan.json``.
2. A **per-plugin status table** in the JSON shape that
   ``scripts/format_menu.py status_table`` consumes — so the
   slash-command orchestrator can print one Unicode-bordered table
   listing every plugin in the batch with its initial status.
3. A **shard grouping** — every batch with N plugins is split into
   ``ceil(N / max_parallel)`` groups so the main session can dispatch
   one batch group per Agent-tool message (Anthropic spec: only the
   main session can spawn parallel subagents).

This module is **side-effect-free** with respect to the work
itself: it does NOT spawn agents, does NOT modify any plugin's
files, does NOT call the validators. The actual ``Agent`` tool
dispatches happen in the slash-command body, which reads
``plan.json`` to know what to dispatch.

Default parallelism is 8 (matches ``cpv_batch_planner``). Cap is
16 — beyond that the message size for a single batch dispatch
exceeds practical context budgets.

Iron rule: zero LLM cost. Every step here is deterministic shell
+ JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

# Re-export so callers can use either module's ResolvedInput interchangeably.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_marketplace_input import ResolvedInput  # noqa: E402

SCHEMA_VERSION = 1
DEFAULT_MAX_PARALLEL = 8
MAX_PARALLEL_CAP = 16


@dataclass
class PluginEntry:
    """One plugin in the batch."""

    plugin_index: int
    display_name: str
    abs_path: str
    source_url: str | None
    kind: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchPlan:
    """The full plan written to ``plan.json``."""

    schema_version: int
    created_at: str
    session_dir: str
    agent_type: str
    agent_mode: str
    max_parallel: int
    plugin_count: int
    plugins: list[PluginEntry]
    dispatch_groups: list[list[int]]  # list of [plugin_index, ...]
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # asdict already recurses into PluginEntry dataclasses.
        return d


def shard_groups(n: int, max_parallel: int) -> list[list[int]]:
    """Split ``range(n)`` into groups of size ``max_parallel``.

    Returns a list of index lists. Empty list if ``n == 0``.
    """
    if n <= 0:
        return []
    cap = max(1, min(max_parallel, MAX_PARALLEL_CAP))
    return [list(range(i, min(i + cap, n))) for i in range(0, n, cap)]


def status_table_json(
    plugins: Sequence[PluginEntry],
    title: str = "Plugins in this batch",
    initial_status: str = "○",
    initial_status_label: str = "queued",
) -> dict[str, Any]:
    """Build the JSON dict ``format_menu.py status_table`` consumes."""
    rows = []
    for p in plugins:
        notes_parts = [f"kind={p.kind}"]
        if p.source_url:
            notes_parts.append(p.source_url)
        else:
            notes_parts.append(p.abs_path)
        rows.append(
            {
                "name": p.display_name,
                "status_symbol": initial_status,
                "status_label": initial_status_label,
                "notes": " · ".join(notes_parts),
            }
        )
    return {"title": title, "rows": rows}


def _build_plugins(resolved: Sequence[ResolvedInput]) -> list[PluginEntry]:
    out: list[PluginEntry] = []
    for i, ri in enumerate(resolved):
        out.append(
            PluginEntry(
                plugin_index=i,
                display_name=ri.display_name or Path(str(ri.abs_path)).name,
                abs_path=str(ri.abs_path),
                source_url=ri.source_url,
                kind=ri.kind,
                metadata=dict(ri.metadata),
            )
        )
    return out


def _new_session_dir(agent_type: str, base: Path | None = None) -> Path:
    if base is None:
        import tempfile
        base = Path(tempfile.gettempdir()) / "cpv-batch"
    ts = time.strftime("%Y%m%d_%H%M%S")
    sd = base / f"{ts}-{agent_type}"
    sd.mkdir(parents=True, exist_ok=True)
    return sd


def make_plan(
    resolved: Sequence[ResolvedInput],
    *,
    agent_type: str,
    agent_mode: str,
    max_parallel: int = DEFAULT_MAX_PARALLEL,
    session_dir: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> BatchPlan:
    """Build a ``BatchPlan`` for the given resolved inputs.

    Args:
        resolved: list of ``ResolvedInput`` (from ``cpv_marketplace_input.resolve``).
        agent_type: target subagent type, e.g. ``plugin-validator``,
            ``plugin-fixer``, ``cache-optimizer-agent``, ``cpv-doctor-agent``.
        agent_mode: per-agent mode keyword (e.g. ``batch_validate``,
            ``batch_security_audit``, ``batch_scope_diagnose``).
        max_parallel: maximum number of agents dispatched from one
            main-session message (default 8, capped at 16).
        session_dir: optional explicit session dir; default is a fresh
            ``${TMPDIR}/cpv-batch/<ts>-<agent>/``.
        extra: arbitrary additional context written into ``plan.json``
            under the ``extra`` key. Use for skill-specific options.
    """
    if session_dir is None:
        session_dir = _new_session_dir(agent_type)
    else:
        session_dir.mkdir(parents=True, exist_ok=True)
    plugins = _build_plugins(resolved)
    groups = shard_groups(len(plugins), max_parallel)
    return BatchPlan(
        schema_version=SCHEMA_VERSION,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        session_dir=str(session_dir),
        agent_type=agent_type,
        agent_mode=agent_mode,
        max_parallel=max(1, min(max_parallel, MAX_PARALLEL_CAP)),
        plugin_count=len(plugins),
        plugins=plugins,
        dispatch_groups=groups,
        extra=dict(extra or {}),
    )


def write_plan(plan: BatchPlan) -> Path:
    """Write ``plan.json`` into the session dir; return its path."""
    out = Path(plan.session_dir) / "plan.json"
    out.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    return out


def write_status_table(plan: BatchPlan, **kwargs: Any) -> Path:
    """Write the initial ``status_table.json`` (format_menu.py input)."""
    data = status_table_json(plan.plugins, **kwargs)
    out = Path(plan.session_dir) / "status_table.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


def aggregate_status(
    plan_path: Path,
    *,
    initial_status: str = "○",
    initial_status_label: str = "queued",
) -> dict[str, Any]:
    """Re-read a plan + every per-plugin status JSON the dispatched agents
    have written so far, and produce the current status_table dict.

    Each agent is expected to write its per-plugin status as
    ``<session_dir>/plugin-<plugin_index>.status.json`` with at least
    these keys::

        {
          "status_symbol": "✓" | "✗" | "⚠" | "◐" | "○" | "⊝",
          "status_label": "<short text>",
          "notes": "<optional additional text>"
        }

    Missing files for a plugin keep the initial queued state.
    """
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    session_dir = Path(plan_data["session_dir"])
    rows: list[dict[str, Any]] = []
    for p in plan_data["plugins"]:
        idx = p["plugin_index"]
        notes_parts = [f"kind={p['kind']}"]
        notes_parts.append(p["source_url"] or p["abs_path"])
        per_status = session_dir / f"plugin-{idx}.status.json"
        if per_status.is_file():
            try:
                d = json.loads(per_status.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                d = {}
        else:
            d = {}
        symbol = d.get("status_symbol", initial_status)
        label = d.get("status_label", initial_status_label)
        extra_note = d.get("notes")
        if extra_note:
            notes_parts.append(str(extra_note))
        rows.append(
            {
                "name": p["display_name"],
                "status_symbol": symbol,
                "status_label": label,
                "notes": " · ".join(notes_parts),
            }
        )
    return {"title": "Batch progress", "rows": rows}


# ----------------------- CLI -------------------------------------------


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan a CPV batch dispatch from a list of inputs."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="Build plan.json + status_table.json")
    p_plan.add_argument("inputs", nargs="+", help="Inputs (paths/URLs/@listfile)")
    p_plan.add_argument(
        "--agent",
        required=True,
        help="Target subagent type (plugin-validator/plugin-fixer/...)",
    )
    p_plan.add_argument(
        "--mode",
        required=True,
        help="Agent mode keyword (batch_validate/batch_security_audit/...)",
    )
    p_plan.add_argument(
        "--max-parallel",
        type=int,
        default=DEFAULT_MAX_PARALLEL,
        help=f"Max parallel agents per dispatch (default {DEFAULT_MAX_PARALLEL}, cap {MAX_PARALLEL_CAP})",
    )
    p_plan.add_argument(
        "--no-url",
        action="store_true",
        help="Reject URL inputs (used by scope-aware doctor skills)",
    )
    p_plan.add_argument(
        "--session-dir",
        type=Path,
        default=None,
        help="Override session dir (default ${TMPDIR}/cpv-batch/<ts>-<agent>/)",
    )

    p_status = sub.add_parser(
        "status",
        help="Re-aggregate per-plugin status JSONs into a status_table dict",
    )
    p_status.add_argument("plan_path", type=Path, help="Path to plan.json")

    args = parser.parse_args(argv)

    if args.command == "plan":
        from cpv_marketplace_input import resolve

        resolved_all: list[ResolvedInput] = []
        try:
            for spec in args.inputs:
                resolved_all.extend(resolve(spec, allow_url=not args.no_url))
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        plan = make_plan(
            resolved_all,
            agent_type=args.agent,
            agent_mode=args.mode,
            max_parallel=args.max_parallel,
            session_dir=args.session_dir,
        )
        plan_path = write_plan(plan)
        status_path = write_status_table(plan)
        print(f"PLAN: {plan_path}")
        print(f"STATUS_TABLE: {status_path}")
        print(f"SESSION_DIR: {plan.session_dir}")
        print(f"PLUGIN_COUNT: {plan.plugin_count}")
        print(f"DISPATCH_GROUPS: {len(plan.dispatch_groups)}")
        return 0

    if args.command == "status":
        data = aggregate_status(args.plan_path)
        print(json.dumps(data))
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli())
