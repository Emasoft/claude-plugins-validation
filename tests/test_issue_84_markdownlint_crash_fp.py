#!/usr/bin/env python3
"""Issue #84 — a markdownlint TOOL CRASH must NOT be scored as a blocking NIT.

When markdownlint-cli2 is launched via ``bunx``/``npx`` and its ESM imports
crash (e.g. ``bunx`` resolves the package up into an unrelated ANCESTOR
``package.json`` whose ``node_modules`` is broken), markdownlint never runs and
emits a Node crash stack (``ERR_MODULE_NOT_FOUND`` …) instead of MD### findings.
That is an ENVIRONMENT failure, not a markdown lint violation — it must surface
as a WARNING (which never blocks ``--strict``), never a NIT (which does).

Two distinct fixes in ``cpv_lint_engine.lint_markdown`` are tested here:

- **Fix A (cwd isolation):** markdownlint runs from an isolated empty temp cwd,
  not ``cwd=repo_root``, so ``bunx``/``npx`` cannot resolve up into an ancestor
  Node project. The file paths + ``--config`` are already absolute, so the cwd
  governs only module resolution, never WHICH files get linted.
- **Fix B (crash discriminator):** the ``surfaced == 0`` fallback emits a
  WARNING for a tool crash / empty output and keeps the NIT only for genuine
  (non-crash) non-parseable markdownlint output.

The tests are TWO-SIDED:
  * crash-shaped output (``ERR_MODULE_NOT_FOUND`` etc.) -> WARNING, NIT == 0;
  * a real MD### finding line  -> NIT (surfaced > 0), the discriminator never
    even runs;
  * the crash regex itself matches real Node crash text AND does NOT match a
    real MD### finding line.

The crash branch is reachable ONLY when ``surfaced == 0``; a real finding line
matches ``_MARKDOWNLINT_FINDING_RE`` (surfaced > 0) and is surfaced as a NIT
before the fallback, so a real finding can never be down-graded to a WARNING.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

# tests/conftest.py adds scripts/ to sys.path; defensive duplicate so the file
# works when collected in isolation.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_lint_engine import (  # noqa: E402
    _MARKDOWNLINT_FINDING_RE,
    _MARKDOWNLINT_TOOL_CRASH_RE,
    lint_markdown,
)
from cpv_validation_common import ValidationReport  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeResult:
    """subprocess.CompletedProcess stand-in with configurable rc/stdout/stderr."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_run(*results: FakeResult, capture_argv: list[list[str]] | None = None):
    """Build a ``_run_linter`` mock returning the next FakeResult per call.

    ``_run_linter`` has the ``(cmd, **kwargs) -> result`` shape; this stand-in
    matches it. ``cwd`` is passed as a kwarg by ``lint_markdown`` (Fix A) — the
    mock accepts and ignores it via ``**kwargs``.
    """
    queue = list(results)

    def fake_run(cmd, **kwargs):  # noqa: ARG001 — kwargs accepted for parity
        if capture_argv is not None:
            capture_argv.append(list(cmd))
        if not queue:
            return FakeResult(0, "", "")
        return queue.pop(0)

    return fake_run


def _counts(report: ValidationReport) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in report.results:
        out[r.level] = out.get(r.level, 0) + 1
    return out


# A realistic Node / markdownlint-cli2 ESM crash stack (the #84 symptom).
_REAL_CRASH_OUTPUT = (
    "node:internal/modules/esm/resolve:264\n"
    "    throw new ERR_MODULE_NOT_FOUND(\n"
    "          ^\n"
    "Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'markdownlint-cli2' "
    "imported from /Users/x/node_modules/.bin/markdownlint-cli2\n"
    "    at new NodeError (node:internal/errors:387:5)\n"
    "  code: 'ERR_MODULE_NOT_FOUND'\n"
)

# A genuine markdownlint per-line finding (matches _MARKDOWNLINT_FINDING_RE).
_REAL_FINDING_LINE = "path.md:5 warning MD013 Line length [Expected: 80; Actual: 213]"


# ---------------------------------------------------------------------------
# Direct discriminator-regex tests (REAL strings, no mocking the code under test)
# ---------------------------------------------------------------------------


class TestCrashDiscriminatorRegex:
    """The crash regex matches Node crash text and not a real MD### finding."""

    def test_crash_regex_matches_err_module_not_found_stack(self) -> None:
        """ERR_MODULE_NOT_FOUND Node-stack crash text matches the crash regex."""
        assert _MARKDOWNLINT_TOOL_CRASH_RE.search(_REAL_CRASH_OUTPUT) is not None

    def test_crash_regex_matches_individual_crash_signatures(self) -> None:
        """Each documented crash signature is recognised by the crash regex."""
        for sig in (
            "ERR_MODULE_NOT_FOUND",
            "ERR_REQUIRE_ESM",
            "Cannot find module 'foo'",
            "Cannot find package 'markdownlint-cli2'",
            "node:internal/modules/esm/resolve",
            "Error [ERR_PACKAGE_PATH_NOT_EXPORTED]",
            "npm error could not determine executable to run",
            "bunx: command not found",
            "/bin/sh: markdownlint-cli2: No such file or directory",
        ):
            assert _MARKDOWNLINT_TOOL_CRASH_RE.search(sig) is not None, sig

    def test_crash_regex_does_not_match_real_finding_line(self) -> None:
        """A real `path.md:5 warning MD013 …` finding does NOT match the crash regex."""
        assert _MARKDOWNLINT_TOOL_CRASH_RE.search(_REAL_FINDING_LINE) is None

    def test_real_finding_matches_finding_regex_not_crash_regex(self) -> None:
        """The two regexes are disjoint on a real finding: finding-RE hits, crash-RE misses."""
        assert _MARKDOWNLINT_FINDING_RE.search(_REAL_FINDING_LINE) is not None
        assert _MARKDOWNLINT_TOOL_CRASH_RE.search(_REAL_FINDING_LINE) is None

    def test_crash_text_does_not_match_finding_regex(self) -> None:
        """The crash stack carries no MD### finding line, so finding-RE misses it."""
        assert _MARKDOWNLINT_FINDING_RE.search(_REAL_CRASH_OUTPUT) is None


# ---------------------------------------------------------------------------
# Behavioural tests — drive lint_markdown's fallback (Fix B), two-sided
# ---------------------------------------------------------------------------


class TestLintMarkdownCrashFallback:
    """lint_markdown: crash output -> WARNING (non-blocking); real finding -> NIT."""

    def test_crash_output_becomes_warning_not_nit(self, tmp_path: Path) -> None:
        """A markdownlint crash (no findings) -> exactly one WARNING, zero NIT."""
        f = tmp_path / "doc.md"
        f.write_text("# Title\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=["bunx", "markdownlint-cli2"]):
            with patch(
                "cpv_lint_engine._run_linter",
                side_effect=_make_run(FakeResult(1, "", _REAL_CRASH_OUTPUT)),
            ):
                ok = lint_markdown(tmp_path, [f], report)
        # Never blocks: WARNING does not flip the return, and there are NO NITs.
        assert ok is True
        counts = _counts(report)
        assert counts.get("NIT", 0) == 0, f"crash must NOT produce a NIT: {counts}"
        warns = [r for r in report.results if r.level == "WARNING" and "markdownlint" in r.message]
        assert warns, "crash must surface exactly one markdownlint WARNING"
        assert "could not run" in warns[0].message

    def test_real_finding_stays_nit(self, tmp_path: Path) -> None:
        """A real MD### finding -> NIT (surfaced > 0); the crash branch never runs."""
        f = tmp_path / "doc.md"
        f.write_text("# Title\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=["bunx", "markdownlint-cli2"]):
            with patch(
                "cpv_lint_engine._run_linter",
                side_effect=_make_run(FakeResult(1, "", _REAL_FINDING_LINE + "\n")),
            ):
                ok = lint_markdown(tmp_path, [f], report)
        assert ok is True
        nits = [r for r in report.results if r.level == "NIT" and "markdownlint" in r.message]
        assert nits, "a real MD### finding must surface as a NIT"
        assert any("MD013" in r.message for r in nits)
        # No spurious crash WARNING when real findings were surfaced.
        assert not any(
            r.level == "WARNING" and "could not run" in r.message for r in report.results
        )

    def test_non_crash_unparseable_output_stays_nit(self, tmp_path: Path) -> None:
        """Genuine non-crash, non-parseable markdownlint output -> NIT (not WARNING).

        Output that does not match the FINDING regex (so surfaced == 0) but also
        does not match the CRASH regex is real (if unstructured) markdownlint
        signal — it keeps the existing NIT behaviour, preserving issue #20's
        silent-failure surface for the legitimate-output case.
        """
        f = tmp_path / "doc.md"
        f.write_text("# Title\n")
        report = ValidationReport()
        weird = "markdownlint-cli2 found problems but could not format them\n"
        assert _MARKDOWNLINT_FINDING_RE.search(weird) is None
        assert _MARKDOWNLINT_TOOL_CRASH_RE.search(weird) is None
        with patch("cpv_lint_engine._resolve", return_value=["bunx", "markdownlint-cli2"]):
            with patch(
                "cpv_lint_engine._run_linter",
                side_effect=_make_run(FakeResult(1, "", weird)),
            ):
                ok = lint_markdown(tmp_path, [f], report)
        assert ok is True
        counts = _counts(report)
        assert counts.get("NIT", 0) == 1, f"non-crash unparseable output stays a NIT: {counts}"
        assert not any(r.level == "WARNING" for r in report.results)

    def test_empty_output_becomes_warning(self, tmp_path: Path) -> None:
        """Non-zero exit with EMPTY output -> WARNING (markdownlint could not run)."""
        f = tmp_path / "doc.md"
        f.write_text("# Title\n")
        report = ValidationReport()
        with patch("cpv_lint_engine._resolve", return_value=["bunx", "markdownlint-cli2"]):
            with patch(
                "cpv_lint_engine._run_linter",
                side_effect=_make_run(FakeResult(3, "", "")),
            ):
                ok = lint_markdown(tmp_path, [f], report)
        assert ok is True
        counts = _counts(report)
        assert counts.get("NIT", 0) == 0
        assert any(
            r.level == "WARNING" and "could not run" in r.message for r in report.results
        )


# ---------------------------------------------------------------------------
# Fix A — cwd isolation: markdownlint runs from a temp cwd, not repo_root
# ---------------------------------------------------------------------------


class TestLintMarkdownCwdIsolation:
    """markdownlint is invoked from an isolated temp cwd, not repo_root (Fix A)."""

    def test_run_linter_cwd_is_isolated_temp_not_repo_root(self, tmp_path: Path) -> None:
        """The cwd passed to _run_linter is a fresh cpv-mdlint-* temp dir, not repo_root.

        Fix A: an empty temp cwd has no ancestor `package.json`, so `bunx`/`npx`
        cannot resolve markdownlint up into an unrelated broken Node project.
        """
        f = tmp_path / "doc.md"
        f.write_text("# Title\n")
        report = ValidationReport()
        captured: dict[str, object] = {}

        def fake_run(cmd, **kwargs):  # noqa: ARG001
            captured["cwd"] = kwargs.get("cwd")
            return FakeResult(0, "", "")

        with patch("cpv_lint_engine._resolve", return_value=["bunx", "markdownlint-cli2"]):
            with patch("cpv_lint_engine._run_linter", side_effect=fake_run):
                ok = lint_markdown(tmp_path, [f], report)

        assert ok is True
        cwd = captured.get("cwd")
        assert cwd is not None, "lint_markdown must pass an explicit cwd to _run_linter"
        cwd_path = Path(str(cwd))
        # Isolation: NOT the repo root, and a recognisable cpv-mdlint-* temp dir.
        assert cwd_path != tmp_path, "cwd must NOT be repo_root (ancestor-resolution FP)"
        assert "cpv-mdlint-" in cwd_path.name, f"cwd must be the isolated temp dir: {cwd_path}"

    def test_absolute_file_paths_still_passed_so_files_linted_are_unchanged(
        self, tmp_path: Path
    ) -> None:
        """The markdown file paths handed to markdownlint stay ABSOLUTE.

        Because the paths are absolute, changing the cwd to an isolated temp dir
        cannot change WHICH files get linted — only module resolution.
        """
        f = tmp_path / "doc.md"
        f.write_text("# Title\n")
        report = ValidationReport()
        captured_argv: list[list[str]] = []

        with patch("cpv_lint_engine._resolve", return_value=["bunx", "markdownlint-cli2"]):
            with patch(
                "cpv_lint_engine._run_linter",
                side_effect=_make_run(FakeResult(0, "", ""), capture_argv=captured_argv),
            ):
                lint_markdown(tmp_path, [f], report)

        assert captured_argv, "expected one _run_linter invocation"
        argv = captured_argv[0]
        assert str(f) in argv, "the absolute markdown file path must be passed verbatim"
        assert Path(str(f)).is_absolute()
