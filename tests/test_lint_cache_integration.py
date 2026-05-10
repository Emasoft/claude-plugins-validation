"""Phase D integration tests: scanner-result cache wired into lint_repo.

These tests pin the contract that ``lint_repo`` actually consults the
``ScannerCache`` it receives:

  - Cold cache → linters run, populate the cache.
  - Warm cache (same files) → linters do NOT run; cached results are
    replayed into the report.
  - Touching one source file → only the touched language re-scans;
    every other language hits the cache.

The cold/warm distinction is verified via mock injection, not by
relying on real linter binaries (which may or may not be present on
the test machine, and which would also produce non-deterministic
findings depending on their version).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# tests/conftest.py adds scripts/ to sys.path; defensive duplicate so
# the file works when collected in isolation.
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


def _make_repo_with_python(tmp_path: Path) -> Path:
    """Create a tiny "plugin" tree with one python file ready for linting."""
    repo = tmp_path / "fake-plugin"
    repo.mkdir()
    (repo / "scripts").mkdir()
    (repo / "scripts" / "hello.py").write_text(
        "def hello() -> str:\n    return 'world'\n",
        encoding="utf-8",
    )
    return repo


def _patched_lints(call_log: list[str]) -> dict:
    """Build a dispatch-table patch that records each lint invocation.

    Each entry returns True (no findings, no failure). The test asserts
    that the python entry is invoked exactly once on the cold run, and
    NOT invoked at all on the warm run.
    """

    def make(name: str):
        def fn(plugin_root, files, report, *, strict_missing_tools: bool = True):  # noqa: ARG001
            call_log.append(name)
            report.passed(f"{name}: ok")
            return True

        return fn

    return {
        "python": make("python"),
        "javascript": make("javascript"),
        "shell": make("shell"),
        "go": make("go"),
        "rust": make("rust"),
        "markdown": make("markdown"),
        "json": make("json"),
        "yaml": make("yaml"),
        "dockerfile": make("dockerfile"),
        "xml": make("xml"),
        "css": make("css"),
        "html": make("html"),
        "sql": make("sql"),
        "toml": make("toml"),
        "powershell": make("powershell"),
    }


# ---------------------------------------------------------------------------
# 1. cold cache
# ---------------------------------------------------------------------------


def test_cold_cache_runs_scanner_and_populates(tmp_path: Path) -> None:
    """First call against an empty cache → linter runs, cache gets a
    new entry.

    This pins that ``lint_repo`` does NOT silently swallow the cache
    parameter — passing one in actually wires it through to ``_run_one``.
    """
    repo = _make_repo_with_python(tmp_path)
    cache_dir = tmp_path / "cache"
    cache = ScannerCache(cache_dir=cache_dir)

    call_log: list[str] = []
    with patch.object(cpv_lint_engine, "_DISPATCH", _patched_lints(call_log)):
        report = ValidationReport()
        passed = lint_repo(repo, report, cache=cache)

    assert passed is True
    assert call_log == ["python"], f"expected exactly one python lint call, got {call_log}"
    # Cache directory should now have at least one .json entry —
    # every lint that produced findings (even just "passed: ok")
    # gets cached.
    cache_files = list(cache_dir.iterdir())
    assert any(f.name.endswith(".json") for f in cache_files), (
        f"cold run did not populate cache: {[f.name for f in cache_files]}"
    )


# ---------------------------------------------------------------------------
# 2. warm cache
# ---------------------------------------------------------------------------


def test_warm_cache_skips_scanner_subprocess(tmp_path: Path) -> None:
    """Two consecutive calls against the same tree:
      - first call populates the cache
      - second call must NOT invoke the patched linter at all
        (cache hit replays the stored findings)

    This is the headline warm-run win: re-running validate_plugin
    against an unchanged tree skips every linter subprocess.
    """
    repo = _make_repo_with_python(tmp_path)
    cache = ScannerCache(cache_dir=tmp_path / "cache")

    cold_log: list[str] = []
    with patch.object(cpv_lint_engine, "_DISPATCH", _patched_lints(cold_log)):
        report1 = ValidationReport()
        lint_repo(repo, report1, cache=cache)
    assert cold_log == ["python"], "cold run did not call python lint"

    warm_log: list[str] = []
    with patch.object(cpv_lint_engine, "_DISPATCH", _patched_lints(warm_log)):
        report2 = ValidationReport()
        lint_repo(repo, report2, cache=cache)

    # The warm run must NOT have invoked the patched linter.
    assert warm_log == [], (
        f"warm run still invoked the linter (cache miss?): {warm_log}"
    )
    # The replayed report must contain the cached findings (the
    # "python: ok" PASSED line we wrote on the cold run).
    levels = [r.level for r in report2.results]
    messages = [r.message for r in report2.results]
    assert "PASSED" in levels, f"warm run report has no PASSED entry: {levels}"
    assert any("python: ok" in m for m in messages), (
        f"warm run did not replay the cached PASSED message: {messages}"
    )


# ---------------------------------------------------------------------------
# 3. targeted change
# ---------------------------------------------------------------------------


def test_targeted_file_change_invalidates_only_that_language(tmp_path: Path) -> None:
    """Touch one python file → the python entry MUST re-scan; every
    other entry stays cached.

    Sets up a tree with both python AND markdown files so the test
    can verify that only the language whose tree merkle changed gets
    invalidated, not the others.
    """
    repo = _make_repo_with_python(tmp_path)
    # Add one markdown file so detect_languages also picks markdown.
    (repo / "README.md").write_text("# hello\n", encoding="utf-8")

    cache = ScannerCache(cache_dir=tmp_path / "cache")

    cold_log: list[str] = []
    with patch.object(cpv_lint_engine, "_DISPATCH", _patched_lints(cold_log)):
        report1 = ValidationReport()
        lint_repo(repo, report1, cache=cache)
    # Both languages were detected and linted on the cold run.
    assert "python" in cold_log
    assert "markdown" in cold_log

    # Drift the python file — same name, different content. The
    # tree merkle for python flips; the markdown merkle does not.
    (repo / "scripts" / "hello.py").write_text(
        "def hello() -> str:\n    return 'CHANGED'\n",
        encoding="utf-8",
    )

    warm_log: list[str] = []
    with patch.object(cpv_lint_engine, "_DISPATCH", _patched_lints(warm_log)):
        report2 = ValidationReport()
        lint_repo(repo, report2, cache=cache)

    # python re-scanned (its content drifted); markdown stayed cached.
    assert "python" in warm_log, f"python lint did not re-run after edit: {warm_log}"
    assert "markdown" not in warm_log, (
        f"markdown lint re-ran despite no content change: {warm_log}"
    )


# ---------------------------------------------------------------------------
# 4. cache=None default constructs a real cache without crashing
# ---------------------------------------------------------------------------


def test_cache_none_default_constructs_real_cache(tmp_path: Path) -> None:
    """Calling ``lint_repo`` without a cache parameter must construct
    one against ``~/.cache/cpv/scanner-results/`` and not crash.

    Mocks ScannerCache so the real user cache directory is never
    touched. The point is to verify the default branch is reachable.
    """
    repo = _make_repo_with_python(tmp_path)
    fake_cache = ScannerCache(cache_dir=tmp_path / "fake-default-cache")

    call_log: list[str] = []
    with (
        patch.object(cpv_lint_engine, "_DISPATCH", _patched_lints(call_log)),
        patch("cpv_lint_engine.ScannerCache", return_value=fake_cache),
    ):
        report = ValidationReport()
        passed = lint_repo(repo, report)  # NO cache parameter

    assert passed is True
    # The fake cache directory must have been used (not the real one).
    assert (tmp_path / "fake-default-cache").exists()
