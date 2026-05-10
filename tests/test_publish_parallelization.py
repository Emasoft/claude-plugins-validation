"""Phase A regression tests: pytest-xdist wiring in publish.py Gate 2.

These tests pin the contract: Gate 2 must invoke pytest with -n auto
--dist=worksteal --maxfail=1 (the semantic equivalent of the old -x flag
under parallel execution). If anyone reverts to serial pytest, these
tests catch it.

The actual speedup is measured at publish time, not asserted here — pinning
a wall-clock budget would make the suite flaky on slow CI runners.
"""

from __future__ import annotations

import inspect
import sys
import textwrap
from pathlib import Path

# Add scripts/ to sys.path so we can import publish without uv
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import publish  # noqa: E402


def test_gate2_uses_xdist_n_auto():
    """Gate 2 (stage_run_tests) must invoke pytest with -n auto.

    Source-of-truth check: read the function source and confirm the literal
    flags appear. We cannot easily monkey-patch + run the actual pytest call
    without spawning a subprocess that re-runs the suite, so we pin the
    command line by inspection.
    """
    src = inspect.getsource(publish.stage_run_tests)
    # The four flags that Phase A introduced — every one must be present.
    for flag in ('"-n"', '"auto"', '"--dist=worksteal"', '"--maxfail=1"'):
        assert flag in src, f"Gate 2 lost flag {flag} — pytest no longer parallel"


def test_gate2_does_not_use_legacy_x_flag():
    """The old `-x` flag is incompatible with xdist worker semantics.

    Under xdist, workers can't coordinate stop-on-first-fail; pytest-xdist
    drops `-x` silently. The replacement is `--maxfail=1`. If `-x` reappears
    here, someone bypassed Phase A — this test forces a discussion.
    """
    src = inspect.getsource(publish.stage_run_tests)
    # Match the literal `"-x"` argument (not just the letter x in any context).
    assert '"-x"' not in src, (
        "Gate 2 reintroduced `-x` after Phase A. "
        "Use `--maxfail=1` instead — it is xdist-compatible."
    )


def test_xdist_collection_smoke(tmp_path):
    """Smoke test: pytest --collect-only with -n auto succeeds on a tiny
    fixture suite. Catches the case where pytest-xdist isn't installed
    (regression: someone removes it from pyproject.toml).
    """
    # Build a 3-test fixture suite.
    test_file = tmp_path / "test_dummy.py"
    test_file.write_text(textwrap.dedent('''
        def test_a(): assert 1 + 1 == 2
        def test_b(): assert "a" + "b" == "ab"
        def test_c(): assert [1, 2, 3][0] == 1
    '''))

    import subprocess
    result = subprocess.run(
        ["uv", "run", "pytest", str(test_file), "--collect-only", "-q",
         "-n", "auto", "--dist=worksteal"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, (
        f"pytest --collect-only -n auto failed (exit {result.returncode}). "
        f"pytest-xdist may not be installed.\nstderr: {result.stderr[:400]}"
    )
    # Confirm the 3 tests were collected (xdist's collection output may
    # vary across versions, so look for the count rather than exact format).
    assert "3 tests collected" in result.stdout or "test_a" in result.stdout, (
        f"xdist collection didn't see 3 tests: {result.stdout[:400]}"
    )
