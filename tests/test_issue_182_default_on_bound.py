#!/usr/bin/env python3
"""Issue #182, second half — the per-file hard-kill bound is DEFAULT-ON.

v4.2.0 made a file whose security scan did not complete BLOCK instead of pass,
but the only mechanism that can actually bound a wedged scan (killing the worker,
via ``cpv_scan_supervisor``) stayed opt-in — so the default pool path was still
unbounded. This suite pins the routing that turns it on by default, and pins that
turning it on did not weaken the v4.2.0 verdict.

Two-sided throughout: every "the bound engages" assertion has a sibling proving
it does NOT engage on work too small to warrant the fixed supervisor cost, and
every env-override assertion has a sibling proving the override cannot be used to
switch the bound off.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import cpv_parallel_runner as pr  # noqa: E402
from cpv_parallel_runner import (  # noqa: E402
    DEFAULT_HARD_KILL_AFTER_S,
    ScanResult,
    hard_kill_after_s,
    parallel_scan,
    resolve_hard_kill_after_s,
    result_is_timeout,
    retry_failed_serially,
    supervise_min_files,
    supervise_size_window,
    supervision_is_warranted,
)

MIB = 1024 * 1024

_ROUTING_ENV_VARS = (
    "CPV_SCAN_SUPERVISE_MIN_FILES",
    "CPV_SCAN_SUPERVISE_LARGE_FILE_MIN_BYTES",
    "CPV_SCAN_SUPERVISE_LARGE_FILE_MAX_BYTES",
    "CPV_SCAN_SKIP_STUCK_AFTER",
)


@pytest.fixture(autouse=True)
def _clean_routing_env(monkeypatch):
    """Every test starts from the shipped defaults, not the caller's shell."""
    for var in _ROUTING_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _tiny_files(tmp_path: Path, count: int, *, prefix: str = "f") -> list[Path]:
    out = []
    for i in range(count):
        p = tmp_path / f"{prefix}{i}.md"
        p.write_text("# tiny\n", encoding="utf-8")
        out.append(p)
    return out


def _sized_file(tmp_path: Path, name: str, size: int) -> Path:
    p = tmp_path / name
    p.write_bytes(b"x" * size)
    return p


# ── Top-level worker callables (must be picklable for the spawn pool) ────────


def _wedge_scan(path):
    """A scan that never returns in any budget a test would set."""
    time.sleep(120)
    return []


def _clean_scan(path):
    return []


# ─────────────────────────────────────────────────────────────────────────────
# The bound ENGAGES where a wedge is plausible
# ─────────────────────────────────────────────────────────────────────────────


class TestRoutingEngages:
    def test_engages_at_the_file_count_threshold(self, tmp_path):
        files = _tiny_files(tmp_path, supervise_min_files())
        assert supervision_is_warranted(files) is True
        assert resolve_hard_kill_after_s(files) == DEFAULT_HARD_KILL_AFTER_S

    def test_engages_above_the_file_count_threshold(self, tmp_path):
        files = _tiny_files(tmp_path, supervise_min_files() + 40)
        assert supervision_is_warranted(files) is True

    def test_engages_for_one_large_file_far_below_the_count_threshold(self, tmp_path):
        files = [_sized_file(tmp_path, "big.md", 2 * MIB)]
        assert len(files) < supervise_min_files()
        assert supervision_is_warranted(files) is True
        assert resolve_hard_kill_after_s(files) == DEFAULT_HARD_KILL_AFTER_S

    def test_engages_at_the_exact_window_floor(self, tmp_path):
        files = [_sized_file(tmp_path, "edge.md", MIB)]
        assert supervision_is_warranted(files) is True

    def test_engages_at_the_exact_window_ceiling(self, tmp_path):
        files = [_sized_file(tmp_path, "edge.md", 8 * MIB)]
        assert supervision_is_warranted(files) is True

    def test_one_large_file_among_small_ones_is_enough(self, tmp_path):
        files = _tiny_files(tmp_path, 3)
        files.append(_sized_file(tmp_path, "big.md", 3 * MIB))
        assert supervision_is_warranted(files) is True


# ─────────────────────────────────────────────────────────────────────────────
# ...and does NOT engage where the fixed ~0.2-0.28s cost would not amortise
# ─────────────────────────────────────────────────────────────────────────────


class TestRoutingDoesNotEngage:
    def test_does_not_engage_below_both_arms(self, tmp_path):
        files = _tiny_files(tmp_path, 3)
        assert supervision_is_warranted(files) is False
        assert resolve_hard_kill_after_s(files) is None

    def test_does_not_engage_one_below_the_count_threshold(self, tmp_path):
        files = _tiny_files(tmp_path, supervise_min_files() - 1)
        assert supervision_is_warranted(files) is False

    def test_does_not_engage_just_under_the_window_floor(self, tmp_path):
        files = [_sized_file(tmp_path, "small.md", MIB - 1)]
        assert supervision_is_warranted(files) is False

    def test_does_not_engage_above_the_window_ceiling(self, tmp_path):
        # A file bigger than the ceiling is dropped by the scanners before any
        # per-line work happens (validate_security's MAX_SCAN_BYTES is 8 MiB and
        # the skillaudit walker drops anything over 2 MB), so it cannot wedge a
        # worker — there is nothing there to supervise.
        files = [_sized_file(tmp_path, "huge.md", 9 * MIB)]
        assert supervision_is_warranted(files) is False

    def test_empty_work_set_does_not_engage(self):
        assert supervision_is_warranted([]) is False
        assert resolve_hard_kill_after_s([]) is None

    def test_unreadable_item_does_not_raise_and_does_not_engage(self, tmp_path):
        # Routing must never crash on something it cannot stat. The scan itself
        # reports an unreadable file; the router just declines to measure it.
        assert supervision_is_warranted([tmp_path / "does-not-exist.md"]) is False
        assert supervision_is_warranted([object()]) is False


# ─────────────────────────────────────────────────────────────────────────────
# Budget resolution
# ─────────────────────────────────────────────────────────────────────────────


class TestBudgetResolution:
    def test_explicit_budget_always_wins_over_routing(self, tmp_path):
        files = _tiny_files(tmp_path, 2)  # routing would say None
        assert resolve_hard_kill_after_s(files, 12.5) == 12.5

    def test_explicit_budget_wins_even_when_routing_would_engage(self, tmp_path):
        files = _tiny_files(tmp_path, supervise_min_files() + 1)
        assert resolve_hard_kill_after_s(files, 7.0) == 7.0

    def test_operator_env_budget_is_used_when_routing_engages(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CPV_SCAN_SKIP_STUCK_AFTER", "45")
        files = _tiny_files(tmp_path, supervise_min_files())
        assert resolve_hard_kill_after_s(files) == 45.0

    def test_default_budget_has_headroom_over_the_worst_measured_file(self):
        # CPV's own tree, cache off: the slowest single file measured 27.13s with
        # google-re2 and 28.25s without. A bound the work cannot finish inside is
        # issue #179's defect; keep an order of magnitude of headroom.
        assert DEFAULT_HARD_KILL_AFTER_S >= 10 * 28.25


# ─────────────────────────────────────────────────────────────────────────────
# An env override may WIDEN the bound; it can never disable it
# ─────────────────────────────────────────────────────────────────────────────


class TestEnvOverridesCannotDisableTheBound:
    @pytest.mark.parametrize("raw", ["", "   ", "0", "-1", "-99", "abc", "32.5", "None", "off"])
    def test_min_files_typo_safe_fallback(self, raw, monkeypatch):
        monkeypatch.setenv("CPV_SCAN_SUPERVISE_MIN_FILES", raw)
        assert supervise_min_files() == pr._DEFAULT_SUPERVISE_MIN_FILES

    def test_min_files_override_may_lower_the_bar(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CPV_SCAN_SUPERVISE_MIN_FILES", "4")
        assert supervise_min_files() == 4
        assert supervision_is_warranted(_tiny_files(tmp_path, 4)) is True

    def test_min_files_override_may_not_raise_the_bar(self, monkeypatch, tmp_path):
        # Raising it is how you would switch the bound off for a whole tree
        # without ever writing "off".
        monkeypatch.setenv("CPV_SCAN_SUPERVISE_MIN_FILES", "100000")
        assert supervise_min_files() == pr._DEFAULT_SUPERVISE_MIN_FILES
        files = _tiny_files(tmp_path, pr._DEFAULT_SUPERVISE_MIN_FILES)
        assert supervision_is_warranted(files) is True
        assert resolve_hard_kill_after_s(files) == DEFAULT_HARD_KILL_AFTER_S

    @pytest.mark.parametrize("raw", ["", "  ", "0", "-1", "nope"])
    def test_size_window_typo_safe_fallback(self, raw, monkeypatch):
        monkeypatch.setenv("CPV_SCAN_SUPERVISE_LARGE_FILE_MIN_BYTES", raw)
        monkeypatch.setenv("CPV_SCAN_SUPERVISE_LARGE_FILE_MAX_BYTES", raw)
        assert supervise_size_window() == (
            pr._DEFAULT_SUPERVISE_LARGE_FILE_MIN_BYTES,
            pr._DEFAULT_SUPERVISE_LARGE_FILE_MAX_BYTES,
        )

    def test_size_floor_override_may_lower_the_floor(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CPV_SCAN_SUPERVISE_LARGE_FILE_MIN_BYTES", str(64 * 1024))
        assert supervise_size_window()[0] == 64 * 1024
        assert supervision_is_warranted([_sized_file(tmp_path, "m.md", 100 * 1024)]) is True

    def test_size_floor_override_may_not_raise_the_floor(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CPV_SCAN_SUPERVISE_LARGE_FILE_MIN_BYTES", str(64 * MIB))
        assert supervise_size_window()[0] == pr._DEFAULT_SUPERVISE_LARGE_FILE_MIN_BYTES
        assert supervision_is_warranted([_sized_file(tmp_path, "big.md", 2 * MIB)]) is True

    def test_size_ceiling_override_may_raise_the_ceiling(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CPV_SCAN_SUPERVISE_LARGE_FILE_MAX_BYTES", str(64 * MIB))
        assert supervise_size_window()[1] == 64 * MIB
        assert supervision_is_warranted([_sized_file(tmp_path, "huge.md", 9 * MIB)]) is True

    def test_size_ceiling_override_may_not_lower_the_ceiling(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CPV_SCAN_SUPERVISE_LARGE_FILE_MAX_BYTES", "1024")
        assert supervise_size_window()[1] == pr._DEFAULT_SUPERVISE_LARGE_FILE_MAX_BYTES
        assert supervision_is_warranted([_sized_file(tmp_path, "big.md", 2 * MIB)]) is True

    def test_overridden_window_always_contains_the_shipped_window(self, monkeypatch):
        monkeypatch.setenv("CPV_SCAN_SUPERVISE_LARGE_FILE_MIN_BYTES", str(7 * MIB))
        monkeypatch.setenv("CPV_SCAN_SUPERVISE_LARGE_FILE_MAX_BYTES", "512")
        lo, hi = supervise_size_window()
        assert lo <= pr._DEFAULT_SUPERVISE_LARGE_FILE_MIN_BYTES
        assert hi >= pr._DEFAULT_SUPERVISE_LARGE_FILE_MAX_BYTES
        assert lo <= hi

    @pytest.mark.parametrize("raw", ["", "   ", "0", "-5", "forever", "inf-ish", "none"])
    def test_budget_typo_safe_fallback(self, raw, monkeypatch):
        monkeypatch.setenv("CPV_SCAN_SKIP_STUCK_AFTER", raw)
        assert hard_kill_after_s() == DEFAULT_HARD_KILL_AFTER_S

    def test_budget_override_can_only_change_how_soon_the_kill_lands(self, monkeypatch):
        # There is no spelling of this variable that means "no bound": a smaller
        # value is STRICTER (kills sooner), a larger one still kills.
        monkeypatch.setenv("CPV_SCAN_SKIP_STUCK_AFTER", "0.5")
        assert hard_kill_after_s() == 0.5
        monkeypatch.setenv("CPV_SCAN_SKIP_STUCK_AFTER", "999999")
        assert hard_kill_after_s() == 999999.0


# ─────────────────────────────────────────────────────────────────────────────
# Fail-closed: a killed scan is a BLOCKING finding, never a clean empty result
# ─────────────────────────────────────────────────────────────────────────────


class TestWedgedFileStillBlocks:
    def test_a_wedged_file_is_killed_and_reported_not_reported_clean(self, tmp_path, monkeypatch):
        # A single large file: below the count threshold, engaged by the size arm.
        big = _sized_file(tmp_path, "big.md", 2 * MIB)
        monkeypatch.setenv("CPV_SCAN_SKIP_STUCK_AFTER", "2")
        budget = resolve_hard_kill_after_s([big])
        assert budget == 2.0, "the size arm must have engaged the bound"

        started = time.monotonic()
        results = parallel_scan([big], _wedge_scan, hard_kill_after_s=budget)
        elapsed = time.monotonic() - started

        assert len(results) == 1
        assert results[0].error is not None, "a scan that never returned must not report success"
        assert result_is_timeout(results[0])
        assert results[0].findings == [], "a killed scan has no findings to report"
        # Bounded: without the kill this would have taken the worker's full 120s.
        assert elapsed < 60, f"the wedge was not bounded (took {elapsed:.1f}s)"

    def test_a_timeout_is_never_retried_in_process(self, tmp_path):
        # Retrying a wedge here would hang the main thread with nothing left to
        # preempt it, and a timeout is already proof the work does not terminate.
        big = _sized_file(tmp_path, "big.md", 2 * MIB)
        timed_out = ScanResult(file_path=big, findings=[], error="TimeoutError: scan exceeded 2s (worker hard-killed)")
        started = time.monotonic()
        out = retry_failed_serially([timed_out], [big], _wedge_scan)
        assert time.monotonic() - started < 5
        assert out[0].error == timed_out.error

    def test_skillaudit_maps_an_unfinished_scan_to_a_blocking_finding(self, tmp_path, monkeypatch):
        """The v4.2.0 verdict survives the routing change: still MAJOR, not a mute."""
        import cpv_skillaudit_native as sa

        target = _sized_file(tmp_path, "big.md", 2 * MIB)

        def _fake_parallel_scan(files, scan_func, **kwargs):
            assert kwargs.get("hard_kill_after_s") is not None, (
                "the skillaudit parallel path must pass a resolved hard-kill budget"
            )
            return [ScanResult(file_path=f, findings=[], error="TimeoutError: worker hard-killed") for f in files]

        monkeypatch.setattr(pr, "parallel_scan", _fake_parallel_scan)
        findings, scanned = sa._scan_path_parallel(tmp_path, [target])

        worker_errors = [f for f in findings if f.get("ruleId") == "SKILLAUDIT_WORKER_ERROR"]
        assert len(worker_errors) == 1
        assert worker_errors[0]["severity"] == "high", "high maps to CPV major, which blocks in BOTH modes"
        assert scanned == 0


# ─────────────────────────────────────────────────────────────────────────────
# The wiring itself — a routing nobody calls is a routing that does nothing
# ─────────────────────────────────────────────────────────────────────────────


class TestSecuritySinksConsultTheRouting:
    def test_validate_security_resolves_the_budget_from_the_file_list(self):
        src = (Path(__file__).parent.parent / "scripts" / "validate_security.py").read_text(encoding="utf-8")
        assert "resolve_hard_kill_after_s(files, hard_kill_after_s)" in src

    def test_skillaudit_resolves_the_budget_and_routes_large_sets_to_the_pool(self):
        src = (Path(__file__).parent.parent / "scripts" / "cpv_skillaudit_native.py").read_text(encoding="utf-8")
        assert "resolve_hard_kill_after_s(files)" in src
        assert "supervision_is_warranted(files)" in src

    def test_skillaudit_dispatch_sends_a_small_large_file_set_to_the_pool(self, tmp_path, monkeypatch):
        import cpv_skillaudit_native as sa

        files = [_sized_file(tmp_path, "big.md", 2 * MIB)]
        taken: list[str] = []
        monkeypatch.setattr(sa, "_scan_path_parallel", lambda root, f: (taken.append("parallel"), ([], 0))[1])
        monkeypatch.setattr(sa, "_scan_path_serial", lambda root, f: (taken.append("serial"), ([], 0))[1])
        sa._scan_dispatch(tmp_path, files)
        assert taken == ["parallel"], "a below-threshold set with a large file must run where it can be killed"

    def test_skillaudit_dispatch_keeps_tiny_sets_serial(self, tmp_path, monkeypatch):
        import cpv_skillaudit_native as sa

        files = _tiny_files(tmp_path, 3)
        taken: list[str] = []
        monkeypatch.setattr(sa, "_scan_path_parallel", lambda root, f: (taken.append("parallel"), ([], 0))[1])
        monkeypatch.setattr(sa, "_scan_path_serial", lambda root, f: (taken.append("serial"), ([], 0))[1])
        sa._scan_dispatch(tmp_path, files)
        assert taken == ["serial"], "tiny scans must not pay the fixed supervisor cost"


class TestForkParity:
    def test_routing_adds_no_pool_of_its_own(self):
        """Every pool still comes from cpv_fork_safety (tests/test_fork_safety.py
        enforces this at source level; assert it here too so a future routing
        change cannot quietly introduce a fork-defaulted pool)."""
        src = (Path(__file__).parent.parent / "scripts" / "cpv_parallel_runner.py").read_text(encoding="utf-8")
        assert "mp_context=safe_mp_context()" in src
        assert "mp.get_context()" not in src
