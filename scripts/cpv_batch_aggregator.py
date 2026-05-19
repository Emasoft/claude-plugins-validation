#!/usr/bin/env python3
"""cpv_batch_aggregator — merge per-shard status files into one report.

Zero-LLM-cost aggregator that reads a batch session's ``index.json`` plus
every ``shard-K.status.json`` and writes:

  1. A consolidated markdown report under
     ``$MAIN_ROOT/reports/plugin-fixer/<ts±tz>-<plugin>-batch.md``.
  2. A short one-line summary printed to stdout, e.g.

     ``DONE: 327 → 0 (fixed: 327, failed: 0). Report: <abs-path>``

  3. Exit code 0 if all shards finished cleanly, 1 if any shard reported
     ``agent_exit_reason != "clean"`` or any remaining findings.

The slash-command orchestrator (``/cpv-batch-fix``) reads only the
summary line and the report path — it never pulls the report body into
main-session context.

Design — TRDD-71e68ab5.

Usage::

    python3 scripts/cpv_batch_aggregator.py <session-dir> [--report-path PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


@dataclass
class ShardSummary:
    shard_id: int
    fixed: int = 0
    failed: int = 0
    remaining: int = 0
    agent_exit_reason: str = "missing"
    started_at: str = ""
    finished_at: str = ""
    per_file: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None  # populated when status JSON is missing/malformed


def load_index(session_dir: Path) -> dict[str, Any]:
    index_path = session_dir / "index.json"
    if not index_path.exists():
        raise FileNotFoundError(f"index.json not found in session dir: {session_dir}")
    parsed: dict[str, Any] = json.loads(index_path.read_text())
    return parsed


def load_shard_status(status_path: Path, shard_id: int) -> ShardSummary:
    """Read one shard's status JSON; tolerate missing/malformed files."""
    if not status_path.exists():
        return ShardSummary(shard_id=shard_id, error=f"status file missing: {status_path}")
    try:
        data = json.loads(status_path.read_text())
    except json.JSONDecodeError as exc:
        return ShardSummary(shard_id=shard_id, error=f"status file unparseable: {exc}")
    if not isinstance(data, dict):
        return ShardSummary(shard_id=shard_id, error="status file is not a JSON object")
    return ShardSummary(
        shard_id=int(data.get("shard_id", shard_id)),
        fixed=int(data.get("fixed", 0)),
        failed=int(data.get("failed", 0)),
        remaining=int(data.get("remaining", 0)),
        agent_exit_reason=str(data.get("agent_exit_reason", "unknown")),
        started_at=str(data.get("started_at", "")),
        finished_at=str(data.get("finished_at", "")),
        per_file=list(data.get("per_file", [])) if isinstance(data.get("per_file"), list) else [],
    )


def resolve_main_root() -> Path:
    """Find the main repo root (worktree-safe)."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "worktree", "list"], capture_output=True, text=True, check=True, timeout=30
        )
        first = out.stdout.strip().splitlines()
        if first:
            return Path(first[0].split()[0])
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Fallback: assume the script lives under <root>/scripts/
    return Path(__file__).resolve().parent.parent


def render_report(
    index: dict[str, Any],
    shard_summaries: list[ShardSummary],
) -> str:
    """Render the consolidated markdown report body."""
    total_fixed = sum(s.fixed for s in shard_summaries)
    total_failed = sum(s.failed for s in shard_summaries)
    total_remaining = sum(s.remaining for s in shard_summaries)
    total_findings = int(index.get("total_findings", 0))
    counts = index.get("counts_by_severity", {})
    plugin_path = index.get("plugin_path", "(unknown)")
    report_source = index.get("report_source", "(unknown)")

    lines: list[str] = []
    lines.append("# Batch-fix consolidated report")
    lines.append("")
    lines.append(f"- **Plugin:** `{plugin_path}`")
    lines.append(f"- **Source report:** `{report_source}`")
    lines.append(f"- **Shards:** {index.get('shard_count', len(shard_summaries))}")
    lines.append(f"- **Total findings (planned):** {total_findings}")
    lines.append(
        f"- **Severity mix (planned):** "
        f"CRITICAL={counts.get('CRITICAL', 0)}, "
        f"MAJOR={counts.get('MAJOR', 0)}, "
        f"MINOR={counts.get('MINOR', 0)}, "
        f"NIT={counts.get('NIT', 0)}, "
        f"WARNING={counts.get('WARNING', 0)}"
    )
    lines.append("")
    lines.append("## Aggregate result")
    lines.append("")
    lines.append("| # | Metric | Count |")
    lines.append("|---|--------|-------|")
    lines.append(f"| 1 | Fixed | {total_fixed} |")
    lines.append(f"| 2 | Failed | {total_failed} |")
    lines.append(f"| 3 | Remaining | {total_remaining} |")
    lines.append("")

    lines.append("## Per-shard outcomes")
    lines.append("")
    lines.append("| # | Shard | Exit reason | Fixed | Failed | Remaining | Started | Finished |")
    lines.append("|---|-------|-------------|-------|--------|-----------|---------|----------|")
    for i, s in enumerate(shard_summaries, 1):
        exit_reason = s.error if s.error else s.agent_exit_reason
        lines.append(
            f"| {i} | {s.shard_id} | {exit_reason} | {s.fixed} | {s.failed} | "
            f"{s.remaining} | {s.started_at} | {s.finished_at} |"
        )
    lines.append("")

    failures = [s for s in shard_summaries if s.failed or s.remaining or s.error]
    if failures:
        lines.append("## Shards needing follow-up")
        lines.append("")
        for s in failures:
            lines.append(f"### Shard {s.shard_id}")
            lines.append("")
            if s.error:
                lines.append(f"- **Error:** {s.error}")
            lines.append(f"- **Exit reason:** {s.agent_exit_reason}")
            lines.append(f"- **Failed:** {s.failed}")
            lines.append(f"- **Remaining:** {s.remaining}")
            if s.per_file:
                lines.append("")
                lines.append("| # | File | Fixed | Remaining | Errors |")
                lines.append("|---|------|-------|-----------|--------|")
                for i, pf in enumerate(s.per_file, 1):
                    errs = pf.get("errors", [])
                    err_str = "; ".join(str(e) for e in errs) if errs else ""
                    lines.append(
                        f"| {i} | `{pf.get('path', '?')}` | {pf.get('fixed_count', 0)} | "
                        f"{pf.get('remaining_count', 0)} | {err_str} |"
                    )
            lines.append("")
    else:
        lines.append("All shards completed cleanly with zero remaining findings.")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def aggregate(session_dir: Path, report_path: Path | None = None) -> dict[str, Any]:
    """Top-level entry; returns a result dict for the CLI + tests."""
    if not session_dir.exists() or not session_dir.is_dir():
        raise FileNotFoundError(f"session dir not found: {session_dir}")

    index = load_index(session_dir)
    shards = index.get("shards", [])
    if not isinstance(shards, list):
        raise RuntimeError("index.json missing 'shards' list")

    shard_summaries = [
        load_shard_status(Path(s["status_path"]), int(s["shard_id"])) for s in shards
    ]

    body = render_report(index, shard_summaries)

    if report_path is None:
        main_root = resolve_main_root()
        report_dir = main_root / "reports" / "plugin-fixer"
        report_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S") + time.strftime("%z")
        plugin_basename = Path(index.get("plugin_path", "plugin")).name or "plugin"
        report_path = report_dir / f"{ts}-{plugin_basename}-batch.md"
    else:
        report_path.parent.mkdir(parents=True, exist_ok=True)

    report_path.write_text(body)

    total_fixed = sum(s.fixed for s in shard_summaries)
    total_failed = sum(s.failed for s in shard_summaries)
    total_remaining = sum(s.remaining for s in shard_summaries)
    all_clean = (
        all(s.agent_exit_reason == "clean" and not s.error for s in shard_summaries)
        and total_remaining == 0
        and total_failed == 0
    )

    return {
        "report_path": str(report_path),
        "shard_count": len(shard_summaries),
        "fixed": total_fixed,
        "failed": total_failed,
        "remaining": total_remaining,
        "all_clean": all_clean,
        "shard_summaries": [
            {
                "shard_id": s.shard_id,
                "fixed": s.fixed,
                "failed": s.failed,
                "remaining": s.remaining,
                "agent_exit_reason": s.agent_exit_reason,
                "error": s.error,
            }
            for s in shard_summaries
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge per-shard status files into a consolidated report.")
    parser.add_argument("session_dir", type=Path, help="Batch session directory containing index.json")
    parser.add_argument(
        "--report-path",
        type=Path,
        default=None,
        help="Explicit output path; default $MAIN_ROOT/reports/plugin-fixer/<ts>-<plugin>-batch.md",
    )
    args = parser.parse_args(argv)

    try:
        out = aggregate(args.session_dir, args.report_path)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    summary = (
        f"DONE: shards={out['shard_count']} fixed={out['fixed']} "
        f"failed={out['failed']} remaining={out['remaining']}. "
        f"Report: {out['report_path']}"
    )
    print(summary)
    return 0 if out["all_clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
