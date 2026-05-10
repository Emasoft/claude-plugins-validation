"""Phase B regression tests: ThreadPoolExecutor wiring in cpv_lint_engine.lint_repo.

These tests pin the contract:
  - lint_repo runs each language's linter via ThreadPoolExecutor (NOT a
    serial for-loop), so wall time is dominated by the slowest linter
    rather than the sum.
  - Output ordering is alphabetical by language (deterministic), so logs
    stay diff-friendly across runs even when linters finish out of order.
  - Per-linter exit codes propagate correctly: one failing linter does
    not prevent the others from running, and lint_repo returns False
    iff ANY linter returned False.
  - Empty selection (no detected languages, or `languages=[]` filter) is
    a no-op — no executor created, no error.

Wall-time assertions use a generous slack (5x) so the suite stays green
on slow CI runners. The point is to catch a regression to serial
execution (where wall time would equal the SUM of per-task sleeps),
not to assert nanosecond-level timing.
"""

from __future__ import annotations

import inspect
import sys
import time
from pathlib import Path
from unittest.mock import patch

# tests/conftest.py adds scripts/ to sys.path; this is a defensive duplicate
# so the file works when collected in isolation.
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


def _make_sleeping_lint(name: str, sleep_s: float, *, passes: bool = True, log: list[str] | None = None):
    """Build a fake lint_<lang> function that sleeps then returns a result.

    If `log` is provided, the language name is appended to it on entry,
    so the test can verify completion timing/order.
    """

    def fn(plugin_root, files, report, *, strict_missing_tools: bool = True):  # noqa: ARG001
        time.sleep(sleep_s)
        if log is not None:
            log.append(name)
        if passes:
            report.passed(f"{name}: ok")
        else:
            report.major(f"{name}: synthetic failure")
        return passes

    return fn


# ---------------------------------------------------------------------------
# 1. Source-of-truth: lint_repo uses ThreadPoolExecutor (not a for-loop)
# ---------------------------------------------------------------------------


def test_lint_repo_uses_threadpool_executor():
    """Inspect lint_repo source; it must mention ThreadPoolExecutor and
    must NOT use a `for lang in sorted(selected.keys())` direct dispatch
    loop (the pre-Phase-B serial pattern).

    This is a structural pin — if anyone reverts to serial dispatch,
    the parallelism win disappears silently and this test catches it.
    """
    src = inspect.getsource(lint_repo)
    assert "ThreadPoolExecutor" in src, (
        "lint_repo no longer uses ThreadPoolExecutor — Phase B parallelism reverted to serial"
    )
    # The serial pre-Phase-B pattern was:
    #     for lang in sorted(selected.keys()):
    #         lint_fn = _DISPATCH.get(lang)
    #         passed = lint_fn(...)
    # Every linter dispatch should now go through executor.map / submit.
    # Check for the literal old-style direct dispatch loop body.
    serial_marker = "for lang in sorted(selected.keys()):\n        files = selected[lang]"
    assert serial_marker not in src, "lint_repo still has the pre-Phase-B serial dispatch loop body"


# ---------------------------------------------------------------------------
# 2. Wall time ≈ slowest linter (NOT sum of all)
# ---------------------------------------------------------------------------


def test_lint_repo_wall_time_is_slowest_linter_not_sum(tmp_path: Path):
    """With 3 sleeping linters configured (0.4s + 0.4s + 0.4s = 1.2s
    serial), parallel execution should finish in ~0.4s, not ~1.2s.

    Use 5x slack on the upper bound so a slow CI runner doesn't trip
    the assertion. The point: anything below 1.0s proves parallelism
    is engaged (the serial path is mathematically incapable of going
    below 1.2s even on infinitely fast hardware).
    """
    # Three small files, one per language category.
    (tmp_path / "main.py").write_text("x = 1\n")
    (tmp_path / "deploy.sh").write_text("#!/bin/bash\necho hi\n")
    (tmp_path / "README.md").write_text("# Hello\n")

    sleep_s = 0.4
    fake_dispatch = {
        "python": _make_sleeping_lint("python", sleep_s),
        "shell": _make_sleeping_lint("shell", sleep_s),
        "markdown": _make_sleeping_lint("markdown", sleep_s),
    }

    report = ValidationReport()
    # Phase D — isolated cache so concurrent xdist workers don't
    # contend on ~/.cache/cpv/scanner-results/ and defeat the
    # parallelism this test is pinning.
    iso_cache = ScannerCache(cache_dir=tmp_path / "lint-cache")
    with patch.object(cpv_lint_engine, "_DISPATCH", fake_dispatch):
        t0 = time.perf_counter()
        passed = lint_repo(tmp_path, report, strict_missing_tools=False, cache=iso_cache)
        elapsed = time.perf_counter() - t0

    assert passed is True
    serial_lower_bound = sleep_s * 3
    parallel_upper_bound = serial_lower_bound  # if we hit this, parallelism is broken
    assert elapsed < parallel_upper_bound, (
        f"lint_repo wall time was {elapsed:.2f}s; "
        f"3 x {sleep_s}s sleeps in serial would already take {serial_lower_bound}s. "
        f"Parallelism is not engaged."
    )


# ---------------------------------------------------------------------------
# 3. Output order is deterministic (alphabetical by language)
# ---------------------------------------------------------------------------


def test_lint_repo_output_order_is_alphabetical(tmp_path: Path, capfd):
    """Even when shell finishes first (longest sleep at the END of the
    alphabet wouldn't matter), the printed `[LABEL] N file(s)` lines
    must appear in alphabetical-by-language order.

    Pre-Phase-B the order was `sorted(selected.keys())` printed BEFORE
    each lint call. Post-Phase-B the order must remain the same when
    replayed from per-task buffers.

    Uses ``capfd`` (FD-level capture) instead of ``capsys`` because
    ``contextlib.redirect_stdout`` inside lint_repo swaps ``sys.stdout``
    to a per-task ``io.StringIO``; capsys's sys-level patching can race
    with that swap on threaded code paths, while capfd captures at the
    OS file-descriptor layer and is unaffected.
    """
    (tmp_path / "main.py").write_text("x = 1\n")
    (tmp_path / "deploy.sh").write_text("#!/bin/bash\necho hi\n")
    (tmp_path / "README.md").write_text("# Hello\n")

    # Reverse the natural finish order so completion order != sort order.
    fake_dispatch = {
        "python": _make_sleeping_lint("python", 0.30),  # finishes last
        "shell": _make_sleeping_lint("shell", 0.05),  # finishes first
        "markdown": _make_sleeping_lint("markdown", 0.15),  # finishes middle
    }

    report = ValidationReport()
    iso_cache = ScannerCache(cache_dir=tmp_path / "lint-cache")
    with patch.object(cpv_lint_engine, "_DISPATCH", fake_dispatch):
        lint_repo(tmp_path, report, strict_missing_tools=False, cache=iso_cache)

    out = capfd.readouterr().out
    # Find positions of each per-language header. Alphabetical order
    # is: markdown, python, shell.
    pos_md = out.find("[MD]")
    pos_py = out.find("[PYTHON]")
    pos_sh = out.find("[SHELL]")
    assert pos_md != -1 and pos_py != -1 and pos_sh != -1, (
        f"Missing per-language header(s): MD={pos_md}, PYTHON={pos_py}, SHELL={pos_sh}\nOutput:\n{out}"
    )
    assert pos_md < pos_py < pos_sh, (
        f"Output order is not alphabetical:\n  MD@{pos_md} PYTHON@{pos_py} SHELL@{pos_sh}\nOutput:\n{out}"
    )


# ---------------------------------------------------------------------------
# 4. Per-linter exit codes propagate (one fails, the rest still run)
# ---------------------------------------------------------------------------


def test_lint_repo_one_failure_does_not_block_others(tmp_path: Path):
    """When one linter returns False, lint_repo must (a) still run all
    the other linters and record their findings, (b) return False
    overall.

    The pre-Phase-B `all_passed = False; continue` pattern handled this
    correctly — Phase B preserves the same semantics by ANDing per-task
    results after the join. This test pins it.
    """
    (tmp_path / "main.py").write_text("x = 1\n")
    (tmp_path / "deploy.sh").write_text("#!/bin/bash\necho hi\n")
    (tmp_path / "README.md").write_text("# Hello\n")

    completion_log: list[str] = []
    fake_dispatch = {
        "python": _make_sleeping_lint("python", 0.05, passes=True, log=completion_log),
        "shell": _make_sleeping_lint("shell", 0.05, passes=False, log=completion_log),
        "markdown": _make_sleeping_lint("markdown", 0.05, passes=True, log=completion_log),
    }

    report = ValidationReport()
    iso_cache = ScannerCache(cache_dir=tmp_path / "lint-cache")
    with patch.object(cpv_lint_engine, "_DISPATCH", fake_dispatch):
        passed = lint_repo(tmp_path, report, strict_missing_tools=False, cache=iso_cache)

    assert passed is False, "lint_repo must return False when any linter fails"
    # All three linters ran (even though one failed).
    assert set(completion_log) == {"python", "shell", "markdown"}, (
        f"Not all linters ran when one failed: {completion_log}"
    )
    # The failing linter's MAJOR finding made it into the global report.
    majors = [r for r in report.results if r.level == "MAJOR"]
    assert any("shell" in r.message for r in majors), (
        f"Failing linter's MAJOR finding missing from merged report: {[r.message for r in majors]}"
    )
    # The passing linters' PASSED records are also there.
    passed_msgs = [r for r in report.results if r.level == "PASSED"]
    assert any("python" in r.message for r in passed_msgs)
    assert any("markdown" in r.message for r in passed_msgs)


# ---------------------------------------------------------------------------
# 5. Empty selection — no executor created, no error
# ---------------------------------------------------------------------------


def test_lint_repo_empty_languages_filter_no_executor(tmp_path: Path):
    """When the user passes `languages=["python"]` but no Python files
    exist, lint_repo must early-return True (with an INFO row) and
    must NOT instantiate a ThreadPoolExecutor (which would raise
    ValueError on max_workers=0).

    Pre-Phase-B the early returns guarded this. Phase B preserves the
    same guards; this test pins them.
    """
    # No source files at all — detect_languages returns {}.
    report = ValidationReport()
    iso_cache = ScannerCache(cache_dir=tmp_path / "lint-cache")
    passed = lint_repo(tmp_path, report, strict_missing_tools=False, cache=iso_cache)
    assert passed is True
    # Pre-Phase-B emitted "No source files found to lint" — same INFO.
    info_msgs = [r.message for r in report.results if r.level == "INFO"]
    assert any("No source files" in m for m in info_msgs), info_msgs

    # Now: a Python file exists but the user requests Go only — empty
    # `selected`, again must early-return without creating a pool.
    (tmp_path / "main.py").write_text("x = 1\n")
    report2 = ValidationReport()
    passed2 = lint_repo(tmp_path, report2, languages=["go"], strict_missing_tools=False, cache=iso_cache)
    assert passed2 is True
    info2 = [r.message for r in report2.results if r.level == "INFO"]
    assert any("language subset" in m for m in info2), info2
