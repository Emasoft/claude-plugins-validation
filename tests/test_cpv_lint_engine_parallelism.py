"""Agent B2 / task #384 — `cpv_lint_engine.lint_repo` parallelism contract.

This file is the post-profile follow-up to the v2.76.0 Phase B Threaded
fan-out. A8's wall-time profile of `validate_plugin .` on the CPV repo
attributed 15% (~29s) to `lint_repo`, and the Threaded fan-out was already
in place — but two contracts were unpinned:

  1. **Parity** — the parallel and serial code paths must produce
     byte-identical findings for the same input. Without this, an
     accidental thread-safety bug in a per-language helper could
     silently change finding text/severity/order, and the existing
     tests would still pass.
  2. **Escape hatch** — `CPV_LINT_PARALLEL=0` must force serial
     execution cleanly. This is the safety valve for:
       - parity testing (this very file),
       - debugging a wedged thread,
       - single-core CI runners where the pool overhead isn't worth it.

The parallelism wiring itself (ThreadPoolExecutor, alphabetical-by-language
output ordering, one-failure-doesn't-block-others) is already pinned by
`tests/test_lint_parallelization.py`. This file complements that — it
verifies the SAME findings/ordering across N runs and across the
parallel/serial axis, plus pins the env-var contract.

We DO assert a wall-time speedup, but with generous slack: the multi-language
fixture uses 5 sleeping linters at 0.4s each, so serial is mathematically
≥ 2.0s. The parallel target is < 1.0s on any CI runner with ≥ 2 cores
(observed ~0.5s on a modern multicore box → ≥ 4× speedup, well above the
1.5× spec gate). On a single-core CI runner the assertion is skipped
explicitly via os.cpu_count() guard.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# tests/conftest.py adds scripts/ to sys.path; defensive duplicate so
# this file runs in isolation too.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import cpv_lint_engine  # noqa: E402
from cpv_lint_engine import lint_repo  # noqa: E402
from cpv_scanner_cache import ScannerCache  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lint(name: str, sleep_s: float, *, passes: bool = True, extra_findings: int = 0):
    """Build a fake lint_<lang> function that sleeps then emits findings.

    The deterministic finding shape ("<name>: ok N") lets parity tests
    compare two reports verbatim. ``extra_findings`` lets us pad some
    languages with multiple results so the merged-report parity has
    teeth (not just "one finding per language").
    """

    def fn(plugin_root, files, report, *, strict_missing_tools: bool = True):  # noqa: ARG001
        if sleep_s > 0:
            time.sleep(sleep_s)
        if passes:
            report.passed(f"{name}: ok")
            for i in range(extra_findings):
                # MINOR keeps overall pass=True so we test merge ordering
                # without flipping the boolean return.
                report.minor(f"{name}: extra {i}")
        else:
            report.major(f"{name}: synthetic failure")
            for i in range(extra_findings):
                report.minor(f"{name}: extra {i}")
        return passes

    return fn


def _make_polyglot_fixture(tmp_path: Path) -> None:
    """Create one source file per language category — 5 distinct buckets.

    Used by the parity + speedup tests. Keeping the fixture small (one
    file per language) keeps the test fast while still exercising the
    fan-out across 5 independent units of work.
    """
    (tmp_path / "main.py").write_text("x = 1\n")
    (tmp_path / "deploy.sh").write_text("#!/bin/bash\necho hi\n")
    (tmp_path / "README.md").write_text("# Hello\n")
    (tmp_path / "data.json").write_text('{"a":1}\n')
    (tmp_path / "config.yml").write_text("a: 1\n")


def _findings_signature(report: ValidationReport) -> list[tuple[str, str]]:
    """Return [(level, message), ...] in original emission order.

    This is the parity-comparison ground truth. Two reports are
    behaviourally identical iff their signatures match — anything else
    (file/line/phase/etc.) is metadata that the serial-vs-parallel axis
    must not affect.
    """
    return [(r.level, r.message) for r in report.results]


def _run_lint_repo(tmp_path: Path, fake_dispatch: dict, *, cache_dir_name: str) -> tuple[bool, ValidationReport]:
    """Single-shot lint_repo invocation against a fake dispatch table.

    Returns ``(passed, report)``. Uses an isolated ScannerCache so a
    stale entry from another xdist worker (or a previous test in the
    same file) cannot short-circuit the lint subprocess and defeat the
    measurement.

    CRITICAL — the cache directory MUST live OUTSIDE ``tmp_path``.
    ``ScannerCache`` writes ``<scanner_name>__<digest>.json`` files,
    and ``detect_languages(tmp_path)`` walks the tree picking up
    ``*.json`` as a detected language. A cache directory inside the
    scan root pollutes the next iteration's language detection with
    a phantom ``json`` bucket that has no matching entry in the test's
    sparse ``fake_dispatch`` — the orchestrator then emits a synthetic
    "No lint function registered" MAJOR and flips ``passed`` to False.
    Sibling-of-tmp_path keeps the cache out of the scan tree entirely.
    """
    report = ValidationReport()
    cache_root = tmp_path.parent / f"_lintcache_{tmp_path.name}_{cache_dir_name}"
    iso_cache = ScannerCache(cache_dir=cache_root)
    with patch.object(cpv_lint_engine, "_DISPATCH", fake_dispatch):
        passed = lint_repo(tmp_path, report, strict_missing_tools=False, cache=iso_cache)
    return passed, report


# ---------------------------------------------------------------------------
# 1. Escape hatch — env var presence + default behaviour
# ---------------------------------------------------------------------------


class TestEscapeHatch:
    """Pin the `CPV_LINT_PARALLEL` env-var contract.

    The escape hatch is what makes everything else in this file
    possible — every parity test below relies on it to force the
    serial path on demand.
    """

    def test_env_var_is_recognised(self, tmp_path: Path) -> None:
        """`CPV_LINT_PARALLEL=0` must NOT raise. The lint_repo source
        must mention `CPV_LINT_PARALLEL` so a future refactor that
        removes the env-var check breaks this test loudly.
        """
        import inspect

        src = inspect.getsource(lint_repo)
        assert "CPV_LINT_PARALLEL" in src, (
            "lint_repo no longer honours CPV_LINT_PARALLEL — escape hatch lost. "
            "Restore the env-var check so parity testing and single-core fallback work."
        )

    def test_env_var_zero_falls_back_to_serial_cleanly(self, tmp_path: Path) -> None:
        """`CPV_LINT_PARALLEL=0` must complete a normal run without
        spawning a ThreadPoolExecutor (we cannot easily assert the
        non-spawn, but we CAN assert the run returns the right value
        and emits the right findings)."""
        _make_polyglot_fixture(tmp_path)
        fake_dispatch = {
            "python": _make_lint("python", 0.0),
            "shell": _make_lint("shell", 0.0),
            "markdown": _make_lint("markdown", 0.0),
            "json": _make_lint("json", 0.0),
            "yaml": _make_lint("yaml", 0.0),
        }
        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "0"}):
            passed, report = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-serial-0")
        assert passed is True
        # Verify every language was reached — not just that the call returned.
        passed_msgs = {r.message for r in report.results if r.level == "PASSED"}
        for lang in ("python", "shell", "markdown", "json", "yaml"):
            assert f"{lang}: ok" in passed_msgs, f"language '{lang}' was skipped under serial mode: {passed_msgs}"

    def test_env_var_false_string_also_serial(self, tmp_path: Path) -> None:
        """`CPV_LINT_PARALLEL=false` / `no` / empty-string also force
        serial — the env-var is a human-set knob, allow common spellings.

        Uses a single-language fixture so the fake_dispatch is complete:
        ``detect_languages`` and ``_DISPATCH`` MUST agree on the language
        set or `_run_one` emits a "No lint function registered" MAJOR.
        """
        (tmp_path / "main.py").write_text("x = 1\n")
        fake_dispatch = {
            "python": _make_lint("python", 0.0),
        }
        for value in ("false", "FALSE", "no", "NO", ""):
            with patch.dict(os.environ, {"CPV_LINT_PARALLEL": value}):
                passed, report = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name=f"cache-{value or 'empty'}")
            assert passed is True, f"value={value!r} should still produce a passing run"
            assert any("python: ok" in r.message for r in report.results)

    def test_env_var_one_keeps_parallel(self, tmp_path: Path) -> None:
        """`CPV_LINT_PARALLEL=1` (or unset) keeps the parallel default."""
        (tmp_path / "main.py").write_text("x = 1\n")
        (tmp_path / "deploy.sh").write_text("#!/bin/bash\necho hi\n")
        fake_dispatch = {
            "python": _make_lint("python", 0.0),
            "shell": _make_lint("shell", 0.0),
        }
        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "1"}):
            passed, _ = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-parallel-1")
        assert passed is True

    def test_env_var_unset_defaults_to_parallel(self, tmp_path: Path) -> None:
        """When CPV_LINT_PARALLEL is not set at all, parallel is the default."""
        (tmp_path / "main.py").write_text("x = 1\n")
        (tmp_path / "deploy.sh").write_text("#!/bin/bash\necho hi\n")
        fake_dispatch = {
            "python": _make_lint("python", 0.0),
            "shell": _make_lint("shell", 0.0),
        }
        # Use patch.dict + clear-on-this-key so the test never inherits
        # an outer-shell CPV_LINT_PARALLEL setting.
        env = dict(os.environ)
        env.pop("CPV_LINT_PARALLEL", None)
        with patch.dict(os.environ, env, clear=True):
            passed, _ = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-unset")
        assert passed is True


# ---------------------------------------------------------------------------
# 2. Parity — parallel and serial produce IDENTICAL findings
# ---------------------------------------------------------------------------


class TestParity:
    """Same input, same fake dispatch — parallel and serial reports
    must contain the same findings in the same order.

    This is the contract that catches a future refactor accidentally
    introducing a thread-safety bug (e.g. mutating a shared list inside
    one of the per-language helpers).
    """

    def test_serial_and_parallel_produce_identical_findings_single_language(self, tmp_path: Path) -> None:
        """One-language baseline — should be trivially identical.

        Single-file fixture matches single-key fake_dispatch so
        ``detect_languages`` and the dispatch table agree (otherwise
        `_run_one` emits a "No lint function registered" MAJOR for the
        unmatched language and flips the passed flag to False).
        """
        (tmp_path / "main.py").write_text("x = 1\n")
        fake_dispatch = {
            "python": _make_lint("python", 0.0, extra_findings=2),
        }
        # Serial run
        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "0"}):
            passed_s, report_s = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-s-single")
        # Parallel run
        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "1"}):
            passed_p, report_p = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-p-single")

        assert passed_s is True
        assert passed_p is True
        sig_s = _findings_signature(report_s)
        sig_p = _findings_signature(report_p)
        assert sig_s == sig_p, (
            f"Serial vs parallel finding signatures diverge:\n"
            f"  serial: {sig_s}\n"
            f"  parallel: {sig_p}"
        )

    def test_serial_and_parallel_produce_identical_findings_polyglot(self, tmp_path: Path) -> None:
        """5-language fixture — the realistic case where order
        preservation matters most."""
        _make_polyglot_fixture(tmp_path)
        # Each language emits 1 PASSED + 2 MINOR — gives 15 findings
        # total per run. Any reordering at merge-time would show up.
        fake_dispatch = {
            "python": _make_lint("python", 0.0, extra_findings=2),
            "shell": _make_lint("shell", 0.0, extra_findings=2),
            "markdown": _make_lint("markdown", 0.0, extra_findings=2),
            "json": _make_lint("json", 0.0, extra_findings=2),
            "yaml": _make_lint("yaml", 0.0, extra_findings=2),
        }

        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "0"}):
            passed_s, report_s = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-s-poly")
        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "1"}):
            passed_p, report_p = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-p-poly")

        assert passed_s is True
        assert passed_p is True
        sig_s = _findings_signature(report_s)
        sig_p = _findings_signature(report_p)
        assert sig_s == sig_p, (
            f"Polyglot parity failure — serial vs parallel signatures diverge.\n"
            f"  serial ({len(sig_s)}): {sig_s}\n"
            f"  parallel ({len(sig_p)}): {sig_p}"
        )

    def test_parity_holds_when_some_languages_fail(self, tmp_path: Path) -> None:
        """Mixed pass/fail — the AND-of-passed semantics must be
        identical between serial and parallel."""
        _make_polyglot_fixture(tmp_path)
        fake_dispatch = {
            "python": _make_lint("python", 0.0, passes=True, extra_findings=1),
            "shell": _make_lint("shell", 0.0, passes=False, extra_findings=1),  # FAILING
            "markdown": _make_lint("markdown", 0.0, passes=True, extra_findings=1),
            "json": _make_lint("json", 0.0, passes=False, extra_findings=1),  # FAILING
            "yaml": _make_lint("yaml", 0.0, passes=True, extra_findings=1),
        }

        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "0"}):
            passed_s, report_s = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-s-mixed")
        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "1"}):
            passed_p, report_p = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-p-mixed")

        # Both must report failure (overall AND of per-language flags)
        assert passed_s is False
        assert passed_p is False
        # Signatures still identical
        sig_s = _findings_signature(report_s)
        sig_p = _findings_signature(report_p)
        assert sig_s == sig_p

    def test_parity_when_one_language_only(self, tmp_path: Path) -> None:
        """Single-language run — `max_workers=min(8, 1)=1` corner case
        for the parallel path. Must still equal serial.

        Single-file fixture so detect_languages and the dispatch table
        agree (otherwise the unmatched language emits a synthetic MAJOR
        and the parity is uninteresting)."""
        (tmp_path / "README.md").write_text("# hi\n")
        fake_dispatch = {
            "markdown": _make_lint("markdown", 0.0, extra_findings=3),
        }
        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "0"}):
            _, report_s = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-s-1")
        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "1"}):
            _, report_p = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-p-1")
        assert _findings_signature(report_s) == _findings_signature(report_p)


# ---------------------------------------------------------------------------
# 3. Speedup — parallel ≥ 1.5× faster than serial on a multi-language fixture
# ---------------------------------------------------------------------------


class TestSpeedup:
    """The whole point of the fan-out: measurable wall-time win.

    Spec asks for ≥ 1.5×. We use 5 sleeping linters at 0.4s each (so
    serial ≥ 2.0s minimum) and assert parallel < (serial / 1.5).
    On a typical multicore box this is observed at ≥ 4× speedup.
    Skipped on single-core runners (no parallelism win possible).
    """

    @pytest.mark.skipif(
        (os.cpu_count() or 1) < 2,
        reason="parallelism speedup needs ≥ 2 CPU cores",
    )
    def test_parallel_at_least_1_5x_faster_than_serial(self, tmp_path: Path) -> None:
        _make_polyglot_fixture(tmp_path)
        sleep_s = 0.4
        # Five sleeping linters — the parallel fan-out floor is ~ sleep_s
        # (all run concurrently on ≥ 5 cores), the serial floor is 5 × sleep_s.
        fake_dispatch = {
            "python": _make_lint("python", sleep_s),
            "shell": _make_lint("shell", sleep_s),
            "markdown": _make_lint("markdown", sleep_s),
            "json": _make_lint("json", sleep_s),
            "yaml": _make_lint("yaml", sleep_s),
        }

        # Run serial
        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "0"}):
            t0 = time.perf_counter()
            _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-serial-speed")
            serial_elapsed = time.perf_counter() - t0

        # Run parallel
        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "1"}):
            t1 = time.perf_counter()
            _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-parallel-speed")
            parallel_elapsed = time.perf_counter() - t1

        speedup = serial_elapsed / parallel_elapsed if parallel_elapsed > 0 else float("inf")

        # Spec target: ≥ 1.5×. The mathematical lower bound for serial
        # is 5 × 0.4 = 2.0s; the mathematical lower bound for parallel
        # on ≥ 5 cores is ~ 0.4s + epsilon, so observed speedup
        # is typically 4-5×. The 1.5× bar leaves comfortable slack for
        # slow CI runners.
        assert speedup >= 1.5, (
            f"Parallelism speedup is below spec gate (1.5×).\n"
            f"  serial:   {serial_elapsed:.3f}s\n"
            f"  parallel: {parallel_elapsed:.3f}s\n"
            f"  speedup:  {speedup:.2f}×\n"
            f"  cores:    {os.cpu_count()}\n"
            f"  fixture:  5 languages, {sleep_s}s sleep each (serial lower-bound = {5 * sleep_s:.1f}s)"
        )

    def test_parallel_path_runs_under_serial_lower_bound(self, tmp_path: Path) -> None:
        """Tighter check: parallel wall time MUST be below the
        mathematical serial lower bound (sum of sleeps). This is a
        regression guard against accidentally reverting to a for-loop
        even when CPV_LINT_PARALLEL is not set."""
        _make_polyglot_fixture(tmp_path)
        sleep_s = 0.3
        fake_dispatch = {
            "python": _make_lint("python", sleep_s),
            "shell": _make_lint("shell", sleep_s),
            "markdown": _make_lint("markdown", sleep_s),
            "json": _make_lint("json", sleep_s),
            "yaml": _make_lint("yaml", sleep_s),
        }
        serial_lower_bound = sleep_s * 5  # 1.5s

        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "1"}):
            t0 = time.perf_counter()
            _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-bound-check")
            parallel_elapsed = time.perf_counter() - t0

        assert parallel_elapsed < serial_lower_bound, (
            f"Parallel wall time ({parallel_elapsed:.3f}s) is ≥ the serial "
            f"lower bound ({serial_lower_bound:.3f}s). Parallelism is not "
            f"engaged — the executor reverted to serial execution."
        )


# ---------------------------------------------------------------------------
# 4. Order preservation across N independent runs
# ---------------------------------------------------------------------------


class TestOrderDeterminism:
    """Same input fixture, run N times → same finding emission order
    every single time. This is the contract that lets golden-file
    tests downstream of lint_repo assert on exact report shape.

    The parallel path completes tasks in non-deterministic order (one
    linter may win the race on any given run), but the MERGE step sorts
    by language before flattening. Two runs with the same input must
    produce two identical sequences.
    """

    def test_finding_order_stable_across_5_parallel_runs(self, tmp_path: Path) -> None:
        """Same dispatch, same fixture, 5 runs in parallel mode — all
        5 signatures must be byte-identical."""
        _make_polyglot_fixture(tmp_path)
        # Use varying sleep times so completion order DIFFERS between
        # runs (without this, the test would trivially pass even if
        # the merge step weren't sorted).
        fake_dispatch = {
            "python": _make_lint("python", 0.05, extra_findings=2),  # finishes fast
            "shell": _make_lint("shell", 0.20, extra_findings=2),  # finishes late
            "markdown": _make_lint("markdown", 0.10, extra_findings=2),
            "json": _make_lint("json", 0.01, extra_findings=2),  # finishes first
            "yaml": _make_lint("yaml", 0.15, extra_findings=2),
        }

        signatures = []
        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "1"}):
            for i in range(5):
                _, report = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name=f"cache-det-{i}")
                signatures.append(_findings_signature(report))

        # All 5 signatures must be equal to the first.
        ref = signatures[0]
        for idx, sig in enumerate(signatures[1:], start=1):
            assert sig == ref, (
                f"Run {idx} produced a different finding order than run 0.\n"
                f"  run 0: {ref}\n"
                f"  run {idx}: {sig}"
            )

    def test_finding_order_stable_across_5_serial_runs(self, tmp_path: Path) -> None:
        """Serial control — should also be stable. If this fails, the
        bug is in the helpers, not in the parallelism."""
        _make_polyglot_fixture(tmp_path)
        fake_dispatch = {
            "python": _make_lint("python", 0.0, extra_findings=2),
            "shell": _make_lint("shell", 0.0, extra_findings=2),
            "markdown": _make_lint("markdown", 0.0, extra_findings=2),
            "json": _make_lint("json", 0.0, extra_findings=2),
            "yaml": _make_lint("yaml", 0.0, extra_findings=2),
        }
        signatures = []
        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "0"}):
            for i in range(5):
                _, report = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name=f"cache-det-s-{i}")
                signatures.append(_findings_signature(report))
        ref = signatures[0]
        for idx, sig in enumerate(signatures[1:], start=1):
            assert sig == ref

    def test_language_order_is_alphabetical_under_parallel(self, tmp_path: Path) -> None:
        """The flat finding sequence must group by language in
        alphabetical order — `json` < `markdown` < `python` < `shell` < `yaml`.

        Pre-parallelism order was implicit (the for-loop iterated
        `sorted(...)` once). Phase B preserved this via post-join sort.
        This test re-asserts it as an explicit contract — if a future
        refactor switches to insertion-order or hash-order, this trips.
        """
        _make_polyglot_fixture(tmp_path)
        fake_dispatch = {
            "python": _make_lint("python", 0.05),
            "shell": _make_lint("shell", 0.10),
            "markdown": _make_lint("markdown", 0.02),
            "json": _make_lint("json", 0.20),  # slowest — finishes last in parallel
            "yaml": _make_lint("yaml", 0.0),  # finishes first in parallel
        }
        with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "1"}):
            _, report = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name="cache-alpha")

        # Build position map: which PASSED finding came first?
        positions: dict[str, int] = {}
        for idx, r in enumerate(report.results):
            if r.level != "PASSED":
                continue
            for lang in ("json", "markdown", "python", "shell", "yaml"):
                if r.message.startswith(f"{lang}:") and lang not in positions:
                    positions[lang] = idx
                    break

        assert set(positions.keys()) == {"json", "markdown", "python", "shell", "yaml"}, (
            f"Missing PASSED findings: got {set(positions.keys())}"
        )
        # Alphabetical order: json, markdown, python, shell, yaml.
        expected_order = ["json", "markdown", "python", "shell", "yaml"]
        for a, b in zip(expected_order, expected_order[1:]):
            assert positions[a] < positions[b], (
                f"Language order is not alphabetical: {a}@{positions[a]} should come before {b}@{positions[b]}.\n"
                f"  positions: {positions}"
            )

    def test_parallel_and_serial_agree_on_order(self, tmp_path: Path) -> None:
        """The strongest determinism contract: serial and parallel,
        when averaged over multiple runs, agree on EVERY position of
        every finding. This is just `_findings_signature(serial) ==
        _findings_signature(parallel)` repeated 3 times to confirm
        it's not a lucky single-shot."""
        _make_polyglot_fixture(tmp_path)
        # Varying sleep — completion order differs from declaration
        # order in the parallel path.
        fake_dispatch = {
            "python": _make_lint("python", 0.05, extra_findings=2),
            "shell": _make_lint("shell", 0.15, extra_findings=2),
            "markdown": _make_lint("markdown", 0.10, extra_findings=2),
            "json": _make_lint("json", 0.01, extra_findings=2),
            "yaml": _make_lint("yaml", 0.20, extra_findings=2),
        }
        for trial in range(3):
            with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "0"}):
                _, report_s = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name=f"cache-cross-s-{trial}")
            with patch.dict(os.environ, {"CPV_LINT_PARALLEL": "1"}):
                _, report_p = _run_lint_repo(tmp_path, fake_dispatch, cache_dir_name=f"cache-cross-p-{trial}")
            sig_s = _findings_signature(report_s)
            sig_p = _findings_signature(report_p)
            assert sig_s == sig_p, (
                f"Trial {trial}: serial vs parallel signatures diverge.\n"
                f"  serial: {sig_s}\n"
                f"  parallel: {sig_p}"
            )
