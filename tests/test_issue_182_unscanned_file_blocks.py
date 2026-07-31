#!/usr/bin/env python3
"""Issue #182 — a file whose per-file scan did not complete must BLOCK.

``parallel_scan`` returns ``ScanResult(findings=[], error=...)`` when a worker
times out or dies. ``findings=[]`` is indistinguishable downstream from "this
file is clean", and until v4.2.0 both SECURITY sinks leaned on that:

* ``validate_security`` reported a WARNING, which never blocks — not even under
  ``--strict`` (``exit_code_strict``: "WARNING still does not block even in
  strict mode").
* ``cpv_skillaudit_native`` reported severity ``"low"`` → NIT, which blocks only
  under ``--strict`` — and the untrusted pre-install scan runs in DEFAULT mode.

Measured before the fix, on a plugin whose ``curl … | sh`` payload lived in a
file padded until its scan took 7.27 s: killing that scan moved the run from
``NIT:3 WARNING:0`` to ``NIT:2 WARNING:1`` — the payload's blocking finding
replaced by one that cannot block. A plugin author controls how slow their own
files are to scan, so that is an attacker-triggerable mute.

Two-sided throughout: an incomplete scan must block, and a COMPLETED scan must
be entirely unaffected (the fix must not invent findings for healthy plugins).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_parallel_runner import (  # noqa: E402
    ScanResult,
    result_is_timeout,
    retry_failed_serially,
)
from cpv_skillaudit_native import _SEVERITY_MAP, _to_cpv_severity  # noqa: E402
from cpv_validation_common import EXIT_OK, ValidationReport  # noqa: E402

# ---------------------------------------------------------------------------
# The severity contract that makes the change load-bearing
# ---------------------------------------------------------------------------


def test_warning_does_not_block_in_either_mode() -> None:
    """This is WHY the old WARNING was a mute, stated as an executable fact."""
    r = ValidationReport()
    r.warning("security scan did not complete", "skills/demo/SKILL.md")
    assert r.exit_code == EXIT_OK, "a WARNING must not block in default mode"
    assert r.exit_code_strict() == EXIT_OK, "a WARNING must not block under --strict either"


def test_major_blocks_in_both_modes() -> None:
    """The replacement severity must block in DEFAULT mode too — the untrusted
    pre-install scan does not pass --strict."""
    r = ValidationReport()
    r.major("security scan did not complete", "skills/demo/SKILL.md")
    assert r.exit_code != EXIT_OK, "a MAJOR must block in default mode"
    assert r.exit_code_strict() != EXIT_OK, "a MAJOR must block under --strict"


def test_nit_does_not_block_default_mode() -> None:
    """NEGATIVE control for the skillaudit half: the old "low"→nit severity was
    invisible in exactly the mode that matters most."""
    r = ValidationReport()
    r.nit("skillaudit worker failed")
    assert r.exit_code == EXIT_OK, "a NIT does not block default mode — the old hole"
    assert r.exit_code_strict() != EXIT_OK


def test_skillaudit_worker_error_severity_maps_to_major() -> None:
    """The synthetic finding uses "high"; the map must turn that into major."""
    assert _SEVERITY_MAP["high"] == "major"
    assert _to_cpv_severity("high") == "major"
    # And the superseded value really was the weaker one — so a future revert
    # to "low" cannot pass unnoticed.
    assert _to_cpv_severity("low") == "nit"


def test_skillaudit_emits_high_for_an_unscannable_file(monkeypatch, tmp_path: Path) -> None:
    """End-to-end through the real aggregator: a worker that always raises must
    yield a MAJOR-mapping finding, not a nit."""
    import cpv_skillaudit_native as sa

    target = tmp_path / "SKILL.md"
    target.write_text("# doc\n", encoding="utf-8")

    def _always_raises(_path: Path) -> list:
        raise RuntimeError("boom")

    # Patch BOTH the pool worker and the serial retry target (same symbol).
    monkeypatch.setattr(sa, "_scan_one_file_skillaudit", _always_raises)
    findings, scanned = sa._scan_path_parallel(tmp_path, [target])

    worker_errors = [f for f in findings if f["ruleId"] == "SKILLAUDIT_WORKER_ERROR"]
    assert len(worker_errors) == 1, "an unscannable file must produce exactly one finding"
    assert worker_errors[0]["severity"] == "high"
    assert _to_cpv_severity(worker_errors[0]["severity"]) == "major"
    assert scanned == 0, "a file that failed must not count as scanned"


def _run_security_merge_with(monkeypatch, tmp_path: Path, scan_results) -> ValidationReport:
    """Drive validate_security's merge loop over a supplied ScanResult list.

    We patch ``parallel_scan`` rather than the worker because the worker runs in
    a SPAWNED child, which never sees a parent-side monkeypatch.
    """
    import cpv_parallel_runner
    import validate_security as vs

    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "SKILL.md").write_text("# doc\n", encoding="utf-8")
    files = vs._collect_files_for_scan(tmp_path)
    assert files, "fixture must produce at least one scannable file"

    monkeypatch.setattr(vs, "_parallel_scan_threshold", lambda: 1)
    monkeypatch.setattr(
        cpv_parallel_runner,
        "parallel_scan",
        lambda f, fn, **kw: [scan_results(p) for p in f],
    )
    report = ValidationReport()
    vs.scan_all_files(tmp_path, report)
    return report


def test_security_merge_blocks_when_a_file_was_not_scanned(monkeypatch, tmp_path: Path) -> None:
    """The sink under test: a timed-out file must BLOCK, in DEFAULT mode.

    A timeout (not a crash) is used deliberately — it is the shape #182 reports,
    and it is the one retry_failed_serially must not paper over.
    """
    report = _run_security_merge_with(
        monkeypatch,
        tmp_path,
        lambda p: ScanResult(file_path=p, findings=[], error="TimeoutError: scan exceeded 300s"),
    )
    assert report.exit_code != EXIT_OK, "an unscanned file must block WITHOUT --strict"
    assert report.exit_code_strict() != EXIT_OK
    assert any("UNVERIFIED" in str(r.message) for r in report.results), (
        "the finding must say the file is unverified, not merely that a worker failed"
    )


def test_security_merge_does_not_block_a_fully_scanned_tree(monkeypatch, tmp_path: Path) -> None:
    """NEGATIVE control: when every file scanned, the fix adds nothing."""
    report = _run_security_merge_with(
        monkeypatch,
        tmp_path,
        lambda p: ScanResult(file_path=p, findings=[({}, {})], error=None),
    )
    assert report.exit_code == EXIT_OK, "a clean, fully-scanned tree must stay clean"
    assert not any("UNVERIFIED" in str(r.message) for r in report.results)


# ---------------------------------------------------------------------------
# retry_failed_serially — absorbs the transient, never the wedge
# ---------------------------------------------------------------------------


def test_retry_replaces_a_crashed_result_when_the_retry_succeeds() -> None:
    """A transient pool death (CI OOM-kill) must not fail an honest plugin."""
    items = [Path("a.md"), Path("b.md")]
    results = [
        ScanResult(file_path=items[0], findings=["ok"], error=None),
        ScanResult(file_path=items[1], findings=[], error="RuntimeError: transient"),
    ]
    out = retry_failed_serially(results, items, lambda p: [f"rescanned:{p.name}"])

    assert out[0].findings == ["ok"], "a successful result must be left untouched"
    assert out[1].error is None, "a crash whose retry succeeds must clear"
    assert out[1].findings == ["rescanned:b.md"]


def test_retry_keeps_the_original_error_when_the_retry_fails_again() -> None:
    """A REPRODUCIBLE failure stays reported — and keeps the FIRST diagnosis,
    which is the representative one."""
    items = [Path("a.md")]
    results = [ScanResult(file_path=items[0], findings=[], error="RuntimeError: first")]

    def _still_broken(_p: Path) -> list:
        raise ValueError("second, less informative")

    out = retry_failed_serially(results, items, _still_broken)
    assert out[0].error == "RuntimeError: first"
    assert out[0].findings == []


def test_a_timeout_is_never_retried() -> None:
    """THE load-bearing negative: re-running a wedged scan in-process would hang
    the main thread with nothing left to preempt it."""
    items = [Path("wedged.md")]
    results = [ScanResult(file_path=items[0], findings=[], error="TimeoutError: scan exceeded 300s")]
    calls: list[Path] = []

    def _must_not_run(p: Path) -> list:
        calls.append(p)
        return ["should never happen"]

    out = retry_failed_serially(results, items, _must_not_run)
    assert calls == [], "a timed-out file must NOT be re-run in this process"
    assert out[0].error == "TimeoutError: scan exceeded 300s", "the timeout must survive to the sink"


def test_retry_does_not_touch_a_clean_run() -> None:
    """NEGATIVE: with nothing failed, the helper is a no-op and never calls the
    scan function — a healthy plugin pays nothing."""
    items = [Path("a.md"), Path("b.md")]
    results = [ScanResult(file_path=p, findings=[], error=None) for p in items]
    calls: list[Path] = []

    out = retry_failed_serially(results, items, lambda p: calls.append(p) or [])
    assert calls == []
    assert out == results


def test_retry_preserves_order_with_mixed_outcomes() -> None:
    items = [Path(f"{i}.md") for i in range(4)]
    results = [
        ScanResult(file_path=items[0], findings=["a"], error=None),
        ScanResult(file_path=items[1], findings=[], error="RuntimeError: transient"),
        ScanResult(file_path=items[2], findings=[], error="TimeoutError: wedged"),
        ScanResult(file_path=items[3], findings=["d"], error=None),
    ]
    out = retry_failed_serially(results, items, lambda p: [f"r:{p.name}"])

    assert [r.file_path for r in out] == items, "input order must be preserved"
    assert out[0].findings == ["a"]
    assert out[1].findings == ["r:1.md"], "the crash was retried"
    assert out[2].error == "TimeoutError: wedged", "the timeout was not"
    assert out[3].findings == ["d"]


def test_retry_rejects_a_length_mismatch() -> None:
    """Fail loudly rather than silently mis-pairing a result with a file."""
    with pytest.raises(ValueError, match="length mismatch"):
        retry_failed_serially([], [Path("a.md")], lambda p: [])


def test_result_is_timeout_is_two_sided() -> None:
    assert result_is_timeout(ScanResult(file_path=Path("a"), findings=[], error="TimeoutError: x"))
    assert not result_is_timeout(ScanResult(file_path=Path("a"), findings=[], error="RuntimeError: x"))
    assert not result_is_timeout(ScanResult(file_path=Path("a"), findings=[], error=None))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
