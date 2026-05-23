"""Smoke tests for ``scripts/cpv_security_benchmark.py`` (v2.104.0).

These tests verify the script's surface (CLI, phase definitions, env
construction, report path resolution, cache wipe behaviour, RE2
fallback handling) WITHOUT actually invoking validate_security.py — a
real run would take 30+ seconds per phase × 5 phases × 3 runs each =
several minutes per test. Instead we monkeypatch ``subprocess.run`` /
``_run_phase`` to return canned timing data.

The benchmark itself is the thing we test for shape; the underlying
scanner's correctness is tested elsewhere (test_validate_security.py).

Per the spec:
- run with ``--runs 1 --no-report`` → exits 0, prints all 5 phase lines
- ``--clear-cache`` flag wipes cache between phases (verified via spy)
- report path resolution honors ``--report-root`` override
- report is written under ``reports/security-benchmark/`` by default
- per-run distribution is captured (not just median)
- phase A < phase D OR docs explain why
- script gracefully reports when google-re2 unavailable (phase C falls
  back to Python re)
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path
from typing import Any

import pytest

# Add scripts/ to sys.path so we can import cpv_security_benchmark directly.
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Import the module under test once at module load. Tests will reach
# into it to monkeypatch / inspect attributes.
sec_bench = importlib.import_module("cpv_security_benchmark")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_run_phase(
    seq_iter: Any,
    exit_codes: tuple[int, ...] = (0,),
) -> Any:
    """Build a fake ``_run_phase`` that returns the next wall time from ``seq_iter``.

    ``seq_iter`` must be an iterator yielding floats. Each call consumes
    one value. ``exit_codes`` cycles for multi-run phases.
    """
    exit_iter = iter(exit_codes * 1000)  # plenty of headroom

    def fake(label: str, plugin_root: Path, env: dict[str, str], *, verbose: bool, timeout: int = 1800) -> tuple[float, int]:
        return next(seq_iter), next(exit_iter)

    return fake


# ---------------------------------------------------------------------------
# Test 1 — module is importable + main() is callable
# ---------------------------------------------------------------------------


def test_module_importable_and_main_callable():
    """Module loads and exposes a callable main() entry point."""
    assert hasattr(sec_bench, "main")
    assert callable(sec_bench.main)
    # Spec-required helpers are also present
    assert hasattr(sec_bench, "_build_env")
    assert hasattr(sec_bench, "_clear_scanner_cache")
    assert hasattr(sec_bench, "_phase_specs")
    assert hasattr(sec_bench, "_re2_available")
    assert hasattr(sec_bench, "_compose_report_path")
    assert hasattr(sec_bench, "_run_phase")
    assert hasattr(sec_bench, "_median")


# ---------------------------------------------------------------------------
# Test 2 — runs with --runs 1 --no-report, exits 0, prints all 5 phase lines
# ---------------------------------------------------------------------------


def test_runs_1_no_report_exits_zero_prints_5_phase_lines(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """End-to-end shape test: 5 phases × 1 run = 5 timed invocations.

    Patches ``_run_phase`` (the only thing that touches subprocess) and
    ``_clear_scanner_cache`` (so we don't actually wipe the user's
    cache). Verifies the stdout output contains every phase's short
    label.
    """
    # Make a real-looking plugin root (must be a directory)
    fake_plugin = tmp_path / "fake_plugin"
    fake_plugin.mkdir()

    # Each phase gets one run; 5 phases × 1 run = 5 wall times
    times = iter([1.0, 2.0, 0.5, 0.8, 1.1])
    monkeypatch.setattr(sec_bench, "_run_phase", _fake_run_phase(times))
    monkeypatch.setattr(sec_bench, "_clear_scanner_cache", lambda **kw: None)

    rc = sec_bench.main([
        str(fake_plugin),
        "--runs", "1",
        "--no-report",
    ])
    assert rc == 0

    captured = capsys.readouterr()
    out = captured.out
    # Every phase short label should appear in the output table
    for short in ("A", "B-cold", "B-warm", "C", "D"):
        # Use a regex anchored to avoid matching the inside of words
        assert re.search(rf"\b{re.escape(short)}\b", out), (
            f"Phase '{short}' not found in stdout:\n{out}"
        )


# ---------------------------------------------------------------------------
# Test 3 — --clear-cache wipes cache between phases (B-warm exempted)
# ---------------------------------------------------------------------------


def test_clear_cache_flag_wipes_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When --clear-cache is set, _clear_scanner_cache is invoked for
    every cold phase but NEVER for B-warm (which would invalidate the
    warm-cache measurement)."""
    fake_plugin = tmp_path / "fake_plugin"
    fake_plugin.mkdir()

    # All phases × 1 run = 5 invocations of _run_phase
    times = iter([1.0] * 5)
    monkeypatch.setattr(sec_bench, "_run_phase", _fake_run_phase(times))

    # Spy on _clear_scanner_cache — count calls + record which short
    # label was being prepared for. We do this by inspecting the
    # phases list and tracking sequential calls.
    wipe_count = {"n": 0}

    def spy_wipe(**kwargs: Any) -> None:
        wipe_count["n"] += 1

    monkeypatch.setattr(sec_bench, "_clear_scanner_cache", spy_wipe)

    rc = sec_bench.main([
        str(fake_plugin),
        "--runs", "1",
        "--no-report",
        "--clear-cache",
    ])
    assert rc == 0

    # Default phases that wipe before every run (clear_cache_before=True):
    #   A, B-cold, C, D = 4 wipes
    # With --clear-cache, B-warm is STILL skipped (it would defeat the
    # measurement), so we expect exactly 4 wipes regardless.
    assert wipe_count["n"] == 4, (
        f"Expected exactly 4 cache wipes (A/B-cold/C/D), got {wipe_count['n']}"
    )


def test_b_warm_does_not_wipe_cache_even_with_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """B-warm is the ONE phase that MUST NOT wipe the cache, even when
    --clear-cache is on — its whole purpose is to measure the warm-cache
    path. This is enforced by special-case logic in main()."""
    fake_plugin = tmp_path / "fake_plugin"
    fake_plugin.mkdir()

    times = iter([1.0] * 5)
    monkeypatch.setattr(sec_bench, "_run_phase", _fake_run_phase(times))

    # Record the ORDER of phase labels that triggered a wipe
    wipe_order: list[str] = []

    # We need a way to know WHICH phase is about to run when wipe is called.
    # Easiest: wrap _run_phase and append the run label first, then the
    # wipe spy keys off the most-recently-seen label.
    last_label = {"x": ""}

    def label_capturing_run_phase(label: str, *args: Any, **kwargs: Any) -> tuple[float, int]:
        # This fires AFTER the wipe for each run. We instead need to
        # capture the wipe BEFORE this. So we approach the test
        # differently — record total wipes and ensure none of them
        # happens "around" the B-warm phase. Simpler: count wipes
        # between known marker labels.
        last_label["x"] = label
        return next(times), 0

    monkeypatch.setattr(sec_bench, "_run_phase", label_capturing_run_phase)

    def spy_wipe(**kwargs: Any) -> None:
        # Before the FIRST _run_phase call, last_label is empty — the
        # wipe corresponds to phase A. After phase A, last_label says
        # "A: ..." and a wipe means we're prepping B-cold. Etc.
        wipe_order.append(last_label["x"])

    monkeypatch.setattr(sec_bench, "_clear_scanner_cache", spy_wipe)

    rc = sec_bench.main([
        str(fake_plugin),
        "--runs", "1",
        "--no-report",
        "--clear-cache",
    ])
    assert rc == 0

    # No wipe should have occurred immediately after the B-cold run
    # (i.e., the wipe whose "previous label" was B-cold). The sequence
    # of wipes when --clear-cache is on:
    #   wipe before A (empty label) → run A
    #   wipe before B-cold ("A...") → run B-cold
    #   NO wipe before B-warm
    #   wipe before C ("B-warm...") → run C
    #   wipe before D ("C...") → run D
    # So we should NEVER see a "B-cold" label in the wipe-trigger
    # sequence (which would mean a wipe happened between B-cold and
    # B-warm, defeating the measurement).
    b_cold_triggered_wipes = [w for w in wipe_order if "B-cold" in w]
    assert not b_cold_triggered_wipes, (
        f"B-warm cache must not be wiped! Wipe order: {wipe_order}"
    )


# ---------------------------------------------------------------------------
# Test 4 — --report-root override honored
# ---------------------------------------------------------------------------


def test_report_root_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """--report-root <path> should redirect the report under <path>/reports/
    instead of the default $MAIN_ROOT/reports/."""
    fake_plugin = tmp_path / "fake_plugin"
    fake_plugin.mkdir()
    report_root = tmp_path / "custom_reports"
    report_root.mkdir()

    times = iter([1.0] * 5)
    monkeypatch.setattr(sec_bench, "_run_phase", _fake_run_phase(times))
    monkeypatch.setattr(sec_bench, "_clear_scanner_cache", lambda **kw: None)

    rc = sec_bench.main([
        str(fake_plugin),
        "--runs", "1",
        "--report-root", str(report_root),
        "--slug", "test-bench",
    ])
    assert rc == 0

    # Report must land under the override path, NOT under $MAIN_ROOT
    expected_dir = report_root / "reports" / "security-benchmark"
    assert expected_dir.is_dir(), f"Expected dir not created: {expected_dir}"

    reports = list(expected_dir.glob("*-test-bench.md"))
    assert len(reports) == 1, (
        f"Expected exactly one *-test-bench.md report under {expected_dir}, "
        f"got: {reports}"
    )


# ---------------------------------------------------------------------------
# Test 5 — default report path is under reports/security-benchmark/
# ---------------------------------------------------------------------------


def test_default_report_path_under_security_benchmark(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When --report-root is omitted, the report goes under
    <main-root>/reports/security-benchmark/ (we monkeypatch
    _resolve_main_root to point at tmp_path)."""
    fake_plugin = tmp_path / "fake_plugin"
    fake_plugin.mkdir()
    fake_main_root = tmp_path / "fake_main_root"
    fake_main_root.mkdir()

    times = iter([1.0] * 5)
    monkeypatch.setattr(sec_bench, "_run_phase", _fake_run_phase(times))
    monkeypatch.setattr(sec_bench, "_clear_scanner_cache", lambda **kw: None)
    monkeypatch.setattr(sec_bench, "_resolve_main_root", lambda: fake_main_root)

    rc = sec_bench.main([
        str(fake_plugin),
        "--runs", "1",
        "--slug", "default-path-test",
    ])
    assert rc == 0

    expected_dir = fake_main_root / "reports" / "security-benchmark"
    assert expected_dir.is_dir()
    reports = list(expected_dir.glob("*-default-path-test.md"))
    assert len(reports) == 1


# ---------------------------------------------------------------------------
# Test 6 — per-run distribution captured (not just median)
# ---------------------------------------------------------------------------


def test_per_run_distribution_captured_in_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With --runs 3, the report's Per-run distribution section must
    show 3 distinct wall times per phase (not just the median)."""
    fake_plugin = tmp_path / "fake_plugin"
    fake_plugin.mkdir()
    report_root = tmp_path / "reports_out"
    report_root.mkdir()

    # 5 phases × 3 runs = 15 wall times; each phase gets 3 distinct values
    # so the per-run section can be verified by spotting all 3 values.
    times = iter([
        # Phase A
        1.0, 1.1, 1.2,
        # Phase B-cold
        1.5, 1.6, 1.7,
        # Phase B-warm
        0.1, 0.2, 0.3,
        # Phase C
        0.4, 0.5, 0.6,
        # Phase D
        0.7, 0.8, 0.9,
    ])
    monkeypatch.setattr(sec_bench, "_run_phase", _fake_run_phase(times))
    monkeypatch.setattr(sec_bench, "_clear_scanner_cache", lambda **kw: None)

    rc = sec_bench.main([
        str(fake_plugin),
        "--runs", "3",
        "--report-root", str(report_root),
        "--slug", "distribution-test",
    ])
    assert rc == 0

    reports = list((report_root / "reports" / "security-benchmark").glob("*-distribution-test.md"))
    assert len(reports) == 1
    body = reports[0].read_text(encoding="utf-8")

    # The Per-run distribution section must exist
    assert "## Per-run distribution" in body

    # Each of the 15 wall times must appear in the report (median of
    # 1.0/1.1/1.2 is 1.1 — the median appears in the table; the raw
    # values appear in the Per-run section).
    for t in (1.0, 1.1, 1.2, 1.5, 1.6, 1.7, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        assert f"{t:.2f}" in body, f"Wall time {t:.2f} missing from report:\n{body}"


# ---------------------------------------------------------------------------
# Test 7 — phase A vs phase D commentary / structure
# ---------------------------------------------------------------------------


def test_phase_a_vs_d_relationship_documented(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Phase D = ALL features on (cache + RE2 + binary). Binary scan
    ADDS work but ADDS coverage, so D can be SLOWER than C — and the
    report must explain this trade-off (so the reader doesn't see
    D-slower-than-C and think it's a regression).

    The check is on the report's documentation, not on the timing
    numbers themselves (which the test fakes)."""
    fake_plugin = tmp_path / "fake_plugin"
    fake_plugin.mkdir()
    report_root = tmp_path / "reports_out"
    report_root.mkdir()

    # Intentionally make D slower than A — the report must STILL render
    # cleanly, with documentation explaining why D > A is acceptable.
    times = iter([1.0, 1.2, 0.1, 0.5, 2.0])  # A=1.0, D=2.0 (D slower)
    monkeypatch.setattr(sec_bench, "_run_phase", _fake_run_phase(times))
    monkeypatch.setattr(sec_bench, "_clear_scanner_cache", lambda **kw: None)

    rc = sec_bench.main([
        str(fake_plugin),
        "--runs", "1",
        "--report-root", str(report_root),
        "--slug", "tradeoff-test",
    ])
    assert rc == 0

    reports = list((report_root / "reports" / "security-benchmark").glob("*-tradeoff-test.md"))
    assert len(reports) == 1
    body = reports[0].read_text(encoding="utf-8")

    # The report must document the D-adds-coverage trade-off so the
    # reader doesn't interpret D > C as a regression.
    assert "binary scan ADDS work" in body or "binary scan ADD" in body.upper().replace(" ", " "), (
        "Report must explain that binary scan adds work but adds coverage"
    )
    assert "What to look at" in body, (
        "Report must include a 'What to look at' section guiding interpretation"
    )


# ---------------------------------------------------------------------------
# Test 8 — RE2 fallback gracefully reported when google-re2 missing
# ---------------------------------------------------------------------------


def test_re2_unavailable_fallback_reported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When _re2_available() returns False, the C phase label gains a
    "fallback to Python re" annotation, AND the system-info section of
    the report flags re2 as NOT available."""
    fake_plugin = tmp_path / "fake_plugin"
    fake_plugin.mkdir()
    report_root = tmp_path / "reports_out"
    report_root.mkdir()

    times = iter([1.0] * 5)
    monkeypatch.setattr(sec_bench, "_run_phase", _fake_run_phase(times))
    monkeypatch.setattr(sec_bench, "_clear_scanner_cache", lambda **kw: None)
    # Force the RE2-missing code path even though our project venv has it
    monkeypatch.setattr(sec_bench, "_re2_available", lambda: False)

    rc = sec_bench.main([
        str(fake_plugin),
        "--runs", "1",
        "--report-root", str(report_root),
        "--slug", "re2-fallback",
    ])
    assert rc == 0

    reports = list((report_root / "reports" / "security-benchmark").glob("*-re2-fallback.md"))
    assert len(reports) == 1
    body = reports[0].read_text(encoding="utf-8")

    # System info section flags re2 as NOT available
    assert "google-re2 importable:" in body
    assert "NO" in body  # the "NO (Phase C falls back to Python re)" string

    # The Phase C label is annotated with the fallback
    assert "fallback to Python re" in body


# ---------------------------------------------------------------------------
# Bonus tests — additional surface coverage
# ---------------------------------------------------------------------------


def test_build_env_phase_a_baseline() -> None:
    """Phase A env: all 3 feature knobs forced OFF."""
    env = sec_bench._build_env(scan_cache=False, binary_scan=False, re2_enabled=False)
    assert env["CPV_SCAN_CACHE"] == "0"
    assert env["CPV_BINARY_SCAN"] == "0"
    assert env["CPV_RE2_DISABLE"] == "1"
    # Side-channel knobs always set
    assert env["PLUGIN_SKIP_GITHUB_INTEGRITY"] == "1"
    assert env["NO_COLOR"] == "1"


def test_build_env_phase_d_default() -> None:
    """Phase D env: every feature knob unset (default behaviour)."""
    env = sec_bench._build_env(scan_cache=True, binary_scan=True, re2_enabled=True)
    # All three CPV_* feature vars must be ABSENT — leaving the
    # validator to use its default (= ON for each feature in v2.104.0)
    assert "CPV_SCAN_CACHE" not in env
    assert "CPV_BINARY_SCAN" not in env
    assert "CPV_RE2_DISABLE" not in env


def test_build_env_strips_parent_cpv_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the parent shell has CPV_SCAN_CACHE=0 set, _build_env with
    scan_cache=True must UNSET it (not inherit it)."""
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")
    monkeypatch.setenv("CPV_BINARY_SCAN", "0")
    monkeypatch.setenv("CPV_RE2_DISABLE", "1")
    env = sec_bench._build_env(scan_cache=True, binary_scan=True, re2_enabled=True)
    # All three must be ABSENT despite the parent shell having them set
    assert "CPV_SCAN_CACHE" not in env
    assert "CPV_BINARY_SCAN" not in env
    assert "CPV_RE2_DISABLE" not in env


def test_median_odd_and_even() -> None:
    """_median: odd-N returns middle, even-N returns average of two middles."""
    assert sec_bench._median([1.0, 2.0, 3.0]) == 2.0  # odd, middle
    assert sec_bench._median([1.0, 2.0, 3.0, 4.0]) == 2.5  # even, avg of 2/3
    assert sec_bench._median([5.0]) == 5.0  # single value
    assert sec_bench._median([]) == 0.0  # empty → 0


def test_invalid_plugin_path_returns_1(tmp_path: Path) -> None:
    """Non-existent plugin path → exit 1, not crash."""
    nonexistent = tmp_path / "nope"
    rc = sec_bench.main([str(nonexistent), "--runs", "1", "--no-report"])
    assert rc == 1


def test_runs_zero_rejected(tmp_path: Path) -> None:
    """--runs 0 must be rejected (we need at least one timing per phase)."""
    fake_plugin = tmp_path / "fake_plugin"
    fake_plugin.mkdir()
    rc = sec_bench.main([str(fake_plugin), "--runs", "0", "--no-report"])
    assert rc == 1


def test_phase_specs_count_is_five() -> None:
    """The spec calls for exactly 5 phases: A, B-cold, B-warm, C, D."""
    specs = sec_bench._phase_specs(re2_actually_available=True)
    assert len(specs) == 5
    shorts = [s["short"] for s in specs]
    assert shorts == ["A", "B-cold", "B-warm", "C", "D"]


def test_phase_specs_re2_fallback_label() -> None:
    """When re2 is missing, the C phase label is annotated."""
    specs_with = sec_bench._phase_specs(re2_actually_available=True)
    specs_without = sec_bench._phase_specs(re2_actually_available=False)
    c_with = next(s for s in specs_with if s["short"] == "C")
    c_without = next(s for s in specs_without if s["short"] == "C")
    assert "fallback" not in c_with["label"]
    assert "fallback to Python re" in c_without["label"]


def test_resolve_main_root_returns_path() -> None:
    """_resolve_main_root must always return a Path (even if git fails)."""
    result = sec_bench._resolve_main_root()
    assert isinstance(result, Path)
    # Should be the CPV repo (its scripts/ subdir is where this module lives)
    assert (result / "scripts" / "cpv_security_benchmark.py").is_file()


def test_compose_report_path_creates_directory(tmp_path: Path) -> None:
    """_compose_report_path must mkdir -p the target dir if missing."""
    base = tmp_path / "fresh_base"
    # Note: base does NOT exist yet
    path = sec_bench._compose_report_path(base, "security-benchmark", "smoke")
    assert path.parent.is_dir()
    assert path.parent == base / "reports" / "security-benchmark"
    assert path.name.endswith("-smoke.md")


def test_clear_scanner_cache_safe_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_clear_scanner_cache must NOT raise if the cache dir doesn't exist."""
    # Redirect HOME to an empty tmp dir so .cache/cpv doesn't exist
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Should not raise
    sec_bench._clear_scanner_cache(verbose=False)


def test_console_table_includes_speedup_column(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """The stdout table must include a 'Speedup vs A' column header."""
    fake_plugin = tmp_path / "fake_plugin"
    fake_plugin.mkdir()
    times = iter([1.0, 0.5, 0.1, 0.3, 0.4])
    monkeypatch.setattr(sec_bench, "_run_phase", _fake_run_phase(times))
    monkeypatch.setattr(sec_bench, "_clear_scanner_cache", lambda **kw: None)
    rc = sec_bench.main([str(fake_plugin), "--runs", "1", "--no-report"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Speedup vs A" in out
    # Phase A speedup vs itself should be 1.00x (header row test)
    assert "1.00x" in out or "1.00×" in out
