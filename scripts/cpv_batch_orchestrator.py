#!/usr/bin/env python3
"""Batch-skill orchestrator (TRDD-3dcbb37c §2 + TRDD-4de479a0 Phase 1).

Given a list of ``ResolvedInput`` (the output of
``cpv_marketplace_input.resolve``), an ``agent_type``, and an
``agent_mode``, this module produces:

1. A **batch plan** JSON written to a session directory:
   ``${TMPDIR}/cpv-batch/<ts>-<agent>/plan.json``.
2. A **per-plugin status table** in the **claude-menu-system**
   spec shape (``spec_version: 1``, ``mode: "status_table"``,
   ``plugin: "cpv"``, ``slug``, ``row_header``, ``rows: [{label,
   status, notes}]``). The slash-command orchestrator hands this
   spec to ``cpv_menu.py`` (the CMS bridge), which queues the
   table for the CMS Stop hook to emit POST-TURN via
   ``systemMessage`` — zero token cost, no fork, no context
   pollution.
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

## CMS status_table mapping (TRDD-4de479a0)

CPV used to emit ``status_symbol`` (✓ ✗ ⚠ ◐ ○ ⊝) + a free-form
``status_label`` (clean / fixed / partial / failed / queued / ...).
CMS's ``status_table`` mode requires a fixed enum: ``ok``,
``implemented``, ``missing``, ``buggy``, ``partial``, ``pending``,
``skipped``, ``info``. We translate by symbol — the label is rolled
into the ``notes`` cell so no information is lost. The mapping
favours CMS's intent (``buggy`` for ⚠ "warning" reads more
correctly in a status table than ``info``):

    ✓ → ok        ✗ → missing       ⚠ → buggy
    ◐ → partial   ○ → pending       ⊝ → skipped

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


# CPV-symbol → CMS-enum mapping. CMS's ``status_table`` mode accepts ONLY
# these enum values, so legacy CPV ``status_symbol`` glyphs must be translated
# before the spec reaches ``menu_write.py``. See module docstring §"CMS
# status_table mapping" for the rationale.
_CMS_STATUS_FOR_SYMBOL: dict[str, str] = {
    "✓": "ok",
    "✗": "missing",
    "⚠": "buggy",
    "◐": "partial",
    "○": "pending",
    "⊝": "skipped",
}

# Friendly fallback when an agent writes an unrecognised symbol — keep the
# row visible but mark it ``info`` so CMS still renders it.
_CMS_DEFAULT_STATUS = "info"


def _cms_status(symbol: str) -> str:
    """Translate a CPV ``status_symbol`` glyph into a CMS ``status`` enum."""
    return _CMS_STATUS_FOR_SYMBOL.get(symbol, _CMS_DEFAULT_STATUS)


def _row_notes(
    label: str, plugin_kind: str, source_url: str | None, abs_path: str, extra_note: str | None = None
) -> str:
    """Build the ``notes`` cell text.

    CPV rolls the free-form ``status_label`` plus the kind plus the
    source (URL or path) plus any per-plugin agent-supplied note into a
    single cell, since CMS's status_table is a fixed three-column shape.
    """
    parts = [label, f"kind={plugin_kind}", source_url or abs_path]
    if extra_note:
        parts.append(str(extra_note))
    return " · ".join(p for p in parts if p)


def _cms_slug_for(agent_type: str) -> str:
    """Per-skill stable slug. Stable so the queue path is debuggable."""
    return f"batch-{agent_type}-status"


def status_table_json(
    plugins: Sequence[PluginEntry],
    title: str = "Plugins in this batch",
    initial_status: str = "○",
    initial_status_label: str = "queued",
    *,
    slug: str = "batch-status",
) -> dict[str, Any]:
    """Build a **claude-menu-system status_table spec** for ``cpv_menu.py``.

    Returned shape (CMS v0.1.5):
        {
          "spec_version": 1,
          "mode": "status_table",
          "plugin": "cpv",
          "slug": <slug>,
          "title": <title>,
          "row_header": "Plugin",
          "rows": [{"label", "status", "notes"}, ...]
        }

    The orchestrator MUST hand this dict (or its JSON) to
    ``cpv_menu.py`` (the CMS bridge), which queues it for the CMS Stop
    hook to emit at turn end via ``systemMessage`` — zero token cost,
    never enters the transcript.

    ``initial_status_label`` is a free-form CPV string (clean/queued/
    fixed/...) preserved in the ``notes`` cell since CMS uses a fixed
    enum for the ``status`` column.
    """
    rows = []
    for p in plugins:
        rows.append(
            {
                "label": p.display_name,
                "status": _cms_status(initial_status),
                "notes": _row_notes(initial_status_label, p.kind, p.source_url, p.abs_path),
            }
        )
    return {
        "spec_version": 1,
        "mode": "status_table",
        "plugin": "cpv",
        "slug": slug,
        "title": title,
        "row_header": "Plugin",
        "rows": rows,
    }


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
    # Include the GMT offset (%z) per the report-location timestamp rule so
    # the session dir name is unambiguous across machines / timezones, and
    # use a single strftime read so date-time and offset can't straddle midnight.
    ts = time.strftime("%Y%m%d_%H%M%S%z")
    base.mkdir(parents=True, exist_ok=True)
    # The timestamp is only second-granular, so two batches of the SAME agent
    # started within one wall-clock second would resolve to the same path.
    # With exist_ok=True that silently MERGES both batches into one dir — the
    # second plan.json overwrites the first and their per-plugin status files
    # intermix (data corruption). Claim the dir atomically with exist_ok=False
    # and, only on the rare collision, append a short numeric suffix so each
    # batch keeps a private session dir while the documented "<ts>-<agent>"
    # format is preserved in the common case.
    candidate = base / f"{ts}-{agent_type}"
    suffix = 1
    while True:
        try:
            candidate.mkdir(exist_ok=False)
            return candidate
        except FileExistsError:
            candidate = base / f"{ts}-{agent_type}-{suffix}"
            suffix += 1


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
        agent_type: target subagent type, e.g. ``cpv-plugin-validator-agent``,
            ``cpv-plugin-fixer-agent``, ``cpv-cache-optimizer-agent``, ``cpv-doctor-agent``.
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
    """Write the initial CMS status_table spec to ``status_table.json``.

    The file lives in the session dir alongside ``plan.json`` so the
    slash-command body (Step 1 of every batch command) can hand its
    path to ``cpv_menu.py`` without rebuilding the spec.
    """
    kwargs.setdefault("slug", _cms_slug_for(plan.agent_type))
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
    have written so far, and produce the current CMS status_table spec.

    Each agent writes its per-plugin status as
    ``<session_dir>/plugin-<plugin_index>.status.json`` with at least
    these keys::

        {
          "status_symbol": "✓" | "✗" | "⚠" | "◐" | "○" | "⊝",
          "status_label": "<short text>",
          "notes": "<optional additional text>"
        }

    (The agents still emit CPV's native ``status_symbol`` form — it's
    documented in every batch command's agent prompt. The orchestrator
    translates symbol→CMS-enum here so the agents need no awareness of
    the CMS spec.) Missing files for a plugin keep the initial queued
    state.

    Returns a full CMS ``status_table`` spec dict ready to hand to
    ``cpv_menu.write_menu()`` / the ``cpv_menu.py`` CLI.
    """
    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    session_dir = Path(plan_data["session_dir"])
    rows: list[dict[str, Any]] = []
    for p in plan_data["plugins"]:
        idx = p["plugin_index"]
        per_status = session_dir / f"plugin-{idx}.status.json"
        if per_status.is_file():
            try:
                d = json.loads(per_status.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                d = {}
            # A subagent's status file is untrusted: it may be valid JSON yet
            # not an object (``null``, a bare string, a list, a number — none
            # of which raise JSONDecodeError). The ``d.get(...)`` calls below
            # would then raise AttributeError and abort aggregation for the
            # WHOLE batch (every other plugin's row is lost). Treat any
            # non-dict payload as "no status yet", same as a missing/malformed
            # file, so one bad agent never breaks the rest of the table.
            if not isinstance(d, dict):
                d = {}
        else:
            d = {}
        symbol = d.get("status_symbol", initial_status)
        label = d.get("status_label", initial_status_label)
        extra_note = d.get("notes")
        rows.append(
            {
                "label": p["display_name"],
                "status": _cms_status(symbol),
                "notes": _row_notes(label, p["kind"], p.get("source_url"), p["abs_path"], extra_note),
            }
        )
    return {
        "spec_version": 1,
        "mode": "status_table",
        "plugin": "cpv",
        "slug": _cms_slug_for(plan_data["agent_type"]),
        "title": "Batch progress",
        "row_header": "Plugin",
        "rows": rows,
    }


def emit_status_table(plan_path: Path) -> Path:
    """Build the live CMS status_table spec and queue it via ``cpv_menu``.

    Programmatic counterpart to invoking ``cpv_menu.py`` from Bash. This
    is what ``cpv_batch_orchestrator.py emit-status`` calls (Step 3 in
    every batch command). The CMS Stop hook will emit the menu at the
    end of THIS turn via ``systemMessage`` (zero token cost). Returns
    the queue path that ``menu_write.py`` allocated.

    NEVER print the rendered table inline; the orchestrator's turn must
    END after this call so the Stop hook can fire. This is the single
    invariant of the TRDD-4de479a0 routing model.
    """
    # Local import — keeps ``cpv_menu`` (which requires CMS) optional for
    # the side-effect-free plan/aggregate functions above. Importing only
    # here means tests that exercise plan/aggregate don't need a CMS
    # install to pass.
    from cpv_menu import write_menu  # noqa: PLC0415

    spec = aggregate_status(plan_path)
    return write_menu(spec)


# ----------------------- CLI -------------------------------------------


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan a CPV batch dispatch from a list of inputs.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="Build plan.json + status_table.json")
    p_plan.add_argument("inputs", nargs="+", help="Inputs (paths/URLs/@listfile)")
    p_plan.add_argument(
        "--agent",
        required=True,
        help="Target subagent type (cpv-plugin-validator-agent/cpv-plugin-fixer-agent/...)",
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
        help="Re-aggregate per-plugin status JSONs into a CMS status_table spec",
    )
    p_status.add_argument("plan_path", type=Path, help="Path to plan.json")

    p_emit = sub.add_parser(
        "emit-status",
        help="Re-aggregate + hand the spec to cpv_menu (CMS Stop hook emits post-turn)",
    )
    p_emit.add_argument("plan_path", type=Path, help="Path to plan.json")

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

    if args.command == "emit-status":
        queue_path = emit_status_table(args.plan_path)
        print(queue_path)
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli())
