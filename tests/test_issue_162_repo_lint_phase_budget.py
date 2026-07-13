"""Regression tests for issue #162 — REPO LINT hangs ~27 min on CI despite the
v2.145.0 per-linter timeout (recurrence of #148, different root cause).

#148 (v2.145.0) added a HARD PER-LINTER ceiling (``PLUGIN_REPO_LINT_TIMEOUT``)
so no single linter spawn is unbounded. That is necessary but NOT sufficient:
it bounds each linter, never their SUM. ~17 linters each capped at 60-180s is
~34 min, and on a cold CI runner uv/npm serialize the concurrent ``uvx``/``npx``
first-run fetches on a global cache lock — so the parallel fan-out degrades
toward serial and the phase marches past the CI job's own ``timeout-minutes``,
getting the job SIGKILL'd with the orphaned ``uv``/``python`` children #162
reported. The fix is an AGGREGATE wall-clock budget for the whole phase
(``PLUGIN_REPO_LINT_PHASE_TIMEOUT``, default 600s): once exhausted the remaining
languages are WARNING-skipped (never blocking — the same degrade as a per-linter
timeout and ``PLUGIN_SKIP_REPO_LINT``) and the phase returns promptly, so REPO
LINT can never approach the job wall-clock.

Every behavioral check uses fake sleeping lint functions (monkeypatched
``detect_languages`` + ``_DISPATCH`` + ``_build_cache_key``) so the tests are
CI-stable, need no real linter, and spawn no subprocess. Each assertion is
two-sided: the budget TRIPS on a slow storm AND does NOT trip on a fast run.
"""

from __future__ import annotations

import ast
import sys
import time
from pathlib import Path
from typing import Callable

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cpv_lint_engine  # noqa: E402
from cpv_lint_engine import lint_repo  # noqa: E402
from cpv_scanner_cache import ScannerCache  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402

LINT_ENGINE_SRC = SCRIPTS_DIR / "cpv_lint_engine.py"
PHASE_ENV = "PLUGIN_REPO_LINT_PHASE_TIMEOUT"


def _isolated_cache(tmp_path: Path) -> ScannerCache:
    """A ScannerCache rooted under tmp_path so tests never touch the real cache."""
    return ScannerCache(cache_dir=tmp_path / "cache")


def _sleeper_lint(sleep_s: float, marker: str) -> Callable[..., bool]:
    """A fake lint function that sleeps then records a per-language marker.

    The marker lands in the per-language local report; it only reaches the shared
    report if that language's result is actually MERGED — so a skipped language's
    marker is ABSENT, which is exactly how the tests prove what ran vs was skipped.
    """

    def _fn(plugin_root: Path, files: list[Path], report: ValidationReport, *, strict_missing_tools: bool = True) -> bool:
        time.sleep(sleep_s)
        report.info(marker)
        return True

    return _fn


def _install_fake_langs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    specs: dict[str, float],
) -> dict[str, str]:
    """Wire ``detect_languages``/``_DISPATCH``/``_build_cache_key`` so ``lint_repo``
    sees the given fake ``{lang: sleep_seconds}`` map. Returns ``{lang: marker}``."""
    langs = sorted(specs)
    markers = {lang: f"__ran__{lang}" for lang in langs}
    monkeypatch.setattr(
        cpv_lint_engine, "detect_languages", lambda root: {lang: [tmp_path / f"{lang}.x"] for lang in langs}
    )
    monkeypatch.setattr(
        cpv_lint_engine, "_DISPATCH", {lang: _sleeper_lint(specs[lang], markers[lang]) for lang in langs}
    )
    # Disable the result cache so every language actually invokes its (fake) linter.
    monkeypatch.setattr(cpv_lint_engine, "_build_cache_key", lambda *a, **k: None)
    return markers


def _phase_warnings(report: ValidationReport) -> list[str]:
    return [r.message for r in report.results if r.level == "WARNING" and "phase budget" in r.message]


def _infos(report: ValidationReport) -> list[str]:
    return [r.message for r in report.results if r.level == "INFO"]


# ---------------------------------------------------------------------------
# 1. _phase_timeout() env parsing (unit) — mirrors the #148 _effective_timeout
# ---------------------------------------------------------------------------


class TestPhaseTimeoutEnvParsing:
    """``PLUGIN_REPO_LINT_PHASE_TIMEOUT`` resolves to a positive float or the default."""

    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unset env → the built-in default budget (never disabled by omission)."""
        monkeypatch.delenv(PHASE_ENV, raising=False)
        assert cpv_lint_engine._phase_timeout() == cpv_lint_engine._DEFAULT_PHASE_TIMEOUT

    def test_positive_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A positive value replaces the default."""
        monkeypatch.setenv(PHASE_ENV, "45")
        assert cpv_lint_engine._phase_timeout() == 45.0

    def test_ignores_zero_negative_and_garbage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-positive / non-numeric / blank value falls back to the default, so
        a typo can never DISABLE the guard or set a near-zero ceiling that skips
        every language."""
        for bad in ("0", "-5", "abc", "  "):
            monkeypatch.setenv(PHASE_ENV, bad)
            assert cpv_lint_engine._phase_timeout() == cpv_lint_engine._DEFAULT_PHASE_TIMEOUT


class TestPhaseBudgetMessage:
    """The skip WARNING names the knob, the budget, the issue, and every skipped lang."""

    def test_message_content(self) -> None:
        msg = cpv_lint_engine._phase_budget_skip_message(600.0, ["python", "rust"])
        assert PHASE_ENV in msg
        assert "600s" in msg
        assert "#162" in msg
        assert "python" in msg and "rust" in msg


# ---------------------------------------------------------------------------
# 2. Static guard — the parallel path keeps its aggregate deadline
# ---------------------------------------------------------------------------


class TestParallelMapHasAggregateTimeout:
    """Regression-lock: the parallel ``executor.map`` call in ``lint_repo`` passes a
    ``timeout=`` so a future refactor cannot silently drop the aggregate bound."""

    def test_ex_map_has_timeout_kwarg(self) -> None:
        tree = ast.parse(LINT_ENGINE_SRC.read_text(encoding="utf-8"))
        map_calls_with_timeout = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "map"
            and any(kw.arg == "timeout" for kw in node.keywords)
        ]
        assert map_calls_with_timeout, "the parallel executor.map(...) must pass timeout= (issue #162 aggregate bound)"


# ---------------------------------------------------------------------------
# 3. Budget TRIPS on a slow storm — parallel path
# ---------------------------------------------------------------------------


class TestPhaseBudgetTripsParallel:
    """The parallel path stops at the aggregate deadline and WARNING-skips the rest."""

    def test_partial_completion_parallel(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Fast early languages complete and merge; the slow later ones are skipped
        as a non-blocking WARNING, and the phase returns FAR sooner than the slow
        linters' own duration (it did not wait for them)."""
        markers = _install_fake_langs(
            monkeypatch, tmp_path, {"aaa": 0.02, "bbb": 0.02, "ccc": 4.0, "ddd": 4.0}
        )
        monkeypatch.delenv("CPV_LINT_PARALLEL", raising=False)  # parallel (default)
        monkeypatch.setenv(PHASE_ENV, "0.7")
        report = ValidationReport()

        start = time.monotonic()
        ok = lint_repo(report=report, plugin_root=tmp_path, cache=_isolated_cache(tmp_path), quiet=True)
        elapsed = time.monotonic() - start

        assert ok is True, "a budget-forced skip is a WARNING, never a blocking failure"
        assert elapsed < 3.0, f"phase waited on the 4s slow linters instead of stopping at the budget ({elapsed:.1f}s)"
        warns = _phase_warnings(report)
        assert warns, "budget exhaustion must record a phase-budget WARNING"
        assert "ccc" in warns[0] and "ddd" in warns[0], f"WARNING must name the skipped langs: {warns[0]!r}"
        infos = _infos(report)
        assert markers["aaa"] in infos and markers["bbb"] in infos, "fast early languages must complete and merge"
        assert markers["ccc"] not in infos and markers["ddd"] not in infos, "skipped languages must not merge"
        assert not any(r.level in {"MAJOR", "CRITICAL"} for r in report.results)

    def test_all_skipped_parallel_does_not_crash(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Edge: the budget trips before the FIRST result — zero merged, all skipped,
        empty-results merge loop is safe, and the phase still returns True."""
        _install_fake_langs(monkeypatch, tmp_path, {"aaa": 3.0})
        monkeypatch.delenv("CPV_LINT_PARALLEL", raising=False)
        monkeypatch.setenv(PHASE_ENV, "0.5")
        report = ValidationReport()

        start = time.monotonic()
        ok = lint_repo(report=report, plugin_root=tmp_path, cache=_isolated_cache(tmp_path), quiet=True)
        elapsed = time.monotonic() - start

        assert ok is True
        assert elapsed < 2.0, f"phase waited on the 3s slow linter ({elapsed:.1f}s)"
        warns = _phase_warnings(report)
        assert warns and "aaa" in warns[0]


# ---------------------------------------------------------------------------
# 4. Budget TRIPS on a slow storm — serial path (CPV_LINT_PARALLEL=0)
# ---------------------------------------------------------------------------


class TestPhaseBudgetTripsSerial:
    """The serial fallback stops launching new languages once the budget is spent."""

    def test_partial_completion_serial(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """The first (slow) language runs and merges; once elapsed >= budget the
        remaining languages are WARNING-skipped."""
        markers = _install_fake_langs(
            monkeypatch, tmp_path, {"aaa": 1.0, "bbb": 0.02, "ccc": 0.02, "ddd": 0.02}
        )
        monkeypatch.setenv("CPV_LINT_PARALLEL", "0")
        monkeypatch.setenv(PHASE_ENV, "0.5")
        report = ValidationReport()

        ok = lint_repo(report=report, plugin_root=tmp_path, cache=_isolated_cache(tmp_path), quiet=True)

        assert ok is True
        warns = _phase_warnings(report)
        assert warns, "serial budget exhaustion must record a phase-budget WARNING"
        assert "bbb" in warns[0] and "ccc" in warns[0] and "ddd" in warns[0]
        assert "aaa" not in warns[0], "the completed first language must not be in the skipped list"
        infos = _infos(report)
        assert markers["aaa"] in infos, "the first (slow) language ran and merged before the budget tripped"
        assert markers["bbb"] not in infos, "languages after the budget must be skipped"

    def test_at_least_one_language_always_runs_serial(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Even a near-zero budget runs the FIRST language (the ``idx > 0`` guard)
        before skipping the rest — the budget can never skip everything serially."""
        markers = _install_fake_langs(monkeypatch, tmp_path, {"aaa": 0.02, "bbb": 0.02})
        monkeypatch.setenv("CPV_LINT_PARALLEL", "0")
        monkeypatch.setenv(PHASE_ENV, "0.001")
        report = ValidationReport()

        ok = lint_repo(report=report, plugin_root=tmp_path, cache=_isolated_cache(tmp_path), quiet=True)

        assert ok is True
        infos = _infos(report)
        assert markers["aaa"] in infos, "the first language must ALWAYS run regardless of budget"
        warns = _phase_warnings(report)
        assert warns and "bbb" in warns[0], "the tiny budget honored: subsequent languages skipped"


# ---------------------------------------------------------------------------
# 5. Control — a fast run does NOT trip the budget (two-sided sibling)
# ---------------------------------------------------------------------------


class TestPhaseBudgetDoesNotTripFastRun:
    """FN-safe sibling: a normal (fast) run lints every language with no WARNING."""

    def test_fast_run_completes_all_parallel(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        markers = _install_fake_langs(monkeypatch, tmp_path, {"aaa": 0.02, "bbb": 0.02, "ccc": 0.02, "ddd": 0.02})
        monkeypatch.delenv("CPV_LINT_PARALLEL", raising=False)
        monkeypatch.setenv(PHASE_ENV, "60")  # ample budget
        report = ValidationReport()

        ok = lint_repo(report=report, plugin_root=tmp_path, cache=_isolated_cache(tmp_path), quiet=True)

        assert ok is True
        assert not _phase_warnings(report), "a fast run must not emit a phase-budget WARNING"
        infos = _infos(report)
        assert all(m in infos for m in markers.values()), "every language must complete and merge on a fast run"

    def test_fast_run_completes_all_serial(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        markers = _install_fake_langs(monkeypatch, tmp_path, {"aaa": 0.02, "bbb": 0.02, "ccc": 0.02})
        monkeypatch.setenv("CPV_LINT_PARALLEL", "0")
        monkeypatch.setenv(PHASE_ENV, "60")
        report = ValidationReport()

        ok = lint_repo(report=report, plugin_root=tmp_path, cache=_isolated_cache(tmp_path), quiet=True)

        assert ok is True
        assert not _phase_warnings(report)
        infos = _infos(report)
        assert all(m in infos for m in markers.values())

    def test_default_budget_does_not_trip_normal_run(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """With the env UNSET (600s default), a fast run is unaffected — the guard
        only bites a pathological cold-runner storm, never a healthy run."""
        markers = _install_fake_langs(monkeypatch, tmp_path, {"aaa": 0.02, "bbb": 0.02})
        monkeypatch.delenv("CPV_LINT_PARALLEL", raising=False)
        monkeypatch.delenv(PHASE_ENV, raising=False)
        report = ValidationReport()

        ok = lint_repo(report=report, plugin_root=tmp_path, cache=_isolated_cache(tmp_path), quiet=True)

        assert ok is True
        assert not _phase_warnings(report)
        infos = _infos(report)
        assert all(m in infos for m in markers.values())
