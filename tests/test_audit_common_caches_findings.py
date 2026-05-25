#!/usr/bin/env python3
"""Two-sided regression tests for the common/integrity-caches audit.

Covers three verified findings against ``cpv_validation_common.py``:

- #6  ``is_path_gitignored`` re-compiled the pathspec on every call; it is
      called once-per-path in hot tree-walk loops. Fixed by memoizing the
      compiled spec on ``tuple(patterns)``. Tests: results are identical to
      the un-cached behaviour AND repeated calls compile only once (cache
      hit); a different pattern set produces a distinct spec (cache miss).
- #7  ``save_report_and_print_summary`` swapped ``sys.stdout`` globally and
      wrote the report file non-atomically. Tests: the report is written
      correctly, no ``.tmp`` leftover remains, and ``sys.stdout`` is
      restored even when ``print_fn`` raises.
- #11 dead ``COLORS["MAJOR_DARK"]`` entry. Tests: the key is gone, COLORS
      still has an entry for every ``Level`` value, and ``colorize`` still
      works for every level.

Each finding has a bug-case-now-handled assertion AND a benign-case-still-
works assertion. Nothing under test is mocked.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import get_args

import pytest

# Add scripts directory to path for imports.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import cpv_validation_common as cvc  # noqa: E402
from cpv_validation_common import (  # noqa: E402
    COLORS,
    Level,
    ValidationReport,
    colorize,
    is_path_gitignored,
    save_report_and_print_summary,
)


@pytest.fixture(autouse=True)
def _restore_color_enabled():
    """Reset the process-global ANSI-color flag around every test.

    A sibling test calls validate_plugin.main() which flips the flag off
    when stdout isn't a TTY (true under pytest-xdist). Without this the
    colorize() assertions below see ANSI codes stripped and fail.
    """
    cvc._COLOR_ENABLED = True
    yield
    cvc._COLOR_ENABLED = True


@pytest.fixture(autouse=True)
def _cold_spec_cache():
    """Each test observes a cold ``_compile_gitignore_spec`` LRU.

    The cache is process-global; another test (or an earlier case in this
    file) may have warmed it. Clearing before AND after keeps the cache-hit
    accounting assertions deterministic under any worker scheduling.
    """
    cvc._compile_gitignore_spec.cache_clear()
    yield
    cvc._compile_gitignore_spec.cache_clear()


# ---------------------------------------------------------------------------
# #6 — is_path_gitignored memoizes the compiled pathspec
# ---------------------------------------------------------------------------


class TestGitignoreSpecMemoization:
    """#6: compiled PathSpec is cached on tuple(patterns)."""

    def test_repeated_same_patterns_compile_once_and_match_identically(self):
        """Same pattern set across many calls → one compile, identical results.

        Bug case (fixed): pre-fix every call re-ran PathSpec.from_lines, so a
        hot per-path loop recompiled on every iteration. The cache must now
        register exactly one miss for the first distinct pattern set and only
        hits thereafter — while returning byte-for-byte the same answers.
        """
        patterns = ["*.pyc", "node_modules/", "build/"]
        paths = [
            "foo.pyc",
            "src/bar.pyc",
            "node_modules/x.js",
            "build/out.o",
            "src/main.py",
            "README.md",
        ]

        before = cvc._compile_gitignore_spec.cache_info()
        assert before.hits == 0
        assert before.misses == 0

        # Call once per path several times over — simulating the hot loop.
        results_round_one = [is_path_gitignored(p, patterns) for p in paths]
        results_round_two = [is_path_gitignored(p, patterns) for p in paths]

        # Results are identical between rounds (correctness preserved).
        assert results_round_one == results_round_two
        # And match the documented gitignore semantics for these patterns.
        assert results_round_one == [True, True, True, True, False, False]

        info = cvc._compile_gitignore_spec.cache_info()
        # Exactly one distinct pattern tuple → exactly one compile (miss).
        assert info.misses == 1, f"expected a single compile, got {info.misses}"
        # Every call after the first reused the cached spec.
        assert info.hits == (2 * len(paths)) - 1
        assert info.currsize == 1

    def test_caching_returns_same_spec_object_for_equal_patterns(self):
        """Equal pattern tuples resolve to the SAME compiled PathSpec object.

        Object identity is the strongest proof the spec is reused rather than
        rebuilt — a fresh from_lines() would yield a new object each time.
        """
        patterns_a = ["*.log", "tmp/"]
        # A separate list with equal contents must hit the same cache slot.
        patterns_b = ["*.log", "tmp/"]

        spec_first = cvc._compile_gitignore_spec(tuple(patterns_a))
        spec_again = cvc._compile_gitignore_spec(tuple(patterns_b))

        assert spec_first is not None
        assert spec_first is spec_again
        info = cvc._compile_gitignore_spec.cache_info()
        assert info.misses == 1
        assert info.hits == 1

    def test_different_patterns_produce_distinct_specs_and_results(self):
        """Benign/contrast case: a different pattern set → a different spec.

        Guards against an over-eager cache that returns a stale spec for new
        patterns. Two distinct pattern tuples must miss separately and yield
        their own (correct, different) match results.
        """
        patterns_one = ["*.pyc"]
        patterns_two = ["*.log"]

        spec_one = cvc._compile_gitignore_spec(tuple(patterns_one))
        spec_two = cvc._compile_gitignore_spec(tuple(patterns_two))
        assert spec_one is not spec_two

        info = cvc._compile_gitignore_spec.cache_info()
        assert info.misses == 2
        assert info.currsize == 2

        # And the two specs actually behave differently.
        assert is_path_gitignored("a.pyc", patterns_one) is True
        assert is_path_gitignored("a.pyc", patterns_two) is False
        assert is_path_gitignored("a.log", patterns_two) is True
        assert is_path_gitignored("a.log", patterns_one) is False

    def test_empty_patterns_short_circuit_without_touching_cache(self):
        """No patterns → False, and the compiler is never invoked.

        Edge case: an empty pattern list must not compile an empty spec
        (wasted work) nor pollute the cache.
        """
        assert is_path_gitignored("anything.py", []) is False
        info = cvc._compile_gitignore_spec.cache_info()
        assert info.misses == 0
        assert info.hits == 0
        assert info.currsize == 0


# ---------------------------------------------------------------------------
# #7 — save_report_and_print_summary: stdout restore + atomic write
# ---------------------------------------------------------------------------


def _make_report() -> ValidationReport:
    report = ValidationReport()
    report.info("an informational note", "file.txt")
    report.major("a major problem", "file.txt")
    return report


class TestSaveReportAtomicAndStdoutSafe:
    """#7: stdout always restored; report file written atomically."""

    def test_report_written_correctly_and_no_tmp_leftover(self, tmp_path):
        """Happy path: report content lands on disk, no sibling .tmp remains.

        Benign case: the atomic write must still produce a complete report
        file at the requested path, and must clean up its temp sibling via
        os.replace (which renames, leaving nothing behind).
        """
        report_path = tmp_path / "sub" / "report.md"
        sentinel = "VERBOSE-REPORT-BODY-12345"

        def fake_print(rep, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            print(sentinel)

        save_report_and_print_summary(
            _make_report(), report_path, "Test Title", fake_print
        )

        assert report_path.exists()
        assert sentinel in report_path.read_text()
        # The atomic write uses report_path + ".tmp"; it must be gone.
        tmp_sibling = report_path.with_suffix(report_path.suffix + ".tmp")
        assert not tmp_sibling.exists()
        # No stray .tmp anywhere in the report dir.
        assert list(report_path.parent.glob("*.tmp")) == []

    def test_stdout_restored_after_print_fn_raises(self, tmp_path, capsys):
        """Bug case (fixed): a print_fn that raises must still restore stdout.

        Pre-fix the stdout swap relied on try/finally — this asserts that
        contract holds: when print_fn blows up, the exception propagates BUT
        sys.stdout is the real stdout again afterwards (so the rest of the
        process can still print). We confirm via a marker that reaches capsys.
        """
        report_path = tmp_path / "report.md"
        saved_real_stdout = sys.stdout

        def exploding_print(rep, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            print("partial output before boom")
            raise RuntimeError("print_fn blew up")

        with pytest.raises(RuntimeError, match="print_fn blew up"):
            save_report_and_print_summary(
                _make_report(), report_path, "Test Title", exploding_print
            )

        # stdout must be the exact object it was before the call.
        assert sys.stdout is saved_real_stdout
        # And a subsequent print() must reach the real stdout, not a dead
        # StringIO — capsys proves the stream is live again.
        print("AFTER-RAISE-MARKER")
        captured = capsys.readouterr()
        assert "AFTER-RAISE-MARKER" in captured.out
        # The verbose "partial output" was captured into the (discarded)
        # buffer, so it must NOT have leaked to the real stdout.
        assert "partial output before boom" not in captured.out

    def test_no_tmp_leftover_when_print_fn_raises(self, tmp_path):
        """Bug case (fixed): a raising print_fn leaves no report and no .tmp.

        Because the write happens after the captured-output block, a failed
        print_fn aborts before any file is created — neither the final report
        nor the temp sibling should exist.
        """
        report_path = tmp_path / "report.md"

        def exploding_print(rep, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            save_report_and_print_summary(
                _make_report(), report_path, "Test Title", exploding_print
            )

        assert not report_path.exists()
        tmp_sibling = report_path.with_suffix(report_path.suffix + ".tmp")
        assert not tmp_sibling.exists()

    def test_overwrites_existing_report_atomically(self, tmp_path):
        """Atomic replace overwrites a pre-existing report in place.

        os.replace is atomic and overwrites the destination, so a second run
        must fully replace stale content (no append, no merge, no .tmp).
        """
        report_path = tmp_path / "report.md"
        report_path.write_text("STALE CONTENT THAT MUST BE GONE")

        def fake_print(rep, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            print("FRESH CONTENT")

        save_report_and_print_summary(
            _make_report(), report_path, "Test Title", fake_print
        )

        body = report_path.read_text()
        assert "FRESH CONTENT" in body
        assert "STALE CONTENT" not in body
        assert not report_path.with_suffix(report_path.suffix + ".tmp").exists()


# ---------------------------------------------------------------------------
# #11 — dead COLORS["MAJOR_DARK"] removed
# ---------------------------------------------------------------------------


class TestColorsMajorDarkRemoved:
    """#11: the unused MAJOR_DARK key is gone; COLORS stays complete."""

    def test_major_dark_key_absent(self):
        """Bug case (fixed): the dead MAJOR_DARK entry no longer exists."""
        assert "MAJOR_DARK" not in COLORS

    def test_colors_has_entry_for_every_level(self):
        """Benign case: every Level value still has a COLORS entry.

        Removing a key must not strand any severity level without a color —
        every member of the Level Literal must map to a real ANSI code, plus
        the structural RESET/BOLD/DIM helpers.
        """
        for level in get_args(Level):
            assert level in COLORS, f"COLORS missing entry for level {level!r}"
            assert COLORS[level], f"COLORS[{level!r}] is empty"
        for helper in ("RESET", "BOLD", "DIM"):
            assert helper in COLORS

    def test_colorize_still_works_for_every_level(self):
        """colorize() wraps text in the right code + RESET for each level.

        Proves the public colorize() entry point still resolves a color for
        every level after the dict edit — and that disabling color is a no-op.
        """
        for level in get_args(Level):
            out = colorize("msg", level)
            assert out == f"{COLORS[level]}msg{COLORS['RESET']}"

        cvc._COLOR_ENABLED = False
        try:
            assert colorize("msg", "MAJOR") == "msg"
        finally:
            cvc._COLOR_ENABLED = True
