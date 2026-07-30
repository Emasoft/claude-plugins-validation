#!/usr/bin/env python3
"""Issue #183 — a blocking preflight gate must print the errors, not just a count.

Reported symptom: `ci-preflight` blocked a publish with

    ✗ mypy: mypy found type errors in scripts/ … Found 2 errors in 1 file

and nothing else — no file, no line, no message. The gate HAD the per-error lines
(it captures stdout/stderr) and reduced them to the trailing summary. It is also
one of the few gates whose failure the reader cannot reproduce with the obvious
command: `uv run mypy` resolves against the project venv while the preflight (like
CI) does not, so every local re-run said "Success" while the gate said "CI would
fail". Hiding the diagnostics there costs the most.

Two-sided throughout: the FAIL set gains the tool's own output, and PASS /
WARNING / static-detector findings must stay one-line summaries.
"""

from __future__ import annotations

import io
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_ci_preflight import (  # noqa: E402
    _FAIL_DETAIL_MAX_LINES,
    _SEV_FAIL,
    _SEV_PASS,
    _SEV_WARNING,
    PreflightResult,
    _fail_detail,
    _gate_mypy,
    _print_report,
)


def _render(result: PreflightResult) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        _print_report(result)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _fail_detail — normalisation + the cap
# ---------------------------------------------------------------------------


def test_fail_detail_keeps_every_line_under_the_cap() -> None:
    text = "a.py:1: error: one\na.py:2: error: two\n"
    assert _fail_detail(text) == "a.py:1: error: one\na.py:2: error: two"


def test_fail_detail_drops_blank_lines_and_trailing_space() -> None:
    assert _fail_detail("\n\n  x  \n\n") == "  x"


def test_fail_detail_of_empty_output_is_empty() -> None:
    """Empty in → empty out, so the renderer prints no orphan block."""
    assert _fail_detail("") == ""
    assert _fail_detail("   \n\n ") == ""


def test_fail_detail_caps_and_states_how_many_lines_were_elided() -> None:
    """Truncation is always ANNOUNCED — silently dropping output is the bug."""
    over = _FAIL_DETAIL_MAX_LINES + 7
    out = _fail_detail("\n".join(f"line{i}" for i in range(over)))
    lines = out.splitlines()
    assert len(lines) == _FAIL_DETAIL_MAX_LINES + 1
    assert lines[-1] == "… and 7 more lines"


def test_fail_detail_singular_wording_for_one_elided_line() -> None:
    out = _fail_detail("\n".join(f"line{i}" for i in range(_FAIL_DETAIL_MAX_LINES + 1)))
    assert out.splitlines()[-1] == "… and 1 more line"


# ---------------------------------------------------------------------------
# Renderer — FAIL echoes, PASS/WARNING do not
# ---------------------------------------------------------------------------


def test_report_echoes_the_detail_under_a_fail(tmp_path: Path) -> None:
    r = PreflightResult(plugin_path=tmp_path)
    r.add("mypy", _SEV_FAIL, "mypy found type errors", detail="v.py:156: error: bad thing")
    out = _render(r)
    assert "v.py:156: error: bad thing" in out, "a blocking gate must name the error"


def test_report_does_not_echo_detail_for_pass_or_warning(tmp_path: Path) -> None:
    """NEGATIVE: only the blocking set is expanded — the rest stay summaries."""
    r = PreflightResult(plugin_path=tmp_path)
    r.add("mypy", _SEV_PASS, "clean", detail="SHOULD-NOT-APPEAR-PASS")
    r.add("actionlint", _SEV_WARNING, "absent", detail="SHOULD-NOT-APPEAR-WARN")
    out = _render(r)
    assert "SHOULD-NOT-APPEAR-PASS" not in out
    assert "SHOULD-NOT-APPEAR-WARN" not in out


def test_report_of_a_detail_free_fail_is_unchanged(tmp_path: Path) -> None:
    """NEGATIVE: a static CIP-N detector has no tool output and renders as before."""
    r = PreflightResult(plugin_path=tmp_path)
    r.add("CIP-6", _SEV_FAIL, "stale CPV ref @main")
    out = _render(r)
    assert "✗ CIP-6: stale CPV ref @main" in out
    body = [ln for ln in out.splitlines() if ln.startswith("      ")]
    assert body == [], "a finding with no detail must not open an empty block"


# ---------------------------------------------------------------------------
# End-to-end — the reporter's exact case, through the REAL mypy gate
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("mypy") is None, reason="mypy not on PATH")
def test_real_mypy_failure_reports_the_error_lines_not_only_the_count(tmp_path: Path) -> None:
    """A real type error must reach the report with its file, line and message."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "typed.py").write_text(
        "def f(x: int) -> str:\n    return x\n",  # int returned where str declared
        encoding="utf-8",
    )
    r = PreflightResult(plugin_path=tmp_path)
    _gate_mypy(r)

    assert r.fails, "a real type error must FAIL the gate"
    out = _render(r)
    assert "Found 1 error" in out, "the summary count is still reported"
    assert "typed.py" in out, "the report must name the FILE"
    assert ":2:" in out, "the report must name the LINE"
    assert "error:" in out, "the report must carry mypy's own message"


@pytest.mark.skipif(shutil.which("mypy") is None, reason="mypy not on PATH")
def test_clean_mypy_run_passes_with_no_detail_block(tmp_path: Path) -> None:
    """NEGATIVE control: a clean tree still PASSes and echoes nothing."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "typed.py").write_text("def f(x: int) -> int:\n    return x\n", encoding="utf-8")
    r = PreflightResult(plugin_path=tmp_path)
    _gate_mypy(r)

    assert not r.fails
    assert all(not f.detail for f in r.findings)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
