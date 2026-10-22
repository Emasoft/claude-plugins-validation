"""Issues #235 (bunx cache race) and #252 (markdownlint cap hides total)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_lint_engine import _run_linter, lint_markdown  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402

RACE = "error: EEXIST: file already exists, Failed to link package\nResolving dependencies\n"


def _cp(cmd: list[str], rc: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, rc, out, err)


def test_race_once_then_success_retries_and_returns_success(tmp_path: Path) -> None:
    """A bunx EEXIST failure followed by success is retried once and yields rc 0."""
    calls: list[int] = []

    def fake(cmd: list[str], *, timeout: float, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        return _cp(cmd, 1, err=RACE) if len(calls) == 1 else _cp(cmd, 0)

    with patch("cpv_lint_engine._spawn_linter_once", side_effect=fake):
        res = _run_linter(["bunx", "markdownlint-cli2"], timeout=5)
    assert res.returncode == 0 and len(calls) == 2


def test_race_retry_is_single_and_non_bunx_not_retried() -> None:
    """Only one retry happens, and non-bunx commands are never retried."""
    calls: list[int] = []

    def fake(cmd: list[str], *, timeout: float, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        return _cp(cmd, 1, err=RACE)

    with patch("cpv_lint_engine._spawn_linter_once", side_effect=fake):
        _run_linter(["bunx", "x"], timeout=5)
        assert len(calls) == 2
        calls.clear()
        _run_linter(["npx", "x"], timeout=5)
        assert len(calls) == 1


def _lint(tmp_path: Path, runner: object) -> tuple[bool, ValidationReport]:
    f = tmp_path / "a.md"
    f.write_text("# t\n")
    report = ValidationReport()
    with patch("cpv_lint_engine._resolve", return_value=["bunx", "markdownlint-cli2"]):
        with patch("cpv_lint_engine._spawn_linter_once", side_effect=runner):
            ok = lint_markdown(tmp_path, [f], report)
    return ok, report


def test_markdown_race_then_success_has_no_finding(tmp_path: Path) -> None:
    """EEXIST once then success: no finding of any level is produced."""
    n: list[int] = []

    def runner(cmd: list[str], *, timeout: float, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        n.append(1)
        return _cp(cmd, 1, err=RACE) if len(n) == 1 else _cp(cmd, 0)

    ok, report = _lint(tmp_path, runner)
    assert ok is True
    assert not [r for r in report.results if r.level in ("NIT", "MINOR", "MAJOR", "CRITICAL", "WARNING")]


def test_markdown_race_always_fails_blocks_with_named_error(tmp_path: Path) -> None:
    """A persistent bunx race is a named MAJOR tool-environment error (blocking)."""

    def runner(cmd: list[str], *, timeout: float, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return _cp(cmd, 1, err=RACE)

    ok, report = _lint(tmp_path, runner)
    assert ok is False
    assert any(
        r.level == "MAJOR" and "tool-environment error: bunx package-cache race" in r.message for r in report.results
    )


def test_cap_surfaces_20_and_summary_reports_total_and_per_rule(tmp_path: Path) -> None:
    """25 findings: 20 NITs listed, one INFO summary with total 25 and per-rule counts."""
    lines = [f"a.md:{i} error MD013/line-length too long" for i in range(1, 16)]
    lines += [f"a.md:{i} error MD012/no-multiple-blanks blanks" for i in range(16, 26)]

    def runner(cmd: list[str], *, timeout: float, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return _cp(cmd, 1, err="\n".join(lines))

    ok, report = _lint(tmp_path, runner)
    assert ok is True
    nits = [r for r in report.results if r.level == "NIT"]
    assert len(nits) == 20
    summ = [r for r in report.results if "Markdown lint summary" in r.message]
    assert len(summ) == 1 and summ[0].level == "INFO"
    assert "25 finding(s)" in summ[0].message and "md013: 15" in summ[0].message and "md012: 10" in summ[0].message


def test_md004_branch_obeys_cap_and_summary_counts_listed(tmp_path: Path) -> None:
    """25 MD004-poisoned files: exactly 20 NITs, '20 listed', total 25."""
    lines = [f"f{i}.md:3 error MD004/ul-style Unordered list style [Expected: dash; Actual: plus]" for i in range(25)]

    def runner(cmd: list[str], *, timeout: float, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return _cp(cmd, 1, err="\n".join(lines))

    ok, report = _lint(tmp_path, runner)
    assert ok is True
    assert len([r for r in report.results if r.level == "NIT"]) == 20
    summ = [r for r in report.results if "Markdown lint summary" in r.message]
    assert len(summ) == 1 and "25 finding(s)" in summ[0].message and "20 listed" in summ[0].message


def test_non_race_bunx_failure_blocks_with_neutral_wording(tmp_path: Path) -> None:
    """A bunx failure with only 'Resolving dependencies' chatter blocks, without the race label."""

    def runner(cmd: list[str], *, timeout: float, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return _cp(cmd, 2, err="Resolving dependencies\nconfig error: bad .markdownlint.json")

    ok, report = _lint(tmp_path, runner)
    assert ok is False
    majors = [r for r in report.results if r.level == "MAJOR"]
    assert majors and "(bunx, no finding output)" in majors[0].message and "cache race" not in majors[0].message
