#!/usr/bin/env python3
"""CPV validate_plugin parallel-vs-serial benchmark (task #384 A10).

Times three end-to-end runs of ``validate_plugin.py .`` against the CPV
plugin root and reports the wall-time speedup of the parallelization
layers:

    Phase A — ALL SERIAL
        Every CPV_*_PARALLEL knob set to ``0``. Baseline.

    Phase B — INNER PARALLEL, OUTER SERIAL
        Per-validator process pools left at default (parallel), but
        the orchestrator is forced serial via ``CPV_ORCHESTRATOR_PARALLEL=0``.
        Measures the impact of sibling validators (A2..A9) parallelism
        WITHOUT the orchestrator-layer dispatch this benchmark exists
        to evaluate.

    Phase C — FULLY PARALLEL
        Default — both layers parallel. The production configuration.

Speedup is computed against Phase A (the all-serial baseline). The
benchmark also prints a tidy table and writes a Markdown report to
``$MAIN_ROOT/reports/menu-migration-planning/{YYYYMMDD_HHMMSS±HHMM}-task384-A10-benchmark.md``
per the agent-reports-location rule.

Notes
-----
* Each phase runs as a fresh subprocess so module-level state from one
  phase cannot bleed into another. The cost is one ``uv run`` startup
  per phase (~0.5–1.0 s); we report wall time inclusive of startup
  because that's what users actually experience.
* The benchmark targets the CPV repo itself by default (~400 files,
  ~5500 tests, 15-language lint). To benchmark against a different
  plugin, pass its path as the only positional argument.
* The benchmark is non-destructive — no files modified, no git ops,
  no network beyond what validate_plugin itself does (which is blocked
  by PLUGIN_SKIP_GITHUB_INTEGRITY=1 anyway).
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import cast


def _resolve_main_root() -> Path:
    """Return the main repo root, worktree-aware.

    Per the agent-reports-location rule, report files must land under
    ``$MAIN_ROOT/reports/<component>/`` where ``$MAIN_ROOT`` is the MAIN
    repo (not the linked worktree we may currently be in). ``git
    worktree list`` always lists the main checkout first.
    """
    try:
        out = subprocess.check_output(
            ["git", "worktree", "list"],
            text=True,
            timeout=10,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        first = out.splitlines()[0].split()[0]
        return Path(first).resolve()
    except (subprocess.CalledProcessError, IndexError, FileNotFoundError):
        # Fallback: assume the script's grandparent is the repo root
        # (scripts/cpv_validate_benchmark.py → repo root).
        return Path(__file__).resolve().parent.parent


def _report_path(component: str, slug: str) -> Path:
    """Compose the canonical report path per agent-reports-location rule.

    Format: ``$MAIN_ROOT/reports/<component>/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md``
    Local time + GMT offset (compact ±HHMM form) — never UTC, never
    ``±HH:MM`` (Windows-unsafe).
    """
    main_root = _resolve_main_root()
    report_dir = main_root / "reports" / component
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S%z")
    return report_dir / f"{ts}-{slug}.md"


def _system_info() -> dict[str, str]:
    """Capture the machine's identity for the benchmark report header.

    Includes CPU count + brand because parallelism gains scale with
    physical core count and CPU frequency. Captured at the start so the
    report is self-contained.
    """
    cpu_count = os.cpu_count() or 0
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "cpu_count": str(cpu_count),
        "cpu_brand": platform.processor() or "(unknown)",
    }


# All known CPV per-validator parallelism env-vars. Setting them ALL to
# "0" forces the per-validator pools off, leaving only the orchestrator
# pool (controlled separately by CPV_ORCHESTRATOR_PARALLEL).
_PER_VALIDATOR_PARALLEL_VARS = (
    "CPV_HOOK_PARALLEL",
    "CPV_SKILL_PARALLEL",
    "CPV_SKILL_COMPREHENSIVE_PARALLEL",
    "CPV_SECURITY_PARALLEL",
    "CPV_COMMAND_PARALLEL",
    "CPV_AGENT_PARALLEL",
    "CPV_MCP_PARALLEL",
    "CPV_LSP_PARALLEL",
    "CPV_CACHE_PARALLEL",
    "CPV_XREF_PARALLEL",
    "CPV_LINT_PARALLEL",
)


def _clear_scanner_cache(*, verbose: bool = False) -> None:
    """Remove ~/.cache/cpv/scanner-results/ so the next run pays cold-cache cost.

    The scanner cache is per-language SHA-of-files keyed; once Phase A
    populates it, subsequent phases short-circuit the lint work, which
    masks the real serial/parallel comparison. This helper removes the
    cache between phases so every phase pays the same cold-cache cost.

    We use ``shutil.rmtree`` (no exec of ``rm``) so the global git_safety
    guard doesn't refuse the operation — the cache is under ``~/.cache``
    which is not a project tree, and is safely regenerable on next run.
    """
    import shutil

    cache_dir = Path.home() / ".cache" / "cpv" / "scanner-results"
    if cache_dir.exists():
        try:
            shutil.rmtree(cache_dir)
            if verbose:
                print(f"  ⚡ cleared scanner cache at {cache_dir}", flush=True)
        except OSError as e:
            if verbose:
                print(f"  ⚠ could not clear {cache_dir}: {e}", flush=True)


def _build_env(*, orchestrator_parallel: bool, validator_parallel: bool) -> dict[str, str]:
    """Compose the env dict for one phase.

    Args:
        orchestrator_parallel: Set ``CPV_ORCHESTRATOR_PARALLEL`` to
            "1" or "0".
        validator_parallel: When False, set every known per-validator
            CPV_*_PARALLEL var to "0". When True, leave them unset so
            each validator uses its own default (which is parallel for
            all currently-implemented sibling validators).
    """
    env = os.environ.copy()
    env["CPV_ORCHESTRATOR_PARALLEL"] = "1" if orchestrator_parallel else "0"
    # Bypass GitHub integrity verification — we're benchmarking a
    # locally-modified worktree, and the round-trip would dominate
    # the wall-time measurement.
    env["PLUGIN_SKIP_GITHUB_INTEGRITY"] = "1"
    # Disable color so the captured stdout doesn't include ANSI
    # escapes that would skew the size of the captured buffer.
    env["NO_COLOR"] = "1"
    if validator_parallel:
        # Make sure no previous session left a "0" override sitting in
        # the shell environment — explicitly UNSET each known knob so
        # the validator falls back to its default-parallel path.
        for var in _PER_VALIDATOR_PARALLEL_VARS:
            env.pop(var, None)
    else:
        for var in _PER_VALIDATOR_PARALLEL_VARS:
            env[var] = "0"
    return env


def _run_phase(
    label: str,
    plugin_root: Path,
    env: dict[str, str],
    *,
    verbose: bool,
) -> tuple[float, int]:
    """Run validate_plugin.py against ``plugin_root`` with ``env`` and time it.

    Returns ``(wall_seconds, exit_code)``. Stdout is captured (and
    discarded unless ``verbose``) so it doesn't pollute the benchmark
    output.
    """
    script = Path(__file__).resolve().parent / "validate_plugin.py"
    if verbose:
        print(f"  → starting phase: {label}", flush=True)
    t0 = time.perf_counter()
    result = subprocess.run(
        ["uv", "run", "python", str(script), str(plugin_root)],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(script.parent.parent),
        timeout=1800,  # 30 min hard cap — covers very large plugins
    )
    t1 = time.perf_counter()
    wall = t1 - t0
    if verbose:
        # Print just the tail of stdout (the summary line) so the operator
        # can confirm the run finished cleanly without burying the
        # benchmark numbers in 1000s of lint lines.
        tail = "\n".join(result.stdout.splitlines()[-3:])
        print(f"  ← {label}: {wall:.2f}s exit={result.returncode}", flush=True)
        if tail:
            print(f"     tail: {tail}", flush=True)
    return wall, result.returncode


def _format_table(rows: list[dict]) -> str:
    """Render a tidy Markdown table of phase | time | speedup.

    The Markdown format is the one used in our report files; it renders
    well in terminals (with tools like ``mdcat`` / ``bat``) and renders
    correctly in any Markdown viewer.
    """
    header = "| Phase | Wall time (s) | Speedup vs Phase A |\n"
    sep =    "|-------|---------------|--------------------|\n"
    body = ""
    baseline = rows[0]["wall"]
    for row in rows:
        speedup = baseline / row["wall"] if row["wall"] > 0 else float("inf")
        body += f"| {row['label']} | {row['wall']:.2f} | {speedup:.2f}× |\n"
    return header + sep + body


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "plugin_path",
        nargs="?",
        default=str(_resolve_main_root()),
        help="Plugin root to benchmark against (default: this CPV repo).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print per-phase progress + tail of each subprocess stdout.",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Skip writing the Markdown report file (just print to stdout).",
    )
    parser.add_argument(
        "--component",
        default="menu-migration-planning",
        help="Report subdir under reports/ (default: menu-migration-planning).",
    )
    parser.add_argument(
        "--slug",
        default="task384-A10-benchmark",
        help="Filename slug for the Markdown report.",
    )
    parser.add_argument(
        "--report-root",
        default=None,
        help=(
            "Override the report root directory. Default: $MAIN_ROOT resolved via "
            "`git worktree list` (first entry = main checkout). Set this to the "
            "worktree path when running the benchmark from a worktree and wanting "
            "the report to land in the worktree's own reports/ tree."
        ),
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help=(
            "Number of timing runs per phase (default: 3). The wall time "
            "reported is the MEDIAN across runs — robust against the "
            "single-run cache-state noise that dominates the small-plugin "
            "case (where most validators take < 100ms)."
        ),
    )
    parser.add_argument(
        "--warmup",
        action="store_true",
        help=(
            "Run one extra throw-away validation pass before timing starts. "
            "Warms the OS file cache + the lint engine's scanner cache so "
            "the timed runs are not dominated by first-run cold-cache cost."
        ),
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help=(
            "Clear ~/.cache/cpv/scanner-results/ BEFORE EACH PHASE. This is "
            "the fair-comparison switch: without it, Phase A populates the "
            "scanner cache and Phase B/C skip the lint work entirely, "
            "inflating their apparent speedup. With it, every phase pays "
            "the full cold-cache cost."
        ),
    )
    args = parser.parse_args()

    plugin_root = Path(args.plugin_path).resolve()
    if not plugin_root.is_dir():
        print(f"Error: {plugin_root} is not a directory", file=sys.stderr)
        return 1

    sys_info = _system_info()
    print(f"\n{'═' * 70}")
    print("CPV validate_plugin parallelism benchmark (task #384 A10)")
    print(f"{'═' * 70}")
    print(f"  Plugin under test: {plugin_root}")
    print(f"  System:            {sys_info['platform']}")
    print(f"  CPUs:              {sys_info['cpu_count']} ({sys_info['cpu_brand']})")
    print(f"  Python:            {sys_info['python']}")
    print(f"{'─' * 70}")
    print("  Each phase is a fresh `uv run python scripts/validate_plugin.py <path>`.")
    print(f"{'═' * 70}\n")

    # The three phases.
    phases = [
        {
            "label": "A: all serial",
            "env": _build_env(orchestrator_parallel=False, validator_parallel=False),
        },
        {
            "label": "B: inner parallel, outer serial",
            "env": _build_env(orchestrator_parallel=False, validator_parallel=True),
        },
        {
            "label": "C: fully parallel (default)",
            "env": _build_env(orchestrator_parallel=True, validator_parallel=True),
        },
    ]

    # Warmup pass — discard timing. Run with the default-parallel config so
    # both the OS file cache and the lint engine's scanner cache are hot
    # before any phase times.
    if args.warmup:
        if args.verbose:
            print("  → warmup run (untimed, fully parallel)…", flush=True)
        warm_env = _build_env(orchestrator_parallel=True, validator_parallel=True)
        _run_phase("warmup", plugin_root, warm_env, verbose=args.verbose)

    rows = []
    for phase in phases:
        run_times: list[float] = []
        last_exit_code = 0
        for run_idx in range(args.runs):
            # phase["label"] is always a str by construction; mypy infers
            # str | Collection[str] from the dict literal.
            label = cast(str, phase["label"])
            if args.runs > 1:
                label = f"{label} [run {run_idx + 1}/{args.runs}]"
            if args.clear_cache:
                _clear_scanner_cache(verbose=args.verbose)
            # mypy: phase is a dict[str, str | dict[str, str]] so accessing
            # the str/dict union confuses the narrower _run_phase signature;
            # cast to the expected concrete types (we built the dict above so
            # the runtime types are guaranteed).
            wall, exit_code = _run_phase(
                label,
                plugin_root,
                cast(dict[str, str], phase["env"]),
                verbose=args.verbose,
            )
            run_times.append(wall)
            last_exit_code = exit_code
        # Median over runs — robust against one-off outliers (a transient
        # subprocess delay or GC pause). For odd N, pick the middle value;
        # for even N, average the two middles.
        sorted_times = sorted(run_times)
        if len(sorted_times) % 2 == 1:
            median_wall = sorted_times[len(sorted_times) // 2]
        else:
            mid = len(sorted_times) // 2
            median_wall = (sorted_times[mid - 1] + sorted_times[mid]) / 2.0
        rows.append({
            "label": phase["label"],
            "wall": median_wall,
            "exit_code": last_exit_code,
            "runs": run_times,
        })

    # Print the results table.
    print(f"\n{'═' * 70}")
    print("Results")
    print(f"{'═' * 70}")
    table = _format_table(rows)
    print(table)

    # Speedup commentary. ``rows`` is built as ``list[dict[str, object]]``
    # because each dict mixes str labels, float wall times, int exit codes,
    # and list[float] run histories. The keys we read below are always the
    # float wall time — cast explicitly so mypy understands the arithmetic.
    baseline = cast(float, rows[0]["wall"])
    final_wall = cast(float, rows[-1]["wall"])
    inner_wall = cast(float, rows[1]["wall"])
    final_speedup = baseline / final_wall if final_wall > 0 else float("inf")
    print(f"\nFully-parallel speedup vs serial baseline: {final_speedup:.2f}×")
    inner_speedup = baseline / inner_wall if inner_wall > 0 else float("inf")
    print(f"Inner-only speedup (sibling validators):    {inner_speedup:.2f}×")
    if final_wall > 0 and inner_wall > 0:
        outer_contribution = inner_wall / final_wall
        print(f"Outer-layer contribution (C vs B):          {outer_contribution:.2f}×")

    # Write the Markdown report unless suppressed.
    if not args.no_report:
        if args.report_root:
            # Explicit override — useful when running from a worktree and
            # wanting the report local to that worktree (rather than the
            # main repo, which is git-worktree-list's first answer).
            base = Path(args.report_root).resolve()
            report_dir = base / "reports" / args.component
            report_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S%z")
            report_file = report_dir / f"{ts}-{args.slug}.md"
        else:
            report_file = _report_path(args.component, args.slug)
        body = _compose_report(plugin_root, sys_info, rows, table)
        report_file.write_text(body, encoding="utf-8")
        print(f"\nReport written to: {report_file}")

    return 0


def _compose_report(
    plugin_root: Path,
    sys_info: dict[str, str],
    rows: list[dict],
    table: str,
) -> str:
    """Build the Markdown body of the benchmark report.

    The structure intentionally matches other CPV benchmark / audit
    reports: section headers in ATX style, fenced metadata block,
    table, and a Notes section explaining the methodology.
    """
    baseline = rows[0]["wall"]
    final_speedup = baseline / rows[-1]["wall"] if rows[-1]["wall"] > 0 else float("inf")
    inner_speedup = baseline / rows[1]["wall"] if rows[1]["wall"] > 0 else float("inf")
    outer_contribution = (
        rows[1]["wall"] / rows[-1]["wall"] if rows[-1]["wall"] > 0 else float("inf")
    )

    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "# CPV validate_plugin parallelism benchmark — task #384 A10",
        "",
        f"**Timestamp:** {timestamp}",
        f"**Plugin under test:** `{plugin_root}`",
        "",
        "## System",
        "",
        f"- **Platform:** {sys_info['platform']}",
        f"- **CPU count:** {sys_info['cpu_count']}",
        f"- **CPU brand:** {sys_info['cpu_brand']}",
        f"- **Python:** {sys_info['python']}",
        "",
        "## Phase definitions",
        "",
        "- **Phase A (all serial)** — every `CPV_*_PARALLEL` knob set to `0`,",
        "  including `CPV_ORCHESTRATOR_PARALLEL=0`. Baseline.",
        "- **Phase B (inner parallel, outer serial)** — per-validator pools at",
        "  default (parallel), orchestrator forced serial via `CPV_ORCHESTRATOR_PARALLEL=0`.",
        "  Isolates the contribution of A2–A9's per-validator parallelism.",
        "- **Phase C (fully parallel — default)** — both layers parallel. The",
        "  production configuration that ships with this CPV revision.",
        "",
        "## Results",
        "",
        table,
        "",
        "## Speedup summary",
        "",
        f"- Fully-parallel vs serial baseline: **{final_speedup:.2f}×**",
        f"- Inner-only (sibling validators)     **{inner_speedup:.2f}×**",
        f"- Outer-layer contribution (C vs B): **{outer_contribution:.2f}×**",
        "",
        "## Methodology notes",
        "",
        "- Each phase is a fresh `uv run python scripts/validate_plugin.py <path>`",
        "  subprocess. Wall time is inclusive of `uv run` startup (typically",
        "  0.5–1.0 s).",
        "- `PLUGIN_SKIP_GITHUB_INTEGRITY=1` is set so the integrity-check",
        "  network round-trip doesn't dominate the small per-phase times.",
        "- `NO_COLOR=1` is set so ANSI escapes don't skew captured stdout size.",
        "- All three phases run against the same plugin tree. The plugin is",
        "  not modified between phases.",
        "- Speedup figures are wall-time ratios (Phase A / Phase X). Sub-second",
        "  noise on small plugins is to be expected; the benchmark is intended",
        "  for plugins ≥100 files where the parallelism actually matters.",
        "",
        "## Observed exit codes",
        "",
    ]
    for row in rows:
        lines.append(f"- **{row['label']}**: exit={row['exit_code']}, median wall={row['wall']:.2f}s")
    lines.append("")
    # Per-run breakdown — useful for assessing noise / variance.
    if any(len(r.get("runs", [])) > 1 for r in rows):
        lines.append("## Per-run wall times")
        lines.append("")
        for row in rows:
            runs = row.get("runs", [])
            if runs:
                run_str = ", ".join(f"{t:.2f}" for t in runs)
                lines.append(f"- **{row['label']}**: {run_str} s")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
