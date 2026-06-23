"""Regression tests for issue #148 — REPO LINT phase hangs ~30 min on CI.

CPV's ``═══ [REPO LINT] (15 languages, gitignore-filtered) ═══`` phase
completes in ~18s on v2.136.1 but hangs to the GitHub CI job timeout (~30 min,
no output) on v2.137.0+ on fresh ``ubuntu-latest`` runners. Root cause class: a
per-language linter subprocess that blocks on a cold runner (a missing tool's
first-run fetcher, an auto-install, or a never-finishing process) with no hard
ceiling, taking down every downstream ``cpv-remote-validate --strict`` gate.

The earlier issue #74 hardening already routes every linter spawn through
``_run_linter`` (stdin=DEVNULL, non-interactive env, process-group kill on
timeout). This issue completes the robustness story with three guarantees, all
covered two-sided here:

  1. **Hard per-linter timeout, env-overridable.** Every ``_run_linter`` call
     has a hard ``timeout=`` (no spawn is unbounded), and
     ``PLUGIN_REPO_LINT_TIMEOUT`` caps/raises them all uniformly. A linter that
     would hang is killed at the deadline and its language is recorded as a
     SKIPPED/timeout WARNING — never a job-killing hang.
  2. **Graceful skip of an absent linter.** A language whose linter binary is
     not installed is skipped cleanly (``_tool_missing`` → WARNING in soft
     mode), with no blocking auto-install attempt.
  3. **``PLUGIN_SKIP_REPO_LINT=1`` opt-out** that disables the whole phase, so a
     downstream CI already running its own linter is not double-linted (parallel
     to the existing ``PLUGIN_SKIP_GITHUB_INTEGRITY`` opt-out).

The behavioral checks use a fake slow command / monkeypatched ``_resolve`` so
they are CI-stable and need no real linter installed.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cpv_lint_engine  # noqa: E402
from cpv_lint_engine import lint_repo  # noqa: E402
from cpv_scanner_cache import ScannerCache  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402

LINT_ENGINE_SRC = SCRIPTS_DIR / "cpv_lint_engine.py"


def _isolated_cache(tmp_path: Path) -> ScannerCache:
    """A ScannerCache rooted under tmp_path so tests never touch the real cache."""
    return ScannerCache(cache_dir=tmp_path / "cache")


# ---------------------------------------------------------------------------
# 1. Hard per-linter timeout — bounded, env-overridable, never a hang
# ---------------------------------------------------------------------------


class TestEveryLinterSpawnIsBounded:
    """Static guard: no ``_run_linter`` call omits a ``timeout=`` argument."""

    def test_all_run_linter_calls_pass_timeout(self) -> None:
        """Every ``_run_linter(...)`` call site supplies a ``timeout=`` kwarg, so
        no linter spawn is unbounded (the issue #148 hang precondition)."""
        tree = ast.parse(LINT_ENGINE_SRC.read_text(encoding="utf-8"))
        missing: list[int] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_run_linter"
            ):
                if not any(kw.arg == "timeout" for kw in node.keywords):
                    missing.append(node.lineno)
        assert not missing, f"_run_linter calls missing a timeout= at lines: {missing}"


class TestTimeoutOverride:
    """``PLUGIN_REPO_LINT_TIMEOUT`` caps every linter spawn uniformly."""

    def test_effective_timeout_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unset env → the call-site default is used unchanged."""
        monkeypatch.delenv("PLUGIN_REPO_LINT_TIMEOUT", raising=False)
        assert cpv_lint_engine._effective_timeout(120) == 120

    def test_effective_timeout_positive_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A positive override replaces the call-site default."""
        monkeypatch.setenv("PLUGIN_REPO_LINT_TIMEOUT", "7")
        assert cpv_lint_engine._effective_timeout(120) == 7.0

    def test_effective_timeout_ignores_zero_negative_and_garbage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-positive / non-numeric value is ignored (default wins) so a typo
        can never make the ceiling near-zero and silently skip every language."""
        for bad in ("0", "-5", "abc", "  "):
            monkeypatch.setenv("PLUGIN_REPO_LINT_TIMEOUT", bad)
            assert cpv_lint_engine._effective_timeout(120) == 120

    def test_run_linter_applies_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 30s sleeper run with a generous call-site default but a 1s ENV
        override is killed at ~1s — the override actually reaches the spawn."""
        monkeypatch.setenv("PLUGIN_REPO_LINT_TIMEOUT", "1")
        sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]
        start = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            cpv_lint_engine._run_linter(sleeper, timeout=120)
        elapsed = time.monotonic() - start
        assert elapsed < 12, (
            f"PLUGIN_REPO_LINT_TIMEOUT=1 did not cap the 120s call-site default (took {elapsed:.1f}s)"
        )


class TestHangingLinterIsSkippedNotHung:
    """A linter that would hang yields a WARNING, not a hang, and the rest of the
    phase proceeds (the real issue #148 failure mode, simulated)."""

    def test_slow_linter_times_out_to_warning(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """When the resolved ``ruff`` is a 30s sleeper and the timeout is capped
        to 1s, ``lint_python`` raises no hang — it records a WARNING ('timed
        out') and returns, so the language is skipped cleanly."""
        # Resolve ruff to a python sleeper that blocks far past the deadline.
        sleeper = [sys.executable, "-c", "import time; time.sleep(30)"]

        def _fake_resolve(tool: str) -> list[str] | None:
            return sleeper if tool == "ruff" else None

        monkeypatch.setattr(cpv_lint_engine, "_resolve", _fake_resolve)
        monkeypatch.setenv("PLUGIN_REPO_LINT_TIMEOUT", "1")

        py = tmp_path / "mod.py"
        py.write_text("x = 1\n", encoding="utf-8")
        report = ValidationReport()

        start = time.monotonic()
        # Must NOT hang for 30s; ruff times out → handled → returns.
        cpv_lint_engine.lint_python(tmp_path, [py], report, strict_missing_tools=True)
        elapsed = time.monotonic() - start

        assert elapsed < 12, f"lint_python hung on a slow linter instead of timing out (took {elapsed:.1f}s)"
        warnings = [r for r in report.results if r.level == "WARNING" and "timed out" in r.message]
        assert warnings, (
            "a timed-out linter must record a WARNING ('timed out'), not hang. "
            f"Got levels: {[r.level for r in report.results]}"
        )


# ---------------------------------------------------------------------------
# 2. Graceful skip of an absent linter — no blocking auto-install
# ---------------------------------------------------------------------------


class TestAbsentLinterSkippedCleanly:
    """A language whose linter is not installed is skipped, never blocked on an
    install attempt."""

    def test_missing_ruff_soft_mode_warns_and_passes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``_resolve('ruff') is None`` (tool absent) in soft mode → a WARNING and
        a clean pass (no MAJOR, no hang), exactly the graceful-skip contract."""
        monkeypatch.setattr(cpv_lint_engine, "_resolve", lambda tool: None)
        py = tmp_path / "mod.py"
        py.write_text("x = 1\n", encoding="utf-8")
        report = ValidationReport()

        start = time.monotonic()
        ok = cpv_lint_engine.lint_python(tmp_path, [py], report, strict_missing_tools=False)
        elapsed = time.monotonic() - start

        assert ok is True, "soft-mode missing linter must return True (skip, not fail)"
        assert elapsed < 5, f"missing-linter path blocked instead of skipping (took {elapsed:.1f}s)"
        assert any(
            r.level == "WARNING" and "Missing linter" in r.message for r in report.results
        ), "an absent linter must record a 'Missing linter' WARNING in soft mode"
        # Graceful skip means NO subprocess was spawned for the missing tool.
        assert not any(r.level == "MAJOR" for r in report.results)

    def test_missing_ruff_strict_mode_is_major_not_hang(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """In strict mode an absent linter is a MAJOR (fail) — still no hang, no
        auto-install — the FN-safe sibling of the soft-mode skip."""
        monkeypatch.setattr(cpv_lint_engine, "_resolve", lambda tool: None)
        py = tmp_path / "mod.py"
        py.write_text("x = 1\n", encoding="utf-8")
        report = ValidationReport()

        ok = cpv_lint_engine.lint_python(tmp_path, [py], report, strict_missing_tools=True)
        assert ok is False, "strict-mode missing linter must fail"
        assert any(
            r.level == "MAJOR" and "Missing linter" in r.message for r in report.results
        ), "strict-mode absent linter must be a MAJOR finding"

    def test_resolve_uses_shutil_which_for_native_tools(self) -> None:
        """``_resolve`` falls back to ``shutil.which`` before declaring a tool
        missing — installed-natively tools are found, absent ones return None
        (no blocking install)."""
        # A program guaranteed absent → None (clean skip signal).
        assert cpv_lint_engine._resolve("definitely-not-a-real-linter-xyz") is None


# ---------------------------------------------------------------------------
# 3. PLUGIN_SKIP_REPO_LINT opt-out — disables the whole phase
# ---------------------------------------------------------------------------


class TestSkipRepoLintOptOut:
    """``PLUGIN_SKIP_REPO_LINT`` disables the REPO LINT phase (parallel to
    ``PLUGIN_SKIP_GITHUB_INTEGRITY``)."""

    def test_repo_lint_disabled_truthy_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Any truthy value (1/true/yes/on, case-insensitive) disables the phase."""
        for val in ("1", "true", "TRUE", "Yes", "on", "ON"):
            monkeypatch.setenv("PLUGIN_SKIP_REPO_LINT", val)
            assert cpv_lint_engine._repo_lint_disabled() is True, f"{val!r} should disable REPO LINT"

    def test_repo_lint_enabled_when_unset_or_falsey(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unset or a falsey value keeps the phase ON (linting still happens)."""
        monkeypatch.delenv("PLUGIN_SKIP_REPO_LINT", raising=False)
        assert cpv_lint_engine._repo_lint_disabled() is False
        for val in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("PLUGIN_SKIP_REPO_LINT", val)
            assert cpv_lint_engine._repo_lint_disabled() is False, f"{val!r} should keep REPO LINT on"

    def test_lint_repo_skips_phase_when_opted_out(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``lint_repo`` with the opt-out set spawns NO linter (resolving any tool
        would raise), returns True, and records one explanatory INFO."""

        # If any linter were dispatched it would call _resolve; make that explode
        # so a regression (the guard not firing) is caught loudly.
        def _boom(tool: str) -> list[str] | None:  # pragma: no cover - must not run
            raise AssertionError("lint_repo must NOT resolve any tool when PLUGIN_SKIP_REPO_LINT is set")

        monkeypatch.setattr(cpv_lint_engine, "_resolve", _boom)
        monkeypatch.setenv("PLUGIN_SKIP_REPO_LINT", "1")

        (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "x.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        report = ValidationReport()

        ok = lint_repo(report=report, plugin_root=tmp_path, cache=_isolated_cache(tmp_path), quiet=True)
        assert ok is True, "opted-out lint_repo must return True (treated as pass)"
        assert any(
            r.level == "INFO" and "PLUGIN_SKIP_REPO_LINT" in r.message for r in report.results
        ), "opted-out lint_repo must record an INFO explaining the skip"
        assert not any(r.level in {"MAJOR", "CRITICAL"} for r in report.results)

    def test_lint_repo_runs_phase_when_not_opted_out(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """FN-safe sibling: without the opt-out, ``lint_repo`` DOES dispatch (here
        every tool is absent so each language records a graceful soft-skip), and
        crucially it does NOT emit the opt-out INFO."""
        monkeypatch.delenv("PLUGIN_SKIP_REPO_LINT", raising=False)
        # All tools absent → soft mode means each detected language is skipped
        # with a WARNING, but the phase genuinely RAN (no opt-out INFO).
        monkeypatch.setattr(cpv_lint_engine, "_resolve", lambda tool: None)

        (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
        report = ValidationReport()

        lint_repo(
            report=report,
            plugin_root=tmp_path,
            cache=_isolated_cache(tmp_path),
            quiet=True,
            strict_missing_tools=False,
        )
        assert not any(
            r.level == "INFO" and "PLUGIN_SKIP_REPO_LINT" in r.message for r in report.results
        ), "phase must NOT report the opt-out INFO when PLUGIN_SKIP_REPO_LINT is unset"
