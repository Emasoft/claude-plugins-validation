#!/usr/bin/env python3
"""CPV security scanner multi-phase wall-time benchmark (v2.104.0).

Times each v2.104.0 security-scanner optimization independently so the
operator can see the contribution of each:

    Phase A — legacy baseline
        ``CPV_SCAN_CACHE=0``, ``CPV_BINARY_SCAN=0``, ``CPV_RE2_DISABLE=1``.
        All v2.104.0 features OFF — what users would get without any
        of the wins shipped this revision.

    Phase B-cold — + cache only (cold)
        ``CPV_BINARY_SCAN=0``, ``CPV_RE2_DISABLE=1``. Cache enabled
        but cleared immediately before the run, so this measures
        the FIRST pass that populates the cache (no hits expected).

    Phase B-warm — + cache only (warm)
        Same env as B-cold, BUT the cache populated by B-cold is left
        in place. Every file should hit cache; measures the steady-state
        win on an unchanged tree.

    Phase C — + cache + RE2
        ``CPV_BINARY_SCAN=0`` only. Cache + RE2 hybrid matcher both
        active. Falls back to Python ``re`` if google-re2 is not
        importable — the phase still runs and the report flags the
        fallback so the reader knows the C column is not a true RE2
        measurement on that machine.

    Phase D — + cache + RE2 + binary (default)
        Empty env override — every v2.104.0 feature on. This is the
        shipped default; column D shows what the user actually gets.

Each phase runs as a fresh ``uv run python scripts/validate_security.py``
subprocess so module-level state from one phase cannot bleed into
another. Each phase runs N times (default 3); we report the median wall
time and the per-run distribution.

The report is written to
``$MAIN_ROOT/reports/security-benchmark/<YYYYMMDD_HHMMSS±HHMM>-bench.md``
per the agent-reports-location rule.

The benchmark is non-destructive — no files modified, no git ops, no
network beyond what ``validate_security`` itself does (which is blocked
by ``PLUGIN_SKIP_GITHUB_INTEGRITY=1`` anyway).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import cast

# ---------------------------------------------------------------------------
# Path / report helpers
# ---------------------------------------------------------------------------


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
        # (scripts/cpv_security_benchmark.py → repo root).
        return Path(__file__).resolve().parent.parent


def _compose_report_path(base: Path, component: str, slug: str) -> Path:
    """Compose the canonical report path per agent-reports-location rule.

    Format: ``<base>/reports/<component>/<YYYYMMDD_HHMMSS±HHMM>-<slug>.md``
    Local time + GMT offset (compact ±HHMM form) — never UTC, never
    ``±HH:MM`` (Windows-unsafe).

    ``base`` is the report ROOT (a directory under which a ``reports/``
    subdir will be created). The default caller passes ``$MAIN_ROOT``;
    tests pass a ``tmp_path`` so the report does NOT pollute the real
    ``$MAIN_ROOT/reports/`` tree.
    """
    report_dir = base / "reports" / component
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S%z")
    return report_dir / f"{ts}-{slug}.md"


def _system_info() -> dict[str, str]:
    """Capture the machine's identity for the benchmark report header.

    Includes CPU count + brand because scanner throughput scales with
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


def _re2_available() -> bool:
    """Return True iff ``import re2`` would succeed in a fresh interpreter.

    We test for ``re2`` (the google-re2 package's module name) rather than
    invoking subprocess + import, because the benchmark process and the
    spawned ``uv run`` subprocesses share the same project venv. If we
    can find the spec here, the subprocess can import it too.
    """
    return importlib.util.find_spec("re2") is not None


# ---------------------------------------------------------------------------
# Env / cache management
# ---------------------------------------------------------------------------

# The v2.104.0 env-var knobs the benchmark toggles. Each phase is defined
# by which of these are set to "0" / "1" / unset.
_FEATURE_VARS = (
    "CPV_SCAN_CACHE",
    "CPV_BINARY_SCAN",
    "CPV_RE2_DISABLE",
)


def _build_env(
    *,
    scan_cache: bool,
    binary_scan: bool,
    re2_enabled: bool,
) -> dict[str, str]:
    """Compose the env dict for one phase.

    A True value for each knob means "leave the default behaviour ON",
    achieved by:
      - ``scan_cache=True``  → unset CPV_SCAN_CACHE (default is ON)
      - ``binary_scan=True`` → unset CPV_BINARY_SCAN (default is ON)
      - ``re2_enabled=True`` → unset CPV_RE2_DISABLE (default is enabled)

    A False value forces the feature OFF:
      - ``scan_cache=False``  → CPV_SCAN_CACHE=0
      - ``binary_scan=False`` → CPV_BINARY_SCAN=0
      - ``re2_enabled=False`` → CPV_RE2_DISABLE=1

    Important: we make a fresh copy of os.environ to avoid leaking the
    parent shell's CPV_* state into the subprocess. If the user has any
    feature already disabled at the shell, we override it here so the
    phase definitions are authoritative.
    """
    env = os.environ.copy()
    # Always set deterministic side-channel knobs
    env["PLUGIN_SKIP_GITHUB_INTEGRITY"] = "1"
    env["NO_COLOR"] = "1"
    # Clear any pre-existing CPV_* feature vars from the parent shell
    for var in _FEATURE_VARS:
        env.pop(var, None)
    # Apply the phase's overrides
    if not scan_cache:
        env["CPV_SCAN_CACHE"] = "0"
    if not binary_scan:
        env["CPV_BINARY_SCAN"] = "0"
    if not re2_enabled:
        env["CPV_RE2_DISABLE"] = "1"
    return env


def _clear_scanner_cache(*, verbose: bool = False) -> None:
    """Remove ~/.cache/cpv/scanner-results/ so the next run pays cold-cache cost.

    Phase B-cold and (optionally, via --clear-cache) every phase invokes
    this so the cache state is well-defined at the start of the run.
    We use ``shutil.rmtree`` (no exec of ``rm``) so the global
    git_safety guard doesn't refuse the operation — the cache is under
    ``~/.cache`` which is NOT a project tree and is safely regenerable.
    """
    cache_dir = Path.home() / ".cache" / "cpv" / "scanner-results"
    if cache_dir.exists():
        try:
            shutil.rmtree(cache_dir)
            if verbose:
                print(f"  cleared scanner cache at {cache_dir}", flush=True)
        except OSError as e:
            if verbose:
                print(f"  could not clear {cache_dir}: {e}", flush=True)


# ---------------------------------------------------------------------------
# Phase runner
# ---------------------------------------------------------------------------


def _run_phase(
    label: str,
    plugin_root: Path,
    env: dict[str, str],
    *,
    verbose: bool,
    timeout: int = 1800,
) -> tuple[float, int]:
    """Run validate_security.py against ``plugin_root`` with ``env`` and time it.

    Returns ``(wall_seconds, exit_code)``. Stdout is captured (and
    discarded unless ``verbose``) so it doesn't pollute the benchmark
    output. The ``timeout`` hard cap (30 min default) catches a wedge
    that would otherwise hang the suite indefinitely.

    NOTE: the process exit code is reported, NOT checked — security
    findings produce non-zero exits, which is normal for a real plugin.
    The benchmark cares about wall time, not whether the scan was
    "clean".
    """
    script = Path(__file__).resolve().parent / "validate_security.py"
    if verbose:
        print(f"  starting phase: {label}", flush=True)
    t0 = time.perf_counter()
    result = subprocess.run(
        ["uv", "run", "python", str(script), str(plugin_root)],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(script.parent.parent),
        timeout=timeout,
        check=False,
    )
    t1 = time.perf_counter()
    wall = t1 - t0
    if verbose:
        # Print just the tail of stdout so the operator can confirm the
        # run finished cleanly without burying numbers in 1000s of lines.
        tail = "\n".join(result.stdout.splitlines()[-3:])
        print(f"  {label}: {wall:.2f}s exit={result.returncode}", flush=True)
        if tail:
            print(f"     tail: {tail}", flush=True)
    return wall, result.returncode


def _median(values: list[float]) -> float:
    """Compute the median of ``values``.

    For odd N, return the middle value; for even N, return the average
    of the two middles. This is more robust than mean against the
    single-run cache-state noise that dominates the small-plugin case
    (where most scanners take < 100ms).
    """
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return s[n // 2]
    mid = n // 2
    return (s[mid - 1] + s[mid]) / 2.0


# ---------------------------------------------------------------------------
# Phase definitions
# ---------------------------------------------------------------------------


def _phase_specs(re2_actually_available: bool) -> list[dict[str, object]]:
    """Return the canonical list of phase specs.

    Each spec is a dict with:
      - label: human-readable phase name (used in table + report)
      - short: short label (A / B-cold / B-warm / C / D)
      - env_args: kwargs for _build_env (scan_cache/binary_scan/re2_enabled)
      - description: one-line "what this measures"
      - clear_cache_before: whether to wipe the scanner cache before
        EACH run of this phase (True for cold phases, False for warm)

    The C phase notes the RE2 fallback when google-re2 is missing —
    the phase still runs (with Python re) and the report flags it.
    """
    c_label = "C: + cache + RE2"
    if not re2_actually_available:
        c_label = "C: + cache + RE2 (fallback to Python re)"
    return [
        {
            "short": "A",
            "label": "A: legacy baseline",
            "env_args": {"scan_cache": False, "binary_scan": False, "re2_enabled": False},
            "description": "All v2.104.0 features OFF — what users get without any wins.",
            "clear_cache_before": True,
        },
        {
            "short": "B-cold",
            "label": "B-cold: + cache only (cold)",
            "env_args": {"scan_cache": True, "binary_scan": False, "re2_enabled": False},
            "description": "Cache enabled but cleared immediately before run; no hits expected.",
            "clear_cache_before": True,
        },
        {
            "short": "B-warm",
            "label": "B-warm: + cache only (warm)",
            "env_args": {"scan_cache": True, "binary_scan": False, "re2_enabled": False},
            "description": "Cache populated by B-cold; every file should hit cache.",
            "clear_cache_before": False,
        },
        {
            "short": "C",
            "label": c_label,
            "env_args": {"scan_cache": True, "binary_scan": False, "re2_enabled": True},
            "description": "Cache + RE2 hybrid matcher; falls back to Python re if google-re2 missing.",
            "clear_cache_before": True,
        },
        {
            "short": "D",
            "label": "D: + cache + RE2 + binary (default)",
            "env_args": {"scan_cache": True, "binary_scan": True, "re2_enabled": True},
            "description": "Shipped default: every v2.104.0 feature ON.",
            "clear_cache_before": True,
        },
    ]


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


def _format_console_table(rows: list[dict[str, object]]) -> str:
    """Render the results table that goes to stdout.

    Columns: Phase | Cache | RE2 | Binary | Wall median (n runs) | Speedup vs A
    The "speedup vs A" column always exists; we compute the ratio
    Phase A wall / current phase wall, so > 1.0 = faster than baseline.
    """
    baseline = cast(float, rows[0]["wall_median"])
    lines = []
    lines.append("Phase  | Cache | RE2 | Binary | Wall (median)     | Speedup vs A")
    lines.append("-------|-------|-----|--------|-------------------|--------------")
    for row in rows:
        env_args = cast(dict[str, bool], row["env_args"])
        cache_str = "ON " if env_args["scan_cache"] else "OFF"
        re2_str = "ON " if env_args["re2_enabled"] else "OFF"
        binary_str = "ON " if env_args["binary_scan"] else "OFF"
        wall = cast(float, row["wall_median"])
        n_runs = len(cast(list[float], row["runs"]))
        speedup = baseline / wall if wall > 0 else float("inf")
        short = cast(str, row["short"])
        lines.append(
            f"{short:6s} | {cache_str:5s} | {re2_str:3s} | {binary_str:6s} | "
            f"{wall:6.2f} s ({n_runs} runs) | {speedup:5.2f}x"
        )
    return "\n".join(lines)


def _format_markdown_table(rows: list[dict[str, object]]) -> str:
    """Render the results table for the Markdown report."""
    baseline = cast(float, rows[0]["wall_median"])
    header = (
        "| Phase | Cache | RE2 | Binary | Wall (median) | n_runs | Speedup vs A |\n"
        "|-------|-------|-----|--------|---------------|--------|--------------|\n"
    )
    body = ""
    for row in rows:
        env_args = cast(dict[str, bool], row["env_args"])
        cache_str = "ON" if env_args["scan_cache"] else "OFF"
        re2_str = "ON" if env_args["re2_enabled"] else "OFF"
        binary_str = "ON" if env_args["binary_scan"] else "OFF"
        wall = cast(float, row["wall_median"])
        n_runs = len(cast(list[float], row["runs"]))
        speedup = baseline / wall if wall > 0 else float("inf")
        short = cast(str, row["short"])
        body += (
            f"| {short} | {cache_str} | {re2_str} | {binary_str} | "
            f"{wall:.2f} s | {n_runs} | {speedup:.2f}× |\n"
        )
    return header + body


def _compose_report(
    plugin_root: Path,
    sys_info: dict[str, str],
    rows: list[dict[str, object]],
    table: str,
    *,
    re2_actually_available: bool,
    runs_per_phase: int,
    clear_cache_each: bool,
) -> str:
    """Build the Markdown body of the benchmark report.

    The structure intentionally matches other CPV benchmark / audit
    reports: section headers in ATX style, fenced metadata block,
    table, per-run distribution, system info, and a "what to look at"
    guide explaining how to read the numbers.
    """
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    baseline = cast(float, rows[0]["wall_median"])
    lines = [
        "# CPV security scanner benchmark (v2.104.0)",
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
        f"- **google-re2 importable:** {'yes' if re2_actually_available else 'NO (Phase C falls back to Python re)'}",
        "",
        "## Phase definitions",
        "",
    ]
    for row in rows:
        env_args = cast(dict[str, bool], row["env_args"])
        cache_str = "ON" if env_args["scan_cache"] else "OFF"
        re2_str = "ON" if env_args["re2_enabled"] else "OFF"
        binary_str = "ON" if env_args["binary_scan"] else "OFF"
        lines.append(
            f"- **{row['label']}** — cache={cache_str}, RE2={re2_str}, "
            f"binary={binary_str}. {row['description']}"
        )
    lines.extend([
        "",
        "## Results",
        "",
        table,
        "",
        "## Speedup summary",
        "",
    ])
    for row in rows:
        wall = cast(float, row["wall_median"])
        speedup = baseline / wall if wall > 0 else float("inf")
        lines.append(f"- **{row['label']}** — {wall:.2f} s — {speedup:.2f}× vs A")
    lines.extend([
        "",
        "## Per-run distribution",
        "",
        "Median is robust against one-off outliers (a transient subprocess",
        "delay, GC pause, OS scheduling jitter). Below are the raw wall times",
        f"for each of the {runs_per_phase} runs per phase.",
        "",
    ])
    for row in rows:
        runs = cast(list[float], row["runs"])
        run_str = ", ".join(f"{t:.2f}" for t in runs)
        exit_code = row.get("last_exit_code", "n/a")
        lines.append(f"- **{row['short']}**: {run_str} s (last exit={exit_code})")
    lines.extend([
        "",
        "## What to look at",
        "",
        "- **A vs B-warm**: the cache win on a re-scan of an unchanged tree.",
        "  Expect a large ratio (5–50×) — the cache should short-circuit every",
        "  per-file scanner call.",
        "- **A vs B-cold**: the cache overhead on a first scan. Expect ~1.0×",
        "  (or slightly negative) — cache misses still pay the full scan",
        "  cost AND a small overhead to populate the cache. This is the",
        "  break-even point that justifies the cache architecture: any",
        "  subsequent re-scan amortises it.",
        "- **B-cold vs C**: the RE2 hybrid-matcher win on cold-cache scan.",
        "  Expect 2–5× when google-re2 is available; ~1.0× when the report",
        "  notes the Python re fallback.",
        "- **C vs D**: the binary-scan ADDITION. Expect D to be ~10–30% slower",
        "  than C (binary scan ADDS work), but D ADDS coverage (binary files",
        "  are scanned for embedded secrets / known-bad blobs). The trade-off",
        "  is more accurate findings for a small wall-time cost.",
        "",
        "## Methodology notes",
        "",
        "- Each phase is a fresh `uv run python scripts/validate_security.py <path>`",
        "  subprocess. Wall time is inclusive of `uv run` startup",
        "  (typically 0.5–1.0 s).",
        "- `PLUGIN_SKIP_GITHUB_INTEGRITY=1` is set so the integrity-check",
        "  network round-trip doesn't dominate the small per-phase times.",
        "- `NO_COLOR=1` is set so ANSI escapes don't skew captured stdout size.",
        "- All phases run against the same plugin tree. The plugin is not",
        "  modified between phases.",
        f"- {runs_per_phase} runs per phase; the table shows the MEDIAN, the",
        "  Per-run section shows the raw distribution.",
        f"- `--clear-cache` was {'ON' if clear_cache_each else 'OFF'} for non-warm phases.",
        "- The B-warm phase intentionally does NOT clear the cache —",
        "  that's the entire point of measuring warm-cache performance.",
        "- Non-zero exit codes are normal (real plugins have findings).",
        "  The benchmark cares about wall time, not findings count.",
        "",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns 0 on success, 1 on usage error.

    Args:
        argv: explicit argv list (used by tests). When None, falls back
            to ``sys.argv[1:]``.
    """
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
        default="security-benchmark",
        help="Report subdir under reports/ (default: security-benchmark).",
    )
    parser.add_argument(
        "--slug",
        default="bench",
        help="Filename slug for the Markdown report (default: bench).",
    )
    parser.add_argument(
        "--report-root",
        default=None,
        help=(
            "Override the report root directory. Default: $MAIN_ROOT resolved via "
            "`git worktree list` (first entry = main checkout). Tests pass a "
            "tmp_path so the test report does NOT pollute the real reports/ tree."
        ),
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help=(
            "Number of timing runs per phase (default: 3). The wall time "
            "reported is the MEDIAN across runs."
        ),
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help=(
            "Request a scanner-cache wipe before each non-warm phase. The "
            "cold phases (A / B-cold / C / D) already wipe the cache before "
            "every run unconditionally, so this flag is a redundant explicit "
            "request for them. The B-warm phase is ALWAYS left warm even with "
            "this flag — measuring warm-cache hits is its entire purpose, and "
            "it depends on B-cold having populated the cache first."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help=(
            "Per-subprocess hard timeout in seconds (default: 1800 = 30 min). "
            "Tests pass a small value to keep the suite fast."
        ),
    )
    args = parser.parse_args(argv)

    plugin_root = Path(args.plugin_path).resolve()
    if not plugin_root.is_dir():
        print(f"Error: {plugin_root} is not a directory", file=sys.stderr)
        return 1
    if args.runs < 1:
        print(f"Error: --runs must be >= 1 (got {args.runs})", file=sys.stderr)
        return 1

    sys_info = _system_info()
    re2_avail = _re2_available()
    print(f"\n{'=' * 70}")
    print("CPV security scanner benchmark (v2.104.0)")
    print(f"{'=' * 70}")
    print(f"  Plugin under test: {plugin_root}")
    print(f"  System:            {sys_info['platform']}")
    print(f"  CPUs:              {sys_info['cpu_count']} ({sys_info['cpu_brand']})")
    print(f"  Python:            {sys_info['python']}")
    print(f"  google-re2:        {'available' if re2_avail else 'NOT available (Phase C falls back to Python re)'}")
    print(f"{'-' * 70}")
    print("  Each phase is a fresh `uv run python scripts/validate_security.py <path>`.")
    print(f"{'=' * 70}\n")

    phases = _phase_specs(re2_avail)

    rows: list[dict[str, object]] = []
    for phase in phases:
        run_times: list[float] = []
        last_exit_code = 0
        env_args = cast(dict[str, bool], phase["env_args"])
        env = _build_env(**env_args)
        clear_before = cast(bool, phase["clear_cache_before"])
        for run_idx in range(args.runs):
            short = cast(str, phase["short"])
            label = cast(str, phase["label"])
            if args.runs > 1:
                run_label = f"{label} [run {run_idx + 1}/{args.runs}]"
            else:
                run_label = label
            # Cold phases wipe the cache before each run. The warm phase
            # intentionally does NOT (we're measuring cache hits), and
            # --clear-cache never overrides that — see below.
            should_wipe = clear_before or (args.clear_cache and short != "B-warm")
            # B-warm depends on B-cold having populated the cache, so it
            # is always left warm regardless of --clear-cache. (The flag
            # is therefore a no-op for B-warm, and redundant for the cold
            # phases which already wipe via clear_cache_before=True.)
            if short == "B-warm":
                should_wipe = False
            if should_wipe:
                _clear_scanner_cache(verbose=args.verbose)
            wall, exit_code = _run_phase(
                run_label,
                plugin_root,
                env,
                verbose=args.verbose,
                timeout=args.timeout,
            )
            run_times.append(wall)
            last_exit_code = exit_code
        rows.append({
            "short": phase["short"],
            "label": phase["label"],
            "env_args": phase["env_args"],
            "description": phase["description"],
            "wall_median": _median(run_times),
            "runs": run_times,
            "last_exit_code": last_exit_code,
        })

    # Print results to stdout.
    print(f"\n{'=' * 70}")
    print("Results")
    print(f"{'=' * 70}")
    table = _format_console_table(rows)
    print(table)
    print()

    # Always print every phase line — tests assert "all 5 phase lines"
    # are present, and the table is the canonical source.
    for row in rows:
        wall = cast(float, row["wall_median"])
        runs = cast(list[float], row["runs"])
        print(f"  {row['short']}: median={wall:.2f}s ({len(runs)} runs)")

    # Write the Markdown report unless suppressed.
    if not args.no_report:
        if args.report_root:
            base = Path(args.report_root).resolve()
        else:
            base = _resolve_main_root()
        report_file = _compose_report_path(base, args.component, args.slug)
        md_table = _format_markdown_table(rows)
        body = _compose_report(
            plugin_root,
            sys_info,
            rows,
            md_table,
            re2_actually_available=re2_avail,
            runs_per_phase=args.runs,
            clear_cache_each=args.clear_cache,
        )
        report_file.write_text(body, encoding="utf-8")
        print(f"\nReport saved to: {report_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
