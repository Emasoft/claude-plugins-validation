"""Tests for the row-13/row-22 canon-template-audit fixes in cpv_network_resilience.py.

Covers the worst_case_seconds() docstring/helper parity and the
capture_output=False empty-stderr retry-classification fallback.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_network_resilience as cnr  # noqa: E402


def test_docstring_worst_case_numbers_match_worst_case_seconds() -> None:
    """The module docstring's gh/git worst-case figures equal worst_case_seconds() output, so they cannot drift."""
    doc = cnr.__doc__ or ""
    gh_worst = cnr.worst_case_seconds(cnr.GH_MAX_ATTEMPTS, cnr.DEFAULT_TIMEOUT_SEC, cnr.GH_BACKOFF_SEC)
    git_worst = cnr.worst_case_seconds(cnr.GIT_MAX_ATTEMPTS, cnr.DEFAULT_TIMEOUT_SEC, cnr.GIT_BACKOFF_SEC)
    assert gh_worst == 18174
    assert git_worst == 36236
    # Docstring must actually cite these exact numbers (not a stale copy).
    assert re.search(rf"\b{int(gh_worst)}s\b", doc), doc
    assert re.search(rf"\b{int(git_worst)}s\b", doc), doc


def test_worst_case_seconds_formula() -> None:
    """worst_case_seconds() = attempts*(timeout+sleep) - sleep, matching a hand-traced small example."""
    # 3 attempts, 10s timeout each, 2s sleep between: t,s,t,s,t = 10+2+10+2+10 = 34
    assert cnr.worst_case_seconds(3, 10, 2) == 34
    assert cnr.worst_case_seconds(1, 10, 2) == 10  # no retries -> no sleep at all


def test_empty_stderr_transient_exit_code_retries_and_caps() -> None:
    """capture_output=False + git's ambiguous exit 128 retries at least once and at most the documented cap of 1."""
    call_count = 0

    def fake_popen(cmd: list[str], **kwargs: object) -> object:
        nonlocal call_count
        call_count += 1

        class _P:
            returncode = 128
            pid = 1000 + call_count

            def communicate(self, timeout: float | None = None) -> tuple[None, None]:
                return (None, None)

            def poll(self) -> int | None:
                return self.returncode

        return _P()

    with (
        patch("cpv_network_resilience.subprocess.Popen", side_effect=fake_popen),
        patch("cpv_network_resilience.time.sleep"),
    ):
        with pytest.raises(Exception, match=".*") as exc_info:
            cnr.run_with_retry(
                ["git", "push"],
                capture_output=False,
                max_attempts=60,  # the full git budget -- must NOT be exhausted
                backoff=0.0,
            )
    # CalledProcessError on terminal failure (check=True default).
    import subprocess as sp

    assert isinstance(exc_info.value, sp.CalledProcessError)
    # Exactly 2 Popen calls: the first attempt + the one capped ambiguous retry.
    assert call_count == 2


def test_empty_stderr_permanent_exit_code_does_not_retry() -> None:
    """capture_output=False + a non-ambiguous exit code (e.g. 1) never retries -- positive control."""
    call_count = 0

    def fake_popen(cmd: list[str], **kwargs: object) -> object:
        nonlocal call_count
        call_count += 1

        class _P:
            returncode = 1
            pid = 2000 + call_count

            def communicate(self, timeout: float | None = None) -> tuple[None, None]:
                return (None, None)

            def poll(self) -> int | None:
                return self.returncode

        return _P()

    with (
        patch("cpv_network_resilience.subprocess.Popen", side_effect=fake_popen),
        patch("cpv_network_resilience.time.sleep"),
    ):
        with pytest.raises(Exception, match=".*"):
            cnr.run_with_retry(
                ["git", "push"],
                capture_output=False,
                max_attempts=60,
                backoff=0.0,
            )
    assert call_count == 1  # no retry at all


def test_captured_transient_stderr_still_retries_as_before() -> None:
    """A real captured transient stderr still retries up to max_attempts, unaffected by the None-stderr cap."""
    call_count = 0

    def fake_popen(cmd: list[str], **kwargs: object) -> object:
        nonlocal call_count
        call_count += 1

        class _P:
            returncode = 1
            pid = 3000 + call_count

            def communicate(self, timeout: float | None = None) -> tuple[str, str]:
                return ("", "fatal: Could not resolve host: github.com")

            def poll(self) -> int | None:
                return self.returncode

        return _P()

    with (
        patch("cpv_network_resilience.subprocess.Popen", side_effect=fake_popen),
        patch("cpv_network_resilience.time.sleep"),
    ):
        with pytest.raises(Exception, match=".*"):
            cnr.run_with_retry(
                ["git", "push"],
                capture_output=True,
                max_attempts=4,
                backoff=0.0,
            )
    assert call_count == 4  # full budget spent, not capped at 2 like the None-stderr path


def test_is_transient_subprocess_error_none_vs_empty_string() -> None:
    """None stderr falls back to the exit-code allowlist; '' (captured-but-empty) stays permanent."""
    assert cnr.is_transient_subprocess_error(None, 128) is True
    assert cnr.is_transient_subprocess_error(None, 1) is False
    assert cnr.is_transient_subprocess_error("", 128) is False
    assert cnr.is_transient_subprocess_error("", 1) is False
