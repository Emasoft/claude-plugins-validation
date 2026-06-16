#!/usr/bin/env python3
"""Issue #108 — ruff/lint findings must carry the rule code, line:col, and
message, not just a bare count.

Two surfaces share one defect in ``cpv_lint_engine``:

1. The plugin ``--strict`` repo-lint phase (``lint_python``) reported only
   ``Ruff: N error(s) in <file>`` — no rule code, no line, no message.
2. The ``lint`` subcommand (``cpv_lint_engine.main``) printed a ``Summary:``
   with the MAJOR count but ZERO Detail lines.

These tests pin BOTH surfaces and the FN-safe / no-regression contract:

* the per-FILE MAJOR grouping (and therefore the MAJOR count) is UNCHANGED —
  one MAJOR per file with findings, regardless of how many findings the file
  has;
* each MAJOR's message now lists every finding as
  ``<code> <rel>:<line>[:<col>] <message>``;
* the ``lint`` subcommand prints a ``Details:`` block (one line per non-PASSED
  result) BEFORE the ``Summary:`` line;
* a clean file emits NO Detail lines and still shows ``PASSED=N``.

Ruff is mocked via ``_resolve`` + ``_run_linter`` (the same seam the existing
``test_cpv_lint_engine`` suite uses), so these tests run with no real ruff and
are deterministic in CI.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# scripts/ on sys.path (conftest also does this; explicit for direct runs).
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from cpv_lint_engine import lint_python, main  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402


class FakeResult:
    """subprocess.CompletedProcess stand-in (mirrors test_cpv_lint_engine)."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_run(*results: FakeResult):
    """Return a ``_run_linter`` mock yielding each FakeResult in turn."""
    queue = list(results)

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003, ARG001 — parity with subprocess.run
        if not queue:
            return FakeResult(0, "", "")
        return queue.pop(0)

    return fake_run


def _ruff_present(tool: str) -> list[str] | None:
    """``_resolve`` mock: ruff resolves, everything else (mypy) does not."""
    return ["/bin/" + tool] if tool == "ruff" else None


# ---------------------------------------------------------------------------
# Surface 1 — lint_python per-file MAJOR now carries the full finding detail
# ---------------------------------------------------------------------------


class TestSurface1LintPython:
    def test_major_message_contains_rule_code_location_and_message(self, tmp_path: Path) -> None:
        """Each ruff finding appears as '<code> <file>:<line>:<col> <message>'."""
        bad = tmp_path / "bad.py"
        bad.write_text("import os\nimport abc\n")
        # ruff --output-format=concise shape: path:line:col: CODE [*] message
        ruff_stdout = (
            f"{bad}:1:1: I001 [*] Import block is un-sorted or un-formatted\n"
            f"{bad}:1:8: F401 [*] `os` imported but unused\n"
            f"{bad}:2:8: F401 [*] `abc` imported but unused\n"
            "Found 3 errors.\n"
            "[*] 3 fixable with the `--fix` option.\n"
        )
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", side_effect=_ruff_present):
            with patch("cpv_lint_engine._run_linter", side_effect=_make_run(FakeResult(1, ruff_stdout, ""))):
                ok = lint_python(tmp_path, [bad], report)

        assert ok is False
        majors = [r for r in report.results if r.level == "MAJOR"]
        # FN-safe: still ONE MAJOR for the one file (count unchanged), even
        # though the file has three findings.
        assert len(majors) == 1
        msg = majors[0].message
        # Rule codes present.
        assert "I001" in msg
        assert "F401" in msg
        # file:line(:col) present for each.
        assert "bad.py:1:1" in msg
        assert "bad.py:1:8" in msg
        assert "bad.py:2:8" in msg
        # Human messages present.
        assert "Import block is un-sorted or un-formatted" in msg
        assert "imported but unused" in msg
        # The count header is preserved.
        assert "Ruff: 3 error(s) in bad.py" in msg

    def test_major_count_is_per_file_not_per_finding(self, tmp_path: Path) -> None:
        """Two files, four findings -> exactly TWO MAJORs (per-file grouping)."""
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_text("import os\n")
        b.write_text("import sys\n")
        ruff_stdout = (
            f"{a}:1:8: F401 [*] `os` imported but unused\n"
            f"{a}:3:1: E701 multiple statements on one line\n"
            f"{b}:1:8: F401 [*] `sys` imported but unused\n"
            f"{b}:2:1: E302 expected 2 blank lines\n"
        )
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", side_effect=_ruff_present):
            with patch("cpv_lint_engine._run_linter", side_effect=_make_run(FakeResult(1, ruff_stdout, ""))):
                ok = lint_python(tmp_path, [a, b], report)

        assert ok is False
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert len(majors) == 2
        by_file = {m.file: m.message for m in majors}
        assert "a.py" in by_file
        assert "b.py" in by_file
        assert "Ruff: 2 error(s) in a.py" in by_file["a.py"]
        assert "Ruff: 2 error(s) in b.py" in by_file["b.py"]
        # Detail of each finding is in its file's MAJOR (E701 only in a, E302 only in b).
        assert "E701" in by_file["a.py"] and "E701" not in by_file["b.py"]
        assert "E302" in by_file["b.py"] and "E302" not in by_file["a.py"]

    def test_clean_file_no_major_no_detail(self, tmp_path: Path) -> None:
        """A clean file (ruff rc==0) still PASSES with zero MAJORs (no spurious detail)."""
        good = tmp_path / "good.py"
        good.write_text("x = 1\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", side_effect=lambda t: ["/bin/" + t]):
            with patch(
                "cpv_lint_engine._run_linter",
                side_effect=_make_run(FakeResult(0, "", ""), FakeResult(0, "", "")),
            ):
                ok = lint_python(tmp_path, [good], report)
        assert ok is True
        assert not [r for r in report.results if r.level == "MAJOR"]

    def test_unparseable_finding_line_kept_verbatim(self, tmp_path: Path) -> None:
        """A finding line the rule-code parser can't decompose is still surfaced.

        Defensive: a line that matches the concise '<path>:<line>:' anchor but
        not the full '<code>' shape (e.g. a hypothetical future ruff format)
        must remain visible in the MAJOR message, never silently dropped.
        """
        bad = tmp_path / "weird.py"
        bad.write_text("x = 1\n")
        # Matches _RUFF_CONCISE_FINDING_RE (path:line:col:) but has no CODE token.
        ruff_stdout = f"{bad}:5:2: lowercase-noncode some unusual diagnostic text\n"
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", side_effect=_ruff_present):
            with patch("cpv_lint_engine._run_linter", side_effect=_make_run(FakeResult(1, ruff_stdout, ""))):
                ok = lint_python(tmp_path, [bad], report)
        assert ok is False
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert len(majors) == 1
        # The raw diagnostic text survives in the message.
        assert "some unusual diagnostic text" in majors[0].message


# ---------------------------------------------------------------------------
# Surface 2 — the `lint` subcommand prints Details before Summary
# ---------------------------------------------------------------------------


class TestSurface2LintSubcommand:
    def test_lint_subcommand_prints_details_before_summary(
        self, tmp_path: Path, capsys
    ) -> None:  # noqa: ANN001
        """`cpv_lint_engine.main` emits a Details block (with rule code/loc/msg)
        ahead of the Summary line when there are findings."""
        bad = tmp_path / "bad.py"
        bad.write_text("import os\nimport abc\n")
        ruff_stdout = (
            f"{bad}:1:1: I001 [*] Import block is un-sorted or un-formatted\n"
            f"{bad}:1:8: F401 [*] `os` imported but unused\n"
        )
        # main() routes through lint_repo -> lint_python; mock the seam so no
        # real ruff is needed and only python is "detected".
        with patch("cpv_lint_engine._resolve", side_effect=_ruff_present):
            with patch("cpv_lint_engine._run_linter", side_effect=_make_run(FakeResult(1, ruff_stdout, ""))):
                rc = main([str(tmp_path)])

        out = capsys.readouterr().out
        assert rc == 1
        assert "Details:" in out
        assert "Summary:" in out
        # Order: Details must precede Summary.
        assert out.index("Details:") < out.index("Summary:")
        # The finding detail (rule code, location, message) is in the output.
        assert "I001" in out
        assert "F401" in out
        assert "bad.py:1:1" in out
        assert "imported but unused" in out
        # The MAJOR count is unchanged (one per file).
        assert "MAJOR=1" in out

    def test_lint_subcommand_clean_repo_no_details(self, tmp_path: Path, capsys) -> None:  # noqa: ANN001
        """A clean repo prints NO Details block and reports PASSED in the Summary."""
        good = tmp_path / "good.py"
        good.write_text("x = 1\n")
        with patch("cpv_lint_engine._resolve", side_effect=lambda t: ["/bin/" + t]):
            with patch(
                "cpv_lint_engine._run_linter",
                side_effect=_make_run(FakeResult(0, "", ""), FakeResult(0, "", "")),
            ):
                rc = main([str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Details:" not in out
        assert "Summary:" in out
        assert "PASSED=1" in out
        assert "MAJOR=0" in out
