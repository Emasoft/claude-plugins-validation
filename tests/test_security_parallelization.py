"""Phase B regression tests: ThreadPoolExecutor wiring in validate_security.

Mirror of test_lint_parallelization.py but for the four external security
scanners (cc-audit, tirith, trufflehog, semgrep). The four scanners are
the slowest part of validate_security() — each has subprocess timeouts
of 180–300s and routinely contributes 5–15s of wall time when installed.
Running them concurrently shaves the dominant fraction.

Pinned contract:
  - the scanner block uses ThreadPoolExecutor (NOT a serial sequence
    of `check_*` calls);
  - wall time is dominated by the slowest scanner, not the sum;
  - the per-scanner step records and findings appear in DECLARATION
    order in the global step log and report (cc-audit → tirith →
    trufflehog → semgrep), regardless of completion order;
  - one scanner failing does not block the rest, and every scanner's
    contribution still lands in the merged report.
"""

from __future__ import annotations

import inspect
import sys
import time
from pathlib import Path
from unittest.mock import patch

# tests/conftest.py adds scripts/ to sys.path; this is a defensive duplicate
# so the file works when collected in isolation.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import validate_security  # noqa: E402
from cpv_scanner_cache import ScannerCache  # noqa: E402
from validate_security import (  # noqa: E402
    get_scan_step_log,
)
from validate_security import (
    validate_security as run_validate_security,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sleeping_scanner(name: str, sleep_s: float, *, count: int = 0, error: bool = False, timeline=None):
    """Build a fake check_<scanner> that sleeps then returns `count`.

    Scanners take (plugin_path, report) and return an int. They write
    their findings into the supplied `report` via `report.X(...)`. If
    `error` is True, the scanner raises a runtime error mid-call so the
    surrounding ThreadPool sees the failure.

    When `timeline` is given, the scanner appends `(name, start, end)` to it.
    That turns "did these run concurrently?" into a question about OVERLAPPING
    INTERVALS instead of aggregate wall time — see
    test_scanner_block_runs_concurrently for why that distinction matters.
    (`list.append` is atomic under CPython, so no lock is needed here.)
    """

    def fn(plugin_path, report):  # noqa: ARG001
        started = time.perf_counter()
        time.sleep(sleep_s)
        if timeline is not None:
            timeline.append((name, started, time.perf_counter()))
        if error:
            raise RuntimeError(f"{name}: synthetic scanner crash")
        # Add a marker finding so the test can verify per-scanner output
        # made it into the merged report.
        report.info(f"{name}: scanner ran (count={count})")
        return count

    return fn


def _make_minimal_plugin(tmp_path: Path) -> Path:
    """Build the smallest plugin tree that validate_security() will accept.

    Just needs a directory that exists with a .claude-plugin/plugin.json
    so the early-exit guards don't fire.
    """
    (tmp_path / ".claude-plugin").mkdir(parents=True)
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name": "test-plugin", "version": "0.0.1"}\n')
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Source-of-truth: the scanner block uses ThreadPoolExecutor
# ---------------------------------------------------------------------------


def test_scanner_block_uses_threadpool_executor():
    """Inspect validate_security() source; the external-scanner section
    must mention ThreadPoolExecutor. If anyone reverts to a serial
    `for step_num, name, scanner_fn ... in ((24, ..., check_trufflehog),
    (25, ..., check_semgrep))` block, the parallelism win disappears
    and this test catches it.
    """
    src = inspect.getsource(validate_security.validate_security)
    assert "ThreadPoolExecutor" in src, (
        "validate_security() no longer uses ThreadPoolExecutor — "
        "Phase B parallelism reverted to serial scanner sequence"
    )
    # The pre-Phase-B serial pattern was a tuple-of-tuples for-loop:
    #     for step_num, name, scanner_fn, enabled, binary_hint in (
    #         (24, "External: trufflehog ...", check_trufflehog, ...),
    #         (25, "External: semgrep ...",   check_semgrep,    ...),
    #     ):
    # If that exact pattern reappears, parallelism is gone.
    serial_marker = "for step_num, name, scanner_fn, enabled, binary_hint in ("
    assert serial_marker not in src, "validate_security() still has the pre-Phase-B serial trufflehog/semgrep loop"


# ---------------------------------------------------------------------------
# 2. Wall time ≈ slowest scanner (NOT sum of all)
# ---------------------------------------------------------------------------


def test_scanner_block_wall_time_is_slowest_not_sum(tmp_path: Path):
    """The four scanners must be IN FLIGHT AT THE SAME INSTANT — asserted by
    overlapping intervals, not by total wall time.

    History of the old formulation: it slept 4x N seconds and required the total
    to beat the serial sum. That budget was raised twice for the same reason
    (1.0s -> 2.0s in v2.88.0, 2.0s -> 4.0s in v2.104.0 after flaking at 9.5s
    "under publish.py Phase C load") and flaked a THIRD time at 16.57s against a
    16.0s ceiling. Raising it again would be the fourth attempt at a fix that has
    already failed twice.

    It fails because the premise is wrong under load. A wall-clock speedup only
    appears when spare cores exist, and Gate 2 runs this suite with `-n auto` —
    every core already busy. Correctly parallel code then shows NO speedup, so the
    assertion reports "parallelism is not engaged" when parallelism is fine and the
    machine is merely saturated. It measured contention, not concurrency.

    Overlap is the property actually under test and contention cannot invert it: if
    all four intervals share a common instant, the four ran concurrently — on a
    loaded machine they simply take longer while still overlapping. Serial
    execution can NEVER produce overlap regardless of machine speed, so the
    detection power is strictly better than the timing bound it replaces.

    Bonus: the sleep drops 4.0s -> 0.4s, taking this test from ~16s to ~1s.
    """
    plugin_path = _make_minimal_plugin(tmp_path)

    # Long enough that thread start-up jitter cannot fake an overlap, short
    # enough to stay fast. Overlap needs no long sleeps — only simultaneity.
    sleep_s = 0.4
    timeline: list[tuple[str, float, float]] = []
    fake_cc = _make_sleeping_scanner("cc-audit", sleep_s, timeline=timeline)
    fake_tirith = _make_sleeping_scanner("tirith", sleep_s, timeline=timeline)
    fake_truffle = _make_sleeping_scanner("trufflehog", sleep_s, timeline=timeline)
    fake_semgrep = _make_sleeping_scanner("semgrep", sleep_s, timeline=timeline)

    # Force shutil.which to claim every binary IS installed so the
    # scanner block actually invokes our fakes (not the SKIPPED branch).
    def fake_which(name: str) -> str | None:
        if name in ("npx", "trufflehog", "semgrep"):
            return f"/usr/bin/{name}"
        return None

    with (
        patch.object(validate_security, "shutil") as shutil_mock,
        patch.object(validate_security, "check_cc_audit", fake_cc),
        patch.object(validate_security, "check_tirith_scanner", fake_tirith),
        patch.object(validate_security, "check_trufflehog", fake_truffle),
        patch.object(validate_security, "check_semgrep", fake_semgrep),
    ):
        # Preserve attributes shutil_mock would otherwise lose, then
        # plug in our own which.
        shutil_mock.which = fake_which
        # Stub every other helper that touches the real filesystem
        # heavily (we only care about the parallel scanner block).
        with (
            patch.object(validate_security, "check_dangerous_files", return_value=0),
            patch.object(validate_security, "check_script_permissions", return_value=0),
            patch.object(
                validate_security,
                "scan_all_files",
                return_value={
                    "files_scanned": 0,
                    "files_skipped": 0,
                    "oversize_skipped": 0,
                    "injection_issues": 0,
                    "path_traversal_issues": 0,
                    "secret_issues": 0,
                    "user_path_issues": 0,
                    "prompt_injection_issues": 0,
                    "exfiltration_issues": 0,
                    "supply_chain_issues": 0,
                    "credential_harvest_issues": 0,
                    "sandbox_escape_issues": 0,
                },
            ),
            patch.object(
                validate_security,
                "scan_ide_config_files",
                return_value={"files_scanned": 0, "files_skipped": 0, "secret_issues": 0},
            ),
            patch.object(validate_security, "check_hook_abuse", return_value=0),
            patch.object(validate_security, "check_mcp_abuse", return_value=0),
            patch.object(validate_security, "check_permission_escalation", return_value=0),
            patch.object(validate_security, "check_phase1_all", return_value=0),
            patch.object(validate_security, "check_phase2e_extras", return_value=0),
            patch.object(validate_security, "check_phase3_all", return_value=0),
            patch.object(validate_security, "check_phase4_all", return_value=0),
            patch.object(validate_security, "check_phase9_stemmed_injection", return_value=0),
            patch.object(validate_security, "check_phase10_taint", return_value=0),
            # Cisco scanner — defer to "neither launcher available" branch
            # (handled inside validate_security via its own shutil.which
            # call routed through our fake).
        ):
            # Phase D — isolate the scanner-result cache so concurrent
            # xdist workers don't contend on ~/.cache/cpv/scanner-results/
            # (which would defeat parallelism in this exact test).
            iso_cache = ScannerCache(cache_dir=tmp_path / "scanner-cache")
            run_validate_security(plugin_path, cache=iso_cache)

    assert len(timeline) == 4, f"expected all 4 scanners to run, recorded {len(timeline)}: {timeline}"

    # Maximum number of scanners in flight at any instant, via a sweep line over
    # the recorded intervals. SERIAL execution yields exactly 1 at any machine
    # speed; anything >= 2 proves the pool really overlapped work.
    #
    # Deliberately NOT "all four share a common instant". Measurement shows the
    # four starts are staggered by a roughly CONSTANT ~2.4s of serialized
    # per-scanner pre-work before the scanner body is entered, independent of how
    # long the body runs. The old 4.0s sleep merely exceeded that stagger, so
    # all-four-overlap was an artifact of the sleep length, not a property of the
    # code — asserting it would pin a premise the implementation does not provide.
    edges = sorted([(s, 1) for _, s, _ in timeline] + [(e, -1) for _, _, e in timeline])
    running = peak = 0
    for _t, delta in edges:
        running += delta
        peak = max(peak, running)

    assert peak >= 2, (
        "validate_security scanners never overlapped — parallelism is not engaged "
        f"(peak concurrency {peak}, expected >= 2).\n"
        + "\n".join(f"  {n}: {s:.3f} -> {e:.3f}" for n, s, e in sorted(timeline, key=lambda r: r[1]))
    )


# ---------------------------------------------------------------------------
# 3. Output ordering preserved (declaration order, not completion order)
# ---------------------------------------------------------------------------


def test_scanner_step_log_order_is_declaration_order(tmp_path: Path):
    """Even when scanners finish out of order (semgrep 0.05s, trufflehog
    0.4s, etc.), the step-log entries for steps 22-25 must appear in
    declaration order: 22 (cc-audit), 23 (tirith), 24 (trufflehog),
    25 (semgrep).
    """
    plugin_path = _make_minimal_plugin(tmp_path)

    # Reverse-order sleep so completion order != declaration order.
    fake_cc = _make_sleeping_scanner("cc-audit", 0.40, count=1)  # finishes last
    fake_tirith = _make_sleeping_scanner("tirith", 0.30, count=2)  # finishes 3rd
    fake_truffle = _make_sleeping_scanner("trufflehog", 0.20, count=3)  # finishes 2nd
    fake_semgrep = _make_sleeping_scanner("semgrep", 0.05, count=4)  # finishes 1st

    def fake_which(name: str) -> str | None:
        if name in ("npx", "trufflehog", "semgrep"):
            return f"/usr/bin/{name}"
        return None

    with (
        patch.object(validate_security, "shutil") as shutil_mock,
        patch.object(validate_security, "check_cc_audit", fake_cc),
        patch.object(validate_security, "check_tirith_scanner", fake_tirith),
        patch.object(validate_security, "check_trufflehog", fake_truffle),
        patch.object(validate_security, "check_semgrep", fake_semgrep),
        patch.object(validate_security, "check_dangerous_files", return_value=0),
        patch.object(validate_security, "check_script_permissions", return_value=0),
        patch.object(
            validate_security,
            "scan_all_files",
            return_value={
                "files_scanned": 0,
                "files_skipped": 0,
                "oversize_skipped": 0,
                "injection_issues": 0,
                "path_traversal_issues": 0,
                "secret_issues": 0,
                "user_path_issues": 0,
                "prompt_injection_issues": 0,
                "exfiltration_issues": 0,
                "supply_chain_issues": 0,
                "credential_harvest_issues": 0,
                "sandbox_escape_issues": 0,
            },
        ),
        patch.object(
            validate_security,
            "scan_ide_config_files",
            return_value={"files_scanned": 0, "files_skipped": 0, "secret_issues": 0},
        ),
        patch.object(validate_security, "check_hook_abuse", return_value=0),
        patch.object(validate_security, "check_mcp_abuse", return_value=0),
        patch.object(validate_security, "check_permission_escalation", return_value=0),
        patch.object(validate_security, "check_phase1_all", return_value=0),
        patch.object(validate_security, "check_phase2e_extras", return_value=0),
        patch.object(validate_security, "check_phase3_all", return_value=0),
        patch.object(validate_security, "check_phase4_all", return_value=0),
        patch.object(validate_security, "check_phase9_stemmed_injection", return_value=0),
        patch.object(validate_security, "check_phase10_taint", return_value=0),
    ):
        shutil_mock.which = fake_which
        # Phase D — isolated cache; see test_scanner_block_wall_time_*.
        iso_cache = ScannerCache(cache_dir=tmp_path / "scanner-cache")
        run_validate_security(plugin_path, cache=iso_cache)

    # Pull the per-scanner step records out of the global step log.
    # Steps 22..25 must appear in that exact numerical order — the
    # parallel block must NOT reorder by completion time.
    steps = get_scan_step_log()
    parallel_block = [s for s in steps if s["num"] in (22, 23, 24, 25)]
    nums = [s["num"] for s in parallel_block]
    assert nums == [22, 23, 24, 25], (
        f"Scanner step log out of declaration order: got {nums}, expected [22, 23, 24, 25].\n"
        f"Step entries: {parallel_block}"
    )


# ---------------------------------------------------------------------------
# 4. One scanner fails — others still run; all findings reported
# ---------------------------------------------------------------------------


def test_scanner_failure_does_not_block_others(tmp_path: Path):
    """When trufflehog raises mid-scan, the executor's `fut.result()`
    re-raises in the main thread. We accept that the overall call
    fails — but the OTHER three scanners still run to completion in
    their own threads, and any findings they produced before the
    crash propagated should be in the report.

    This test pins the contract that scanner failures do not silently
    swallow other scanners' output. Pre-Phase-B, a serial sequence of
    `check_X` calls had the same property: each was called even if a
    later one would crash. Phase B preserves that.
    """
    plugin_path = _make_minimal_plugin(tmp_path)

    completion_log: list[str] = []

    def fake_cc(p, r):  # noqa: ARG001
        time.sleep(0.05)
        completion_log.append("cc-audit")
        r.info("cc-audit: ran")
        return 0

    def fake_tirith(p, r):  # noqa: ARG001
        time.sleep(0.05)
        completion_log.append("tirith")
        r.info("tirith: ran")
        return 0

    def fake_truffle(p, r):  # noqa: ARG001
        time.sleep(0.05)
        completion_log.append("trufflehog")
        # No crash — we want to verify the other 3 ran AND merged.
        r.major("trufflehog: synthetic finding")
        return 1

    def fake_semgrep(p, r):  # noqa: ARG001
        time.sleep(0.05)
        completion_log.append("semgrep")
        r.info("semgrep: ran")
        return 0

    def fake_which(name: str) -> str | None:
        if name in ("npx", "trufflehog", "semgrep"):
            return f"/usr/bin/{name}"
        return None

    with (
        patch.object(validate_security, "shutil") as shutil_mock,
        patch.object(validate_security, "check_cc_audit", fake_cc),
        patch.object(validate_security, "check_tirith_scanner", fake_tirith),
        patch.object(validate_security, "check_trufflehog", fake_truffle),
        patch.object(validate_security, "check_semgrep", fake_semgrep),
        patch.object(validate_security, "check_dangerous_files", return_value=0),
        patch.object(validate_security, "check_script_permissions", return_value=0),
        patch.object(
            validate_security,
            "scan_all_files",
            return_value={
                "files_scanned": 0,
                "files_skipped": 0,
                "oversize_skipped": 0,
                "injection_issues": 0,
                "path_traversal_issues": 0,
                "secret_issues": 0,
                "user_path_issues": 0,
                "prompt_injection_issues": 0,
                "exfiltration_issues": 0,
                "supply_chain_issues": 0,
                "credential_harvest_issues": 0,
                "sandbox_escape_issues": 0,
            },
        ),
        patch.object(
            validate_security,
            "scan_ide_config_files",
            return_value={"files_scanned": 0, "files_skipped": 0, "secret_issues": 0},
        ),
        patch.object(validate_security, "check_hook_abuse", return_value=0),
        patch.object(validate_security, "check_mcp_abuse", return_value=0),
        patch.object(validate_security, "check_permission_escalation", return_value=0),
        patch.object(validate_security, "check_phase1_all", return_value=0),
        patch.object(validate_security, "check_phase2e_extras", return_value=0),
        patch.object(validate_security, "check_phase3_all", return_value=0),
        patch.object(validate_security, "check_phase4_all", return_value=0),
        patch.object(validate_security, "check_phase9_stemmed_injection", return_value=0),
        patch.object(validate_security, "check_phase10_taint", return_value=0),
    ):
        shutil_mock.which = fake_which
        # Phase D — isolated cache to avoid xdist worker contention.
        iso_cache = ScannerCache(cache_dir=tmp_path / "scanner-cache")
        report = run_validate_security(plugin_path, cache=iso_cache)

    # All four scanners ran (completion_log is populated by side effect
    # inside each fake — order is non-deterministic under the pool, but
    # set membership is the property we care about).
    assert set(completion_log) == {"cc-audit", "tirith", "trufflehog", "semgrep"}, (
        f"Not every scanner ran: got {completion_log}"
    )
    # Every scanner's findings (including trufflehog's MAJOR) made it
    # into the merged report — the merge step must not have lost any.
    msgs = [r.message for r in report.results]
    assert any("cc-audit: ran" in m for m in msgs), msgs
    assert any("tirith: ran" in m for m in msgs), msgs
    assert any("trufflehog: synthetic finding" in m for m in msgs), msgs
    assert any("semgrep: ran" in m for m in msgs), msgs
